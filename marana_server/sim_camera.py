"""Software camera simulator that serves a real sample image with per-frame noise.

Used in place of MaranaCamera when ``--sim`` is passed. Unlike the Andor SDK
SimCam (a fixed synthetic ramp with no writable AOI, and occasionally flaky),
this serves a real Marana sample image, supports software AOI, and scales
brightness with exposure — so the client looks and behaves like a real camera
for UI/workflow testing.

Duck-typed to the subset of MaranaCamera that the worker and service use.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_SAMPLE_PATH = Path(__file__).parent / "assets" / "sim_sample.tif"
_BASE_EXPOSURE_S = 0.05   # exposure at which the sample is shown at native brightness


class SimCamera:
    def __init__(self) -> None:
        self._img: np.ndarray | None = None
        self._H = 0
        self._W = 0
        self._features: dict = {}
        self._aoi = (0, 0, 0, 0)   # left0, top0, width, height
        self._activity = "idle"
        self._cooling = {"enabled": False, "target_c": 0.0,
                         "sensor_temp_c": 20.0, "status": "Cooler Off"}
        self._rng = np.random.default_rng(1234)

    # --- lifecycle --------------------------------------------------------

    def open(self, sim: bool = True) -> None:
        import tifffile
        self._img = tifffile.imread(str(_SAMPLE_PATH)).astype(np.uint16)
        self._H, self._W = self._img.shape
        self._aoi = (0, 0, self._W, self._H)
        self._features = {
            "ExposureTime": _BASE_EXPOSURE_S,
            "PixelEncoding": "Mono16",
            "PixelReadoutRate": "100 MHz",
            "ElectronicShutteringMode": "Rolling",
            "CycleMode": "Fixed",
            "FrameCount": 1,
            "TriggerMode": "Internal",
            "FrameRate": 30.0,
            "AOIWidth": self._W,
            "AOIHeight": self._H,
            "AOILeft": 1,        # 1-based, like the SDK
            "AOITop": 1,
            "AOIStride": self._W * 2,
        }
        log.info("SimCamera serving %dx%d sample from %s", self._W, self._H, _SAMPLE_PATH)

    def close(self) -> None:
        self._img = None

    def current_activity(self) -> str:
        return self._activity

    # --- identity ---------------------------------------------------------

    @property
    def model(self) -> str:
        return "MARANA-SIM (software)"

    @property
    def serial(self) -> str:
        return "SIM-IMG-001"

    @property
    def sensor_width(self) -> int:
        return self._W

    @property
    def sensor_height(self) -> int:
        return self._H

    # --- features ---------------------------------------------------------

    def get_feature(self, name: str):
        if name in self._features:
            return self._features[name]
        raise ValueError(f"SimCamera has no feature {name!r}")

    def set_feature(self, name: str, value) -> None:
        if name == "AOIWidth":
            self._set_aoi_dim(width=int(value))
        elif name == "AOIHeight":
            self._set_aoi_dim(height=int(value))
        elif name == "AOILeft":
            self._set_aoi_dim(left=int(value) - 1)   # SDK 1-based -> 0-based
        elif name == "AOITop":
            self._set_aoi_dim(top=int(value) - 1)
        else:
            self._features[name] = value

    def enum_options(self, name: str) -> list[str]:
        opts = {
            "PixelEncoding": ["Mono16", "Mono12", "Mono12Packed"],
            "PixelReadoutRate": ["100 MHz", "270 MHz"],
            "ElectronicShutteringMode": ["Rolling", "Global"],
        }
        if name in opts:
            return list(opts[name])
        raise ValueError(f"{name} is not an enum on SimCamera")

    def get_acq_settings(self) -> dict:
        return {
            "options": {
                "PixelReadoutRate": ["100 MHz", "270 MHz"],
                "PixelEncoding": ["Mono16", "Mono12", "Mono12Packed"],
                "GainMode": [],   # SimCam has no gain — UI hides the combo
            },
            "values": {
                "PixelReadoutRate": self._features["PixelReadoutRate"],
                "PixelEncoding": self._features["PixelEncoding"],
                "GainMode": None,
            },
            "readonly": {
                "bit_depth": None,
                "readout_time_s": None,
                "frame_rate_hz": self._features["FrameRate"],
                "max_frame_rate_hz": 200.0,
            },
        }

    # --- AOI --------------------------------------------------------------

    def _set_aoi_dim(self, left=None, top=None, width=None, height=None) -> None:
        l, t, w, h = self._aoi
        if left is not None:
            l = left
        if top is not None:
            t = top
        if width is not None:
            w = width
        if height is not None:
            h = height
        # Clamp to sensor.
        w = max(1, min(w, self._W))
        h = max(1, min(h, self._H))
        l = max(0, min(l, self._W - w))
        t = max(0, min(t, self._H - h))
        self._aoi = (l, t, w, h)
        self._features["AOIWidth"] = w
        self._features["AOIHeight"] = h
        self._features["AOILeft"] = l + 1
        self._features["AOITop"] = t + 1
        self._features["AOIStride"] = w * 2

    def set_aoi(self, x0: int, x1: int, y0: int, y1: int) -> None:
        from marana_proto.errors import FeatureValueOutOfRange
        if not (0 <= x0 <= x1 < self._W and 0 <= y0 <= y1 < self._H):
            raise FeatureValueOutOfRange(
                f"AOI ({x0},{x1},{y0},{y1}) out of sensor ({self._W}x{self._H})")
        self._set_aoi_dim(left=x0, top=y0, width=x1 - x0 + 1, height=y1 - y0 + 1)

    def get_aoi(self) -> tuple[int, int, int, int]:
        l, t, w, h = self._aoi
        return (l, l + w - 1, t, t + h - 1)

    def set_aoi_full(self) -> None:
        self.set_aoi(0, self._W - 1, 0, self._H - 1)

    # --- acquisition ------------------------------------------------------

    def _configure_single_frame_mode(self, exposure_s: float | None = None) -> None:
        if exposure_s is not None:
            self._features["ExposureTime"] = float(exposure_s)

    def _render_frame(self, exposure_s: float | None = None) -> np.ndarray:
        """Crop the sample to the current AOI, scale by exposure, add shot noise."""
        assert self._img is not None
        l, t, w, h = self._aoi
        base = self._img[t:t + h, l:l + w].astype(np.float32)
        exp = float(exposure_s if exposure_s is not None else self._features["ExposureTime"])
        scale = exp / _BASE_EXPOSURE_S
        signal = base * scale
        # Poisson-like shot noise (normal approx, std = sqrt(signal)).
        noise = self._rng.standard_normal(signal.shape).astype(np.float32) * np.sqrt(np.maximum(signal, 1.0))
        out = np.clip(signal + noise, 0, 65535)
        return out.astype(np.uint16)

    def single_shot(self, timeout_ms: int = 3000, exposure_s: float | None = None):
        self._activity = "single_shot"
        try:
            return self._render_frame(exposure_s)
        finally:
            self._activity = "idle"

    def safe_continuous_iter(self, exposure_s: float | None = None, inter_frame_sleep_s: float = 0.01):
        self._activity = "safe_continuous_iter"
        try:
            while True:
                yield self._render_frame(exposure_s)
                if inter_frame_sleep_s > 0:
                    time.sleep(inter_frame_sleep_s)
        except GeneratorExit:
            return
        finally:
            self._activity = "idle"

    def kinetic_burst(self, frame_count, exposure_s, frame_rate_hz,
                      on_progress=None, stop_flag=None):
        import threading
        from marana_proto.errors import KineticValidationError
        if not (1 <= frame_count <= 10000):
            raise KineticValidationError(f"frame_count {frame_count} not in [1, 10000]")
        if stop_flag is None:
            stop_flag = threading.Event()
        l, t, w, h = self._aoi
        frames = np.zeros((frame_count, h, w), dtype=np.uint16)
        period = 1.0 / frame_rate_hz if frame_rate_hz > 0 else 0.0
        t0 = time.monotonic()
        done = 0
        for i in range(frame_count):
            if stop_flag.is_set():
                break
            frames[i] = self._render_frame(exposure_s)
            done += 1
            if period:
                time.sleep(period)
            if on_progress is not None:
                elapsed = time.monotonic() - t0
                on_progress(done, frame_count, done / elapsed if elapsed > 0 else 0.0)
        elapsed = time.monotonic() - t0
        return frames[:done], done, elapsed

    # --- cooling ----------------------------------------------------------

    def get_cooling(self) -> dict:
        return dict(self._cooling)

    def set_cooling(self, enable: bool, target_c: float | None = None) -> None:
        self._cooling["enabled"] = bool(enable)
        if target_c is not None:
            self._cooling["target_c"] = float(target_c)
        self._cooling["status"] = "Cooling" if enable else "Cooler Off"
        if enable and target_c is not None:
            self._cooling["sensor_temp_c"] = float(target_c)
