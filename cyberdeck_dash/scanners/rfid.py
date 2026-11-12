"""RFID / NFC scanners for the MFRC522 reader from the kit.

In plain English: tap a card or key fob on the reader and its UID (a
short hex ID) is reported. That is all these read: the UID, not the
card's contents. The tracker in rules.py then decides if it is a known
tag (with your note) or an unknown one.

- SimRfidScanner     pretends a few tags get tapped now and then.
                     Press r in the dashboard to "tap" a tag on demand.
- Mfrc522Scanner     real reader on SPI0 using the optional `mfrc522`
                     library (imported lazily). Wiring is in docs/DASHBOARD.md.
"""

from __future__ import annotations

import random

from .base import ScanResult, Scanner, TagRead


def format_uid(value: int | bytes | list[int]) -> str:
    """12345 or [0x04, 0xa2] -> '04:a2:...' lowercase hex pairs."""
    if isinstance(value, int):
        raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    else:
        raw = bytes(value)
    return ":".join(f"{b:02x}" for b in raw)


class SimRfidScanner(Scanner):
    name = "rfid"

    KNOWN = ["04:a2:19:7f", "93:5e:01:c4"]          # matches the sample inventory
    STRANGERS = ["04:77:e1:2a:9b:30:80", "de:ad:10:ca"]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.holding: list[str] = []   # tags currently "on" the reader
        self.hold_left = 0
        self.pending: list[str] = []

    def tap(self, uid: str | None = None) -> str:
        """Simulate holding a tag on the reader for a few polls."""
        uid = uid or self.rng.choice(self.KNOWN + self.STRANGERS)
        self.pending.append(uid)
        return uid

    def scan(self) -> ScanResult:
        if self.pending:
            self.holding, self.hold_left = [self.pending.pop(0)], 3
        elif self.hold_left <= 0 and self.rng.random() < 0.02:
            self.holding, self.hold_left = [self.rng.choice(self.KNOWN + self.STRANGERS)], self.rng.randint(1, 4)
        if self.hold_left > 0:
            self.hold_left -= 1
            return ScanResult(self.name, [TagRead(u) for u in self.holding], simulated=True)
        return ScanResult(self.name, [], simulated=True)


class Mfrc522Scanner(Scanner):
    name = "rfid"
    simulated = False

    def __init__(self) -> None:
        self.reader = None
        self.error = ""
        try:
            from mfrc522 import SimpleMFRC522  # lazy: only on the Pi
            self.reader = SimpleMFRC522()
        except Exception as exc:  # ImportError, no SPI, no permissions
            self.error = f"mfrc522 unavailable: {exc}"[:60]

    def scan(self) -> ScanResult:
        if self.reader is None:
            return ScanResult(self.name, error=self.error, simulated=False)
        try:
            uid = self.reader.read_id_no_block()
        except Exception as exc:
            return ScanResult(self.name, error=f"read: {exc}"[:60], simulated=False)
        return ScanResult(self.name, [TagRead(format_uid(uid))] if uid else [], simulated=False)

    def describe(self) -> str:
        return "MFRC522"
