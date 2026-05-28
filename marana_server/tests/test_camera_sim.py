import pytest

pyAndorSDK3 = pytest.importorskip("pyAndorSDK3")

from marana_server.camera import MaranaCamera
from marana_proto.errors import CameraDisconnected


def test_open_sim_camera_reports_simcam_model():
    cam = MaranaCamera()
    cam.open(sim=True)
    try:
        assert "SIM" in cam.model.upper(), f"expected SIM in model, got {cam.model!r}"
        assert cam.sensor_width > 0
        assert cam.sensor_height > 0
        assert cam.serial  # non-empty string
    finally:
        cam.close()


def test_double_open_is_idempotent():
    cam = MaranaCamera()
    cam.open(sim=True)
    cam.open(sim=True)  # should not raise
    cam.close()


def test_close_without_open_is_safe():
    cam = MaranaCamera()
    cam.close()  # no-op
