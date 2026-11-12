"""Cooldowns and rate limits, so alerts and scans never spam you.

In plain English:
- Cooldown: "don't repeat the SAME thing within N seconds." Used so the
  same network or tag does not alert over and over.
- RateLimiter: "no more than N things per minute overall, with a small
  burst allowed." Classic token bucket. Used for alerts, manual scans,
  and the ping sweep.
Both take the current time as an argument so tests can fake the clock.
"""

from __future__ import annotations

import time


class Cooldown:
    """Per-key minimum gap between two allowed events."""

    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self._last: dict[str, float] = {}

    def ready(self, key: str, now: float | None = None) -> bool:
        """True (and starts the cooldown) if key is allowed right now."""
        now = time.monotonic() if now is None else now
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self._last[key] = now
        return True

    def remaining(self, key: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        last = self._last.get(key)
        return 0.0 if last is None else max(0.0, self.seconds - (now - last))


class RateLimiter:
    """Token bucket: `rate` tokens per `per` seconds, holding up to `burst`."""

    def __init__(self, rate: float, per: float = 60.0, burst: int = 1) -> None:
        self.rate = float(rate)
        self.per = float(per)
        self.capacity = max(1.0, float(burst))
        self.tokens = self.capacity
        self._stamp: float | None = None
        self.dropped = 0  # how many were refused, shown in the Field panel

    def _refill(self, now: float) -> None:
        if self._stamp is not None and self.rate > 0:
            gained = (now - self._stamp) * self.rate / self.per
            self.tokens = min(self.capacity, self.tokens + gained)
        self._stamp = now

    def allow(self, now: float | None = None) -> bool:
        """Take one token if available."""
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        self.dropped += 1
        return False
