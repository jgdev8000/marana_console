"""Image display widget — pyqtgraph ImageView wrapped with display transforms +
a single-source-of-truth contrast model.

Contrast model:
- The black/white display levels (_lo/_hi, absolute pixel values) are the single
  source of truth, shared with the image's draggable histogram. Dragging the
  histogram, typing in the contrast boxes, and 'Auto' all set the same levels;
  levelsChanged keeps the numeric boxes in sync.
- Levels PERSIST across frames. update_frame(auto=True) recomputes them per frame
  (live, after the user presses Auto); otherwise they hold (snaps, manual drags).
- 'Auto' = best-fit (data min..max) + a fixed bias (AUTO_BLACK/WHITE_BIAS_PCT)
  approximating Andor Solis: black pushed up, white given headroom.
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
    # Black/white display levels changed (auto, typed, or histogram drag), as
    # absolute pixel values — the contrast panel mirrors these into its boxes.
    levelsChanged = QtCore.pyqtSignal(float, float)
    # The user dragged the histogram handles (vs a programmatic change).
    userEditedLevels = QtCore.pyqtSignal()

    _MIN_DRAG_PX = 4   # ignore tiny drags / clicks

    def __init__(self, parent=None):
        super().__init__(parent)
        # Black backdrop for the whole central area, incl. the empty grid corner.
        # The app stylesheet sets `QWidget { background:#0a1424 }`; an #id selector
        # is more specific, so this wins for this widget (palette would not).
        self.setObjectName("imageArea")
        self.setStyleSheet("QWidget#imageArea { background-color: #000; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._full_scale = 65535
        self.image_item = pg.ImageView()
        self.image_item.ui.roiBtn.hide()
        self.image_item.ui.menuBtn.hide()
        self.image_item.ui.histogram.hide()   # contrast lives on the side panel boxes + Auto
        self.image_item.getView().setBackgroundColor("k")
        self.image_item.ui.graphicsView.setBackground("k")   # letterbox area behind the image

        # Solis-style layout: vertical line profile (left), image (centre),
        # horizontal line profile (bottom), pixel readout strip (very bottom).
        self._build_profiles()
        self.pixel_label = QtWidgets.QLabel("")
        self.pixel_label.setStyleSheet(
            "color: #94a3b8; background-color: #000; font-family: monospace; padding: 2px 6px;")
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.vert_profile, 0, 0)
        grid.addWidget(self.image_item, 0, 1)
        grid.addWidget(self.horiz_profile, 1, 1)
        corner = QtWidgets.QWidget()   # explicit black filler so the empty cell isn't blue
        corner.setStyleSheet("background-color: #000;")
        grid.addWidget(corner, 1, 0)
        grid.addWidget(self.pixel_label, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        layout.addLayout(grid)

        self.state = DisplayState()
        self._install_aoi_drag()
        self._vb.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._last_raw: np.ndarray | None = None       # last frame, untransformed
        # Single source of truth: the actual black/white display levels (pixel values).
        self._lo: float | None = None
        self._hi: float | None = None

    def _build_profiles(self) -> None:
        """Compact line-profile panes: intensity vs pixel position for the cursor's
        row (horizontal, bottom) and column (vertical, left). Soft blue trace on
        black with dim axes — Andor Solis style, kept low-profile."""
        trace = pg.mkPen("#60a5fa", width=1)        # eye-friendly blue
        axis = pg.mkPen("#3b4a63")                  # dim axes, not pronounced

        def _style(pw):
            pw.setBackground("k")
            pw.setMouseEnabled(x=False, y=False)
            pw.hideButtons()
            pw.setMenuEnabled(False)
            for ax in ("left", "bottom"):
                a = pw.getAxis(ax)
                a.setPen(axis)
                a.setTextPen("#64748b")

        self.horiz_profile = pg.PlotWidget()
        self.horiz_profile.setFixedHeight(80)
        _style(self.horiz_profile)
        self.horiz_curve = self.horiz_profile.plot([], [], pen=trace)
        self.horiz_cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen("#475569", width=1))
        self.horiz_profile.addItem(self.horiz_cursor)

        self.vert_profile = pg.PlotWidget()
        self.vert_profile.setFixedWidth(80)
        _style(self.vert_profile)
        self.vert_profile.getPlotItem().invertY(True)   # row 0 at top, like the image
        self.vert_curve = self.vert_profile.plot([], [], pen=trace)   # x=intensity, y=row
        self.vert_cursor = pg.InfiniteLine(angle=0, pen=pg.mkPen("#475569", width=1))
        self.vert_profile.addItem(self.vert_cursor)

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

    def set_levels(self, lo: float, hi: float) -> None:
        """Set the black/white levels to absolute pixel values (from the numeric
        boxes). Persists — not overwritten by later frames unless auto is on."""
        self._lo, self._hi = float(lo), float(hi)
        if self._hi <= self._lo:
            self._hi = self._lo + 1.0
        self._apply_levels()

    def reset_auto(self) -> None:
        """The 'Auto' button: compute best-fit + Solis bias from the current
        frame and apply it as the levels."""
        if self._last_raw is None:
            return
        self._lo, self._hi = self._auto_levels(self._last_raw)
        self._apply_levels()

    def _auto_levels(self, frame: np.ndarray) -> tuple[float, float]:
        # Best-fit (data min..max) plus a fixed bias approximating Andor Solis:
        # raise black ~11% of span (push background to black) and white ~32%
        # (headroom above the peak so it isn't blown out).
        lo0 = float(frame.min())
        hi0 = float(frame.max())
        span = max(hi0 - lo0, 1.0)
        lo = lo0 + (AUTO_BLACK_BIAS_PCT / 100.0) * span
        hi = hi0 + (AUTO_WHITE_BIAS_PCT / 100.0) * span
        if hi <= lo:
            hi = lo + 1.0
        return (lo, hi)

    def _apply_levels(self) -> None:
        """Push _lo/_hi to the image and notify the contrast panel."""
        if self._lo is None or self._hi is None:
            return
        self.image_item.setLevels(self._lo, self._hi)
        self._set_profile_ranges()
        self.levelsChanged.emit(self._lo, self._hi)

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
        # Auto is opt-in: caller passes auto=True per live frame only after the
        # user presses Auto. Otherwise levels persist (snaps and manual drags
        # hold). The first frame ever gets a one-time auto so the view isn't blank.
        recompute = auto or self._lo is None
        if recompute:
            self._lo, self._hi = self._auto_levels(frame)
        view = self._apply_transform(frame)
        self.image_item.setImage(view.T, autoLevels=False, autoRange=False, autoHistogramRange=False)
        if recompute:
            self._apply_levels()
        elif self._lo is not None:
            # Re-assert current levels (setImage can reset them) without recomputing.
            self.image_item.setLevels(self._lo, self._hi)
            self._set_profile_ranges()

    def _on_mouse_moved(self, scene_pos) -> None:
        """Update the pixel readout and the row/column line profiles for the cursor."""
        if self._last_raw is None or not self._vb.sceneBoundingRect().contains(scene_pos):
            self.pixel_label.setText("")
            return
        p = self._vb.mapSceneToView(scene_pos)
        rc = self._raw_coords_at(int(p.y()), int(p.x()))
        if rc is None:
            self.pixel_label.setText("")
            return
        rr, cc = rc
        self.pixel_label.setText(f"x={cc}  y={rr}  value={int(self._last_raw[rr, cc])}")
        self._update_profiles(rr, cc)

    def _raw_coords_at(self, drow: int, dcol: int):
        """Map a displayed-image (row, col) back to raw (row, col), inverting
        flip/rotation. Returns None if outside the frame."""
        if self._last_raw is None:
            return None
        H, W = self._last_raw.shape
        Ht, Wt = (W, H) if self.state.rot in (90, 270) else (H, W)
        if not (0 <= drow < Ht and 0 <= dcol < Wt):
            return None
        rr, cc = self._inv_point(drow, dcol, H, W)
        if 0 <= rr < H and 0 <= cc < W:
            return (rr, cc)
        return None

    def _pixel_text_at(self, drow: int, dcol: int) -> str:
        rc = self._raw_coords_at(drow, dcol)
        if rc is None:
            return ""
        rr, cc = rc
        return f"x={cc}  y={rr}  value={int(self._last_raw[rr, cc])}"

    def _update_profiles(self, rr: int, cc: int) -> None:
        """Set the horizontal (row rr) and vertical (column cc) intensity profiles."""
        H, W = self._last_raw.shape
        self.horiz_curve.setData(np.arange(W), self._last_raw[rr, :])
        self.horiz_cursor.setValue(cc)
        self.vert_curve.setData(self._last_raw[:, cc], np.arange(H))   # x=intensity, y=row
        self.vert_cursor.setValue(rr)

    def _set_profile_ranges(self) -> None:
        """Fix the profile axes: position to the frame size, intensity to the
        current black/white window — so a dark, low-variance region reads as a
        flat line near the bottom instead of an auto-scaled noisy trace."""
        if self._last_raw is None or self._lo is None:
            return
        H, W = self._last_raw.shape
        lo, hi = self._lo, self._hi
        self.horiz_profile.setXRange(0, W, padding=0)
        self.horiz_profile.setYRange(lo, hi, padding=0)
        self.vert_profile.setYRange(0, H, padding=0)
        self.vert_profile.setXRange(lo, hi, padding=0)

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
