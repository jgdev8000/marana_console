import json
import tifffile
import numpy as np
import pytest

from marana_server.meta import build_metadata
from marana_server.io_tiff import write_kinetic_stack


def test_build_metadata_shape():
    md = build_metadata(
        camera={"model": "MARANA", "serial": "SN123", "host": "linuxbox"},
        acquisition={
            "exposure_s": 0.05, "encoding": "Mono16", "speed_mhz": 310,
            "shutter": "Rolling", "aoi_0based_inclusive": [0, 2047, 0, 2047],
            "binning": [1, 1], "sensor_temp_c": -44.8,
            "timestamp_iso": "2026-05-28T10:00:00-07:00",
        },
        display={"rot": 0, "flip_h": False, "flip_v": False},
    )
    assert md["camera"]["model"] == "MARANA"
    assert md["acquisition"]["exposure_s"] == 0.05


def test_write_kinetic_stack_round_trip(tmp_path):
    arr = (np.arange(3 * 16 * 16, dtype=np.uint16) % 1000).reshape(3, 16, 16)
    md = {"frame_count": 3, "target_fps_hz": 10.0, "achieved_fps_hz": 9.8, "acquisition_time_s": 0.31}
    path = tmp_path / "stack.tif"
    written = write_kinetic_stack(str(path), arr, md)
    assert written > 0

    with tifffile.TiffFile(str(path)) as tf:
        pages = tf.pages
        assert len(pages) == 3
        reread = tf.asarray()
    assert reread.shape == arr.shape
    assert reread.dtype == np.uint16
    np.testing.assert_array_equal(reread, arr)

    # Metadata in TIFF tag
    with tifffile.TiffFile(str(path)) as tf:
        desc = tf.pages[0].tags.get("ImageDescription")
        if desc is not None:
            payload = json.loads(desc.value) if desc.value.startswith("{") else None
            if payload:
                assert payload.get("frame_count") == 3
