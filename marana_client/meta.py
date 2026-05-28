"""Builds the JSON metadata blob embedded in TIFF tags for client-written files."""
from __future__ import annotations


def build_snapshot_metadata(server_info: dict, frame_header: dict, display: dict | None = None,
                            extra: dict | None = None) -> dict:
    """server_info: from `hello`. frame_header: from a live_frame or snap_single header."""
    md = {
        "camera": {
            "model": server_info.get("camera_model", ""),
            "serial": server_info.get("camera_serial", ""),
            "host": server_info.get("host", ""),
        },
        "acquisition": {
            "timestamp_iso": frame_header.get("ts_iso", ""),
            "exposure_s": frame_header.get("exposure_s"),
            "encoding": frame_header.get("encoding"),
            "speed_mhz": frame_header.get("speed_mhz"),
            "shutter": frame_header.get("shutter"),
            "aoi_0based_inclusive": frame_header.get("aoi_0based_inclusive"),
            "binning": frame_header.get("binning"),
            "sensor_temp_c": frame_header.get("sensor_temp_c"),
        },
        "display": dict(display or {"rot": 0, "flip_h": False, "flip_v": False}),
    }
    if extra:
        md["acquisition"].update(extra)
    return md
