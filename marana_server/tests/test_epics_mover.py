"""Tests for EpicsMover. Requires the sim IOC (MCS2SIM:) to be running.

Run `./deploy/sim/start-mcs2-sim.sh` in another terminal first; if MCS2SIM:zoneplate_z
is not reachable, every test pytest.skips with a helpful message.
"""
import os
import time

import pytest

epics = pytest.importorskip("epics")

from marana_server.epics_mover import EpicsMover
from marana_proto.errors import CameraDisconnected, AcquisitionTimeout

PV_BASE = os.environ.get("MARANA_MOVER_PV_BASE", "MCS2SIM:zoneplate_z")


@pytest.fixture
def mover():
    try:
        m = EpicsMover(PV_BASE, connect_timeout_s=1.0)
    except CameraDisconnected as e:
        pytest.skip(f"sim IOC not reachable at {PV_BASE}: {e}. Start ./deploy/sim/start-mcs2-sim.sh.")
    z0 = m.read_rbv_mm()
    yield m
    # Restore Z to original position so the rig (or sim) is left as found
    try:
        m.move(z0)
        m.wait_done(timeout_s=5.0, settle_s=0.0)
    except Exception:
        pass
    m.close()


def test_read_rbv_mm_returns_float(mover):
    z = mover.read_rbv_mm()
    assert isinstance(z, float)


def test_read_limits_mm_matches_substitutions(mover):
    dllm, dhlm = mover.read_limits_mm()
    assert dllm == pytest.approx(-10.5, abs=0.001)
    assert dhlm == pytest.approx(10.5, abs=0.001)


def test_egu_is_mm(mover):
    assert mover.egu() == "mm"


def test_move_and_wait_done(mover):
    z0 = mover.read_rbv_mm()
    target = z0 + 0.000010  # 10 µm in mm
    mover.move(target)
    mover.wait_done(timeout_s=5.0, settle_s=0.05)
    actual = mover.read_rbv_mm()
    assert actual == pytest.approx(target, abs=1e-5)  # within ~10 µm tolerance for sim


def test_stop_aborts_in_flight_move(mover):
    z0 = mover.read_rbv_mm()
    far_target = z0 + 5.0  # 5 mm — would take ~5 s at VELO=1
    mover.move(far_target)
    time.sleep(0.1)            # let move get going
    mover.stop()
    mover.wait_done(timeout_s=2.0, settle_s=0.0)
    actual = mover.read_rbv_mm()
    # Should have stopped well short of the 5 mm target
    assert abs(actual - far_target) > 1.0


def test_connect_timeout_raises_camera_disconnected():
    with pytest.raises(CameraDisconnected):
        EpicsMover("DOES_NOT_EXIST:nope", connect_timeout_s=0.5)


def test_wait_done_timeout_raises_acquisition_timeout(mover):
    z0 = mover.read_rbv_mm()
    far = z0 + 5.0
    mover.move(far)
    with pytest.raises(AcquisitionTimeout):
        mover.wait_done(timeout_s=0.3, settle_s=0.0)  # 5 mm at 1 mm/s won't finish
    # Cleanup: stop the in-flight move (fixture's restore will move back)
    mover.stop()
    mover.wait_done(timeout_s=2.0, settle_s=0.0)
