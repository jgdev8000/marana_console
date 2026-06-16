"""Tests MaranaService end-to-end over ZMQ's inproc:// transport with a mock camera."""
import threading
import time
from unittest.mock import MagicMock

import msgpack
import numpy as np
import pytest
import zmq

from marana_server.service import MaranaService
from marana_proto import messages as m


@pytest.fixture
def service():
    cam = MagicMock()
    cam.model = "SIMCAM CMOS"
    cam.serial = "SIM-001"
    cam.sensor_width = 64
    cam.sensor_height = 64
    cam.get_feature.return_value = 0.05
    cam.get_cooling.return_value = {
        "enabled": False, "target_c": 0.0, "sensor_temp_c": 20.0, "status": "Cooler Off",
    }

    ctx = zmq.Context.instance()
    svc = MaranaService(
        camera=cam, ctrl_endpoint="inproc://test_ctrl", pub_endpoint="inproc://test_pub",
        captures_dir="/tmp/marana_test_caps", sim=True, allow_shutdown=False, zmq_ctx=ctx,
    )
    svc.start()
    time.sleep(0.1)
    yield svc, ctx
    svc.shutdown()
    svc.join(timeout=3.0)


def _req(ctx: zmq.Context, endpoint: str, cmd: str, args: dict, timeout_ms: int = 2000):
    sock = ctx.socket(zmq.REQ)
    sock.RCVTIMEO = timeout_ms
    sock.connect(endpoint)
    sock.send(m.encode(m.make_request(cmd, args)))
    raw = sock.recv()
    sock.close()
    return m.decode(raw)


def test_hello_returns_camera_info(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "hello", {})
    assert reply["ok"] is True
    assert reply["result"]["camera_model"] == "SIMCAM CMOS"
    assert reply["result"]["sensor_w"] == 64


def test_get_feature(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "get_feature", {"name": "ExposureTime"})
    assert reply["ok"] is True
    assert reply["result"]["value"] == 0.05


def test_unknown_command_returns_err(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "does_not_exist", {})
    assert reply["ok"] is False
    assert "unknown" in reply["error"]["message"].lower()


from unittest.mock import patch


def test_read_motor_rbv(service):
    """Mocks EpicsMover; verifies read_motor_rbv replies with expected fields."""
    svc, ctx = service
    fake = MagicMock()
    fake.read_rbv_mm.return_value = 0.001234   # 1.234 µm
    fake.read_limits_mm.return_value = (-10.5, 10.5)
    fake.egu.return_value = "mm"
    with patch("marana_server.service.EpicsMover", return_value=fake):
        reply = _req(ctx, "inproc://test_ctrl", "read_motor_rbv",
                     {"mover_pv_base": "MCS2SIM:zoneplate_z"})
    assert reply["ok"] is True
    assert reply["result"]["z_mm"] == pytest.approx(0.001234)
    assert reply["result"]["z_um"] == pytest.approx(1.234)
    assert reply["result"]["dllm_mm"] == pytest.approx(-10.5)
    assert reply["result"]["dhlm_mm"] == pytest.approx(10.5)
    assert reply["result"]["egu"] == "mm"


def test_save_focus_stack_writes_tiff(service, tmp_path):
    """Populate worker's _focus_frames + _focus_meta, then call save_focus_stack."""
    import numpy as np
    svc, ctx = service
    svc._captures_dir = tmp_path
    svc._worker._focus_frames = np.zeros((3, 8, 8), dtype=np.uint16)
    svc._worker._focus_meta = {
        "mover_pv_base": "MCS2SIM:zoneplate_z", "z_start_um": 0.0,
        "direction": 1, "range_um": 20.0, "step_um": 10.0,
        "settle_ms": 50, "return_to_start": True, "returned_to_start": True,
        "z_positions_um": [0.0, 10.0, 20.0], "achieved_elapsed_s": 1.5,
    }
    reply = _req(ctx, "inproc://test_ctrl", "save_focus_stack", {"path": "focus.tif"})
    assert reply["ok"] is True
    assert reply["result"]["frames_written"] == 3
    assert (tmp_path / "focus.tif").exists()


def test_save_focus_stack_auto_names_first_of_day(service, tmp_path):
    """No path arg -> server auto-names focus/<YYMMDD>_1.tif for the first of the day."""
    from datetime import datetime
    svc, ctx = service
    svc._captures_dir = tmp_path
    svc._worker._focus_frames = np.zeros((3, 8, 8), dtype=np.uint16)
    svc._worker._focus_meta = {"z_positions_um": [0.0, 10.0, 20.0]}
    reply = _req(ctx, "inproc://test_ctrl", "save_focus_stack", {})
    assert reply["ok"] is True
    today = datetime.now().strftime("%y%m%d")
    assert reply["result"]["path"].endswith(f"focus/{today}_1.tif")
    assert (tmp_path / "focus" / f"{today}_1.tif").exists()


def test_save_focus_stack_auto_increments_sequence(service, tmp_path):
    """N = max(existing today) + 1; other dates and non-matching names ignored."""
    from datetime import datetime
    svc, ctx = service
    svc._captures_dir = tmp_path
    today = datetime.now().strftime("%y%m%d")
    fdir = tmp_path / "focus"
    fdir.mkdir()
    (fdir / f"{today}_1.tif").write_bytes(b"x")
    (fdir / f"{today}_3.tif").write_bytes(b"x")
    (fdir / "990101_9.tif").write_bytes(b"x")   # different date -> ignored
    (fdir / "notes.txt").write_bytes(b"x")        # non-matching -> ignored
    svc._worker._focus_frames = np.zeros((2, 8, 8), dtype=np.uint16)
    svc._worker._focus_meta = {"z_positions_um": [0.0, 10.0]}
    reply = _req(ctx, "inproc://test_ctrl", "save_focus_stack", {})
    assert reply["ok"] is True
    assert reply["result"]["path"].endswith(f"focus/{today}_4.tif")
    assert (fdir / f"{today}_4.tif").exists()


def test_get_acq_settings_over_req(service):
    svc, ctx = service
    svc._cam.get_acq_settings.return_value = {
        "options": {"PixelReadoutRate": ["100 MHz"], "PixelEncoding": ["Mono16"], "GainMode": []},
        "values": {"PixelReadoutRate": "100 MHz", "PixelEncoding": "Mono16", "GainMode": None},
        "readonly": {"bit_depth": None, "readout_time_s": None, "frame_rate_hz": 90.0, "max_frame_rate_hz": 90.0},
    }
    reply = _req(ctx, "inproc://test_ctrl", "get_acq_settings", {})
    assert reply["ok"] is True
    assert reply["result"]["options"]["PixelReadoutRate"] == ["100 MHz"]
    assert reply["result"]["values"]["GainMode"] is None
