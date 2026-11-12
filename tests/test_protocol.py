"""tests/test_protocol.py — encode and decode of board-to-board messages.

These tests lock down the JSON-line format in cyberdeck/protocol.py
so a future companion implementation can rely on it. They do not
need a terminal or any hardware.
"""

from __future__ import annotations

import unittest

from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_PING,
    TYPE_PONG,
    Message,
    ProtocolError,
    decode,
    decode_buffer,
)


class MessageEncodeDecodeTests(unittest.TestCase):
    """Round-trips and the rules described in docs/PROTOCOL.md."""

    def test_roundtrip_preserves_fields(self):
        # encode() must produce one JSON line; decode() must get the same data back.
        original = Message(type=TYPE_PING, id="abc123", payload={"n": 1})
        encoded = original.encode()
        self.assertTrue(encoded.endswith("\n"))
        self.assertEqual(encoded.count("\n"), 1)
        restored = decode(encoded)
        self.assertEqual(restored.type, original.type)
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.payload, original.payload)

    def test_encode_bytes_is_utf8_of_encode(self):
        msg = Message.request(TYPE_PING, {"ok": True})
        self.assertEqual(msg.encode_bytes(), msg.encode().encode("utf-8"))

    def test_decode_accepts_crlf_and_surrounding_whitespace(self):
        line = '  {"type":"pong","id":"z","payload":{"ok":true}}\r\n'
        msg = decode(line)
        self.assertEqual(msg.type, TYPE_PONG)
        self.assertEqual(msg.id, "z")
        self.assertEqual(msg.payload, {"ok": True})

    def test_decode_bytes(self):
        raw = b'{"type":"ping","id":"1","payload":{}}\n'
        msg = decode(raw)
        self.assertEqual(msg.type, TYPE_PING)
        self.assertEqual(msg.payload, {})

    def test_reply_keeps_the_same_id(self):
        # Replies correlate to requests by copying the id.
        req = Message.request("pet.status", {})
        reply = req.reply("pet.status.ok", {"pet": {"name": "PULSE"}})
        self.assertEqual(reply.id, req.id)
        self.assertEqual(reply.type, "pet.status.ok")
        self.assertEqual(reply.payload["pet"]["name"], "PULSE")

    def test_error_reply_uses_error_type_and_same_id(self):
        req = Message.request("nope", {})
        err = req.error("unknown_type", "unsupported message type: nope")
        self.assertEqual(err.type, TYPE_ERROR)
        self.assertEqual(err.id, req.id)
        self.assertEqual(err.payload["code"], "unknown_type")

    def test_request_generates_unique_ids(self):
        ids = {Message.request(TYPE_PING).id for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_decode_rejects_empty_line(self):
        with self.assertRaises(ProtocolError):
            decode("   \n")

    def test_decode_rejects_invalid_json(self):
        with self.assertRaises(ProtocolError):
            decode("{not json")

    def test_decode_rejects_missing_fields(self):
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","id":"1"}')
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","payload":{}}')

    def test_decode_rejects_non_object_payload(self):
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","id":"1","payload":[]}')

    def test_decode_rejects_non_object_message(self):
        with self.assertRaises(ProtocolError):
            decode("[1, 2]")

    def test_decode_buffer_splits_complete_lines_and_keeps_remainder(self):
        # A stream may end mid-line; leftover text must be returned as-is.
        first = Message(type="ping", id="a", payload={}).encode()
        second_partial = '{"type":"pong","id":"b"'
        messages, rest = decode_buffer(first + second_partial)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "a")
        self.assertEqual(rest, second_partial)

    def test_decode_buffer_bytes_remainder_stays_bytes(self):
        blob = b'{"type":"ping","id":"a","payload":{}}\n{"type":"pong"'
        messages, rest = decode_buffer(blob)
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(rest, bytes)
        self.assertTrue(rest.startswith(b'{"type":"pong"'))


if __name__ == "__main__":
    unittest.main()
