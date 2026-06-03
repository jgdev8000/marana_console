"""Watchdog notifier + usbfs startup check.

WatchdogNotifier pings systemd's watchdog (WATCHDOG=1) on an interval, but only
while the camera worker's run-loop heartbeat is fresh. If the worker wedges, it
stops pinging and systemd's WatchdogSec elapses -> the service is restarted.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from marana_server import sdnotify

log = logging.getLogger(__name__)

USBFS_PATH = "/sys/module/usbcore/parameters/usbfs_memory_mb"


def warn_if_low_usbfs(min_mb: int = 256, path: str = USBFS_PATH) -> int | None:
    """Log a prominent warning if usbfs_memory_mb is below min_mb. Returns the
    value read (or None if unreadable). Does not refuse to start."""
    try:
        with open(path) as f:
            value = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if value < min_mb:
        log.warning(
            "usbfs_memory_mb=%d is low (< %d). Marana USB3 transfers may stall "
            "and hang acquisition. Set it to 1000 — see README 'USB buffer memory'.",
            value, min_mb,
        )
    return value


def _default_dump() -> None:
    """Dump every thread's Python stack to stderr -> journald. Because the SDK
    is cffi (the C call holds the GIL right up to its line), the wedged thread's
    stack pinpoints the exact file:line of the hung SDK call."""
    import faulthandler
    import sys
    faulthandler.dump_traceback(all_threads=True, file=sys.stderr)


class WatchdogNotifier(threading.Thread):
    """Pings systemd WATCHDOG=1 every `interval` s while the worker heartbeat is
    fresher than `stale_after` s. Withholds the ping when stale so systemd
    restarts the wedged server. No-op (harmless) when not under systemd.

    When the heartbeat first crosses `dump_after` (well before `stale_after`, so
    we capture the evidence *before* systemd kills the process), it logs a one-
    line activity summary (`describe_fn`) and dumps all thread stacks (`dump_fn`)
    so a future wedge is self-explanatory in the journal. The dump fires once per
    wedge episode and re-arms once the heartbeat recovers."""

    def __init__(self, heartbeat_fn: Callable[[], float],
                 interval: float = 10.0, stale_after: float = 75.0,
                 dump_after: float = 15.0,
                 describe_fn: Callable[[], str] | None = None,
                 dump_fn: Callable[[], None] | None = None):
        super().__init__(name="WatchdogNotifier", daemon=True)
        self._heartbeat_fn = heartbeat_fn
        self._interval = interval
        self._stale_after = stale_after
        self._dump_after = dump_after
        self._describe_fn = describe_fn
        self._dump_fn = dump_fn or _default_dump
        self._dumped = False   # once-per-episode latch
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def _emit_dump(self, age: float) -> None:
        desc = ""
        if self._describe_fn is not None:
            try:
                desc = self._describe_fn()
            except Exception as e:  # never let diagnostics crash the watchdog
                desc = f"<describe failed: {e}>"
        log.error("WORKER WEDGED: heartbeat stale %.0fs [%s] — dumping all thread "
                  "stacks", age, desc)
        try:
            self._dump_fn()
        except Exception as e:
            log.error("thread stack dump failed: %s", e)

    def run(self) -> None:
        while not self._stop_evt.wait(self._interval):
            age = time.monotonic() - self._heartbeat_fn()
            if age < self._dump_after:
                self._dumped = False           # fresh enough -> re-arm dump
            elif not self._dumped:
                self._dumped = True
                self._emit_dump(age)
            if age < self._stale_after:
                sdnotify.watchdog()
            else:
                log.error("worker heartbeat stale (%.0f s) — withholding watchdog "
                          "ping; systemd will restart the server", age)
