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
    # AOIHeight/AOIWidth must be real ints (focus loop preallocates frames from them);
    # everything else (e.g. ExposureTime) defaults to 0.05.
    def _get_feature(name):
        if name in ("AOIHeight", "AOIWidth"):
            return 4
        return 0.05
    cam.get_feature.side_effect = _get_feature

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


from unittest.mock import patch


def _mover_mock(z_start_mm=0.0, dllm_mm=-10.5, dhlm_mm=10.5):
    """Helper to build an EpicsMover-shaped MagicMock."""
    mk = MagicMock()
    mk.read_rbv_mm.return_value = z_start_mm
    mk.read_limits_mm.return_value = (dllm_mm, dhlm_mm)
    mk.egu.return_value = "mm"
    return mk


def test_start_focus_returns_plan(worker):
    w, cam, _ = worker
    fake_mover = _mover_mock(z_start_mm=0.0)
    with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
        result = w.submit_sync("start_focus", {
            "mover_pv_base": "MCS2SIM:zoneplate_z",
            "direction": 1,
            "range_um": 100.0,
            "step_um": 10.0,
            "exposure_s": 0.001,
            "settle_ms": 50,
            "return_to_start": True,
        })
    # bidirectional: half_steps = floor((100/2)/10) = 5 -> stop_count = 1 + 2*5 = 11
    assert result["stop_count"] == 11
    assert result["z_start_um"] == pytest.approx(0.0)
    # z_end_um is the positive extreme: z_start + half_steps*step = 0 + 5*10 = 50
    assert result["z_end_um"] == pytest.approx(50.0)
    assert result["dllm_mm"] == pytest.approx(-10.5)
    assert result["dhlm_mm"] == pytest.approx(10.5)
    assert "est_time_s" in result


def test_start_focus_rejects_out_of_range(worker):
    w, cam, _ = worker
    fake_mover = _mover_mock(z_start_mm=10.0)  # already near +limit
    with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
        with pytest.raises(Exception) as ei:
            w.submit_sync("start_focus", {
                "mover_pv_base": "MCS2SIM:zoneplate_z",
                "direction": 1,
                "range_um": 5000.0,    # 5 mm → would land at 15 mm, beyond DHLM 10.5
                "step_um": 100.0,
                "exposure_s": 0.001,
                "settle_ms": 50,
                "return_to_start": True,
            })
        assert "limit" in str(ei.value).lower() or "range" in str(ei.value).lower()


def test_get_focus_status_returns_zero_initially(worker):
    w, _, _ = worker
    result = w.submit_sync("get_focus_status", {})
    assert result["frames_done"] == 0
    assert result["frames_total"] == 0


