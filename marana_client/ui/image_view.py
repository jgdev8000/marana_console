"""Image display widget — pyqtgraph ImageView wrapped with display transforms + contrast modes."""
from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets


class ContrastMode(str, enum.Enum):
    AUTO = "auto"
    PERCENTILE = "percentile"
    MANUAL = "manual"
    FREEZE = "freeze"


@dataclass
class DisplayState:
    rot: int = 0           # one of 0, 90, 180, 270
    flip_h: bool = False
    flip_v: bool = False
    contrast: ContrastMode = ContrastMode.PERCENTILE
    manual_min: int = 0
    manual_max: int = 65535
    percentile_lo: float = 1.0
    percentile_hi: float = 99.5


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
        self._last_levels: tuple[float, float] | None = None
        self._last_raw: np.ndarray | None = None  # last frame, untransformed

    def set_rotation(self, deg: int) -> None:
        assert deg in (0, 90, 180, 270)
        self.state.rot = deg
        self._rerender()

    def set_flip(self, h: bool, v: bool) -> None:
        self.state.flip_h = h
        self.state.flip_v = v
        self._rerender()

    def set_contrast(self, mode: ContrastMode, manual_min: int = 0, manual_max: int = 65535,
                     pct_lo: float = 1.0, pct_hi: float = 99.5) -> None:
        self.state.contrast = mode
        self.state.manual_min = manual_min
        self.state.manual_max = manual_max
        self.state.percentile_lo = pct_lo
        self.state.percentile_hi = pct_hi
        self._rerender()

    def _rerender(self) -> None:
        """Re-apply the current transform/contrast to the last frame. Lets
        rotate/flip/contrast affect a static image (e.g. a SNAP), not just the
        live stream where new frames pick up the change automatically."""
        if self._last_raw is not None:
            self.update_frame(self._last_raw)

    def update_frame(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        self._last_raw = frame
        view = self._apply_transform(frame)
        levels = self._compute_levels(view)
        self.image_item.setImage(view.T, autoLevels=False, autoRange=False, autoHistogramRange=False)
        if levels is not None:
            self.image_item.setLevels(levels[0], levels[1])
            self._last_levels = levels

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

    def _compute_levels(self, view: np.ndarray) -> tuple[float, float] | None:
        mode = self.state.contrast
        if mode == ContrastMode.FREEZE:
            return self._last_levels
        if mode == ContrastMode.MANUAL:
            return (self.state.manual_min, self.state.manual_max)
        if mode == ContrastMode.PERCENTILE:
            lo, hi = np.percentile(view, (self.state.percentile_lo, self.state.percentile_hi))
            return (float(lo), float(hi))
        # AUTO
        return (float(view.min()), float(view.max()))
