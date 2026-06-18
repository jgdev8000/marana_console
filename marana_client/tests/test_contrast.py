"""Auto-contrast + level-control behaviour for MaranaImageView."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

from marana_client.ui.image_view import (
    MaranaImageView, AUTO_BLACK_BIAS_PCT, AUTO_WHITE_BIAS_PCT,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _levels(iv):
    return tuple(float(x) for x in iv.image_item.getImageItem().levels)


def _auto(lo0, hi0):
    span = max(hi0 - lo0, 1.0)
    return (lo0 + AUTO_BLACK_BIAS_PCT / 100.0 * span,
            hi0 + AUTO_WHITE_BIAS_PCT / 100.0 * span)


def test_auto_applies_best_fit_plus_bias(app):
    iv = MaranaImageView()
    frame = np.zeros((8, 8), dtype=np.uint16)
    frame[7, 7] = 1000          # min=0, max=1000
    iv.update_frame(frame)      # first frame -> one-time auto
    assert _levels(iv) == pytest.approx(_auto(0, 1000))


def test_levels_frozen_without_auto(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 200, dtype=np.uint16))   # establishes levels
    held = _levels(iv)
    frame2 = np.zeros((8, 8), dtype=np.uint16)
    frame2[7, 7] = 50000          # very different data
    iv.update_frame(frame2)        # auto defaults False -> no rescale
    assert _levels(iv) == held


def test_auto_true_rescales(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 200, dtype=np.uint16))
    frame2 = np.zeros((8, 8), dtype=np.uint16)
    frame2[7, 7] = 1000
    iv.update_frame(frame2, auto=True)
    assert _levels(iv) == pytest.approx(_auto(0, 1000))


def test_set_levels_persists_and_emits(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 100, dtype=np.uint16))
    seen = []
    iv.levelsChanged.connect(lambda lo, hi: seen.append((lo, hi)))
    iv.set_levels(300, 9000)
    assert _levels(iv) == (300.0, 9000.0)
    assert seen[-1] == (300.0, 9000.0)
    # A subsequent non-auto frame keeps the typed levels (no stomp).
    iv.update_frame(np.full((8, 8), 7, dtype=np.uint16))
    assert _levels(iv) == (300.0, 9000.0)


def test_line_profiles_follow_cursor(app):
    iv = MaranaImageView()
    f = np.arange(20 * 30, dtype=np.uint16).reshape(20, 30)   # value = row*30 + col
    iv.update_frame(f)
    iv._update_profiles(5, 10)   # raw row 5, col 10
    xh, yh = iv.horiz_curve.getData()
    xv, yv = iv.vert_curve.getData()
    # horizontal profile = intensity across row 5 (x = column index)
    assert list(xh[:3]) == [0, 1, 2]
    assert list(int(v) for v in yh[:3]) == [150, 151, 152]
    # vertical profile = intensity down column 10 (y = row index, x = intensity)
    assert list(yv[:3]) == [0, 1, 2]
    assert list(int(v) for v in xv[:3]) == [10, 40, 70]
    assert iv.horiz_cursor.value() == 10 and iv.vert_cursor.value() == 5


def test_pixel_readout_reports_raw_xy_and_value(app):
    iv = MaranaImageView()
    f = np.arange(20 * 30, dtype=np.uint16).reshape(20, 30)  # value = row*30 + col
    iv.update_frame(f)
    # rot=0, no flip: displayed (row,col) == raw (row,col)
    assert iv._pixel_text_at(5, 10) == "x=10  y=5  value=160"
    # out of bounds -> empty
    assert iv._pixel_text_at(100, 100) == ""


def test_pixel_readout_inverts_flip(app):
    iv = MaranaImageView()
    f = np.arange(20 * 30, dtype=np.uint16).reshape(20, 30)
    iv.update_frame(f)
    iv.set_flip(h=True, v=False)   # horizontal flip
    # displayed col 0 maps to raw col W-1=29 at row 5 -> value 5*30+29=179
    assert iv._pixel_text_at(5, 0) == "x=29  y=5  value=179"


def test_sweetspot_places_crosshair_on_click(app):
    from PyQt6 import QtCore
    iv = MaranaImageView()
    iv.update_frame(np.zeros((10, 10), dtype=np.uint16))
    assert iv._sweetspot_marker is None
    iv.set_sweetspot_mode()
    assert iv._sweetspot_mode is True

    class _Ev:
        def __init__(self, sp): self._sp = sp
        def button(self): return QtCore.Qt.MouseButton.LeftButton
        def scenePos(self): return self._sp
        def accept(self): pass

    sp = iv._vb.mapViewToScene(QtCore.QPointF(4.0, 6.0))
    iv._on_mouse_clicked(_Ev(sp))
    assert iv._sweetspot_marker is not None      # placed and persists
    assert iv._sweetspot_mode is False           # disarmed after placing
    xs, ys = iv._sweetspot_marker.getData()
    assert xs[0] == pytest.approx(4.0) and ys[0] == pytest.approx(6.0)
