import msgpack
import numpy as np
import pytest

from marana_proto import messages as m


def test_make_request_round_trip():
    req = m.make_request("set_feature", {"name": "ExposureTime", "value": 0.05})
    raw = m.encode(req)
    assert isinstance(raw, bytes)
    decoded = m.decode(raw)
    assert decoded["v"] == 1
    assert decoded["cmd"] == "set_feature"
    assert decoded["args"]["name"] == "ExposureTime"
    assert decoded["args"]["value"] == 0.05
    assert isinstance(decoded["id"], str)
    assert len(decoded["id"]) >= 8


def test_make_reply_ok_round_trip():
    rep = m.make_reply_ok(request_id="abc", result={"applied_value": 0.05})
    raw = m.encode(rep)
    decoded = m.decode(raw)
    assert decoded == {
        "v": 1, "id": "abc", "ok": True,
        "result": {"applied_value": 0.05},
    }


def test_make_reply_err_round_trip():
    rep = m.make_reply_err(request_id="abc", error_type="FeatureNotWritable", message="boom")
    raw = m.encode(rep)
    decoded = m.decode(raw)
    assert decoded["ok"] is False
    assert decoded["error"] == {"type": "FeatureNotWritable", "message": "boom"}


def test_frame_envelope_packs_three_parts():
    arr = np.arange(2048 * 2048, dtype=np.uint16).reshape(2048, 2048)
    header = {"seq": 42, "width": 2048, "height": 2048, "dtype": "uint16"}
    parts = m.make_frame(m.TOPIC_LIVE_FRAME, header, arr.tobytes())
    assert isinstance(parts, list) and len(parts) == 3
    assert parts[0] == m.TOPIC_LIVE_FRAME
    decoded_header = m.decode(parts[1])
    assert decoded_header["seq"] == 42
    assert decoded_header["width"] == 2048
    assert len(parts[2]) == 2048 * 2048 * 2


def test_decode_frame_returns_ndarray():
    arr = np.arange(64 * 32, dtype=np.uint16).reshape(32, 64)
    header = {"width": 64, "height": 32, "dtype": "uint16"}
    parts = m.make_frame(m.TOPIC_LIVE_FRAME, header, arr.tobytes())
    topic, decoded_header, ndarr = m.decode_frame(parts)
    assert topic == m.TOPIC_LIVE_FRAME
    assert decoded_header["width"] == 64
    assert ndarr.shape == (32, 64)
    assert ndarr.dtype == np.uint16
    assert np.array_equal(ndarr, arr)


def test_status_envelope_no_payload():
    parts = m.make_status(m.TOPIC_TEMPERATURE, {"sensor_temp_c": -44.8})
    assert len(parts) == 2
    assert parts[0] == m.TOPIC_TEMPERATURE
    assert m.decode(parts[1])["sensor_temp_c"] == -44.8


def test_topic_constants_are_bytes():
    for name in ("TOPIC_LIVE_FRAME", "TOPIC_KINETIC_PROGRESS",
                "TOPIC_KINETIC_COMPLETE", "TOPIC_KINETIC_FRAME",
                "TOPIC_TEMPERATURE", "TOPIC_STATE", "TOPIC_ERROR"):
        v = getattr(m, name)
        assert isinstance(v, bytes), f"{name} is not bytes"
