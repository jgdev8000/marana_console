"""MaranaService — wires camera + worker + publisher to ZMQ sockets."""
from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

import zmq

from marana_proto import messages as m
from marana_proto.errors import MaranaError, to_wire
from marana_server.publisher import Publisher
from marana_server.worker import CameraWorker, PACIFIC_TZ
from marana_server.io_tiff import write_image_stack, write_single_image
from marana_server.meta import build_metadata
from marana_server.epics_mover import EpicsMover

log = logging.getLogger(__name__)

SERVER_VERSION = "0.1.0"


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(PACIFIC_TZ).isoformat()


def _sdk_version_str() -> str:
    try:
        import pyAndorSDK3
        sdk = pyAndorSDK3.AndorSDK3()
        return str(sdk.SoftwareVersion)
    except Exception:
        return "unknown"


class MaranaService(threading.Thread):
    def __init__(
        self,
        camera,
        ctrl_endpoint: str,
        pub_endpoint: str,
        captures_dir: str,
        sim: bool,
        allow_shutdown: bool = False,
        zmq_ctx: zmq.Context | None = None,
    ):
        super().__init__(name="MaranaService", daemon=True)
        self._cam = camera
        self._ctrl_ep = ctrl_endpoint
        self._pub_ep = pub_endpoint
        self._captures_dir = Path(captures_dir).resolve()
        self._sim = sim
        self._allow_shutdown = allow_shutdown
        self._ctx = zmq_ctx or zmq.Context.instance()
        self._outq: queue.Queue = queue.Queue()
        self._worker = CameraWorker(camera=camera, outbound_queue=self._outq)
        self._stop_evt = threading.Event()
        self._rep_sock = None
        self._pub_sock = None
        self._publisher: Publisher | None = None

    def shutdown(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        self._rep_sock = self._ctx.socket(zmq.REP)
        self._rep_sock.bind(self._ctrl_ep)
        self._pub_sock = self._ctx.socket(zmq.PUB)
        self._pub_sock.SNDHWM = 8
        self._pub_sock.bind(self._pub_ep)
        self._publisher = Publisher(socket=self._pub_sock, outbound_queue=self._outq)
        self._publisher.start()
        self._worker.start()
        log.info("MaranaService ready ctrl=%s pub=%s", self._ctrl_ep, self._pub_ep)

        poller = zmq.Poller()
        poller.register(self._rep_sock, zmq.POLLIN)
        try:
            while not self._stop_evt.is_set():
                socks = dict(poller.poll(timeout=200))
                if self._rep_sock in socks:
                    raw = self._rep_sock.recv()
                    reply = self._handle_request(raw)
                    self._rep_sock.send(m.encode(reply))
        finally:
            log.info("MaranaService stopping")
            self._worker.shutdown()
            self._worker.join(timeout=2.0)
            self._publisher.shutdown()
            self._publisher.join(timeout=2.0)
            self._rep_sock.close(linger=0)
            self._pub_sock.close(linger=0)

    # --- request dispatch ------------------------------------------------

    def _handle_request(self, raw: bytes) -> dict:
        try:
            req = m.decode(raw)
        except Exception as e:
            return m.make_reply_err("?", "ProtocolError", f"msgpack decode failed: {e}")
        req_id = req.get("id", "?")
        cmd = req.get("cmd", "")
        args = req.get("args") or {}
        try:
            result = self._dispatch(cmd, args)
            return m.make_reply_ok(req_id, result=result)
        except MaranaError as e:
            envelope = to_wire(e)
            return m.make_reply_err(req_id, envelope["type"], envelope["message"])
        except Exception as e:
            log.exception("dispatch %s failed", cmd)
            return m.make_reply_err(req_id, type(e).__name__, str(e))

    INSTANT_CMDS = {"hello", "save_kinetic_stack", "save_focus_stack", "save_snapshot",
                    "list_kinetic_save_dir", "read_motor_rbv", "shutdown"}

    def _dispatch(self, cmd: str, args: dict):
        if cmd == "hello":
            return self._cmd_hello()
        if cmd == "save_kinetic_stack":
            return self._cmd_save_kinetic(args)
        if cmd == "save_focus_stack":
            return self._cmd_save_focus_stack(args)
        if cmd == "save_snapshot":
            return self._cmd_save_snapshot(args)
        if cmd == "read_motor_rbv":
            return self._cmd_read_motor_rbv(args)
        if cmd == "list_kinetic_save_dir":
            return self._cmd_list_save_dir(args)
        if cmd == "shutdown":
            if not self._allow_shutdown:
                raise PermissionError("shutdown not enabled on this server")
            self._stop_evt.set()
            return {}
        # Everything else goes to the worker
        return self._worker.submit_sync(cmd, args, timeout=120.0)

    def _cmd_hello(self) -> dict:
        return {
            "server_version": SERVER_VERSION,
            "sdk_version": _sdk_version_str(),
            "camera_model": self._cam.model,
            "camera_serial": self._cam.serial,
            "sensor_w": self._cam.sensor_width,
            "sensor_h": self._cam.sensor_height,
            "sim": self._sim,
        }

    def _next_dated_path(self, subdir: str = "") -> str:
        """Auto-name <subdir>/<YYMMDD>_N.tif with a per-day, per-folder counter.

        N = max(existing today in that folder) + 1, so a file is never
        overwritten. subdir="" uses the captures root (snapshots); "focus" and
        "kinetic" each keep their own independent daily sequence.
        """
        import re
        from datetime import datetime
        d = self._captures_dir / subdir if subdir else self._captures_dir
        d.mkdir(parents=True, exist_ok=True)
        date = datetime.now(PACIFIC_TZ).strftime("%y%m%d")
        pat = re.compile(rf"^{date}_(\d+)\.tif$")
        n = 0
        for child in d.iterdir():
            m = pat.match(child.name)
            if m:
                n = max(n, int(m.group(1)))
        prefix = f"{subdir}/" if subdir else ""
        return f"{prefix}{date}_{n + 1}.tif"

    def _resolve_under_captures(self, path: str) -> Path:
        p = (self._captures_dir / path).resolve()
        # Use Path.is_relative_to to avoid the prefix-string trap where
        # /tmp/foobar would falsely pass startswith(/tmp/foo).
        if p != self._captures_dir and not p.is_relative_to(self._captures_dir):
            raise PermissionError(f"path escapes captures dir: {path}")
        return p

    def _cmd_save_kinetic(self, args: dict) -> dict:
        if self._worker._kinetic_frames is None:
            raise ValueError("no kinetic frames buffered")
        frames = self._worker._kinetic_frames
        # No path -> auto-name kinetic/<YYMMDD>_N.tif; explicit path -> manual save.
        rel_path = args.get("path") or self._next_dated_path("kinetic")
        target = self._resolve_under_captures(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cooling = self._cam.get_cooling()
        aoi = self._cam.get_aoi()
        meta = build_metadata(
            camera={"model": self._cam.model, "serial": self._cam.serial, "host": os.uname().nodename},
            acquisition={
                "exposure_s": float(self._cam.get_feature("ExposureTime")),
                "encoding": str(self._cam.get_feature("PixelEncoding")),
                "speed_mhz": str(self._cam.get_feature("PixelReadoutRate")),
                "shutter": str(self._cam.get_feature("ElectronicShutteringMode")),
                "aoi_0based_inclusive": list(aoi),
                "binning": [1, 1],
                "sensor_temp_c": cooling.get("sensor_temp_c", 0.0),
                "timestamp_iso": _now_iso(),
                "frame_count": int(frames.shape[0]),
                "achieved_fps_hz": self._worker._kinetic_status.get("achieved_fps", 0.0),
                "acquisition_time_s": self._worker._kinetic_status.get("elapsed_s", 0.0),
            },
        )
        bytes_written = write_image_stack(str(target), frames, meta)
        return {"path": str(target), "bytes_written": bytes_written, "frames_written": int(frames.shape[0])}

    def _cmd_read_motor_rbv(self, args: dict) -> dict:
        pv_base = args["mover_pv_base"]
        mover = EpicsMover(pv_base)
        try:
            z_mm = mover.read_rbv_mm()
            dllm_mm, dhlm_mm = mover.read_limits_mm()
            egu = mover.egu()
        finally:
            mover.close()
        return {
            "z_mm": z_mm,
            "z_um": z_mm * 1e3,
            "dllm_mm": dllm_mm,
            "dhlm_mm": dhlm_mm,
            "egu": egu,
        }

    def _cmd_save_focus_stack(self, args: dict) -> dict:
        if self._worker._focus_frames is None or len(self._worker._focus_frames) == 0:
            raise ValueError("no focus frames buffered")
        frames = self._worker._focus_frames
        # No path -> auto-name focus/<YYMMDD>_N.tif; explicit path -> manual save.
        rel_path = args.get("path") or self._next_dated_path("focus")
        target = self._resolve_under_captures(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cooling = self._cam.get_cooling()
        aoi = self._cam.get_aoi()
        focus_meta = dict(self._worker._focus_meta)
        meta = build_metadata(
            camera={"model": self._cam.model, "serial": self._cam.serial, "host": os.uname().nodename},
            acquisition={
                "mode": "focus_series",
                "exposure_s": float(self._cam.get_feature("ExposureTime")),
                "encoding": str(self._cam.get_feature("PixelEncoding")),
                "speed_mhz": str(self._cam.get_feature("PixelReadoutRate")),
                "shutter": str(self._cam.get_feature("ElectronicShutteringMode")),
                "aoi_0based_inclusive": list(aoi),
                "binning": [1, 1],
                "sensor_temp_c": cooling.get("sensor_temp_c", 0.0),
                "timestamp_iso": _now_iso(),
                "frame_count": int(frames.shape[0]),
                **focus_meta,
            },
        )
        bytes_written = write_image_stack(str(target), frames, meta)
        return {"path": str(target), "bytes_written": bytes_written, "frames_written": int(frames.shape[0])}

    def _cmd_save_snapshot(self, args: dict) -> dict:
        """Save one client-supplied displayed frame to the captures root.

        Metadata is built from current camera state at save time (mirrors the
        kinetic/focus saves), so a saved live frame still records real
        exposure / temp / AOI without per-frame header bloat.
        """
        import numpy as np
        w = int(args["width"])
        h = int(args["height"])
        frame = np.frombuffer(args["frame_bytes"], dtype=np.uint16).reshape(h, w).copy()
        # No path -> auto-name <YYMMDD>_N.tif at root; explicit path -> manual save.
        rel_path = args.get("path") or self._next_dated_path("")
        target = self._resolve_under_captures(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cooling = self._cam.get_cooling()
        aoi = self._cam.get_aoi()
        meta = build_metadata(
            camera={"model": self._cam.model, "serial": self._cam.serial, "host": os.uname().nodename},
            acquisition={
                "mode": "snapshot",
                "exposure_s": float(self._cam.get_feature("ExposureTime")),
                "encoding": str(self._cam.get_feature("PixelEncoding")),
                "speed_mhz": str(self._cam.get_feature("PixelReadoutRate")),
                "shutter": str(self._cam.get_feature("ElectronicShutteringMode")),
                "aoi_0based_inclusive": list(aoi),
                "binning": [1, 1],
                "sensor_temp_c": cooling.get("sensor_temp_c", 0.0),
                "timestamp_iso": _now_iso(),
                "frame_count": 1,
            },
            display=args.get("display"),
        )
        bytes_written = write_single_image(str(target), frame, meta)
        return {"path": str(target), "bytes_written": bytes_written}

    def _cmd_list_save_dir(self, args: dict) -> dict:
        sub = args.get("subdir", "")
        p = self._resolve_under_captures(sub)
        if not p.exists():
            return {"entries": [], "abs_path": str(p)}
        entries = []
        for child in sorted(p.iterdir()):
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
            except OSError:
                continue
        return {"entries": entries, "abs_path": str(p)}
