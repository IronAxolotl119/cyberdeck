"""messages.py — the LCD's notification queue and its calm-down rules.

In plain English: lots of things want the little screen at once (an
unknown RFID tag, PULSE learning a move, the companion link dropping).
Each one becomes an LcdMessage with a priority. NotifyQueue decides
what actually gets shown, in this order:

  1. manual messages (`lcd say`, `lcd test`) always go through;
  2. field mode: only priority >= field_min_priority (ALERT) interrupts;
  3. quiet hours: only priority >= quiet_allow_priority, and silently;
  4. de-dupe: the same message key again within dedupe_seconds is dropped;
  5. rate limit: a token bucket (max_per_minute, burst); alerts may dip
     2 tokens into reserve so a real alert is rarely lost;
  6. the queue holds at most max_queue; when full the oldest,
     least important message makes room.

Whatever is dropped still lands in `history`, so `lcd status` can tell
you what the screen skipped and why. Everything takes `now` as an
argument (no hidden clock), which keeps it easy to test.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass, field

# Priorities, low to high. Higher interrupts lower.
IDLE, INFO, NOTICE, ALERT, MANUAL = 0, 1, 2, 3, 4
PRIORITY_NAMES = {IDLE: "idle", INFO: "info", NOTICE: "notice", ALERT: "alert", MANUAL: "manual"}

_counter = itertools.count()


@dataclass
class LcdMessage:
    """One thing to show. `title` goes on line 1, `text` on line 2.

    No title: the text is word-wrapped over both lines when it fits,
    otherwise it scrolls on line 2 under a small header.
    """

    text: str
    title: str = ""
    priority: int = NOTICE
    kind: str = "info"          # where it came from: alert, pet, link, battery, say, test…
    key: str = ""               # de-dupe key (default: kind + text)
    hold: float | None = None   # seconds on screen (None = settings default)
    flash: bool | None = None   # blink the backlight (None = by priority)
    created: float = 0.0
    seq: int = field(default_factory=lambda: next(_counter))

    def preview(self) -> str:
        """Printable one-liner for logs (glyphs drawn as look-alikes)."""
        from .text import sim_view, to_lcd

        return sim_view(to_lcd((self.title + " " + self.text).strip()))

    def dedupe_key(self) -> str:
        return self.key or f"{self.kind}:{self.title}:{self.text}"


def parse_hhmm(text: str) -> int | None:
    """'22:30' -> minutes after midnight (None if it is not a time)."""
    try:
        hh, mm = str(text).strip().split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def in_quiet_hours(now: float, start: str, end: str) -> bool:
    """True when local time is inside start..end (it may cross midnight)."""
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a is None or b is None or a == b:
        return False
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    return a <= m < b if a < b else (m >= a or m < b)


class NotifyQueue:
    """Priority queue + de-dupe + rate limit + quiet hours + field mode."""

    def __init__(self, dedupe_seconds: float = 90.0, max_per_minute: int = 6, burst: int = 3,
                 max_queue: int = 8, field_min_priority: int = ALERT, quiet_enabled: bool = False,
                 quiet_start: str = "22:00", quiet_end: str = "07:00", quiet_allow_priority: int = ALERT) -> None:
        self.dedupe_seconds = dedupe_seconds
        self.rate = max_per_minute / 60.0       # tokens per second
        self.capacity = float(max(1, burst))
        self.tokens = self.capacity
        self._last_refill: float | None = None
        self.max_queue = max_queue
        self.field_min_priority = field_min_priority
        self.field_mode = False
        self.quiet_enabled = quiet_enabled
        self.quiet_start, self.quiet_end = quiet_start, quiet_end
        self.quiet_allow_priority = quiet_allow_priority
        self.items: list[LcdMessage] = []
        self._seen: dict[str, float] = {}
        self.history: deque[tuple[float, str, str]] = deque(maxlen=40)   # (when, decision, text)
        self.counts: dict[str, int] = {}

    # --- the rules ---------------------------------------------------------
    def quiet(self, now: float) -> bool:
        return self.quiet_enabled and in_quiet_hours(now, self.quiet_start, self.quiet_end)

    def _refill(self, now: float) -> None:
        if self._last_refill is not None:
            self.tokens = min(self.capacity, self.tokens + (now - self._last_refill) * self.rate)
        self._last_refill = now

    def check(self, msg: LcdMessage, now: float) -> str:
        """Decide without side effects on the queue: 'ok' or why it is dropped."""
        if msg.priority >= MANUAL:
            return "ok"
        if self.field_mode and msg.priority < self.field_min_priority:
            return "field"
        if self.quiet(now) and msg.priority < self.quiet_allow_priority:
            return "quiet"
        last = self._seen.get(msg.dedupe_key())
        if last is not None and now - last < self.dedupe_seconds:
            return "dup"
        self._refill(now)
        floor = -2.0 if msg.priority >= ALERT else 0.0
        if self.tokens - 1.0 < floor:
            return "rate"
        return "ok"

    def push(self, msg: LcdMessage, now: float) -> str:
        """Add a message if the rules allow. Returns 'ok' or the drop reason."""
        msg.created = msg.created or now
        decision = self.check(msg, now)
        self.counts[decision] = self.counts.get(decision, 0) + 1
        self.history.appendleft((now, decision, msg.preview()))
        if decision != "ok":
            return decision
        if msg.priority < MANUAL:
            self.tokens -= 1.0
            self._seen[msg.dedupe_key()] = now
            if len(self._seen) > 500:            # forget the oldest keys
                for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[:250]:
                    del self._seen[k]
        self.items.append(msg)
        if len(self.items) > self.max_queue:
            # Drop the least important, oldest one.
            victim = min(self.items, key=lambda m: (m.priority, m.seq))
            self.items.remove(victim)
            self.counts["full"] = self.counts.get("full", 0) + 1
            self.history.appendleft((now, "full", victim.preview()))
            if victim is msg:
                return "full"
        return "ok"

    # --- reading -----------------------------------------------------------
    def peek(self) -> LcdMessage | None:
        """The next message to show: highest priority, then oldest."""
        if not self.items:
            return None
        return max(self.items, key=lambda m: (m.priority, -m.seq))

    def pop(self) -> LcdMessage | None:
        msg = self.peek()
        if msg is not None:
            self.items.remove(msg)
        return msg

    def clear(self) -> int:
        n, self.items = len(self.items), []
        return n

    def __len__(self) -> int:
        return len(self.items)
