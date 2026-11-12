"""Board-to-board link: NDJSON protocol, request handler, and transports.

This package implements docs/PROTOCOL.md from the main cyberdeck so the
pet can talk to the deck over TCP or UART. The Python reference on the
main board is cyberdeck/protocol.py; encode/decode here must match it.
"""

from link.protocol import (
    PROTO_VERSION,
    Message,
    ProtocolError,
    decode,
    decode_buffer,
    new_id,
)

__all__ = [
    "PROTO_VERSION",
    "Message",
    "ProtocolError",
    "decode",
    "decode_buffer",
    "new_id",
]
