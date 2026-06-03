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


# --- wedge stack dump -------------------------------------------------------

def _run_notifier(heartbeat_fn, **kw):
    dumps = []
    descs = []
    wn = watchdog.WatchdogNotifier(
        heartbeat_fn=heartbeat_fn,
        interval=0.02,
        stale_after=1000.0,                       # don't withhold; isolate dump logic
        dump_fn=lambda: dumps.append(1),
        describe_fn=lambda: (descs.append(1) or "state=KINETIC sdk=acq_stop"),
        **kw,
    )
    with patch("marana_server.watchdog.sdnotify.watchdog", lambda: None):
        wn.start()
        time.sleep(0.25)
        wn.stop(); wn.join(timeout=1.0)
    return dumps, descs


def test_notifier_dumps_stacks_on_wedge(caplog):
    stale_ts = time.monotonic() - 1000.0
    with caplog.at_level("ERROR"):
        dumps, descs = _run_notifier(lambda: stale_ts, dump_after=0.01)
    assert len(dumps) == 1            # dumped exactly once for the episode
    assert len(descs) >= 1            # included the activity description
    assert any("WEDGED" in r.message for r in caplog.records)


def test_notifier_no_dump_while_fresh():
    dumps, _ = _run_notifier(lambda: time.monotonic(), dump_after=0.01)
    assert dumps == []               # healthy heartbeat never dumps


def test_notifier_redumps_after_recovery():
    # Heartbeat is stale, then recovers (fresh), then wedges again -> 2 dumps.
    state = {"phase": "wedged1"}

    def hb():
        return time.monotonic() - (1000.0 if state["phase"].startswith("wedged") else 0.0)

    dumps = []
    wn = watchdog.WatchdogNotifier(
        heartbeat_fn=hb, interval=0.02, stale_after=1000.0,
        dump_after=0.01, dump_fn=lambda: dumps.append(1),
    )
    with patch("marana_server.watchdog.sdnotify.watchdog", lambda: None):
        wn.start()
        time.sleep(0.1)              # wedged1 -> 1 dump
        state["phase"] = "fresh"
        time.sleep(0.1)              # recovered -> re-arm
        state["phase"] = "wedged2"
        time.sleep(0.1)              # wedged2 -> 2nd dump
        wn.stop(); wn.join(timeout=1.0)
    assert len(dumps) == 2
