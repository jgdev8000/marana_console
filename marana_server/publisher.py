"""PUB thread: drains a queue and writes multipart messages to a ZMQ PUB socket."""
from __future__ import annotations

import logging
import queue
import threading

import zmq

log = logging.getLogger(__name__)


class Publisher(threading.Thread):
    def __init__(self, socket: zmq.Socket, outbound_queue: queue.Queue):
        super().__init__(name="Publisher", daemon=True)
        self._sock = socket
        self._q = outbound_queue
        self._stop_evt = threading.Event()

    def shutdown(self) -> None:
        self._stop_evt.set()
        # Wake the queue.get
        self._q.put(None)

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                # item is a list[bytes] (multipart)
                self._sock.send_multipart(item, flags=zmq.NOBLOCK)
            except zmq.Again:
                log.warning("publisher: SNDHWM hit, dropping message %r", item[0] if item else None)
            except Exception as e:
                log.exception("publisher send failed: %s", e)
        log.info("Publisher exiting")
