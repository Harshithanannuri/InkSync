"""Length-limited newline-delimited JSON protocol over TCP."""
import json

MAX_MESSAGE = 2 * 1024 * 1024  # 2 MiB per framed message

class ProtocolError(Exception):
    pass

def encode(message: dict) -> bytes:
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE:
        raise ProtocolError("Message too large")
    return raw + b"\n"

def decode_line(line: bytes) -> dict:
    if not line or len(line) > MAX_MESSAGE:
        raise ProtocolError("Empty or oversized message")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Invalid JSON frame") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ProtocolError("Message must be an object with a string 'type'")
    return obj
