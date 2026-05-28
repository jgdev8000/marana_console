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

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class WorkerState(str, enum.Enum):
    IDLE = "IDLE"
    LIVE = "LIVE"
    SINGLE = "SINGLE"
    KINETIC = "KINETIC"
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
        "cooling_get": "_h_cooling_get",
        "cooling_set": "_h_cooling_set",
        # Acquisition handlers (start_live, snap_single, kinetic, etc.) added in Task 11.
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
