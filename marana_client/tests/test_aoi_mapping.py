"""display_rect_to_raw must invert the display transform for every orientation.

Strategy: pick a raw sub-rectangle, mark it in a raw frame, run the real
_apply_transform, find the marked region's bounding box in display (view) coords,
feed that bbox to display_rect_to_raw, and assert we recover the original raw rect.
"""
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


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("flip_h", [False, True])
@pytest.mark.parametrize("flip_v", [False, True])
def test_roundtrip_all_orientations(app, rot, flip_h, flip_v):
    H, W = 20, 30
    iv = MaranaImageView()
    # raw sub-rect (inclusive)
    r0, r1, c0, c1 = 4, 11, 7, 18
    raw = np.zeros((H, W), dtype=np.uint16)
    raw[r0:r1 + 1, c0:c1 + 1] = 1
    iv.update_frame(raw)
    iv.set_rotation(rot)
    iv.set_flip(flip_h, flip_v)

    # The displayed array is view.T (what setImage receives); view-coord (vx,vy)
    # indexes the transformed image at row=vy, col=vx. Build that transformed
    # image with the same _apply_transform and find the marked bbox in view rows/cols.
    disp = iv._apply_transform(raw)          # transformed (rows=vy, cols=vx)
    ys, xs = np.where(disp == 1)
    vy0, vy1 = ys.min(), ys.max()
    vx0, vx1 = xs.min(), xs.max()

    got = iv.display_rect_to_raw(vx0, vy0, vx1, vy1)
    assert got == (r0, r1, c0, c1), f"rot={rot} fh={flip_h} fv={flip_v}: {got}"


def test_none_without_frame(app):
    iv = MaranaImageView()
    assert iv.display_rect_to_raw(0, 0, 5, 5) is None
