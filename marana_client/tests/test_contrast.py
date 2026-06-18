"""Auto-contrast (best-fit) behaviour for MaranaImageView."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

from marana_client.ui.image_view import MaranaImageView


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _levels(iv):
    return tuple(float(x) for x in iv.image_item.getImageItem().levels)


def test_baseline_is_frame_min_max(app):
    iv = MaranaImageView()
    frame = np.full((16, 16), 200, dtype=np.uint16)
    frame[0, 0] = 100   # min
    frame[1, 1] = 9000  # max
    iv.update_frame(frame)
    assert iv._auto_lo == 100.0
    assert iv._auto_hi == 9000.0


def test_auto_applies_solis_bias(app):
    """Default auto = best-fit + Solis bias (black +11%, white +32% of span)."""
    iv = MaranaImageView()
    frame = np.zeros((8, 8), dtype=np.uint16)
    frame[7, 7] = 1000          # min=0, max=1000, span=1000
    iv.update_frame(frame)
    lo, hi = _levels(iv)
    assert lo == pytest.approx(0 + 0.11 * 1000)      # 110
    assert hi == pytest.approx(1000 + 0.32 * 1000)   # 1320


def test_levels_frozen_without_auto(app):
    """After the first frame establishes levels, later frames do NOT rescale
    unless auto=True (snap/live-without-auto hold the contrast)."""
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 200, dtype=np.uint16))   # first frame -> lazy init
    held = _levels(iv)
    frame2 = np.zeros((8, 8), dtype=np.uint16)
    frame2[7, 7] = 50000          # very different data
    iv.update_frame(frame2)        # auto defaults False
    assert _levels(iv) == held     # unchanged


def test_auto_true_rescales(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 200, dtype=np.uint16))
    frame2 = np.zeros((8, 8), dtype=np.uint16)
    frame2[7, 7] = 1000           # span 1000
    iv.update_frame(frame2, auto=True)
    assert _levels(iv) == pytest.approx((110.0, 1320.0))   # best-fit + bias of frame2


def test_histogram_axis_pinned_to_full_16bit(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 200, dtype=np.uint16))   # narrow data
    lo, hi = iv.image_item.ui.histogram.item.vb.viewRange()[1]
    assert (round(lo), round(hi)) == (0, 65535)


def test_offsets_add_to_bias(app):
    iv = MaranaImageView()
    frame = np.zeros((8, 8), dtype=np.uint16)
    frame[7, 7] = 1000          # span = 1000
    iv.update_frame(frame)
    iv.set_level_offsets(10, -20)   # +10% black, -20% white ON TOP of bias 11/32
    lo, hi = _levels(iv)
    assert lo == pytest.approx((0.11 + 0.10) * 1000)        # 210
    assert hi == pytest.approx(1000 + (0.32 - 0.20) * 1000)  # 1120
