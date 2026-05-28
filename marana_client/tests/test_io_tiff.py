import json
import tifffile
import numpy as np

from marana_client.io_tiff import write_snapshot
from marana_client.meta import build_snapshot_metadata


def test_write_snapshot_round_trip(tmp_path):
    arr = (np.arange(8 * 8, dtype=np.uint16) % 100).reshape(8, 8)
    header = {"ts_iso": "2026-05-28T10:00:00+00:00", "width": 8, "height": 8, "dtype": "uint16"}
    server_info = {"camera_model": "MARANA", "camera_serial": "SN", "host": "linuxbox"}
    md = build_snapshot_metadata(server_info, header, display={"rot": 90, "flip_h": True, "flip_v": False})
    path = tmp_path / "snap.tif"
    write_snapshot(str(path), arr, md)
    with tifffile.TiffFile(str(path)) as tf:
        reread = tf.asarray()
        desc = tf.pages[0].tags.get("ImageDescription").value
    np.testing.assert_array_equal(reread, arr)
    payload = json.loads(desc)
    assert payload["acquisition"]["timestamp_iso"] == "2026-05-28T10:00:00+00:00"
    assert payload["display"]["rot"] == 90


def test_config_round_trip(tmp_path, monkeypatch):
    from marana_client import config
    monkeypatch.setattr(config, "_CONFIG_DIR", tmp_path / ".marana_console")
    monkeypatch.setattr(config, "_CONFIG_PATH", tmp_path / ".marana_console" / "config.json")
    cfg = config.load()
    cfg["host"] = "linuxbox.example.com"
    cfg["snapshot_dir"] = "/home/me/captures"
    config.save(cfg)
    cfg2 = config.load()
    assert cfg2["host"] == "linuxbox.example.com"
    assert cfg2["snapshot_dir"] == "/home/me/captures"
