"""Field mode and the alert filter (cooldown + rate limiter).

In plain English: lots of findings are routine ("saw the neighbour's
Wi-Fi again"). The AlertFilter decides which ones deserve an alert:
- Field mode OFF: alert from `normal_alert_interest` (medium) and up.
- Field mode ON: scanners run faster in the background, and only
  findings at `alert_interest` (high) or above alert.
Either way, the same alert is muted for `cooldown` seconds and alerts
overall are capped at `max_alerts_per_minute` (with a small burst).
Everything still goes to the journal; the filter only controls alerts.
"""

from __future__ import annotations

import time

from .events import Finding
from .ratelimit import Cooldown, RateLimiter


class AlertFilter:
    def __init__(self, alert_interest: int = 3, normal_interest: int = 2, cooldown: float = 120,
                 per_minute: float = 6, burst: int = 3) -> None:
        self.field_mode = False
        self.alert_interest = alert_interest
        self.normal_interest = normal_interest
        self.cooldown = Cooldown(cooldown)
        self.limiter = RateLimiter(per_minute, per=60.0, burst=burst)
        self.muted = 0      # suppressed by cooldown
        self.passed = 0

    @property
    def threshold(self) -> int:
        return self.alert_interest if self.field_mode else self.normal_interest

    def check(self, f: Finding, now: float | None = None) -> bool:
        """True if this finding should raise an alert right now."""
        now = time.monotonic() if now is None else now
        if f.interest < self.threshold:
            return False
        if not self.cooldown.ready(f.key, now):
            self.muted += 1
            return False
        if not self.limiter.allow(now):
            return False
        self.passed += 1
        return True
