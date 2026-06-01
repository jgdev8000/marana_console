"""Image display widget — pyqtgraph ImageView wrapped with display transforms +
an auto-baseline + offset contrast model.

Contrast model:
- An *auto baseline* (1–99.5% black/white) is established from a frame at trigger
  points: once when live starts, and on each snapshot. It does NOT recompute every
  live frame (that would make the view jump constantly).
- Two *offsets* (black/white, as a percentage of the auto span) sit on top of the
  baseline and persist frame-to-frame. The slider panel drives these live, so any
  contrast tweak is applied relative to the auto result.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets


@dataclass
class DisplayState:
    rot: int = 0           # one of 0, 90, 180, 270
    flip_h: bool = False
    flip_v: bool = False


class MaranaImageView(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image_item = pg.ImageView()
        self.image_item.ui.roiBtn.hide()
        self.image_item.ui.menuBtn.hide()
        layout.addWidget(self.image_item)
        self.state = DisplayState()
        self._last_raw: np.ndarray | None = None       # last frame, untransformed
        self._auto_lo: float | None = None             # auto baseline black point
        self._auto_hi: float | None = None             # auto baseline white point
        self._black_off_pct: int = 0                   # offset, % of auto span
        self._white_off_pct: int = 0

    # --- display transforms ----------------------------------------------

    def set_rotation(self, deg: int) -> None:
        assert deg in (0, 90, 180, 270)
        self.state.rot = deg
        self._rerender()

    def set_flip(self, h: bool, v: bool) -> None:
        self.state.flip_h = h
        self.state.flip_v = v
        self._rerender()

    # --- contrast ---------------------------------------------------------

    def set_level_offsets(self, black_pct: int, white_pct: int) -> None:
        """Live black/white offsets (percent of the auto span), applied on top
        of the current auto baseline."""
        self._black_off_pct = int(black_pct)
        self._white_off_pct = int(white_pct)
        self._rerender()

    def auto_baseline(self) -> None:
        """Recompute the auto baseline (1–99.5%) from the current frame, keeping
        the user's offsets. Called once on live-start and on each snap."""
        if self._last_raw is None:
            return
        self._set_baseline_from(self._last_raw)
        self._rerender()

    def reset_auto(self) -> None:
        """Auto baseline AND re-center the offsets — the 'Auto' button: pristine
        auto-stretch."""
        self._black_off_pct = 0
        self._white_off_pct = 0
        self.auto_baseline()

    def _set_baseline_from(self, frame: np.ndarray) -> None:
        lo, hi = np.percentile(frame, (1.0, 99.5))
        self._auto_lo, self._auto_hi = float(lo), float(hi)
        if self._auto_hi <= self._auto_lo:
            self._auto_hi = self._auto_lo + 1.0

    def _effective_levels(self) -> tuple[float, float] | None:
        if self._auto_lo is None or self._auto_hi is None:
            return None
        span = self._auto_hi - self._auto_lo
        lo = self._auto_lo + (self._black_off_pct / 100.0) * span
        hi = self._auto_hi + (self._white_off_pct / 100.0) * span
        if hi <= lo:
            hi = lo + 1.0
        return (lo, hi)

    # --- rendering --------------------------------------------------------

    def _rerender(self) -> None:
        """Re-apply transform + contrast to the last frame so rotate/flip/contrast
        affect a static image (e.g. a SNAP), not just the live stream."""
        if self._last_raw is not None:
            self.update_frame(self._last_raw)

    def update_frame(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        self._last_raw = frame
        # Establish a baseline lazily on the very first frame so there's always
        # something sensible to display before an explicit trigger fires.
        if self._auto_lo is None:
            self._set_baseline_from(frame)
        view = self._apply_transform(frame)
        levels = self._effective_levels()
        self.image_item.setImage(view.T, autoLevels=False, autoRange=False, autoHistogramRange=False)
        if levels is not None:
            self.image_item.setLevels(levels[0], levels[1])

    def _apply_transform(self, frame: np.ndarray) -> np.ndarray:
        view = frame
        if self.state.flip_h:
            view = view[:, ::-1]
        if self.state.flip_v:
            view = view[::-1, :]
        if self.state.rot:
            k = self.state.rot // 90
            view = np.rot90(view, k=k)
        return np.ascontiguousarray(view)
