from queue import Queue
from unittest.mock import MagicMock

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
