"""Small versioned JSON-lines protocol for the local ASR process."""

import json
import os
import re
from pathlib import Path


DEFAULT_ASR_SOCKET = Path("/tmp") / f"edge-ai-asr-{os.getuid()}.sock"


MAX_MESSAGE_BYTES = 16_384
MAX_TRANSCRIPT_CHARS = 4_000
MESSAGE_TYPES = frozenset({
    "start_listening", "stop_listening", "status", "idle", "listening",
    "speech_detected", "recognizing", "transcript", "error",
})


class ProtocolError(ValueError):
    """An ASR control or event message is invalid."""


def validate_message(message):
    if not isinstance(message, dict) or type(message.get("v")) is not int or message["v"] != 1:
        raise ProtocolError("Invalid voice protocol version")
    if message.get("type") not in MESSAGE_TYPES:
        raise ProtocolError("Unknown voice message type")
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
        raise ProtocolError("Invalid voice request ID")
    if message["type"] == "transcript":
        value = message.get("text")
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TRANSCRIPT_CHARS:
            raise ProtocolError("Invalid transcript")
    if message["type"] == "start_listening":
        duration = message.get("max_listen_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 120:
            raise ProtocolError("Invalid listening duration")
    if message["type"] == "error":
        code = message.get("code")
        detail = message.get("message")
        if code != "no_speech" and (not isinstance(detail, str) or not detail.strip() or len(detail) > 256):
            raise ProtocolError("Invalid ASR error")
    if message["type"] == "status" and "state" in message and message["state"] not in ("idle", "listening", "recognizing"):
        raise ProtocolError("Invalid ASR state")
    return message


def encode_message(message) -> bytes:
    validate_message(message)
    payload = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Voice message too large")
    return payload


def decode_message(payload: bytes):
    if not isinstance(payload, bytes) or len(payload) > MAX_MESSAGE_BYTES or not payload.endswith(b"\n"):
        raise ProtocolError("Invalid voice message frame")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Invalid voice JSON") from exc
    return validate_message(value)
