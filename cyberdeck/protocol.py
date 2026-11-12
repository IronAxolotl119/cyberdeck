"""protocol.py — the shared language between the two boards.

This file turns messages into text and back again. The main cyberdeck
and the companion pet board talk by sending one JSON object per line
(a type, an id, and a payload). Nothing else in the app should build
or parse that JSON by hand — it all goes through Message and decode().

Why this is separate: the companion project can copy docs/PROTOCOL.md
and implement the same rules. If you add a new message type, list it
here and in that document so both sides stay in sync.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

# Protocol version the hello handshake advertises. Bump this if you
# make a breaking change to the message shape.
PROTO_VERSION = 1

# Names for every message type we currently understand. The companion
# should reply with an "error" message if it sees a type it does not
# know, rather than dropping the link.
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
# Optional: the pet's animated text face (or, from older companions, a
# 16x16 picture as palette indexes). See PROTOCOL.md.
TYPE_PET_AVATAR = "pet.avatar"
TYPE_PET_AVATAR_OK = "pet.avatar.ok"
# Rest and sleep: refill PULSE's energy from the deck.
TYPE_PET_REST = "pet.rest"
TYPE_PET_REST_OK = "pet.rest.ok"
TYPE_PET_SLEEP = "pet.sleep"
TYPE_PET_SLEEP_OK = "pet.sleep.ok"
# Pick the next form when the pet is ready to evolve (see evolution.choices).
TYPE_PET_EVOLVE = "pet.evolve"
TYPE_PET_EVOLVE_OK = "pet.evolve.ok"
# Dungeon combat: PULSE fights next to you; the pet decides every outcome.
TYPE_PET_COMBAT_STATS = "pet.combat.stats"
TYPE_PET_COMBAT_STATS_OK = "pet.combat.stats.ok"
TYPE_PET_COMBAT_ACTION = "pet.combat.action"
TYPE_PET_COMBAT_ACTION_OK = "pet.combat.action.ok"
TYPE_PET_COMBAT_HIT = "pet.combat.hit"
TYPE_PET_COMBAT_HIT_OK = "pet.combat.hit.ok"
TYPE_PET_COMBAT_HEAL = "pet.combat.heal"          # deck-side proposal (see PROTOCOL.md)
TYPE_PET_COMBAT_HEAL_OK = "pet.combat.heal.ok"
TYPE_PET_COMBAT_END = "pet.combat.end"
TYPE_PET_COMBAT_END_OK = "pet.combat.end.ok"
TYPE_ERROR = "error"

# Optional push message from the companion (never answered). Only sent
# after a hello that asked for {"events": true} and was echoed back.
TYPE_PET_EVENT = "pet.event"
EVENT_KINDS = ("hungry", "tired", "sad", "evolve_soon", "evolve_choice", "evolving", "evolved", "level_up", "learned_move",
               "signal_found", "woke")

# The evolution ladder, lowest to highest.
STAGES = ("egg", "baby", "in-training", "rookie", "champion", "ultimate", "mega")

# Every valid message must have these three keys.
REQUIRED_FIELDS = ("type", "id", "payload")


# Raised when a line of text is not a valid protocol message.
# Callers can catch this and skip or log the bad line.
class ProtocolError(ValueError):
    """Raised when a line cannot be decoded as a protocol message."""


# A Message is one complete packet: what kind it is, a unique id so a
# reply can be matched to the request, and a payload dict of extra data.
@dataclass(slots=True)
class Message:
    """One framed protocol message (type + id + payload)."""

    type: str
    id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        """Turn this message into one line of JSON with a trailing newline."""
        if not self.type or not isinstance(self.type, str):
            raise ProtocolError("message type must be a non-empty string")
        if not self.id or not isinstance(self.id, str):
            raise ProtocolError("message id must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ProtocolError("message payload must be an object")
        body = {"type": self.type, "id": self.id, "payload": self.payload}
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False) + "\n"

    def encode_bytes(self) -> bytes:
        """Same as encode(), but as UTF-8 bytes for writing to a serial port."""
        return self.encode().encode("utf-8")

    def reply(self, typ: str, payload: dict[str, Any] | None = None) -> Message:
        """Build a response that keeps the same id, so the sender can match it."""
        return Message(type=typ, id=self.id, payload=payload or {})

    def error(self, code: str, message: str, **extra: Any) -> Message:
        """Build a standard error reply for this request."""
        payload = {"code": code, "message": message}
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

    A trailing newline or carriage return is ignored. Raises
    ProtocolError if the line is empty, not JSON, or missing fields.
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
