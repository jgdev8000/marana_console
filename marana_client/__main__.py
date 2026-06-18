"""Entry point: python -m marana_client [options]"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from marana_client import config as cfg_mod
from marana_client.client import MaranaClient, ClientRequestTimeout
from marana_client.io_tiff import write_snapshot, write_stack
from marana_client.meta import build_snapshot_metadata
from marana_client.worker import ClientWorker
from marana_client.ui.connection_card import ConnectionCard
from marana_client.ui.image_view import MaranaImageView
from marana_client.ui.kinetic_panel import KineticPanel
from marana_client.ui.kinetic_save_dialog import KineticSaveDialog
from marana_client.ui.live_panel import LivePanel
from marana_client.ui.focus_panel import FocusPanel
from marana_client.ui.quick_acquire_dialog import QuickAcquireDialog
from marana_client.ui.main_window import MainWindow
from marana_client.ui.side_panels import (
    CoolingPanel, DisplayPanel, ContrastPanel, StatusLog,
)
from marana_client.ui.theme import apply_theme
from marana_proto import messages as m
from marana_proto.errors import MaranaError


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="marana_client")
    cfg = cfg_mod.load()
    p.add_argument("--host", default=cfg["host"])
    p.add_argument("--ctrl-port", type=int, default=cfg["ctrl_port"])
    p.add_argument("--frame-port", type=int, default=cfg["frame_port"])
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    app = QtWidgets.QApplication(sys.argv)
    apply_theme(app)

    cfg = cfg_mod.load()
    cfg["host"] = args.host
    cfg["ctrl_port"] = args.ctrl_port
    cfg["frame_port"] = args.frame_port
    cfg_mod.save(cfg)

    ctrl_ep = f"tcp://{args.host}:{args.ctrl_port}"
    pub_ep = f"tcp://{args.host}:{args.frame_port}"
    client = MaranaClient(ctrl_endpoint=ctrl_ep, pub_endpoint=pub_ep, default_timeout_ms=3000)

    # MainWindow
    win = MainWindow(host=args.host)
    image_view = MaranaImageView()
    win.install_image_view(image_view)
    live = LivePanel(); kinetic = KineticPanel(); focus = FocusPanel()
    win.install_left_panels(live, kinetic, focus)
    cooling = CoolingPanel(); disp = DisplayPanel(); contrast = ContrastPanel(); status_log = StatusLog()
    win.install_right_panels(cooling, disp, contrast, status_log)

    # Worker
    worker = ClientWorker(client)
    th = QtCore.QThread()
    worker.moveToThread(th)
    th.started.connect(worker.run)
    th.start()

    # State the GUI mirrors
    server_info: dict = {}
    latest_live_header: dict = {}
    latest_live_frame: np.ndarray | None = None
    live_auto = {"on": False}   # auto-display mode: when True, every shown frame (live/kinetic/
                                # focus/scrub) auto-stretches. Auto turns it on; manual edit or
                                # live restart turns it off.
    quick_acq = {"active": False, "restore": None}   # ACQUIRE & SAVE quick-acquisition context

    # --- Hello + populate features ---
    try:
        server_info = client.request("hello", {}, timeout_ms=3000)
        win.set_camera_info(server_info.get("camera_model", "--"), server_info.get("camera_serial", "--"))
        win.connection_card.set_state(ConnectionCard.STATE_HEALTHY)
        status_log.append(f"Connected to {server_info.get('camera_model', '?')}", "info")
    except ClientRequestTimeout:
        win.connection_card.set_state(ConnectionCard.STATE_DEGRADED)
        status_log.append(f"hello timeout connecting to {args.host}", "error")
    except Exception as e:
        status_log.append(f"connect failed: {e}", "error")

    # Current exposure
    try:
        live.set_exposure_value(client.request("get_feature", {"name": "ExposureTime"})["value"])
    except Exception as e:
        status_log.append(f"ExposureTime read failed: {e}", "warn")

    # Gain combo + derived Speed/Encoding/BitDepth indicators (availability-filtered).
    def _refresh_acq_settings():
        try:
            snap = client.request("get_acq_settings", {})
            live.populate_acq_settings(snap)
        except Exception as e:
            status_log.append(f"get_acq_settings failed: {e}", "warn")
    _refresh_acq_settings()

    # AOI tracking (origin needed to translate mouse selections to sensor coords)
    cur_aoi = {"x0": 0, "y0": 0}

    def _read_aoi():
        """Read the camera's actual AOI, update tracked origin + both panels."""
        x0 = client.request("get_feature", {"name": "AOILeft"})["value"] - 1
        y0 = client.request("get_feature", {"name": "AOITop"})["value"] - 1
        w_ = client.request("get_feature", {"name": "AOIWidth"})["value"]
        h_ = client.request("get_feature", {"name": "AOIHeight"})["value"]
        cur_aoi["x0"], cur_aoi["y0"] = x0, y0
        live.set_aoi_values(x0, x0 + w_ - 1, y0, y0 + h_ - 1)
        kinetic.set_aoi_for_estimate(x0, x0 + w_ - 1, y0, y0 + h_ - 1)
        focus.set_aoi(x0, x0 + w_ - 1, y0, y0 + h_ - 1)

    try:
        _read_aoi()
    except Exception as e:
        status_log.append(f"AOI initial read failed: {e}", "warn")

    # Focus panel initial state
    focus.apply_persisted_state(cfg)
    try:
        rbv = client.request("read_motor_rbv", {"mover_pv_base": focus.mover_pv_base()})
        focus.set_start_z_um(rbv["z_um"], dllm_um=rbv["dllm_mm"] * 1e3, dhlm_um=rbv["dhlm_mm"] * 1e3)
    except Exception as e:
        status_log.append(f"focus initial Z read failed: {e}", "warn")

    # --- Wire signals -> client REQs ---
    def safe_req(cmd: str, args_: dict | None = None, timeout_ms: int | None = None):
        try:
            result = client.request(cmd, args_ or {}, timeout_ms=timeout_ms)
            win.connection_card.mark_healthy()   # any successful round-trip => connected
            return result
        except ClientRequestTimeout:
            status_log.append(f"{cmd}: timeout", "warn")
            win.connection_card.set_state(ConnectionCard.STATE_DEGRADED)
        except MaranaError as e:
            status_log.append(f"{cmd}: {e}", "error")
        except Exception as e:
            status_log.append(f"{cmd}: {e}", "error")
        return None

    def _on_set_feature(name, value):
        safe_req("set_feature", {"name": name, "value": value})
        if name == "GainMode":
            # Encoding follows gain: 16-bit gain -> Mono16, otherwise Mono12.
            encoding = "Mono16" if "16-bit" in str(value) else "Mono12"
            safe_req("set_feature", {"name": "PixelEncoding", "value": encoding})
            _refresh_acq_settings()   # Speed/Encoding/BitDepth indicators update

    live.requestSetFeature.connect(_on_set_feature)
    def _start_live():
        live_auto["on"] = False   # auto is off on (re)start; user presses Auto to enable per-frame
        safe_req("start_live", {})
        win.set_live_indicator(True)
    live.requestStartLive.connect(_start_live)
    live.requestStop.connect(lambda: (safe_req("stop", {}), win.set_live_indicator(False),
                                      live.set_live_active(False), live_auto.update(on=False)))
    def _apply_aoi(x0, x1, y0, y1):
        """Set the camera AOI (0-based inclusive). AOI is a cold setting and the
        camera isn't thread-safe, so stop live first, apply, then resume — or, if
        idle, snap one frame so the cropped region shows immediately. Re-reads the
        actual applied AOI (the camera snaps to alignment) and refreshes panels."""
        was_live = live.live_button.isChecked()
        if was_live:
            safe_req("stop", {})
            live.set_live_active(False)
            win.set_live_indicator(False)
        safe_req("set_feature", {"name": "AOIWidth", "value": x1 - x0 + 1})
        safe_req("set_feature", {"name": "AOIHeight", "value": y1 - y0 + 1})
        safe_req("set_feature", {"name": "AOILeft", "value": x0 + 1})
        safe_req("set_feature", {"name": "AOITop", "value": y0 + 1})
        try:
            _read_aoi()
        except Exception as e:
            status_log.append(f"AOI re-read failed: {e}", "warn")
        if was_live:
            _start_live()        # frames resume at the new AOI and auto-display
        else:
            _snap_display()      # show the cropped region right away

    live.requestSetAoiFull.connect(
        lambda: _apply_aoi(0, server_info.get("sensor_w", 2048) - 1,
                           0, server_info.get("sensor_h", 2048) - 1))
    live.requestSetAoi.connect(_apply_aoi)

    def _on_aoi_drawn(r0, r1, c0, c1):
        # raw rect is relative to the current AOI; offset by its origin to sensor coords
        _apply_aoi(cur_aoi["x0"] + c0, cur_aoi["x0"] + c1,
                   cur_aoi["y0"] + r0, cur_aoi["y0"] + r1)
        status_log.append(f"AOI set from selection: {c1 - c0 + 1}x{r1 - r0 + 1}", "info")
    image_view.aoiSelected.connect(_on_aoi_drawn)

    # Snapshot save (PC-side)
    def _snap_now():
        if latest_live_frame is None:
            status_log.append("no live frame to snap", "warn")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "Save snapshot", f"{cfg.get('snapshot_dir', '.')}/marana.tif", "TIFF (*.tif)")
        if not path:
            return
        md = build_snapshot_metadata(server_info, latest_live_header,
                                     display={"rot": image_view.state.rot,
                                              "flip_h": image_view.state.flip_h,
                                              "flip_v": image_view.state.flip_v})
        try:
            write_snapshot(path, latest_live_frame.copy(), md)
            status_log.append(f"saved {path}", "info")
            cfg["snapshot_dir"] = os.path.dirname(path); cfg_mod.save(cfg)
        except Exception as e:
            status_log.append(f"save failed: {e}", "error")

    def _acquire_and_save():
        """Quick-acquisition button: pop a modal for exposure / gain / frame
        count, run a one-shot kinetic burst with those settings, then save the
        result (the save + exposure/gain restore happen on KINETIC_COMPLETE).
        SNAP & SAVE remains the one-click 'save the current frame' path."""
        if quick_acq["active"]:
            status_log.append("a quick acquisition is already running", "warn")
            return
        gain_opts = [live.gain_combo.itemText(i) for i in range(live.gain_combo.count())]
        cur_gain = live.gain_combo.currentText() if live.gain_combo.count() else None
        dlg = QuickAcquireDialog(exposure_s=live.exposure_spin.value(),
                                 gain_options=gain_opts, current_gain=cur_gain, parent=win)
        if not dlg.exec():
            return
        vals = dlg.values()
        # Remember the live settings so we can put them back when the burst ends.
        quick_acq["restore"] = {"exposure_s": live.exposure_spin.value(), "gain": cur_gain}
        quick_acq["active"] = True
        # Apply requested settings (gain also drives encoding + indicators).
        safe_req("set_feature", {"name": "ExposureTime", "value": vals["exposure_s"]})
        live.set_exposure_value(vals["exposure_s"])
        if vals["gain"]:
            _on_set_feature("GainMode", vals["gain"])
        # Reuse the server kinetic burst path (frames=1 -> single-frame stack).
        safe_req("stop", {})
        win.set_live_indicator(False)
        win.set_scrubber_available(False)
        r = safe_req("start_kinetic", {"frame_count": vals["frame_count"],
                                       "exposure_s": vals["exposure_s"],
                                       "frame_rate_hz": 200.0})
        if r is None:
            quick_acq["active"] = False
            return
        safe_req("confirm_kinetic", {})
        status_log.append(f"quick acquire: {vals['frame_count']} frame(s) @ {vals['exposure_s']}s", "info")

    def _snap_display():
        # Acquire one fresh frame and show it — no save dialog, nothing written.
        nonlocal latest_live_frame, latest_live_header
        r = safe_req("snap_single", {"exposure_s": live.exposure_spin.value()}, timeout_ms=60000)
        if r is None:
            return
        arr = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(r["header"]["height"], r["header"]["width"])
        # Stash as the currently-displayed frame so SAVE / SNAP & SAVE write this one.
        latest_live_frame = arr
        latest_live_header = r["header"]
        image_view.update_frame(arr)   # snap holds current levels; user presses Auto to stretch
        status_log.append("snapped frame (display only)", "info")

    def _save_displayed():
        # Save the currently displayed frame (snap or live); server auto-names <YYMMDD>_N.tif.
        if latest_live_frame is None:
            status_log.append("no frame to save — SNAP or start LIVE first", "warn")
            return
        frame = latest_live_frame
        r = safe_req("save_snapshot", {
            "frame_bytes": frame.tobytes(),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "display": {"rot": image_view.state.rot,
                        "flip_h": image_view.state.flip_h,
                        "flip_v": image_view.state.flip_v},
        }, timeout_ms=120_000)
        if r is not None:
            status_log.append(f"saved {r['path']} ({r['bytes_written']} bytes)", "info")

    live.requestSnapDisplay.connect(_snap_display)
    live.requestSnapNow.connect(_snap_now)
    live.requestSaveDisplayed.connect(_save_displayed)
    live.requestAcquireAndSave.connect(_acquire_and_save)

    # Kinetic flow — must call `stop` first so the server isn't already in LIVE
    # (the ICE server's contract; concurrent SDK threads cause AT_ERR_TIMEDOUT).
    def _start_kinetic(n, e, fps):
        safe_req("stop", {})            # no-op if IDLE
        win.set_live_indicator(False)
        win.set_scrubber_available(False)   # previous results are stale
        r = safe_req("start_kinetic", {"frame_count": n, "exposure_s": e, "frame_rate_hz": fps})
        if r is not None:
            kinetic.on_kinetic_budget_reply(r["ram_estimate_bytes"], r["ram_free_bytes"])

    kinetic.requestStartKinetic.connect(_start_kinetic)
    kinetic.requestConfirmKinetic.connect(lambda: (safe_req("confirm_kinetic", {}), win.set_scrubber_available(False)))
    kinetic.requestCancelKinetic.connect(lambda: safe_req("cancel_kinetic", {}))

    def _save_stack():
        dialog = KineticSaveDialog(
            list_dir_callable=lambda sub: client.request("list_kinetic_save_dir", {"subdir": sub}),
            default_subdir=cfg.get("kinetic_subdir", ""),
            parent=win,
        )
        if dialog.exec():
            rel = dialog.chosen_relative_path()
            r = safe_req("save_kinetic_stack", {"path": rel}, timeout_ms=120_000)
            if r is not None:
                status_log.append(f"saved stack: {r['path']} ({r['bytes_written']} bytes)", "info")
                cfg["kinetic_subdir"] = "/".join(rel.split("/")[:-1]); cfg_mod.save(cfg)

    def _finish_quick_acquire(frames_done: int):
        """KINETIC_COMPLETE handler for an ACQUIRE & SAVE quick acquisition: pull
        the buffered frames off the server and save them to the CLIENT PC (one
        frame -> single-page TIFF; N>1 -> multi-page stack), then restore the live
        exposure/gain."""
        try:
            if frames_done <= 0:
                status_log.append("quick acquire produced no frames", "warn")
                return
            first = safe_req("get_kinetic_frame", {"index": 0}, timeout_ms=30_000)
            if first is None:
                return
            h = first["header"]["height"]; w = first["header"]["width"]
            stack = np.empty((frames_done, h, w), dtype=np.uint16)
            stack[0] = np.frombuffer(first["frame_bytes"], dtype=np.uint16).reshape(h, w)
            for i in range(1, frames_done):
                r = safe_req("get_kinetic_frame", {"index": i}, timeout_ms=30_000)
                if r is None:
                    status_log.append(f"quick acquire: frame {i} fetch failed; saving {i}", "warn")
                    stack = stack[:i]
                    break
                stack[i] = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(h, w)
            n = int(stack.shape[0])
            default = f"{cfg.get('snapshot_dir', '.')}/marana_acq.tif"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                win, "Save acquired image" + ("s" if n > 1 else ""), default, "TIFF (*.tif)")
            if not path:
                status_log.append("quick acquire: save cancelled", "warn")
                return
            md = build_snapshot_metadata(
                server_info, first["header"],
                display={"rot": image_view.state.rot,
                         "flip_h": image_view.state.flip_h,
                         "flip_v": image_view.state.flip_v},
                extra={"frame_count": n, "mode": "quick_acquire"})
            try:
                if n == 1:
                    write_snapshot(path, stack[0], md)
                else:
                    write_stack(path, stack, md)
                status_log.append(f"saved {path} ({n} frame(s))", "info")
                cfg["snapshot_dir"] = os.path.dirname(path); cfg_mod.save(cfg)
            except Exception as e:
                status_log.append(f"save failed: {e}", "error")
        finally:
            rest = quick_acq.get("restore") or {}
            quick_acq["active"] = False
            quick_acq["restore"] = None
            if rest.get("exposure_s") is not None:
                safe_req("set_feature", {"name": "ExposureTime", "value": rest["exposure_s"]})
                live.set_exposure_value(rest["exposure_s"])
            if rest.get("gain"):
                _on_set_feature("GainMode", rest["gain"])
            status_log.append("quick acquire done; restored live exposure/gain", "info")

    def _save_frame(index: int):
        r = safe_req("get_kinetic_frame", {"index": index}, timeout_ms=30_000)
        if r is None:
            return
        arr = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(r["header"]["height"], r["header"]["width"])
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "Save kinetic frame", f"{cfg.get('snapshot_dir', '.')}/marana_kf_{index:04d}.tif", "TIFF (*.tif)")
        if not path:
            return
        md = build_snapshot_metadata(server_info, r["header"], extra={"kinetic_frame_index": index})
        try:
            write_snapshot(path, arr, md)
            status_log.append(f"saved {path}", "info")
        except Exception as e:
            status_log.append(f"save failed: {e}", "error")

    kinetic.requestSaveStack.connect(_save_stack)
    kinetic.requestSaveFrame.connect(_save_frame)
    kinetic.scrubber.valueChanged.connect(lambda v: _scrub_to(v))

    def _scrub_to(idx: int):
        r = safe_req("get_kinetic_frame", {"index": idx}, timeout_ms=10_000)
        if r is None: return
        arr = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(r["header"]["height"], r["header"]["width"])
        image_view.update_frame(arr, auto=live_auto["on"])

    # --- Focus flow ---
    def _start_focus(params: dict):
        safe_req("stop", {})          # ICE pattern: ensure IDLE
        win.set_live_indicator(False)
        plan = safe_req("start_focus", params)
        if plan is not None:
            focus.on_plan_reply(plan)
        cfg["focus_direction"] = int(params["direction"])
        cfg["focus_range_um"] = float(params["range_um"])
        cfg["focus_step_um"] = float(params["step_um"])
        cfg["focus_exposure_s"] = float(params["exposure_s"])
        cfg["focus_settle_ms"] = int(params["settle_ms"])
        cfg["focus_return_to_start"] = bool(params["return_to_start"])
        cfg_mod.save(cfg)

    def _refresh_start_z(mover_pv_base: str):
        rbv = safe_req("read_motor_rbv", {"mover_pv_base": mover_pv_base})
        if rbv is not None:
            focus.set_start_z_um(rbv["z_um"], dllm_um=rbv["dllm_mm"] * 1e3, dhlm_um=rbv["dhlm_mm"] * 1e3)

    def _persist_mover_source(src: str):
        cfg["mover_source"] = src
        cfg_mod.save(cfg)

    def _save_focus_stack():
        dialog = KineticSaveDialog(
            list_dir_callable=lambda sub: client.request("list_kinetic_save_dir", {"subdir": sub}),
            default_subdir=cfg.get("kinetic_subdir", ""),
            default_name="marana_focus.tif",
            parent=win,
        )
        if dialog.exec():
            rel = dialog.chosen_relative_path()
            r = safe_req("save_focus_stack", {"path": rel}, timeout_ms=120_000)
            if r is not None:
                status_log.append(f"saved focus stack: {r['path']} ({r['bytes_written']} bytes)", "info")
                cfg["kinetic_subdir"] = "/".join(rel.split("/")[:-1]); cfg_mod.save(cfg)

    def _auto_save_focus_stack(done: int, partial: bool):
        # Auto-save every completed series; server picks focus/<YYMMDD>_N.tif.
        if done <= 0:
            return
        r = safe_req("save_focus_stack", {}, timeout_ms=120_000)
        if r is not None:
            tag = " (partial)" if partial else ""
            status_log.append(
                f"Auto-saved focus stack{tag}: {r['path']} ({r['bytes_written']} bytes)", "info")

    focus.requestStartFocus.connect(_start_focus)
    focus.requestConfirmFocus.connect(lambda: safe_req("confirm_focus", {}))
    focus.requestCancelFocus.connect(lambda: safe_req("cancel_focus", {}))
    focus.requestSaveFocusStack.connect(_save_focus_stack)
    focus.requestRefreshStartZ.connect(_refresh_start_z)
    focus.requestSetMoverSource.connect(_persist_mover_source)

    # Side panels
    cooling.requestSetCooling.connect(lambda enable, t: safe_req("cooling_set", {"enable": enable, "target_c": t}))
    disp.requestRotation.connect(image_view.set_rotation)
    disp.requestFlip.connect(image_view.set_flip)
    # Levels are the single source of truth, shared with the histogram.
    contrast.requestSetLevels.connect(lambda lo, hi: (image_view.set_levels(lo, hi),
                                                      live_auto.update(on=False)))
    contrast.requestAuto.connect(lambda: (image_view.reset_auto(), live_auto.update(on=True)))
    image_view.levelsChanged.connect(contrast.set_values)          # auto/drag -> boxes
    image_view.userEditedLevels.connect(lambda: live_auto.update(on=False))  # drag = manual

    # Worker -> GUI updates
    def _on_frame(topic: bytes, header: dict, arr: np.ndarray) -> None:
        nonlocal latest_live_frame, latest_live_header
        win.connection_card.mark_healthy()   # a frame proves the server is alive
        if topic == m.TOPIC_LIVE_FRAME:
            latest_live_frame = arr
            latest_live_header = header
            # Per-frame auto only while live_auto is on (user pressed Auto since
            # this live session started); otherwise hold the current levels.
            image_view.update_frame(arr, auto=live_auto["on"])
        elif topic == m.TOPIC_KINETIC_FRAME:
            image_view.update_frame(arr, auto=live_auto["on"])
        elif topic == m.TOPIC_FOCUS_PROGRESS:
            image_view.update_frame(arr, auto=live_auto["on"])
            focus.on_focus_progress(header["frame_idx"], header["frames_total"], header["z_um"])

    def _on_status(topic: bytes, header: dict) -> None:
        win.connection_card.mark_healthy()   # any status event proves the server is alive
        if topic == m.TOPIC_TEMPERATURE:
            win.set_temperature(header.get("sensor_temp_c", 0.0), header.get("status", "--"))
            cooling.update_cooling(
                header.get("enabled", False), header.get("target_c", 0.0),
                header.get("sensor_temp_c", 0.0), header.get("status", "--"),
            )
        elif topic == m.TOPIC_KINETIC_PROGRESS:
            kinetic.on_progress(header["frames_done"], header["frames_total"], header["achieved_fps"])
        elif topic == m.TOPIC_KINETIC_COMPLETE:
            kinetic.on_complete(header["frames_done"], header["frames_total"], header.get("partial", False))
            win.set_scrubber_available(header["frames_done"] > 0)
            win.set_live_indicator(False)
            if quick_acq["active"]:
                _finish_quick_acquire(header["frames_done"])
        elif topic == m.TOPIC_FOCUS_COMPLETE:
            done = header["frames_done"]
            partial = header.get("partial", False)
            focus.on_focus_complete(done, header["frames_total"], partial)
            win.set_live_indicator(False)
            _auto_save_focus_stack(done, partial)
        elif topic == m.TOPIC_STATE:
            state = header.get("state")
            status_log.append(f"state: {state}", "info")
            is_live = state == "LIVE"
            if state in ("LIVE", "IDLE", "ERROR"):
                win.set_live_indicator(is_live)
                live.set_live_active(is_live)   # keep the LIVE button lit only while actually live

    worker.frameReady.connect(_on_frame)
    worker.statusEvent.connect(_on_status)
    worker.error.connect(lambda sev, msg: status_log.append(msg, sev))

    win.show()
    rc = app.exec()
    worker.stop()
    th.quit(); th.wait(2000)
    client.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
