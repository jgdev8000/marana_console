"""Tests for three client UI fixes:
  1. ConnectionCard recovers from DEGRADED back to HEALTHY (mark_healthy).
  2. The kinetic scrubber shows only on the kinetic tab AND when frames exist.
  3. QuickAcquireDialog collects exposure / gain / frame-count for ACQUIRE & SAVE.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

from marana_client.ui.connection_card import ConnectionCard
from marana_client.ui.main_window import MainWindow
from marana_client.ui.live_panel import LivePanel
from marana_client.ui.kinetic_panel import KineticPanel
from marana_client.ui.focus_panel import FocusPanel
from marana_client.ui.quick_acquire_dialog import QuickAcquireDialog


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- 1. connection recovery -------------------------------------------------

def test_mark_healthy_recovers_from_degraded(app):
    c = ConnectionCard()
    c.set_state(ConnectionCard.STATE_DEGRADED)
    assert c.current_state == ConnectionCard.STATE_DEGRADED
    c.mark_healthy()
    assert c.current_state == ConnectionCard.STATE_HEALTHY


def test_mark_healthy_idempotent_when_already_healthy(app):
    c = ConnectionCard()
    c.set_state(ConnectionCard.STATE_HEALTHY)
    c.mark_healthy()
    assert c.current_state == ConnectionCard.STATE_HEALTHY


# --- 2. scrubber visibility -------------------------------------------------

def _make_window(app):
    win = MainWindow(host="testhost")
    win.install_left_panels(LivePanel(), KineticPanel(), FocusPanel())
    return win


def test_scrubber_hidden_without_frames(app):
    win = _make_window(app)
    win.left_tabs.setCurrentWidget(win.kinetic_tab)
    win.set_scrubber_available(False)
    assert not win.scrubber_strip.isVisibleTo(win)


def test_scrubber_hidden_on_live_tab_even_with_frames(app):
    win = _make_window(app)
    win.set_scrubber_available(True)
    win.left_tabs.setCurrentWidget(win.live_tab)
    assert not win.scrubber_strip.isVisibleTo(win)


def test_scrubber_shown_on_kinetic_tab_with_frames(app):
    win = _make_window(app)
    win.set_scrubber_available(True)
    win.left_tabs.setCurrentWidget(win.kinetic_tab)
    assert win.scrubber_strip.isVisibleTo(win)


def test_scrubber_follows_tab_switches(app):
    win = _make_window(app)
    win.set_scrubber_available(True)
    win.left_tabs.setCurrentWidget(win.kinetic_tab)
    assert win.scrubber_strip.isVisibleTo(win)
    win.left_tabs.setCurrentWidget(win.live_tab)      # leaving kinetic hides it
    assert not win.scrubber_strip.isVisibleTo(win)
    win.left_tabs.setCurrentWidget(win.kinetic_tab)   # returning shows it again
    assert win.scrubber_strip.isVisibleTo(win)


# --- 3. quick-acquire dialog ------------------------------------------------

def test_quick_acquire_values_roundtrip(app):
    d = QuickAcquireDialog(exposure_s=0.02,
                           gain_options=["12-bit (low noise)", "16-bit (high well cap)"],
                           current_gain="16-bit (high well cap)")
    d.exposure_spin.setValue(0.05)
    d.frames_spin.setValue(10)
    v = d.values()
    assert abs(v["exposure_s"] - 0.05) < 1e-9
    assert v["frame_count"] == 10
    assert v["gain"] == "16-bit (high well cap)"


def test_quick_acquire_defaults_to_current(app):
    d = QuickAcquireDialog(exposure_s=0.123, gain_options=["A", "B"], current_gain="B")
    v = d.values()
    assert abs(v["exposure_s"] - 0.123) < 1e-9
    assert v["gain"] == "B"
    assert v["frame_count"] == 1          # default single frame


def test_quick_acquire_gain_omitted_when_unavailable(app):
    d = QuickAcquireDialog(exposure_s=0.02, gain_options=[], current_gain=None)
    assert d.gain_combo.count() == 0
    assert d.values()["gain"] is None
