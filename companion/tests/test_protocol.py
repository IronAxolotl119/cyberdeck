"""Encode, decode, and partial-buffer tests for the board-to-board protocol."""

from __future__ import annotations

import unittest

from link.protocol import (
    TYPE_ERROR,
    TYPE_PING,
    TYPE_PONG,
    Message,
    ProtocolError,
    decode,
    decode_buffer,
    split_complete_lines,
    try_decode_line,
)


class MessageEncodeDecodeTests(unittest.TestCase):
    def test_roundtrip_preserves_fields(self) -> None:
        original = Message(type=TYPE_PING, id="abc123", payload={"n": 1})
        encoded = original.encode()
        self.assertTrue(encoded.endswith("\n"))
        self.assertEqual(encoded.count("\n"), 1)
        self.assertNotIn(" ", encoded)  # compact separators
        restored = decode(encoded)
        self.assertEqual(restored.type, original.type)
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.payload, original.payload)

    def test_encode_bytes_is_utf8_of_encode(self) -> None:
        msg = Message.request(TYPE_PING, {"ok": True})
        self.assertEqual(msg.encode_bytes(), msg.encode().encode("utf-8"))

    def test_decode_accepts_crlf_and_surrounding_whitespace(self) -> None:
        line = '  {"type":"pong","id":"z","payload":{"ok":true}}\r\n'
        msg = decode(line)
        self.assertEqual(msg.type, TYPE_PONG)
        self.assertEqual(msg.id, "z")
        self.assertEqual(msg.payload, {"ok": True})

    def test_decode_bytes(self) -> None:
        raw = b'{"type":"ping","id":"1","payload":{}}\n'
        msg = decode(raw)
        self.assertEqual(msg.type, TYPE_PING)
        self.assertEqual(msg.payload, {})

    def test_decode_ignores_unknown_top_level_keys(self) -> None:
        msg = decode('{"type":"ping","id":"1","payload":{},"extra":true}')
        self.assertEqual(msg.type, "ping")
        self.assertEqual(msg.payload, {})

    def test_reply_keeps_the_same_id(self) -> None:
        req = Message.request("pet.status", {})
        reply = req.reply("pet.status.ok", {"pet": {"name": "PULSE"}})
        self.assertEqual(reply.id, req.id)
        self.assertEqual(reply.type, "pet.status.ok")
        self.assertEqual(reply.payload["pet"]["name"], "PULSE")

    def test_error_reply_uses_error_type_and_same_id(self) -> None:
        req = Message.request("nope", {})
        err = req.error("unknown_type", "unsupported message type: nope")
        self.assertEqual(err.type, TYPE_ERROR)
        self.assertEqual(err.id, req.id)
        self.assertEqual(err.payload["code"], "unknown_type")

    def test_request_generates_unique_ids(self) -> None:
        ids = {Message.request(TYPE_PING).id for _ in range(50)}
        self.assertEqual(len(ids), 50)
        self.assertTrue(all(len(item) == 32 for item in ids))

    def test_decode_rejects_empty_line(self) -> None:
        with self.assertRaises(ProtocolError):
            decode("   \n")

    def test_decode_rejects_invalid_json(self) -> None:
        with self.assertRaises(ProtocolError):
            decode("{not json")

    def test_decode_rejects_missing_fields(self) -> None:
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","id":"1"}')
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","payload":{}}')

    def test_decode_rejects_non_object_payload(self) -> None:
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","id":"1","payload":[]}')

    def test_decode_rejects_non_object_message(self) -> None:
        with self.assertRaises(ProtocolError):
            decode("[1, 2]")

    def test_decode_rejects_empty_type_or_id(self) -> None:
        with self.assertRaises(ProtocolError):
            decode('{"type":"","id":"1","payload":{}}')
        with self.assertRaises(ProtocolError):
            decode('{"type":"ping","id":"","payload":{}}')

    def test_encode_rejects_bad_fields(self) -> None:
        with self.assertRaises(ProtocolError):
            Message(type="", id="1", payload={}).encode()
        with self.assertRaises(ProtocolError):
            Message(type="ping", id="", payload={}).encode()
        with self.assertRaises(ProtocolError):
            Message(type="ping", id="1", payload=[]).encode()  # type: ignore[arg-type]

    def test_decode_buffer_splits_complete_lines_and_keeps_remainder(self) -> None:
        first = Message(type="ping", id="a", payload={}).encode()
        second_partial = '{"type":"pong","id":"b"'
        messages, rest = decode_buffer(first + second_partial)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "a")
        self.assertEqual(rest, second_partial)

    def test_decode_buffer_bytes_remainder_stays_bytes(self) -> None:
        blob = b'{"type":"ping","id":"a","payload":{}}\n{"type":"pong"'
        messages, rest = decode_buffer(blob)
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(rest, bytes)
        self.assertTrue(rest.startswith(b'{"type":"pong"'))

    def test_decode_buffer_ignores_empty_lines(self) -> None:
        blob = b'\n\r\n{"type":"ping","id":"a","payload":{}}\n'
        messages, rest = decode_buffer(blob)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "a")
        self.assertEqual(rest, b"")

    def test_try_decode_line_skips_garbage(self) -> None:
        self.assertIsNone(try_decode_line(b""))
        self.assertIsNone(try_decode_line(b"{not json}\n"))
        self.assertEqual(try_decode_line(b'{"type":"ping","id":"a","payload":{}}\n').type, "ping")

    def test_split_complete_lines(self) -> None:
        lines, rest = split_complete_lines(b"foo\nbar")
        self.assertEqual(lines, [b"foo"])
        self.assertEqual(rest, b"bar")


if __name__ == "__main__":
    unittest.main()
