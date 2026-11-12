#!/usr/bin/env python3
"""Tiny scripted client for the companion link.

The main cyberdeck's SerialCompanionLink is still a stub and it has no TCP
client yet, so this script stands in: it speaks the same NDJSON protocol
and prints a short request/reply transcript.

It prefers the main repo's encoder if /workspace/cyberdeck-main is importable
(so the smoke test really is "their encoder, our server"); otherwise it
uses this project's link.protocol.

    python3 scripts/link_smoke.py 127.0.0.1:7777
    python3 scripts/link_smoke.py --events 127.0.0.1:7777
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Any

# Optional: encode with the main deck's reference implementation.
_MAIN = Path("/workspace/cyberdeck-main")
if _MAIN.is_dir() and str(_MAIN) not in sys.path:
    sys.path.insert(0, str(_MAIN))

try:
    from cyberdeck.protocol import Message as DeckMessage

    USING_DECK = True
except ImportError:
    DeckMessage = None  # type: ignore[assignment]
    USING_DECK = False

# Always have our decoder as a fallback for event lines the main module
# can still parse (unknown types are valid Messages).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from link.protocol import Message, split_complete_lines, try_decode_line  # noqa: E402
from link.transport import parse_host_port  # noqa: E402


def _encode(typ: str, payload: dict[str, Any] | None = None) -> tuple[str, bytes]:
    """Return (id, encoded bytes), using the main deck encoder when possible."""
    if USING_DECK:
        msg = DeckMessage.request(typ, payload or {})
        return msg.id, msg.encode().encode("utf-8")
    msg = Message.request(typ, payload or {})
    return msg.id, msg.encode_bytes()


def _read_until(sock: socket.socket, want_id: str, timeout: float = 2.0) -> list[Any]:
    """Read lines until a message with want_id arrives; also keep extras (events)."""
    sock.settimeout(timeout)
    buffer = b""
    found: list[Any] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buffer += chunk
        lines, buffer = split_complete_lines(buffer)
        for line in lines:
            msg = try_decode_line(line)
            if msg is None:
                continue
            found.append(msg)
            if msg.id == want_id:
                return found
    return found


def exchange(sock: socket.socket, typ: str, payload: dict[str, Any] | None = None) -> list[str]:
    mid, raw = _encode(typ, payload)
    sock.sendall(raw)
    outgoing = raw.decode("utf-8").rstrip("\n")
    transcript = [f"→ {outgoing}"]
    messages = _read_until(sock, mid)
    if not messages:
        transcript.append("← (no reply)")
        return transcript
    for msg in messages:
        transcript.append(f"← {msg.encode().rstrip()}")
    return transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running companion server.")
    parser.add_argument("target", nargs="?", default="127.0.0.1:7777", help="HOST:PORT")
    parser.add_argument("--events", action="store_true", help="Ask for pet.event pushes in hello.")
    args = parser.parse_args(argv)
    host, port = parse_host_port(args.target)

    hello_payload: dict[str, Any] = {"role": "main", "proto": 1}
    if args.events:
        hello_payload["events"] = True

    print(f"Connecting to {host}:{port}  (encoder: {'cyberdeck-main' if USING_DECK else 'cyberdeck-pet'})")
    with socket.create_connection((host, port), timeout=3) as sock:
        lines: list[str] = []
        lines += exchange(sock, "hello", hello_payload)
        lines += exchange(sock, "ping", {})
        lines += exchange(sock, "pet.status", {})
        lines += exchange(sock, "pet.feed", {"item": "ration"})
        lines += exchange(sock, "pet.play", {"game": "spar"})
        lines += exchange(
            sock,
            "pet.gift",
            {"item": {"name": "glitch berry", "kind": "food", "power": 20, "rarity": "common"}},
        )
        lines += exchange(sock, "pet.avatar", {"frame": 0})
        lines += exchange(sock, "nope", {})
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
