"""Client-side single-page TIFF writer."""
from __future__ import annotations

import json

import numpy as np
import tifffile


def write_snapshot(path: str, frame: np.ndarray, metadata: dict) -> None:
    if frame.ndim != 2 or frame.dtype != np.uint16:
        raise ValueError(f"frame must be 2-D uint16, got shape={frame.shape} dtype={frame.dtype}")
    tifffile.imwrite(
        path,
        frame,
        photometric="minisblack",
        description=json.dumps(metadata, separators=(",", ":")),
    )
