import pytest

pyAndorSDK3 = pytest.importorskip("pyAndorSDK3")

from marana_server.camera import MaranaCamera
from marana_proto.errors import CameraDisconnected, FeatureValueOutOfRange, FeatureNotWritable


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


@pytest.fixture
def sim_cam():
    cam = MaranaCamera()
    cam.open(sim=True)
    yield cam
    cam.close()


def test_set_and_get_exposure(sim_cam):
    sim_cam.set_feature("ExposureTime", 0.01)
    val = sim_cam.get_feature("ExposureTime")
    assert val == pytest.approx(0.01, rel=1e-3)


def test_get_feature_returns_enum_string(sim_cam):
    val = sim_cam.get_feature("PixelEncoding")
    assert isinstance(val, str)
    # SimCam supports Mono12, Mono12Packed, etc.; real Marana adds Mono16
    assert val in ("Mono12", "Mono16", "Mono12Packed", "Mono32",
                   "Mono22Parallel", "Mono22PackedParallel", "Mono12Coded", "Mono12CodedPacked", "RGB8Packed")


def test_list_enum_options(sim_cam):
    opts = sim_cam.enum_options("PixelEncoding")
    assert isinstance(opts, list)
    assert len(opts) > 0
    assert all(isinstance(o, str) for o in opts)


def test_set_pixel_encoding_mono12(sim_cam):
    # Mono12 is supported by SimCam; Mono16 is not.
    sim_cam.set_feature("PixelEncoding", "Mono12")
    assert sim_cam.get_feature("PixelEncoding") == "Mono12"


def test_get_aoi_returns_sensor_size(sim_cam):
    # SimCam doesn't allow writing AOI, but reading the default works.
    x0, x1, y0, y1 = sim_cam.get_aoi()
    assert 0 <= x0 <= x1 < sim_cam.sensor_width
    assert 0 <= y0 <= y1 < sim_cam.sensor_height


def test_set_aoi_full_handles_unwritable_gracefully(sim_cam):
    """SimCam doesn't allow AOI writes. The wrapper should raise FeatureNotWritable, not crash."""
    try:
        sim_cam.set_aoi_full()
    except FeatureNotWritable:
        pass  # Expected on SimCam
    else:
        # On a real camera, the AOI should now span the full sensor.
        x0, x1, y0, y1 = sim_cam.get_aoi()
        assert x0 == 0 and y0 == 0
        assert x1 + 1 == sim_cam.sensor_width
        assert y1 + 1 == sim_cam.sensor_height


def test_invalid_aoi_raises(sim_cam):
    # Validation happens before any SDK write — should raise FeatureValueOutOfRange.
    with pytest.raises(FeatureValueOutOfRange):
        sim_cam.set_aoi(0, 10_000, 0, 10_000)  # beyond sensor
