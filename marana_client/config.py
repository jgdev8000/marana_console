"""Tiny JSON config persisted to ~/.marana_console/config.json."""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".marana_console"
_CONFIG_PATH = _CONFIG_DIR / "config.json"

_DEFAULTS = {
    "host": "localhost",
    "ctrl_port": 5555,
    "frame_port": 5556,
    "snapshot_dir": str(Path.home()),
    "kinetic_subdir": "",
    "contrast_mode": "percentile",
    "manual_min": 0,
    "manual_max": 65535,
    "rot": 0,
    "flip_h": False,
    "flip_v": False,
    "mover_source": "sim",
    "focus_direction": 1,
    "focus_range_um": 100.0,
    "focus_step_um": 5.0,
    "focus_exposure_s": 0.05,
    "focus_settle_ms": 100,
    "focus_return_to_start": True,
}


def load() -> dict:
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(_CONFIG_PATH.read_text())
    except Exception:
        data = {}
    merged = dict(_DEFAULTS)
    merged.update(data)
    return merged


def save(cfg: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
