"""Tests for the diagnostic instrumentation added so a future SDK wedge is
self-explanatory in the journal: camera SDK breadcrumbs/activity + slow-call
warnings, and the worker's command logging + activity description.

See the wedge post-mortem: a cffi SDK call can freeze the whole process; these
breadcrumbs + the watchdog stack dump pinpoint which call hung.
"""
import logging
import time
from queue import Queue
from unittest.mock import MagicMock

from marana_server import camera as camera_mod
from marana_server.camera import MaranaCamera
from marana_server.worker import CameraWorker


# --- camera._sdk breadcrumb / activity --------------------------------------

def test_sdk_sets_and_clears_activity():
    c = MaranaCamera()
    assert c.current_activity() == "idle"
    with c._sdk_call("AcquisitionStop"):
        assert c.current_activity() == "AcquisitionStop"
    assert c.current_activity() == "idle"


def test_sdk_resets_activity_even_on_error():
    c = MaranaCamera()
    try:
        with c._sdk_call("flush"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert c.current_activity() == "idle"


def test_sdk_warns_on_slow_call(caplog, monkeypatch):
    monkeypatch.setattr(camera_mod, "SDK_SLOW_WARN_S", 0.05)
    c = MaranaCamera()
    with caplog.at_level("WARNING"):
        with c._sdk_call("wait_buffer"):
            time.sleep(0.08)
    assert any("slow SDK call" in r.message and "wait_buffer" in r.message
               for r in caplog.records)


def test_sdk_quiet_on_fast_call(caplog, monkeypatch):
    monkeypatch.setattr(camera_mod, "SDK_SLOW_WARN_S", 5.0)
    c = MaranaCamera()
    with caplog.at_level("WARNING"):
        with c._sdk_call("queue"):
            pass
    assert not any("slow SDK call" in r.message for r in caplog.records)


# --- worker command logging + describe_activity -----------------------------

def _make_worker():
    cam = MagicMock()
    cam.get_cooling.return_value = {
        "enabled": False, "target_c": 0.0, "sensor_temp_c": 20.0, "status": "Cooler Off",
    }
    cam.current_activity.return_value = "idle"
    w = CameraWorker(camera=cam, outbound_queue=Queue())
    return w, cam


def test_worker_logs_state_changing_command_at_info(caplog):
    w, cam = _make_worker()
    w.start()
    try:
        with caplog.at_level(logging.INFO, logger="marana_server.worker"):
            w.submit_sync("set_feature", {"name": "ExposureTime", "value": 0.1})
        assert any(r.levelno == logging.INFO and "set_feature" in r.message
                   for r in caplog.records)
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_worker_pollers_not_logged_at_info(caplog):
    w, cam = _make_worker()
    cam.get_feature.return_value = 0.05
    w.start()
    try:
        with caplog.at_level(logging.INFO, logger="marana_server.worker"):
            w.submit_sync("get_feature", {"name": "ExposureTime"})
        assert not any(r.levelno == logging.INFO and "get_feature" in r.message
                       for r in caplog.records)
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_describe_activity_reports_state_and_sdk():
    w, cam = _make_worker()
    cam.current_activity.return_value = "safe_continuous_iter.AcquisitionStop"
    desc = w.describe_activity()
    assert "state=IDLE" in desc
    assert "safe_continuous_iter.AcquisitionStop" in desc
