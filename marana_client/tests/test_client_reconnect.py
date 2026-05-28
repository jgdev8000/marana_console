import threading
import time

import pytest
import zmq

from marana_client.client import MaranaClient, ClientRequestTimeout


def _slow_rep_server(endpoint: str, delay_s: float, stop_evt: threading.Event):
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REP)
    sock.bind(endpoint)
    try:
        while not stop_evt.is_set():
            if sock.poll(timeout=200):
                _ = sock.recv()
                time.sleep(delay_s)
                if not stop_evt.is_set():
                    import msgpack
                    sock.send(msgpack.packb({"v": 1, "id": "x", "ok": True, "result": {"pong": True}}))
    finally:
        sock.close(linger=0)


def test_request_timeout_recovers():
    ep_ctrl = "inproc://test_recover_ctrl"
    ep_pub = "inproc://test_recover_pub"
    stop = threading.Event()
    t = threading.Thread(target=_slow_rep_server, args=(ep_ctrl, 2.0, stop), daemon=True)
    t.start()
    time.sleep(0.1)

    c = MaranaClient(ctrl_endpoint=ep_ctrl, pub_endpoint=ep_pub, default_timeout_ms=200, zmq_ctx=zmq.Context.instance())
    try:
        with pytest.raises(ClientRequestTimeout):
            c.request("ping", {})
    finally:
        c.close()
        stop.set()
        t.join(timeout=2.0)


def test_request_succeeds():
    ep_ctrl = "inproc://test_ok_ctrl"
    ep_pub = "inproc://test_ok_pub"
    stop = threading.Event()
    t = threading.Thread(target=_slow_rep_server, args=(ep_ctrl, 0.05, stop), daemon=True)
    t.start()
    time.sleep(0.1)

    c = MaranaClient(ctrl_endpoint=ep_ctrl, pub_endpoint=ep_pub, default_timeout_ms=2000, zmq_ctx=zmq.Context.instance())
    try:
        result = c.request("ping", {})
        assert result == {"pong": True}
    finally:
        c.close()
        stop.set()
        t.join(timeout=2.0)
