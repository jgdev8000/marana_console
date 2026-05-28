"""MaranaClient — thin ZeroMQ REQ + SUB wrapper for the GUI side."""
from __future__ import annotations

import logging
from typing import Any

import zmq

from marana_proto import messages as m
from marana_proto.errors import from_wire

log = logging.getLogger(__name__)


class ClientRequestTimeout(Exception):
    pass


class MaranaClient:
    def __init__(
        self,
        ctrl_endpoint: str,
        pub_endpoint: str,
        default_timeout_ms: int = 3000,
        zmq_ctx: zmq.Context | None = None,
    ):
        self._ctrl_ep = ctrl_endpoint
        self._pub_ep = pub_endpoint
        self._default_timeout_ms = default_timeout_ms
        self._ctx = zmq_ctx or zmq.Context.instance()
        self._req = self._open_req()
        self._sub = self._open_sub()

    def _open_req(self) -> zmq.Socket:
        s = self._ctx.socket(zmq.REQ)
        s.LINGER = 0
        s.RCVTIMEO = self._default_timeout_ms
        s.SNDTIMEO = self._default_timeout_ms
        s.connect(self._ctrl_ep)
        return s

    def _open_sub(self) -> zmq.Socket:
        s = self._ctx.socket(zmq.SUB)
        s.RCVHWM = 8
        s.setsockopt(zmq.SUBSCRIBE, b"")  # all topics
        s.connect(self._pub_ep)
        return s

    @property
    def sub_socket(self) -> zmq.Socket:
        return self._sub

    def close(self) -> None:
        try:
            self._req.close(linger=0)
        except Exception:
            pass
        try:
            self._sub.close(linger=0)
        except Exception:
            pass

    def request(self, cmd: str, args: dict | None = None, timeout_ms: int | None = None) -> dict:
        """Synchronous REQ. Raises ClientRequestTimeout on timeout (auto-reopens REQ socket)
        or the MaranaError subclass that the server returned in the error envelope.
        """
        if timeout_ms is not None:
            self._req.RCVTIMEO = timeout_ms
            self._req.SNDTIMEO = timeout_ms
        else:
            self._req.RCVTIMEO = self._default_timeout_ms
            self._req.SNDTIMEO = self._default_timeout_ms
        payload = m.encode(m.make_request(cmd, args or {}))
        try:
            self._req.send(payload)
            raw = self._req.recv()
        except zmq.Again as e:
            # REQ state machine is now stuck; close + reopen
            log.warning("REQ timeout cmd=%s, reopening socket", cmd)
            self._req.close(linger=0)
            self._req = self._open_req()
            raise ClientRequestTimeout(f"timeout on cmd {cmd}") from e
        reply = m.decode(raw)
        if not reply.get("ok"):
            err = reply.get("error", {})
            raise from_wire(err)
        return reply.get("result", {})
