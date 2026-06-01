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
from marana_client.io_tiff import write_snapshot
from marana_client.meta import build_snapshot_metadata
from marana_client.worker import ClientWorker
from marana_client.ui.connection_card import ConnectionCard
from marana_client.ui.image_view import MaranaImageView
from marana_client.ui.kinetic_panel import KineticPanel
from marana_client.ui.kinetic_save_dialog import KineticSaveDialog
from marana_client.ui.live_panel import LivePanel
from marana_client.ui.focus_panel import FocusPanel
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
    pending_live_auto = {"v": False}   # auto-stretch once on the first frame after live starts

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
            return client.request(cmd, args_ or {}, timeout_ms=timeout_ms)
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
        pending_live_auto["v"] = True   # auto-stretch on the first live frame
        safe_req("start_live", {})
        win.set_live_indicator(True)
    live.requestStartLive.connect(_start_live)
    live.requestStop.connect(lambda: (safe_req("stop", {}), win.set_live_indicator(False)))
    def _apply_aoi(x0, x1, y0, y1):
        """Set the camera AOI (0-based inclusive), then re-read the actual applied
        AOI (the camera snaps to alignment) and refresh both panels + tracked origin."""
        safe_req("set_feature", {"name": "AOIWidth", "value": x1 - x0 + 1})
        safe_req("set_feature", {"name": "AOIHeight", "value": y1 - y0 + 1})
        safe_req("set_feature", {"name": "AOILeft", "value": x0 + 1})
        safe_req("set_feature", {"name": "AOITop", "value": y0 + 1})
        try:
            _read_aoi()
        except Exception as e:
            status_log.append(f"AOI re-read failed: {e}", "warn")

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
        r = safe_req("snap_single", {"exposure_s": live.exposure_spin.value()}, timeout_ms=60000)
        if r is None:
            return
        arr = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(r["header"]["height"], r["header"]["width"])
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "Save acquired image", f"{cfg.get('snapshot_dir', '.')}/marana.tif", "TIFF (*.tif)")
        if not path:
            return
        md = build_snapshot_metadata(server_info, r["header"],
                                     display={"rot": image_view.state.rot,
                                              "flip_h": image_view.state.flip_h,
                                              "flip_v": image_view.state.flip_v})
        try:
            write_snapshot(path, arr, md)
            status_log.append(f"saved {path}", "info")
            cfg["snapshot_dir"] = os.path.dirname(path); cfg_mod.save(cfg)
        except Exception as e:
            status_log.append(f"save failed: {e}", "error")

    def _snap_display():
        # Acquire one fresh frame and show it — no save dialog, nothing written.
        r = safe_req("snap_single", {"exposure_s": live.exposure_spin.value()}, timeout_ms=60000)
        if r is None:
            return
        arr = np.frombuffer(r["frame_bytes"], dtype=np.uint16).reshape(r["header"]["height"], r["header"]["width"])
        image_view.update_frame(arr)
        image_view.auto_baseline()   # auto-stretch on every snap (offsets persist)
        status_log.append("snapped frame (display only)", "info")

    live.requestSnapDisplay.connect(_snap_display)
    live.requestSnapNow.connect(_snap_now)
    live.requestAcquireAndSave.connect(_acquire_and_save)

    # Kinetic flow — must call `stop` first so the server isn't already in LIVE
    # (the ICE server's contract; concurrent SDK threads cause AT_ERR_TIMEDOUT).
    def _start_kinetic(n, e, fps):
        safe_req("stop", {})            # no-op if IDLE
        win.set_live_indicator(False)
        r = safe_req("start_kinetic", {"frame_count": n, "exposure_s": e, "frame_rate_hz": fps})
        if r is not None:
            kinetic.on_kinetic_budget_reply(r["ram_estimate_bytes"], r["ram_free_bytes"])

    kinetic.requestStartKinetic.connect(_start_kinetic)
    kinetic.requestConfirmKinetic.connect(lambda: (safe_req("confirm_kinetic", {}), win.show_scrubber(False)))
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
        image_view.update_frame(arr)

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
    contrast.requestOffsets.connect(image_view.set_level_offsets)
    contrast.requestAuto.connect(lambda: (image_view.reset_auto(), contrast.center()))

    # Worker -> GUI updates
    def _on_frame(topic: bytes, header: dict, arr: np.ndarray) -> None:
        nonlocal latest_live_frame, latest_live_header
        if topic == m.TOPIC_LIVE_FRAME:
            latest_live_frame = arr
            latest_live_header = header
            image_view.update_frame(arr)
            if pending_live_auto["v"]:
                image_view.auto_baseline()   # auto-stretch once at start of live
                pending_live_auto["v"] = False
        elif topic == m.TOPIC_KINETIC_FRAME:
            image_view.update_frame(arr)
        elif topic == m.TOPIC_FOCUS_PROGRESS:
            image_view.update_frame(arr)
            focus.on_focus_progress(header["frame_idx"], header["frames_total"], header["z_um"])

    def _on_status(topic: bytes, header: dict) -> None:
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
            win.show_scrubber(header["frames_done"] > 0)
            win.set_live_indicator(False)
        elif topic == m.TOPIC_FOCUS_COMPLETE:
            focus.on_focus_complete(header["frames_done"], header["frames_total"], header.get("partial", False))
            win.set_live_indicator(False)
        elif topic == m.TOPIC_STATE:
            status_log.append(f"state: {header.get('state')}", "info")
            if header.get("state") == "LIVE":
                win.set_live_indicator(True)
            elif header.get("state") == "IDLE":
                win.set_live_indicator(False)

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