def test_confirm_focus_rejects_when_live_running(cam_with_live):
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    try:
        w.submit_sync("start_live", {"exposure_s": 0.001})
        time.sleep(0.2)
        fake_mover = _mover_mock(z_start_mm=0.0)
        with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
            w.submit_sync("start_focus", {
                "mover_pv_base": "MCS2SIM:zoneplate_z",
                "direction": 1, "range_um": 50.0, "step_um": 10.0,
                "exposure_s": 0.001, "settle_ms": 0, "return_to_start": True,
            })
        with pytest.raises(RuntimeError, match="stop first"):
            w.submit_sync("confirm_focus", {})
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_focus_loop_steps_through_positions(cam_with_live):
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    fake_mover = _mover_mock(z_start_mm=0.0)
    try:
        with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
            w.submit_sync("start_focus", {
                "mover_pv_base": "MCS2SIM:zoneplate_z",
                "direction": 1, "range_um": 40.0, "step_um": 10.0,
                "exposure_s": 0.001, "settle_ms": 0, "return_to_start": True,
            })
            w.submit_sync("confirm_focus", {})
        complete_payload = None; t0 = time.monotonic()
        progress_count = 0
        while time.monotonic() - t0 < 5.0 and complete_payload is None:
            try:
                item = outq.get(timeout=0.1)
                if not item:
                    continue
                if item[0] == m.TOPIC_FOCUS_PROGRESS:
                    progress_count += 1
                elif item[0] == m.TOPIC_FOCUS_COMPLETE:
                    complete_payload = m.decode(item[1])
            except Exception:
                pass
        assert complete_payload is not None
        # bidirectional: half_steps = floor((40/2)/10) = 2 -> total = 1 + 2*2 = 5
        assert complete_payload["frames_total"] == 5
        assert complete_payload["frames_done"] == 5
        assert complete_payload["partial"] is False
        assert len(complete_payload["z_positions_um"]) == 5
        # Moves: 2 silent descent + 4 forward sweep + 1 return-to-start = 7
        assert fake_mover.move.call_count == 7
        assert progress_count == 5  # frame 0 (neg extreme) + 4 forward frames
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_focus_meta_records_swept_range(cam_with_live):
    """_focus_meta records the actual swept travel (2*half_steps*step), not just requested range."""
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    fake_mover = _mover_mock(z_start_mm=0.0)
    try:
        with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
            w.submit_sync("start_focus", {
                "mover_pv_base": "MCS2SIM:zoneplate_z",
                # range/2 = 15 not divisible by step 10 -> half_steps=1 -> swept=20 (< requested 30)
                "direction": -1, "range_um": 30.0, "step_um": 10.0,
                "exposure_s": 0.001, "settle_ms": 0, "return_to_start": False,
            })
            w.submit_sync("confirm_focus", {})
        done = False; t0 = time.monotonic()
        while time.monotonic() - t0 < 5.0 and not done:
            try:
                item = outq.get(timeout=0.1)
                if item and item[0] == m.TOPIC_FOCUS_COMPLETE:
                    done = True
            except Exception:
                pass
        assert done
        assert w._focus_meta["range_um"] == pytest.approx(30.0)       # requested
        assert w._focus_meta["swept_range_um"] == pytest.approx(20.0)  # actual travel
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_cancel_focus_stops_mover(cam_with_live):
    outq = Queue()
    w = CameraWorker(camera=cam_with_live, outbound_queue=outq)
    w.start()
    fake_mover = _mover_mock(z_start_mm=0.0)
    import threading as _threading
    slow_evt = _threading.Event()

    def slow_wait(timeout_s=0, settle_s=0):
        slow_evt.wait(timeout=timeout_s if timeout_s else 5.0)
    fake_mover.wait_done.side_effect = slow_wait
    try:
        with patch("marana_server.worker.EpicsMover", return_value=fake_mover):
            w.submit_sync("start_focus", {
                "mover_pv_base": "MCS2SIM:zoneplate_z",
                "direction": 1, "range_um": 100.0, "step_um": 10.0,
                "exposure_s": 0.001, "settle_ms": 0, "return_to_start": False,
            })
            w.submit_sync("confirm_focus", {})
            time.sleep(0.2)
            w.submit_sync("cancel_focus", {})
        slow_evt.set()
        complete_payload = None; t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0 and complete_payload is None:
            try:
                item = outq.get(timeout=0.1)
                if item and item[0] == m.TOPIC_FOCUS_COMPLETE:
                    complete_payload = m.decode(item[1])
            except Exception:
                pass
        assert complete_payload is not None
        assert complete_payload["partial"] is True
        assert fake_mover.stop.called  # called by _h_cancel_focus
    finally:
        w.shutdown(); w.join(timeout=2.0)


def test_get_acq_settings_passthrough(worker):
    w, cam, _ = worker
    snap = {"options": {"GainMode": ["a", "b"]}, "values": {"GainMode": "a"}, "readonly": {"bit_depth": "16 Bit"}}
    cam.get_acq_settings.return_value = snap
    result = w.submit_sync("get_acq_settings", {})
    assert result == snap
    cam.get_acq_settings.assert_called_once()


def test_last_heartbeat_advances(worker):
    w, _, _ = worker
    import time as _t
    h1 = w.last_heartbeat()
    _t.sleep(0.6)   # > the 0.5s run-loop tick
    h2 = w.last_heartbeat()
    assert h2 > h1
