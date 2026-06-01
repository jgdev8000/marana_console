"""Tests for the dependency-free sd_notify helper."""
import os
import socket

import pytest

from marana_server import sdnotify


def test_noop_without_notify_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.ready() is False
    assert sdnotify.watchdog() is False
    assert sdnotify.status("x") is False


def test_sends_to_unix_dgram_socket(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
    try:
        assert sdnotify.ready() is True
        assert srv.recv(64) == b"READY=1"
        assert sdnotify.watchdog() is True
        assert srv.recv(64) == b"WATCHDOG=1"
        assert sdnotify.status("acquiring") is True
        assert srv.recv(64) == b"STATUS=acquiring"
    finally:
        srv.close()


def test_send_failure_returns_false(monkeypatch, tmp_path):
    # NOTIFY_SOCKET points at a path with no listener -> connect/send fails cleanly.
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "does_not_exist.sock"))
    assert sdnotify.watchdog() is False
