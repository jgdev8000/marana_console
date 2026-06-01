"""Tests for the usbfs check and the watchdog notifier gating."""
import time
from unittest.mock import patch

from marana_server import watchdog


def test_warn_if_low_usbfs_warns_below(tmp_path, caplog):
    p = tmp_path / "usbfs"
    p.write_text("16\n")
    with caplog.at_level("WARNING"):
        v = watchdog.warn_if_low_usbfs(min_mb=256, path=str(p))
    assert v == 16
    assert any("usbfs_memory_mb=16" in r.message for r in caplog.records)


def test_warn_if_low_usbfs_silent_at_or_above(tmp_path, caplog):
    p = tmp_path / "usbfs"
    p.write_text("1000\n")
    with caplog.at_level("WARNING"):
        v = watchdog.warn_if_low_usbfs(min_mb=256, path=str(p))
    assert v == 1000
    assert not any("usbfs_memory_mb" in r.message for r in caplog.records)


def test_warn_if_low_usbfs_missing_path(tmp_path):
    assert watchdog.warn_if_low_usbfs(path=str(tmp_path / "nope")) is None


def test_notifier_pings_while_fresh():
    sends = []
    now = time.monotonic()
    with patch("marana_server.watchdog.sdnotify.watchdog", lambda: sends.append(1)):
        wn = watchdog.WatchdogNotifier(heartbeat_fn=lambda: time.monotonic(),
                                       interval=0.05, stale_after=10.0)
        wn.start()
        time.sleep(0.2)
        wn.stop(); wn.join(timeout=1.0)
    assert len(sends) >= 1   # pinged while heartbeat fresh


def test_notifier_withholds_when_stale():
    sends = []
    stale_ts = time.monotonic() - 1000.0   # very old heartbeat
    with patch("marana_server.watchdog.sdnotify.watchdog", lambda: sends.append(1)):
        wn = watchdog.WatchdogNotifier(heartbeat_fn=lambda: stale_ts,
                                       interval=0.05, stale_after=10.0)
        wn.start()
        time.sleep(0.2)
        wn.stop(); wn.join(timeout=1.0)
    assert sends == []       # never pinged because heartbeat is stale
