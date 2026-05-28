import threading
import time

import pytest
import zmq

pytest.importorskip("PyQt6")
from PyQt6 import QtCore, QtWidgets

from marana_client.client import MaranaClient
from marana_client.worker import ClientWorker
from marana_proto import messages as m


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _rep_pub_server(ctrl_ep: str, pub_ep: str, stop_evt: threading.Event, frame_count: int = 3):
    ctx = zmq.Context.instance()
    rep = ctx.socket(zmq.REP); rep.bind(ctrl_ep)
    pub = ctx.socket(zmq.PUB); pub.bind(pub_ep)
    import numpy as np
    sent = 0
    # Brief pause so subscribers can connect before the first frame is sent
    time.sleep(0.4)
    try:
        while not stop_evt.is_set():
            if sent < frame_count:
                arr = (np.ones((4, 4), dtype="uint16") * sent)
                pub.send_multipart([
                    m.TOPIC_LIVE_FRAME,
                    m.encode({"seq": sent, "width": 4, "height": 4, "dtype": "uint16", "ts_iso": "2026-05-28T00:00:00+00:00"}),
                    arr.tobytes(),
                ])
                sent += 1
                time.sleep(0.05)  # spread frames so slow-joiner doesn't drop all
            if rep.poll(timeout=50):
                req = m.decode(rep.recv())
                rep.send(m.encode(m.make_reply_ok(req["id"], result={"ack": True})))
    finally:
        rep.close(linger=0); pub.close(linger=0)


def test_worker_emits_frame_signal(qapp):
    ctrl_ep = "tcp://127.0.0.1:15571"
    pub_ep = "tcp://127.0.0.1:15572"
    stop_evt = threading.Event()
    t = threading.Thread(target=_rep_pub_server, args=(ctrl_ep, pub_ep, stop_evt), daemon=True)
    t.start()
    time.sleep(0.2)

    client = MaranaClient(ctrl_endpoint=ctrl_ep, pub_endpoint=pub_ep, default_timeout_ms=1000)
    worker = ClientWorker(client)
    received = []

    def on_frame(topic, header, arr):
        received.append(int(arr[0, 0]))

    worker.frameReady.connect(on_frame)

    th = QtCore.QThread()
    worker.moveToThread(th)
    th.started.connect(worker.run)
    th.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(received) < 1:
        qapp.processEvents()
        time.sleep(0.05)

    worker.stop()
    th.quit(); th.wait(2000)
    client.close()
    stop_evt.set(); t.join(timeout=2.0)

    assert len(received) >= 1
