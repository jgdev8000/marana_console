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


def test_best_fit_uses_frame_min_max(app):
    iv = MaranaImageView()
    frame = np.full((16, 16), 200, dtype=np.uint16)
    frame[0, 0] = 100   # min
    frame[1, 1] = 9000  # max
    iv.update_frame(frame)
    assert _levels(iv) == (100.0, 9000.0)


def test_best_fit_rescales_every_frame(app):
    iv = MaranaImageView()
    iv.update_frame(np.full((8, 8), 500, dtype=np.uint16) + np.arange(64, dtype=np.uint16).reshape(8, 8))
    first = _levels(iv)
    # A brighter frame must move the white point up (per-frame rescale, not held).
    frame2 = np.full((8, 8), 1000, dtype=np.uint16)
    frame2[0, 0] = 50
    frame2[7, 7] = 40000
    iv.update_frame(frame2)
    assert _levels(iv) == (50.0, 40000.0)
    assert _levels(iv) != first


def test_offsets_apply_on_top_of_best_fit(app):
    iv = MaranaImageView()
    frame = np.zeros((8, 8), dtype=np.uint16)
    frame[0, 0] = 0
    frame[7, 7] = 1000          # span = 1000
    iv.update_frame(frame)
    assert _levels(iv) == (0.0, 1000.0)
    iv.set_level_offsets(10, -20)   # +10% black, -20% white of span(1000)
    lo, hi = _levels(iv)
    assert lo == pytest.approx(100.0)   # 0 + 0.10*1000
    assert hi == pytest.approx(800.0)   # 1000 - 0.20*1000
