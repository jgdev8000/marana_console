"""Dependency-free sd_notify — talk to systemd's watchdog/readiness protocol.

When the process is not run under systemd ($NOTIFY_SOCKET unset), every call is
a no-op, so manual/dev launches behave exactly as before.
"""
from __future__ import annotations

import os
import socket


def _send(msg: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False  # not under systemd -> no-op
    if addr[0] == "@":  # abstract namespace socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
        return True
    except OSError:
        return False


def ready() -> bool:
    return _send("READY=1")


def watchdog() -> bool:
    return _send("WATCHDOG=1")


def status(text: str) -> bool:
    return _send(f"STATUS={text}")
