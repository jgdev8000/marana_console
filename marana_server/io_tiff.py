"""Server-side multi-page TIFF writer for kinetic stacks."""
from __future__ import annotations

import json
import os

import numpy as np
import tifffile


def write_image_stack(path: str, frames: np.ndarray, metadata: dict) -> int:
    """Write a (N, H, W) uint16 array as a multi-page TIFF. Returns bytes written."""
    if frames.ndim != 3 or frames.dtype != np.uint16:
        raise ValueError(f"frames must be 3-D uint16, got shape={frames.shape} dtype={frames.dtype}")
    tifffile.imwrite(
        path,
        frames,
        photometric="minisblack",
        description=json.dumps(metadata, separators=(",", ":")),
    )
    return os.path.getsize(path)
