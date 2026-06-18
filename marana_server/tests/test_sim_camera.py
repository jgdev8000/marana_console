"""SimCamera (software image simulator) behaviour."""
import numpy as np

from marana_server.sim_camera import SimCamera


def _open():
    c = SimCamera()
    c.open(sim=True)
    return c


def test_serves_real_sample_not_full_range():
    c = _open()
    f = c.single_shot()
    assert f.dtype == np.uint16
    assert f.shape == (c.sensor_height, c.sensor_width)
    # A real sample has a low background and a modest peak — NOT a 0..65535 ramp.
    assert f.max() < 60000
    assert int(np.median(f)) < f.max() // 2


def test_software_aoi_crops_frame():
    c = _open()
    c.set_aoi(100, 355, 200, 519)   # 256 wide x 320 tall
    assert c.get_aoi() == (100, 355, 200, 519)
    assert c.get_feature("AOIWidth") == 256
    assert c.get_feature("AOIHeight") == 320
    f = c.single_shot()
    assert f.shape == (320, 256)


def test_aoi_via_set_feature_matches_client_path():
    c = _open()
    # The client applies AOI as four set_feature calls (1-based left/top).
    c.set_feature("AOIWidth", 128)
    c.set_feature("AOIHeight", 64)
    c.set_feature("AOILeft", 11)   # -> 0-based 10
    c.set_feature("AOITop", 21)    # -> 0-based 20
    assert c.get_aoi() == (10, 137, 20, 83)
    assert c.single_shot().shape == (64, 128)


def test_exposure_scales_brightness():
    c = _open()
    dim = c.single_shot(exposure_s=0.01).astype(np.float64).mean()
    bright = c.single_shot(exposure_s=0.20).astype(np.float64).mean()
    assert bright > dim * 5


def test_kinetic_burst_returns_stack():
    c = _open()
    c.set_aoi(0, 63, 0, 63)
    frames, done, elapsed = c.kinetic_burst(5, 0.001, 200.0)
    assert frames.shape == (5, 64, 64)
    assert done == 5
