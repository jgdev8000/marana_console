"""ClientWorker — QObject that consumes SUB frames and emits Qt signals."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import zmq
from PyQt6 import QtCore

from marana_client.client import MaranaClient
from marana_proto import messages as m

log = logging.getLogger(__name__)


class ClientWorker(QtCore.QObject):
    frameReady = QtCore.pyqtSignal(bytes, dict, object)        # topic, header, numpy array
    statusEvent = QtCore.pyqtSignal(bytes, dict)               # topic, header
    error = QtCore.pyqtSignal(str, str)                         # severity, message

    def __init__(self, client: MaranaClient):
        super().__init__()
        self._client = client
        self._sub = client.sub_socket
        self._running = False

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._running = True
        poller = zmq.Poller()
        poller.register(self._sub, zmq.POLLIN)
        while self._running:
            try:
                socks = dict(poller.poll(timeout=200))
            except zmq.ZMQError as e:
                log.warning("poll error: %s", e)
                continue
            if self._sub in socks:
                try:
                    parts = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                except zmq.Again:
                    continue
                self._handle_parts(parts)

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._running = False

    def _handle_parts(self, parts: list[bytes]) -> None:
        if not parts:
            return
        topic = parts[0]
        try:
            if len(parts) == 3:
                # Frame
                header = m.decode(parts[1])
                dtype = np.dtype(header.get("dtype", "uint16"))
                arr = np.frombuffer(parts[2], dtype=dtype).reshape(header["height"], header["width"])
                self.frameReady.emit(topic, header, arr)
            else:
                header = m.decode(parts[1])
                if topic == m.TOPIC_ERROR:
                    sev = header.get("severity", "error")
                    msg = header.get("message", str(header))
                    self.error.emit(sev, msg)
                else:
                    self.statusEvent.emit(topic, header)
        except Exception as e:
            log.exception("failed to handle parts topic=%r", parts[0])
            self.error.emit("error", f"frame decode failed: {e}")
