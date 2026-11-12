"""NDJSON protocol shared with the main cyberdeck.

The two boards talk by sending one UTF-8 JSON object per line, with
exactly three keys: type, id, and payload. This module is the only place
that should build or parse that JSON. It follows docs/PROTOCOL.md and
the main repo's cyberdeck/protocol.py so a line written here is a line
the main deck can decode, and vice versa.

Incoming bytes are appended to a buffer and split on newlines with
decode_buffer(); a trailing partial line is kept until more data arrives.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

# Protocol version the hello handshake advertises. Bump this if you
# make a breaking change to the message shape.
PROTO_VERSION = 1

# Names for every message type we currently understand. Unknown types
# are answered with an "error" reply rather than dropping the link.
TYPE_HELLO = "hello"
TYPE_HELLO_OK = "hello.ok"
TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_PET_STATUS = "pet.status"
TYPE_PET_STATUS_OK = "pet.status.ok"
TYPE_PET_FEED = "pet.feed"
TYPE_PET_FEED_OK = "pet.feed.ok"
TYPE_PET_PLAY = "pet.play"
TYPE_PET_PLAY_OK = "pet.play.ok"
TYPE_PET_GIFT = "pet.gift"
TYPE_PET_GIFT_OK = "pet.gift.ok"
TYPE_ERROR = "error"
# Text-face avatar. Optional request fields: frame (int), mood.
TYPE_PET_AVATAR = "pet.avatar"
TYPE_PET_AVATAR_OK = "pet.avatar.ok"
TYPE_PET_REST = "pet.rest"
TYPE_PET_REST_OK = "pet.rest.ok"
TYPE_PET_SLEEP = "pet.sleep"
TYPE_PET_SLEEP_OK = "pet.sleep.ok"
TYPE_PET_COMBAT_STATS = "pet.combat.stats"
TYPE_PET_COMBAT_STATS_OK = "pet.combat.stats.ok"
TYPE_PET_COMBAT_ACTION = "pet.combat.action"
TYPE_PET_COMBAT_ACTION_OK = "pet.combat.action.ok"
TYPE_PET_COMBAT_HIT = "pet.combat.hit"
TYPE_PET_COMBAT_HIT_OK = "pet.combat.hit.ok"
TYPE_PET_COMBAT_END = "pet.combat.end"
TYPE_PET_COMBAT_END_OK = "pet.combat.end.ok"
TYPE_PET_EVOLVE = "pet.evolve"
TYPE_PET_EVOLVE_OK = "pet.evolve.ok"
# Opt-in extension: unsolicited pushes when hello.payload.events is true.
# Only emitted after the main deck asks for them. detail is a dict.
TYPE_PET_EVENT = "pet.event"
EVENT_KINDS = (
    "hungry",
    "tired",
    "sad",
    "evolved",
    "level_up",
    "learned_move",
    "signal_found",
    "woke",
    "evolve_soon",
    "evolving",
    "evolve_choice",
)

# Every valid message must have these three keys.
REQUIRED_FIELDS = ("type", "id", "payload")


class ProtocolError(ValueError):
    """Raised when a line cannot be decoded as a protocol message."""


@dataclass(slots=True)
class Message:
    """One framed protocol message (type + id + payload)."""

    type: str
    id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        """Turn this message into one compact JSON line with a trailing newline."""
        if not self.type or not isinstance(self.type, str):
            raise ProtocolError("message type must be a non-empty string")
        if not self.id or not isinstance(self.id, str):
            raise ProtocolError("message id must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ProtocolError("message payload must be an object")
        body = {"type": self.type, "id": self.id, "payload": self.payload}
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False) + "\n"

    def encode_bytes(self) -> bytes:
        """Same as encode(), but as UTF-8 bytes for a socket or serial port."""
        return self.encode().encode("utf-8")

    def reply(self, typ: str, payload: dict[str, Any] | None = None) -> Message:
        """Build a response that keeps the same id, so the sender can match it."""
        return Message(type=typ, id=self.id, payload=payload or {})

    def error(self, code: str, message: str, **extra: Any) -> Message:
        """Build a standard error reply for this request."""
        payload: dict[str, Any] = {"code": code, "message": message}
        payload.update(extra)
        return Message(type=TYPE_ERROR, id=self.id, payload=payload)

    @classmethod
    def request(cls, typ: str, payload: dict[str, Any] | None = None) -> Message:
        """Create a new outgoing request with a fresh unique id."""
        return cls(type=typ, id=new_id(), payload=payload or {})


def new_id() -> str:
    """Return a new correlation id (32-character hex UUID)."""
    return uuid.uuid4().hex


def decode(line: str | bytes) -> Message:
    """Parse one line of text (or bytes) into a Message.

    A trailing newline or carriage return is ignored. Unknown extra
    top-level keys are ignored (forward compatible). Raises ProtocolError
    if the line is empty, not JSON, or missing required fields.
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"line is not valid UTF-8: {exc}") from exc
    raw = line.strip().strip("\x00")
    if not raw:
        raise ProtocolError("empty line")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("message must be a JSON object")
    missing = [key for key in REQUIRED_FIELDS if key not in data]
    if missing:
        raise ProtocolError(f"missing field(s): {', '.join(missing)}")
    typ = data["type"]
    mid = data["id"]
    payload = data["payload"]
    if not typ or not isinstance(typ, str):
        raise ProtocolError("type must be a non-empty string")
    if not mid or not isinstance(mid, str):
        raise ProtocolError("id must be a non-empty string")
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a JSON object")
    return Message(type=typ, id=mid, payload=payload)


def decode_buffer(buffer: str | bytes) -> tuple[list[Message], str | bytes]:
    """Pull complete messages out of a growing serial/socket buffer.

    Data often arrives in chunks, so the last part of the buffer may be
    an incomplete line. This returns the messages that ended with a
    newline, plus the leftover text to keep for next time. The remainder
    has the same type (str or bytes) as the input.

    Empty lines (and a lone carriage return) are ignored. A complete
    line that is not a valid message raises ProtocolError, matching the
    main deck's reference decoder. Transports that want to keep listening
    after a bad line should split first and call decode() per line.
    """
    if isinstance(buffer, bytes):
        parts = buffer.split(b"\n")
        complete, remainder = parts[:-1], parts[-1]
        messages = [decode(part) for part in complete if part.strip() and part.strip() != b"\r"]
        return messages, remainder
    parts = buffer.split("\n")
    complete, remainder = parts[:-1], parts[-1]
    messages = [decode(part) for part in complete if part.strip() and part.strip() != "\r"]
    return messages, remainder


def split_complete_lines(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte buffer on newlines, keeping the leftover partial line.

    Unlike decode_buffer(), this does not parse JSON, so a transport can
    decode each complete line on its own and skip a bad one.
    """
    parts = buffer.split(b"\n")
    return parts[:-1], parts[-1]


def try_decode_line(line: bytes | str) -> Message | None:
    """Decode one complete line, or return None if it is empty or invalid.

    Used by transports so a garbage line is discarded and the link stays up,
    as PROTOCOL.md recommends under Timeouts and retries.
    """
    if isinstance(line, bytes):
        stripped = line.strip()
        if not stripped or stripped == b"\r":
            return None
    else:
        stripped = line.strip()
        if not stripped or stripped == "\r":
            return None
    try:
        return decode(line)
    except ProtocolError:
        return None
