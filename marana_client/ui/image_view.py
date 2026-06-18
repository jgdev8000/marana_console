"""Image display widget — pyqtgraph ImageView wrapped with display transforms +
an auto-baseline + offset contrast model.

Contrast model:
- An *auto baseline* (best-fit: data min..max of the frame) is recomputed on every
  frame, so the live view tracks the data. A fixed bias (AUTO_BLACK/WHITE_BIAS_PCT)
  is added to approximate Andor Solis's auto: black pushed up, white given headroom.
- Two *offsets* (black/white, as a percentage of the auto span) sit on top of the
  baseline and persist frame-to-frame. The slider panel drives these live, so any
  contrast tweak is applied relative to the auto result.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

# Auto-contrast bias applied on top of the best-fit (min..max) window, as a
# percentage of the data span, to approximate Andor Solis's auto. Tune here.
AUTO_BLACK_BIAS_PCT = 11   # raise black point: push background toward black
AUTO_WHITE_BIAS_PCT = 32   # raise white point: leave headroom above the peak


@dataclass
class DisplayState:
    rot: int = 0           # one of 0, 90, 180, 270
    flip_h: bool = False
    flip_v: bool = False


class MaranaImageView(QtWidgets.QWidget):
    # Emitted on mouse-drag release: raw-frame rect (relative to the currently
    # displayed AOI), inclusive: (row0, row1, col0, col1).
    aoiSelected = QtCore.pyqtSignal(int, int, int, int)

    _MIN_DRAG_PX = 4   # ignore tiny drags / clicks

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image_item = pg.ImageView()
        self.image_item.ui.roiBtn.hide()
        self.image_item.ui.menuBtn.hide()
        layout.addWidget(self.image_item)
        # Pin the histogram axis to the full 16-bit range so its numbers are
        # absolute sensor counts (0..65535), not auto-scaled to the data — the
        # level region then shows where black/white sit within the full range.
        self._full_scale = 65535
        self.image_item.ui.histogram.item.setHistogramRange(0, self._full_scale, padding=0)
        self.state = DisplayState()
        self._install_aoi_drag()
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
        # Best-fit: stretch to the actual data min..max of the frame (matches
        # Andor Solis "Best Fit"). This never clips the in-focus peak, unlike a
        # high-percentile white point.
        self._auto_lo = float(frame.min())
        self._auto_hi = float(frame.max())
        if self._auto_hi <= self._auto_lo:
            self._auto_hi = self._auto_lo + 1.0

    def _effective_levels(self) -> tuple[float, float] | None:
        if self._auto_lo is None or self._auto_hi is None:
            return None
        span = self._auto_hi - self._auto_lo
        # Auto = best-fit min..max plus a fixed bias that approximates Andor Solis:
        # raise black ~11% of span (push background toward black) and white ~32%
        # (leave headroom above the peak so it isn't blown out). User offset
        # sliders add on top of this.
        black = (AUTO_BLACK_BIAS_PCT + self._black_off_pct) / 100.0
        white = (AUTO_WHITE_BIAS_PCT + self._white_off_pct) / 100.0
        lo = self._auto_lo + black * span
        hi = self._auto_hi + white * span
        if hi <= lo:
            hi = lo + 1.0
        return (lo, hi)

    # --- rendering --------------------------------------------------------

    def _rerender(self) -> None:
        """Re-apply transform + contrast to the last frame so rotate/flip/contrast
        affect a static image (e.g. a SNAP), not just the live stream."""
        if self._last_raw is not None:
            self.update_frame(self._last_raw)

    def update_frame(self, frame: np.ndarray, auto: bool = False) -> None:
        if frame is None or frame.size == 0:
            return
        self._last_raw = frame
        # Auto is opt-in: the caller passes auto=True per live frame only after
        # the user presses Auto. Snaps (and live before Auto) hold the current
        # levels. The very first frame ever still gets a one-time baseline so
        # the view isn't blank.
        if auto or self._auto_lo is None:
            self._set_baseline_from(frame)
        view = self._apply_transform(frame)
        levels = self._effective_levels()
        self.image_item.setImage(view.T, autoLevels=False, autoRange=False, autoHistogramRange=False)
        # Keep the histogram axis pinned to full scale (setImage can nudge it).
        self.image_item.ui.histogram.item.setHistogramRange(0, self._full_scale, padding=0)
        if levels is not None:
            self.image_item.setLevels(levels[0], levels[1])

    def _install_aoi_drag(self) -> None:
        """Disable view panning (no value here) and repurpose left-drag to draw
        an AOI rubber-band that applies on release."""
        vb = self.image_item.getView()
        vb.setMouseEnabled(x=False, y=False)
        vb.setMenuEnabled(False)
        self._band = QtWidgets.QGraphicsRectItem()
        self._band.setPen(pg.mkPen("#22d3ee", width=1))
        self._band.setBrush(pg.mkBrush(34, 211, 238, 40))
        self._band.hide()
        vb.addItem(self._band)
        self._vb = vb
        vb.mouseDragEvent = self._on_vb_drag

    def _on_vb_drag(self, ev, axis=None) -> None:
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        p1 = self._vb.mapSceneToView(ev.buttonDownScenePos())
        p2 = self._vb.mapSceneToView(ev.scenePos())
        x0, y0, x1, y1 = p1.x(), p1.y(), p2.x(), p2.y()
        rect = QtCore.QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        self._band.setRect(rect)
        self._band.show()
        if ev.isFinish():
            self._band.hide()
            self._finish_selection(x0, y0, x1, y1)

    def _finish_selection(self, vx0, vy0, vx1, vy1) -> None:
        """Map a finished drag to a raw rect and emit aoiSelected, ignoring drags
        too small to be intentional."""
        if abs(vx1 - vx0) < self._MIN_DRAG_PX or abs(vy1 - vy0) < self._MIN_DRAG_PX:
            return
        raw = self.display_rect_to_raw(vx0, vy0, vx1, vy1)
        if raw is None:
            return
        r0, r1, c0, c1 = raw
        if r1 > r0 and c1 > c0:
            self.aoiSelected.emit(r0, r1, c0, c1)

    def display_rect_to_raw(self, vx0, vy0, vx1, vy1):
        """Map a rectangle in pyqtgraph view coords (where view-coord (vx,vy)
        indexes the *displayed* image at row=vy, col=vx) back to raw-frame
        row/col ranges, inverting the current flip+rotation. Returns
        (row0, row1, col0, col1) inclusive in raw-frame space, or None if there's
        no frame. Clamped to the raw frame bounds."""
        if self._last_raw is None:
            return None
        H, W = self._last_raw.shape
        r0, r1 = sorted((int(min(vy0, vy1)), int(max(vy0, vy1))))
        c0, c1 = sorted((int(min(vx0, vx1)), int(max(vx0, vx1))))
        # dims of the transformed (displayed) image
        Ht, Wt = (W, H) if self.state.rot in (90, 270) else (H, W)
        r0 = max(0, min(r0, Ht - 1)); r1 = max(0, min(r1, Ht - 1))
        c0 = max(0, min(c0, Wt - 1)); c1 = max(0, min(c1, Wt - 1))
        corners = [(r0, c0), (r0, c1), (r1, c0), (r1, c1)]
        raw = [self._inv_point(r, c, H, W) for (r, c) in corners]
        rows = [p[0] for p in raw]; cols = [p[1] for p in raw]
        return (min(rows), max(rows), min(cols), max(cols))

    def _inv_point(self, r, c, H, W):
        """Invert flip_h, flip_v, rot90(k) for a single (row, col) in display
        space → (row, col) in raw space. Forward order is flip_h, flip_v, rotk;
        invert in reverse: un-rot, un-flip_v, un-flip_h."""
        k = (self.state.rot // 90) % 4
        # un-rotate: np.rot90(a, k) maps a[i,j] -> out[...]; invert by rot90(-k).
        # Displayed (post-rot) point (r,c) in a Ht x Wt grid -> pre-rot (row,col)
        # in the flipped raw H x W grid.
        if k == 0:
            rr, cc = r, c
        elif k == 1:   # rot90 once (CCW): out[i,j] = flipped[j, W-1-i]
            rr, cc = c, (W - 1 - r)
        elif k == 2:   # 180
            rr, cc = (H - 1 - r), (W - 1 - c)
        else:          # k == 3 (270): out[i,j] = flipped[H-1-j, i]
            rr, cc = (H - 1 - c), r
        # un-flip
        if self.state.flip_v:
            rr = H - 1 - rr
        if self.state.flip_h:
            cc = W - 1 - cc
        return (rr, cc)

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
