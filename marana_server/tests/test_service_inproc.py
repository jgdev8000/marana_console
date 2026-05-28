"""Tests MaranaService end-to-end over ZMQ's inproc:// transport with a mock camera."""
import threading
import time
from unittest.mock import MagicMock

import msgpack
import numpy as np
import pytest
import zmq

from marana_server.service import MaranaService
from marana_proto import messages as m


@pytest.fixture
def service():
    cam = MagicMock()
    cam.model = "SIMCAM CMOS"
    cam.serial = "SIM-001"
    cam.sensor_width = 64
    cam.sensor_height = 64
    cam.get_feature.return_value = 0.05
    cam.get_cooling.return_value = {
        "enabled": False, "target_c": 0.0, "sensor_temp_c": 20.0, "status": "Cooler Off",
    }

    ctx = zmq.Context.instance()
    svc = MaranaService(
        camera=cam, ctrl_endpoint="inproc://test_ctrl", pub_endpoint="inproc://test_pub",
        captures_dir="/tmp/marana_test_caps", sim=True, allow_shutdown=False, zmq_ctx=ctx,
    )
    svc.start()
    time.sleep(0.1)
    yield svc, ctx
    svc.shutdown()
    svc.join(timeout=3.0)


def _req(ctx: zmq.Context, endpoint: str, cmd: str, args: dict, timeout_ms: int = 2000):
    sock = ctx.socket(zmq.REQ)
    sock.RCVTIMEO = timeout_ms
    sock.connect(endpoint)
    sock.send(m.encode(m.make_request(cmd, args)))
    raw = sock.recv()
    sock.close()
    return m.decode(raw)


def test_hello_returns_camera_info(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "hello", {})
    assert reply["ok"] is True
    assert reply["result"]["camera_model"] == "SIMCAM CMOS"
    assert reply["result"]["sensor_w"] == 64


def test_get_feature(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "get_feature", {"name": "ExposureTime"})
    assert reply["ok"] is True
    assert reply["result"]["value"] == 0.05


def test_unknown_command_returns_err(service):
    svc, ctx = service
    reply = _req(ctx, "inproc://test_ctrl", "does_not_exist", {})
    assert reply["ok"] is False
    assert "unknown" in reply["error"]["message"].lower()
