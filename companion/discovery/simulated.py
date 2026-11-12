"""A seeded, fully fake radio scanner. Never touches a real network.

The catalog below is invented. Each call to scan() picks a few of those
entries and jitters their RSSI so the log looks alive, but the same seed
always produces the same sequence of results. That keeps tests and demo
runs reproducible.
"""

from __future__ import annotations

import random

from discovery.base import Scanner, Signal


# (kind, id, name, typical_rssi). typical_rssi is the "center" we jitter around.
_CATALOG: tuple[tuple[str, str, str, int], ...] = (
    ("wifi", "wifi:cafe-lumen", "Cafe Lumen", -42),
    ("wifi", "wifi:lab-net", "LAB-NET", -58),
    ("wifi", "wifi:guest-harbor", "Guest-Harbor", -67),
    ("wifi", "wifi:corner-kiosk", "CornerKiosk", -74),
    ("wifi", "wifi:hidden-ssid", "fx-hidden", -81),
    ("wifi", "wifi:roof-mesh", "RoofMesh", -36),  # strong when it appears
    ("bt", "bt:pixel-buds", "PixelBuds-7A", -55),
    ("bt", "bt:deck-keys", "DeckKeys", -70),
    ("bt", "bt:mystery-beacon", "??-BEACON", -40),  # teaches Stray Handshake
    ("bt", "bt:arcade-cabinet", "ArcadeCab-12", -48),  # teaches Packet Burst
    ("bt", "bt:watch-tag", "WatchTag", -63),
    ("bt", "bt:quiet-fob", "QuietFob", -79),
)

# Signals that only show up some of the time, so a long run still has "new" finds.
_RARE_IDS = frozenset(
    {
        "wifi:roof-mesh",
        "wifi:hidden-ssid",
        "bt:mystery-beacon",
        "bt:arcade-cabinet",
        "bt:quiet-fob",
    }
)


class SimulatedScanner(Scanner):
    """Fake Scanner driven by random.Random(seed)."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        self._scans = 0

    def scan(self) -> list[Signal]:
        """Return 2–5 nearby fake signals, with a chance of a rare one."""
        self._scans += 1
        common = [entry for entry in _CATALOG if entry[1] not in _RARE_IDS]
        rare = [entry for entry in _CATALOG if entry[1] in _RARE_IDS]

        count = self._rng.randint(2, 4)
        picked = self._rng.sample(common, k=min(count, len(common)))

        # A rare signal appears about one scan in three, more often after the
        # airwaves have had a chance to "warm up" so early ticks stay quiet.
        rare_chance = 0.25 if self._scans < 5 else 0.45
        if rare and self._rng.random() < rare_chance:
            picked.append(self._rng.choice(rare))

        signals: list[Signal] = []
        seen = set()
        for kind, ident, name, typical in picked:
            if ident in seen:
                continue
            seen.add(ident)
            # Jitter RSSI by up to 8 dBm in either direction, keep it plausible.
            rssi = typical + self._rng.randint(-8, 8)
            rssi = max(-95, min(-20, rssi))
            signals.append(Signal(kind=kind, id=ident, name=name, rssi=rssi))

        # Stronger signals first — that matches how a real scan often looks.
        signals.sort(key=lambda item: item.rssi, reverse=True)
        return signals
