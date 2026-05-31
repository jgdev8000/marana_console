"""CameraWorker — single thread that owns the camera and dispatches commands."""
from __future__ import annotations

import enum
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from marana_proto import messages as m
from marana_proto.errors import MaranaError, to_wire
from marana_server.epics_mover import EpicsMover

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class WorkerState(str, enum.Enum):
    IDLE = "IDLE"
    LIVE = "LIVE"
    SINGLE = "SINGLE"
    KINETIC = "KINETIC"
    FOCUS = "FOCUS"
    RECONFIG = "RECONFIG"
    ERROR = "ERROR"


_Reply = Callable[[Any, Exception | None], None]


class CameraWorker(threading.Thread):
    """Owns MaranaCamera. Dispatches inbound commands; emits frames + events on outbound_queue.

    Outbound queue items are msgpack multipart lists ready to be ZMQ-published.
    """

    def __init__(self, camera, outbound_queue: queue.Queue):
        super().__init__(name="CameraWorker", daemon=True)
        self._camera = camera
        self._outq = outbound_queue
        self._inq: queue.Queue[tuple[str, dict, _Reply | None]] = queue.Queue()
        self._stop_evt = threading.Event()
        self._state = WorkerState.IDLE
        self._cancel_evt = threading.Event()
        self._live_thread: threading.Thread | None = None
        self._kinetic_thread: threading.Thread | None = None
        self._kinetic_frames = None  # np.ndarray (N, H, W) or None
        self._kinetic_pending_args: dict | None = None
        self._kinetic_status: dict = {"frames_done": 0, "frames_total": 0, "achieved_fps": 0.0, "elapsed_s": 0.0}
        self._frame_seq = 0
        self._focus_thread: threading.Thread | None = None
        self._focus_mover = None
        self._focus_frames = None
        self._focus_z_positions: list[float] = []
        self._focus_pending_params: dict | None = None
        self._focus_status: dict = {"frames_done": 0, "frames_total": 0, "current_z_um": 0.0, "elapsed_s": 0.0}
        self._focus_meta: dict = {}

    # --- public API -------------------------------------------------------

    def submit(self, cmd: str, args: dict, reply: _Reply | None = None) -> None:
        self._inq.put((cmd, args, reply))

    def submit_sync(self, cmd: str, args: dict, timeout: float = 5.0):
        evt = threading.Event()
        box: dict[str, Any] = {}

        def reply(result, error):
            box["result"] = result
            box["error"] = error
            evt.set()

        self.submit(cmd, args, reply)
        if not evt.wait(timeout):
            raise TimeoutError(f"submit_sync timeout: {cmd}")
        if box["error"]:
            raise box["error"]
        return box["result"]

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._cancel_evt.set()
        self._inq.put(("__shutdown__", {}, None))

    # --- thread main ------------------------------------------------------

    def run(self) -> None:
        self._publish_state()
        while not self._stop_evt.is_set():
            try:
                cmd, args, reply = self._inq.get(timeout=0.5)
            except queue.Empty:
                self._idle_tick()
                continue
            if cmd == "__shutdown__":
                break
            self._dispatch(cmd, args, reply)
        log.info("CameraWorker exiting")

    def _idle_tick(self) -> None:
        try:
            cooling = self._camera.get_cooling()
            self._publish_temperature(cooling)
        except Exception as e:
            log.debug("idle cooling poll failed: %s", e)

    # --- dispatch ---------------------------------------------------------

    HANDLERS: dict[str, str] = {
        "get_feature": "_h_get_feature",
        "set_feature": "_h_set_feature",
        "list_features": "_h_list_features",
        "get_acq_settings": "_h_get_acq_settings",
        "cooling_get": "_h_cooling_get",
        "cooling_set": "_h_cooling_set",
        "start_live": "_h_start_live",
        "stop": "_h_stop",
        "snap_single": "_h_snap_single",
        "start_kinetic": "_h_start_kinetic",
        "confirm_kinetic": "_h_confirm_kinetic",
        "cancel_kinetic": "_h_cancel_kinetic",
        "get_kinetic_status": "_h_get_kinetic_status",
        "get_kinetic_frame": "_h_get_kinetic_frame",
        "start_focus": "_h_start_focus",
        "confirm_focus": "_h_confirm_focus",
        "cancel_focus": "_h_cancel_focus",
        "get_focus_status": "_h_get_focus_status",
    }

    def _dispatch(self, cmd: str, args: dict, reply: _Reply | None) -> None:
        handler_name = self.HANDLERS.get(cmd)
        if handler_name is None:
            err = ValueError(f"unknown command: {cmd}")
            if reply:
                reply(None, err)
            else:
                self._publish_error(err)
            return
        handler = getattr(self, handler_name)
        try:
            result = handler(args)
            if reply:
                reply(result, None)
        except MaranaError as e:
            if reply:
                reply(None, e)
            else:
                self._publish_error(e)
        except Exception as e:
            log.exception("handler %s raised", cmd)
            if reply:
                reply(None, e)
            else:
                self._publish_error(e)

    # --- handlers ---------------------------------------------------------

    def _h_get_feature(self, args: dict) -> dict:
        name = args["name"]
        out = {"name": name, "value": self._camera.get_feature(name)}
        # If the feature is an enum, include its options. Sweep failures silently
        # (non-enum features raise). Only include if result is a list of strings.
        try:
            options = self._camera.enum_options(name)
            if isinstance(options, list):
                out["options"] = options
        except Exception:
            pass
        return out

    def _h_set_feature(self, args: dict) -> dict:
        name = args["name"]
        value = args["value"]
        self._camera.set_feature(name, value)
        return {"name": name, "applied_value": self._camera.get_feature(name)}

    def _h_list_features(self, args: dict) -> dict:
        names = ("ExposureTime", "PixelEncoding", "PixelReadoutRate", "ElectronicShutteringMode")
        out = []
        for n in names:
            try:
                out.append({"name": n, "value": self._camera.get_feature(n)})
            except Exception as e:
                out.append({"name": n, "error": str(e)})
        return {"features": out}

    def _h_get_acq_settings(self, args: dict) -> dict:
        return self._camera.get_acq_settings()

    def _h_cooling_get(self, args: dict) -> dict:
        return self._camera.get_cooling()

    def _h_cooling_set(self, args: dict) -> dict:
        self._camera.set_cooling(enable=args.get("enable", False), target_c=args.get("target_c"))
        return self._camera.get_cooling()

    # --- outbound publishing helpers --------------------------------------

    def _set_state(self, new: WorkerState) -> None:
        if new != self._state:
            self._state = new
            self._publish_state()

    def _publish_state(self, detail: str | None = None) -> None:
        self._outq.put(m.make_status(m.TOPIC_STATE, {"state": self._state.value, "detail": detail}))

    def _publish_temperature(self, cooling: dict) -> None:
        self._outq.put(m.make_status(m.TOPIC_TEMPERATURE, cooling))

    def _publish_error(self, exc: Exception) -> None:
        if isinstance(exc, MaranaError):
            payload = to_wire(exc)
            payload["severity"] = "error"
        else:
            payload = {"type": type(exc).__name__, "message": str(exc), "severity": "error"}
        self._outq.put(m.make_status(m.TOPIC_ERROR, payload))

    # --- live -------------------------------------------------------------

    def _h_start_live(self, args: dict) -> dict:
        if self._state != WorkerState.IDLE:
            self._cancel_evt.set()
            self._join_active(timeout=2.0)
        self._cancel_evt.clear()
        exposure = args.get("exposure_s")
        self._set_state(WorkerState.LIVE)
        self._live_thread = threading.Thread(
            target=self._live_loop, args=(exposure,), name="LiveLoop", daemon=True
        )
        self._live_thread.start()
        return {}

    def _h_stop(self, args: dict) -> dict:
        self._cancel_evt.set()
        self._join_active(timeout=2.0)
        self._set_state(WorkerState.IDLE)
        return {}

    def _join_active(self, timeout: float) -> None:
        for t in (self._live_thread, self._kinetic_thread):
            if t and t.is_alive():
                t.join(timeout=timeout)
        self._live_thread = None
        self._kinetic_thread = None

    def _live_loop(self, exposure_s: float | None) -> None:
        try:
            it = self._camera.safe_continuous_iter(exposure_s=exposure_s)
            for frame in it:
                if self._cancel_evt.is_set():
                    it.close()
                    break
                self._publish_live_frame(frame)
                # Cooling poll piggy-backs on live cadence
                try:
                    cooling = self._camera.get_cooling()
                    self._publish_temperature(cooling)
                except Exception:
                    pass
        except Exception as e:
            log.exception("live loop error")
            self._publish_error(e)
            self._set_state(WorkerState.ERROR)

    def _publish_live_frame(self, frame) -> None:
        self._frame_seq += 1
        header = {
            "seq": self._frame_seq,
            "ts_iso": _now_iso(),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "dtype": "uint16",
        }
        self._outq.put(m.make_frame(m.TOPIC_LIVE_FRAME, header, frame.tobytes()))

    # --- snapshot ---------------------------------------------------------

    def _h_snap_single(self, args: dict) -> dict:
        prev_state = self._state
        live_was_running = prev_state == WorkerState.LIVE
        if live_was_running:
            self._cancel_evt.set()
            self._join_active(timeout=2.0)
        self._cancel_evt.clear()
        self._set_state(WorkerState.SINGLE)
        try:
            frame = self._camera.single_shot(timeout_ms=int(2000 + 1000 * args.get("exposure_s", 0.05)))
            header = {
                "ts_iso": _now_iso(),
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "dtype": "uint16",
            }
            result = {"frame_bytes": bytes(frame.tobytes()), "header": header}
        finally:
            self._set_state(WorkerState.IDLE)
            if live_was_running:
                self._h_start_live({"exposure_s": args.get("exposure_s")})
        return result

    # --- kinetic ----------------------------------------------------------

    def _h_start_kinetic(self, args: dict) -> dict:
        frame_count = int(args["frame_count"])
        exposure_s = float(args["exposure_s"])
        frame_rate_hz = float(args["frame_rate_hz"])
        aoi = self._camera.get_aoi()
        w = aoi[1] - aoi[0] + 1
        h = aoi[3] - aoi[2] + 1
        ram_estimate = frame_count * w * h * 2
        try:
            import psutil
            ram_free = psutil.virtual_memory().available
        except Exception:
            ram_free = 0
        self._kinetic_pending_args = {
            "frame_count": frame_count,
            "exposure_s": exposure_s,
            "frame_rate_hz": frame_rate_hz,
        }
        return {"ram_estimate_bytes": ram_estimate, "ram_free_bytes": int(ram_free)}

    def _h_confirm_kinetic(self, args: dict) -> dict:
        if self._kinetic_pending_args is None:
            raise ValueError("no kinetic pending; call start_kinetic first")
        # Matches the BL11.3.2 ICE server contract: caller must stop any
        # in-flight acquisition before starting a new one. Two SDK threads
        # racing on the same camera handle produce AT_ERR_TIMEDOUT after a
        # handful of frames.
        if self._state != WorkerState.IDLE:
            raise RuntimeError(
                f"Must call stop first; current state is {self._state.value}"
            )
        k = self._kinetic_pending_args
        self._kinetic_pending_args = None
        self._cancel_evt.clear()
        self._set_state(WorkerState.KINETIC)
        self._kinetic_thread = threading.Thread(
            target=self._kinetic_loop, args=(k,), name="KineticLoop", daemon=True,
        )
        self._kinetic_thread.start()
        return {}

    def _h_cancel_kinetic(self, args: dict) -> dict:
        self._cancel_evt.set()
        self._join_active(timeout=10.0)
        return {}

    def _h_get_kinetic_status(self, args: dict) -> dict:
        return dict(self._kinetic_status)

    def _h_get_kinetic_frame(self, args: dict) -> dict:
        idx = int(args["index"])
        if self._kinetic_frames is None:
            raise ValueError("no kinetic frames buffered")
        if not (0 <= idx < self._kinetic_frames.shape[0]):
            raise IndexError(f"frame index {idx} out of range")
        f = self._kinetic_frames[idx]
        header = {
            "index": idx,
            "width": int(f.shape[1]),
            "height": int(f.shape[0]),
            "dtype": "uint16",
        }
        return {"frame_bytes": bytes(f.tobytes()), "header": header}

    def _kinetic_loop(self, k: dict) -> None:
        def on_progress(done, total, fps):
            elapsed = self._kinetic_status.get("elapsed_s", 0.0)
            self._kinetic_status = {
                "frames_done": done, "frames_total": total,
                "achieved_fps": fps, "elapsed_s": elapsed,
            }
            self._outq.put(m.make_status(m.TOPIC_KINETIC_PROGRESS, self._kinetic_status))
        try:
            frames, done, elapsed = self._camera.kinetic_burst(
                frame_count=k["frame_count"], exposure_s=k["exposure_s"],
                frame_rate_hz=k["frame_rate_hz"],
                on_progress=on_progress, stop_flag=self._cancel_evt,
            )
            self._kinetic_frames = frames
            partial = done < k["frame_count"]
            self._kinetic_status = {
                "frames_done": done, "frames_total": k["frame_count"],
                "achieved_fps": done / elapsed if elapsed > 0 else 0.0,
                "elapsed_s": elapsed,
            }
            self._outq.put(m.make_status(m.TOPIC_KINETIC_COMPLETE, {
                **self._kinetic_status, "partial": partial,
            }))
        except Exception as e:
            log.exception("kinetic loop error")
            self._publish_error(e)
            self._set_state(WorkerState.ERROR)
            return
        self._set_state(WorkerState.IDLE)

    # --- focus -----------------------------------------------------------

    def _h_start_focus(self, args: dict) -> dict:
        from marana_proto.errors import FeatureValueOutOfRange
        mover_pv_base = str(args["mover_pv_base"])
        direction = int(args["direction"])
        range_um = float(args["range_um"])
        step_um = float(args["step_um"])
        exposure_s = float(args["exposure_s"])
        settle_ms = int(args["settle_ms"])
        return_to_start = bool(args["return_to_start"])
        if direction not in (-1, 1):
            raise ValueError(f"direction must be ±1, got {direction}")
        if range_um <= 0:
            raise ValueError("range_um must be > 0")
        if step_um <= 0:
            raise ValueError("step_um must be > 0")
        if exposure_s <= 0 or exposure_s > 60:
            raise ValueError("exposure_s must be in (0, 60]")

        mover = EpicsMover(mover_pv_base)
        try:
            z_start_mm = mover.read_rbv_mm()
            dllm_mm, dhlm_mm = mover.read_limits_mm()
        finally:
            mover.close()

        step_mm = step_um * 1e-3 * direction
        stop_count = int(range_um // step_um) + 1
        z_end_mm = z_start_mm + (stop_count - 1) * step_mm
        margin_mm = 1e-6
        z_min_mm = min(z_start_mm, z_end_mm)
        z_max_mm = max(z_start_mm, z_end_mm)
        if z_min_mm < dllm_mm + margin_mm or z_max_mm > dhlm_mm - margin_mm:
            raise FeatureValueOutOfRange(
                f"Z range exceeds limits ({dllm_mm:.3f}..{dhlm_mm:.3f} mm); "
                f"plan spans {z_min_mm:.3f}..{z_max_mm:.3f} mm"
            )

        per_step_s = max(0.02, abs(step_mm)) + settle_ms / 1000.0 + exposure_s + 0.05
        est_time_s = stop_count * per_step_s

        self._focus_pending_params = {
            "mover_pv_base": mover_pv_base,
            "direction": direction,
            "range_um": range_um,
            "step_um": step_um,
            "exposure_s": exposure_s,
            "settle_ms": settle_ms,
            "return_to_start": return_to_start,
            "z_start_mm": z_start_mm,
            "stop_count": stop_count,
        }
        return {
            "z_start_um": z_start_mm * 1e3,
            "z_end_um": z_end_mm * 1e3,
            "stop_count": stop_count,
            "est_time_s": est_time_s,
            "dllm_mm": dllm_mm,
            "dhlm_mm": dhlm_mm,
        }

    def _h_get_focus_status(self, args: dict) -> dict:
        return dict(self._focus_status)

    def _h_confirm_focus(self, args: dict) -> dict:
        if self._focus_pending_params is None:
            raise ValueError("no focus pending; call start_focus first")
        if self._state != WorkerState.IDLE:
            raise RuntimeError(
                f"Must call stop first; current state is {self._state.value}"
            )
        params = self._focus_pending_params
        self._focus_pending_params = None
        self._cancel_evt.clear()
        self._set_state(WorkerState.FOCUS)
        # Per-series mover handle; created on confirm so the loop owns it.
        self._focus_mover = EpicsMover(params["mover_pv_base"])
        self._focus_thread = threading.Thread(
            target=self._focus_loop, args=(params,), name="FocusLoop", daemon=True,
        )
        self._focus_thread.start()
        return {}

    def _h_cancel_focus(self, args: dict) -> dict:
        self._cancel_evt.set()
        # Touch .STOP from the dispatch thread to interrupt wait_done blocking
        # in the focus thread. Safe because pyepics PV.put is internally locked.
        mover = self._focus_mover
        if mover is not None:
            try:
                mover.stop()
            except Exception as e:
                log.warning("cancel_focus: mover.stop() failed: %s", e)
        if self._focus_thread and self._focus_thread.is_alive():
            self._focus_thread.join(timeout=15.0)
        self._focus_thread = None
        return {}

    def _focus_loop(self, params: dict) -> None:
        import numpy as np
        mover = self._focus_mover
        try:
            z_start_mm = params["z_start_mm"]
            stop_count = params["stop_count"]
            step_mm = params["step_um"] * 1e-3 * params["direction"]
            settle_s = params["settle_ms"] / 1000.0
            exposure_s = params["exposure_s"]
            move_timeout_s = max(2.0, abs(params["range_um"]) * 1e-3 * 2.0 + 1.0)

            # Configure camera once for single-shot at the requested exposure
            self._camera._configure_single_frame_mode(exposure_s=exposure_s)
            h = int(self._camera.get_feature("AOIHeight"))
            w = int(self._camera.get_feature("AOIWidth"))
            frames = np.zeros((stop_count, h, w), dtype=np.uint16)
            z_positions_um: list[float] = []

            t0 = time.monotonic()
            done = 0
            for i in range(stop_count):
                if self._cancel_evt.is_set():
                    break
                target_mm = z_start_mm + i * step_mm
                mover.move(target_mm)
                try:
                    mover.wait_done(timeout_s=move_timeout_s, settle_s=settle_s)
                except Exception as e:
                    self._publish_error(e)
                    break
                if self._cancel_evt.is_set():
                    break
                actual_mm = mover.read_rbv_mm()
                frame = self._camera.single_shot(timeout_ms=int(2000 + 1000 * exposure_s))
                frames[i] = frame
                z_positions_um.append(actual_mm * 1e3)
                done += 1
                self._focus_status = {
                    "frames_done": done, "frames_total": stop_count,
                    "current_z_um": actual_mm * 1e3,
                    "elapsed_s": time.monotonic() - t0,
                }
                self._publish_focus_frame(i, stop_count, actual_mm * 1e3, frame)

            # Optional return to start (best-effort)
            returned = False
            if params["return_to_start"]:
                try:
                    mover.stop()
                    mover.move(z_start_mm)
                    mover.wait_done(timeout_s=10.0, settle_s=0.1)
                    returned = True
                except Exception as e:
                    log.warning("return-to-start failed: %s", e)
                    self._publish_error(e)

            self._focus_frames = frames[:done]
            self._focus_z_positions = z_positions_um
            self._focus_meta = {
                "mover_pv_base": params["mover_pv_base"],
                "z_start_um": z_start_mm * 1e3,
                "direction": params["direction"],
                "range_um": params["range_um"],
                "step_um": params["step_um"],
                "settle_ms": params["settle_ms"],
                "return_to_start": params["return_to_start"],
                "returned_to_start": returned,
                "z_positions_um": list(z_positions_um),
                "achieved_elapsed_s": time.monotonic() - t0,
            }
            self._outq.put(m.make_status(m.TOPIC_FOCUS_COMPLETE, {
                "frames_done": done, "frames_total": stop_count,
                "partial": done < stop_count,
                "z_positions_um": list(z_positions_um),
                "z_start_um": z_start_mm * 1e3,
                "returned_to_start": returned,
                "elapsed_s": time.monotonic() - t0,
            }))
        except Exception as e:
            log.exception("focus loop error")
            self._publish_error(e)
            self._set_state(WorkerState.ERROR)
            return
        finally:
            try:
                mover.close()
            except Exception:
                pass
            self._focus_mover = None
        self._set_state(WorkerState.IDLE)

    def _publish_focus_frame(self, idx: int, total: int, z_um: float, frame) -> None:
        header = {
            "frame_idx": idx,
            "frames_total": total,
            "z_um": z_um,
            "ts_iso": _now_iso(),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "dtype": "uint16",
        }
        self._outq.put(m.make_frame(m.TOPIC_FOCUS_PROGRESS, header, frame.tobytes()))
