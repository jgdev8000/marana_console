"""End-to-end test: real server subprocess + real client over TCP loopback."""
import os
import socket
import subprocess
import sys
import time

import pytest
import zmq

from marana_client.client import MaranaClient
from marana_proto import messages as m


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(port: int, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture
def server(tmp_path):
    ctrl = _free_port()
    pub = _free_port()
    captures = tmp_path / "caps"
    captures.mkdir()
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "marana_server",
         "--sim", "--bind", "127.0.0.1",
         "--ctrl-port", str(ctrl), "--frame-port", str(pub),
         "--captures-dir", str(captures), "--allow-shutdown"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for_port(ctrl, timeout_s=10.0), f"server did not open port {ctrl}"
        yield {"ctrl": ctrl, "pub": pub, "captures": captures, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_e2e_hello_live_snap_kinetic_save(server):
    c = MaranaClient(
        ctrl_endpoint=f"tcp://127.0.0.1:{server['ctrl']}",
        pub_endpoint=f"tcp://127.0.0.1:{server['pub']}",
        default_timeout_ms=10000,
    )
    try:
        # hello
        info = c.request("hello", {})
        assert info["sim"] is True
        # SimCam-compatible settings (AOI is not writable on sim; encoding must be Mono12)
        c.request("set_feature", {"name": "ExposureTime", "value": 0.005})
        c.request("set_feature", {"name": "PixelEncoding", "value": "Mono12"})

        # subscribe to live frames
        poller = zmq.Poller(); poller.register(c.sub_socket, zmq.POLLIN)
        c.request("start_live", {})
        frame_count = 0
        deadline = time.monotonic() + 15.0  # generous: full-frame sim takes ~1s/frame
        while time.monotonic() < deadline and frame_count < 2:
            socks = dict(poller.poll(timeout=200))
            if c.sub_socket in socks:
                parts = c.sub_socket.recv_multipart(flags=zmq.NOBLOCK)
                if parts and parts[0] == m.TOPIC_LIVE_FRAME:
                    frame_count += 1
        assert frame_count >= 1, "no live frames received"
        c.request("stop", {})

        # snap_single
        snap = c.request("snap_single", {"exposure_s": 0.005}, timeout_ms=15_000)
        assert "frame_bytes" in snap
        assert len(snap["frame_bytes"]) == snap["header"]["width"] * snap["header"]["height"] * 2

        # kinetic (small burst because sim AOI is full-frame)
        budget = c.request("start_kinetic", {"frame_count": 3, "exposure_s": 0.005, "frame_rate_hz": 5.0})
        assert "ram_estimate_bytes" in budget
        c.request("confirm_kinetic", {})

        # wait for completion event
        done = False
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not done:
            socks = dict(poller.poll(timeout=200))
            if c.sub_socket in socks:
                parts = c.sub_socket.recv_multipart(flags=zmq.NOBLOCK)
                if parts and parts[0] == m.TOPIC_KINETIC_COMPLETE:
                    done = True
        assert done, "kinetic_complete not received"

        # save stack
        res = c.request("save_kinetic_stack", {"path": "e2e.tif"}, timeout_ms=30_000)
        assert res["frames_written"] == 3
        assert (server["captures"] / "e2e.tif").exists()

        # list dir
        listing = c.request("list_kinetic_save_dir", {"subdir": ""})
        names = [e["name"] for e in listing["entries"]]
        assert "e2e.tif" in names
    finally:
        try:
            c.request("shutdown", {}, timeout_ms=2000)
        except Exception:
            pass
        c.close()
