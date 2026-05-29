import time
from queue import Queue
from unittest.mock import MagicMock

import numpy as np
import pytest

from marana_server.worker import CameraWorker, WorkerState
from marana_proto import messages as m


@pytest.fixture
def worker():
    cam = MagicMock()
    cam.get_cooling.return_value = {
        "enabled": False, "target_c": 0.0, "sensor_temp_c": 20.0, "status": "Cooler Off",
    }
    outq: Queue = Queue()
    w = CameraWorker(camera=cam, outbound_queue=outq)
    w.start()
    yield w, cam, outq
    w.shutdown()
    w.join(timeout=2.0)


def _drain(q: Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_worker_starts_in_idle(worker):
    w, _, outq = worker
    import time
    time.sleep(0.1)
    events = _drain(outq)
    state_events = [e for e in events if e[0] == m.TOPIC_STATE]
    assert any("IDLE" in str(m.decode(e[1])["state"]) for e in state_events)


def test_submit_get_feature(worker):
    w, cam, outq = worker
    cam.get_feature.return_value = 0.05
    result = w.submit_sync("get_feature", {"name": "ExposureTime"})
    assert result == {"name": "ExposureTime", "value": 0.05}


def test_submit_invalid_command_raises(worker):
    w, _, _ = worker
    with pytest.raises(ValueError, match="unknown command"):
        w.submit_sync("not_a_command", {})


def test_set_feature_dispatches_to_camera(worker):
    w, cam, _ = worker
    w.submit_sync("set_feature", {"name": "ExposureTime", "value": 0.1})
    cam.set_feature.assert_called_with("ExposureTime", 0.1)


@pytest.fixture
def cam_with_live():
    cam = MagicMock()
    cam.get_cooling.return_value = {
        "enabled": False, "target_c": 0.0, "sensor_temp_c": 20.0, "status": "Cooler Off",
    }
    cam.get_feature.return_value = 0.05

    # Yield 3 frames from safe_continuous_iter then stop on close()
    def gen(*a, **kw):
        for i in range(3):
            yield np.full((4, 4), i, dtype=np.uint16)
    cam.safe_continuous_iter = MagicMock(side_effect=gen)

    cam.get_aoi.return_value = (0, 3, 0, 3)
    cam.single_shot = MagicMock(return_value=np.full((4, 4), 99, dtype=np.uint16))

    return cam


def test_start_live_publishes_frames(cam_with_live):
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    try:
        w.submit_sync("start_live", {"exposure_s": 0.001})
        time.sleep(0.5)
        w.submit_sync("stop", {})
        time.sleep(0.2)
        events = _drain(outq)
        frame_topics = [e for e in events if e[0] == m.TOPIC_LIVE_FRAME]
        assert len(frame_topics) >= 1
        state_events = [m.decode(e[1])["state"] for e in events if e[0] == m.TOPIC_STATE]
        assert "LIVE" in state_events
        assert "IDLE" in state_events
    finally:
        w.shutdown()
        w.join(timeout=2.0)


def test_snap_single_returns_frame(cam_with_live):
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    try:
        result = w.submit_sync("snap_single", {})
        assert "frame_bytes" in result
        assert "header" in result
        assert result["header"]["width"] == 4
        assert result["header"]["height"] == 4
        arr = np.frombuffer(result["frame_bytes"], dtype=np.uint16).reshape(4, 4)
        assert int(arr[0, 0]) == 99
    finally:
        w.shutdown()
        w.join(timeout=2.0)


def test_confirm_kinetic_rejects_when_live_running(cam_with_live):
    """Regression: starting kinetic while LIVE is running used to silently race the
    SDK and abort after a handful of frames. Server must now reject so the client
    is forced to call stop() first (matches the BL11.3.2 ICE server contract).
    """
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    try:
        w.submit_sync("start_live", {"exposure_s": 0.001})
        time.sleep(0.2)
        # start_kinetic only computes the RAM budget — allowed in any state
        w.submit_sync("start_kinetic", {"frame_count": 5, "exposure_s": 0.001, "frame_rate_hz": 5.0})
        # confirm_kinetic must reject because state != IDLE
        with pytest.raises(RuntimeError, match="stop first"):
            w.submit_sync("confirm_kinetic", {})
    finally:
        w.shutdown()
        w.join(timeout=2.0)
