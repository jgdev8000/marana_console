"""Wire protocol for the Marana console.

Two channels:
- REQ/REP commands (single msgpack-encoded map each way)
- PUB/SUB frames + status (multipart messages: [topic, header, payload?])
"""
from __future__ import annotations

import uuid
from typing import Any

import msgpack
import numpy as np

PROTOCOL_VERSION = 1

TOPIC_LIVE_FRAME = b"live_frame"
TOPIC_KINETIC_PROGRESS = b"kinetic_progress"
TOPIC_KINETIC_COMPLETE = b"kinetic_complete"
TOPIC_KINETIC_FRAME = b"kinetic_frame"
TOPIC_TEMPERATURE = b"temperature"
TOPIC_STATE = b"state"
TOPIC_ERROR = b"error"
TOPIC_FOCUS_PROGRESS = b"focus_progress"
TOPIC_FOCUS_COMPLETE = b"focus_complete"


def encode(obj: Any) -> bytes:
    return msgpack.packb(obj, use_bin_type=True)


def decode(buf: bytes) -> Any:
    return msgpack.unpackb(buf, raw=False)


def make_request(cmd: str, args: dict | None = None, request_id: str | None = None) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id or uuid.uuid4().hex[:12],
        "cmd": cmd,
        "args": args or {},
    }


def make_reply_ok(request_id: str, result: dict | None = None) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": result or {},
    }


def make_reply_err(request_id: str, error_type: str, message: str) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": {"type": error_type, "message": message},
    }


def make_frame(topic: bytes, header: dict, payload: bytes) -> list[bytes]:
    return [topic, encode(header), payload]


def make_status(topic: bytes, header: dict) -> list[bytes]:
    return [topic, encode(header)]


def decode_frame(parts: list[bytes]) -> tuple[bytes, dict, np.ndarray]:
    """Decode a [topic, header, payload] multipart frame into (topic, header, ndarray)."""
    if len(parts) != 3:
        raise ValueError(f"frame expected 3 parts, got {len(parts)}")
    topic, header_raw, payload = parts
    header = decode(header_raw)
    dtype = np.dtype(header["dtype"])
    arr = np.frombuffer(payload, dtype=dtype).reshape(header["height"], header["width"])
    return topic, header, arr


def decode_status(parts: list[bytes]) -> tuple[bytes, dict]:
    if len(parts) != 2:
        raise ValueError(f"status expected 2 parts, got {len(parts)}")
    return parts[0], decode(parts[1])
