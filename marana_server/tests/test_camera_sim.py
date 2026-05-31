import threading

import pytest
import numpy as np

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


def test_single_shot_returns_uint16_2d(sim_cam):
    sim_cam.set_feature("ExposureTime", 0.001)
    sim_cam.set_feature("PixelEncoding", "Mono12")
    frame = sim_cam.single_shot(timeout_ms=3000)
    assert frame.dtype == np.uint16
    assert frame.ndim == 2
    assert frame.shape == (sim_cam.sensor_height, sim_cam.sensor_width)


def test_safe_continuous_iter_yields_multiple_frames(sim_cam):
    sim_cam.set_feature("ExposureTime", 0.001)
    sim_cam.set_feature("PixelEncoding", "Mono12")
    frames = []
    it = sim_cam.safe_continuous_iter(inter_frame_sleep_s=0.0)
    for i, f in enumerate(it):
        frames.append(f)
        if i >= 2:  # 3 frames total
            it.close()
            break
    assert len(frames) == 3
    for f in frames:
        assert f.shape == (sim_cam.sensor_height, sim_cam.sensor_width)
        assert f.dtype == np.uint16


def test_kinetic_small_burst(sim_cam):
    sim_cam.set_feature("PixelEncoding", "Mono12")
    progress_calls = []

    def on_progress(done, total, fps):
        progress_calls.append((done, total))

    stop = threading.Event()
    frames, count, elapsed = sim_cam.kinetic_burst(
        frame_count=8, exposure_s=0.001, frame_rate_hz=20.0,
        on_progress=on_progress, stop_flag=stop,
    )
    assert frames.shape == (8, sim_cam.sensor_height, sim_cam.sensor_width)
    assert frames.dtype == np.uint16
    assert count == 8
    assert elapsed > 0
    assert len(progress_calls) >= 1
    assert progress_calls[-1][0] == 8


def test_kinetic_cancel_returns_partial(sim_cam):
    sim_cam.set_feature("PixelEncoding", "Mono12")
    stop = threading.Event()

    def on_progress(done, total, fps):
        if done >= 3:
            stop.set()

    frames, count, elapsed = sim_cam.kinetic_burst(
        frame_count=20, exposure_s=0.001, frame_rate_hz=10.0,
        on_progress=on_progress, stop_flag=stop,
    )
    assert count >= 3
    assert count < 20
    assert frames.shape == (20, sim_cam.sensor_height, sim_cam.sensor_width)  # preallocated full size


def test_get_cooling_state_returns_dict(sim_cam):
    state = sim_cam.get_cooling()
    assert "enabled" in state
    assert "target_c" in state
    assert "sensor_temp_c" in state
    assert "status" in state
    assert isinstance(state["enabled"], bool)
    assert isinstance(state["target_c"], (int, float))
    assert isinstance(state["sensor_temp_c"], (int, float))
    assert isinstance(state["status"], str)


def test_set_cooling_does_not_raise(sim_cam):
    # Sim may not actually cool, but the call shouldn't blow up.
    sim_cam.set_cooling(enable=True, target_c=-30.0)
    sim_cam.set_cooling(enable=False, target_c=-30.0)


def test_available_enum_options_filters_unavailable(sim_cam):
    # On the SimCam, PixelEncoding lists 8 entries but only Mono12/Mono12Packed are available.
    avail = sim_cam.available_enum_options("PixelEncoding")
    assert isinstance(avail, list)
    assert "Mono12" in avail
    assert "Mono16" not in avail  # marked unavailable on the sim
    full = sim_cam.enum_options("PixelEncoding")
    assert len(avail) < len(full)  # filtering actually removed some


def test_get_acq_settings_shape(sim_cam):
    s = sim_cam.get_acq_settings()
    assert set(s) == {"options", "values", "readonly"}
    for k in ("PixelReadoutRate", "PixelEncoding", "GainMode"):
        assert k in s["options"] and isinstance(s["options"][k], list)
        assert k in s["values"]
    # SimCam lacks GainMode -> empty options, None value
    assert s["options"]["GainMode"] == []
    assert s["values"]["GainMode"] is None
    # Available encodings are the filtered subset
    assert "Mono16" not in s["options"]["PixelEncoding"]
    # Read-only block
    ro = s["readonly"]
    assert set(ro) == {"bit_depth", "readout_time_s", "frame_rate_hz", "max_frame_rate_hz"}
    assert ro["bit_depth"] is None       # not on sim
    assert ro["readout_time_s"] is None  # not on sim
    assert isinstance(ro["max_frame_rate_hz"], float) and ro["max_frame_rate_hz"] > 0
