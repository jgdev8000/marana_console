"""Builds the JSON metadata blob embedded in TIFF tags."""
from __future__ import annotations


def build_metadata(camera: dict, acquisition: dict, display: dict | None = None) -> dict:
    return {
        "camera": dict(camera),
        "acquisition": dict(acquisition),
        "display": dict(display or {"rot": 0, "flip_h": False, "flip_v": False}),
    }
