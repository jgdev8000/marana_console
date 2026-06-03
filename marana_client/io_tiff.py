"""Client-side TIFF writers (single page + multi-page stack)."""
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


def write_stack(path: str, frames: np.ndarray, metadata: dict) -> None:
    """Write a (N, H, W) uint16 array as a multi-page TIFF (client-side)."""
    if frames.ndim != 3 or frames.dtype != np.uint16:
        raise ValueError(f"frames must be 3-D uint16, got shape={frames.shape} dtype={frames.dtype}")
    tifffile.imwrite(
        path,
        frames,
        photometric="minisblack",
        description=json.dumps(metadata, separators=(",", ":")),
    )
