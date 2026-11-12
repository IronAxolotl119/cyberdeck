"""Shared helpers for queuing and pacing pet.event pushes.

The companion may generate several events in one burst (a gift that evolves
and also teaches a move, or a tick that notices hunger and tiredness). We
still want the wire to stay calm:

  * send at most one pet.event per interval (default 0.5 s of wall clock)
  * while a need event (hungry / tired / sad) is still waiting, a later
    duplicate of the same kind replaces it (latest pet snapshot wins)
  * evolved / level_up / learned_move / move_strengthened / woke / evolve_soon / evolving / evolve_choice are never dropped or merged

Each TCP client has its own queue and its own pacer, so two decks can be
opted in at once without sharing a send timer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from link.protocol import Message

# Wall-clock seconds between outgoing pet.event messages (companion default).
DEFAULT_EVENT_INTERVAL = 0.5

# These three are "the pet still feels this way"; later copies replace earlier ones.
NEED_EVENT_KINDS = frozenset({"hungry", "tired", "sad"})

# Growth events are unique moments and must not be coalesced or discarded.
KEEP_EVENT_KINDS = frozenset({"evolved", "level_up", "learned_move", "move_strengthened", "woke", "evolve_soon", "evolving", "evolve_choice"})

Clock = Callable[[], float]
T = TypeVar("T")


def event_kind(message: Message) -> str:
    """The payload 'event' field, or '' if it is missing."""
    payload = message.payload if isinstance(message.payload, dict) else {}
    return str(payload.get("event") or "")


def enqueue_event(queue: list[Message], message: Message) -> None:
    """Append message, coalescing a still-queued hungry/tired/sad of the same kind."""
    kind = event_kind(message)
    if kind in NEED_EVENT_KINDS:
        for index, existing in enumerate(queue):
            if event_kind(existing) == kind:
                queue[index] = message
                return
    queue.append(message)


class EventPacer:
    """Release at most one queued event per interval."""

    def __init__(
        self,
        interval: float = DEFAULT_EVENT_INTERVAL,
        clock: Clock | None = None,
    ) -> None:
        self.interval = max(0.0, float(interval))
        self.clock: Clock = clock or time.monotonic
        # 0 means "never sent"; the first event is due immediately.
        self._last_sent = 0.0

    def due(self, now: float | None = None) -> bool:
        """True if we may send an event right now."""
        if self.interval <= 0:
            return True
        stamp = self.clock() if now is None else now
        if self._last_sent == 0.0:
            return True
        return (stamp - self._last_sent) >= self.interval

    def wait_time(self, now: float | None = None) -> float:
        """Seconds until the next event may go out, or 0 if one is due."""
        if self.interval <= 0 or self._last_sent == 0.0:
            return 0.0
        stamp = self.clock() if now is None else now
        remaining = self.interval - (stamp - self._last_sent)
        return remaining if remaining > 0 else 0.0

    def mark(self, now: float | None = None) -> None:
        self._last_sent = self.clock() if now is None else now

    def take(self, queue: list[Message]) -> list[Message]:
        """Pop what may be sent now: all of the queue if interval is 0, else at most one."""
        if not queue:
            return []
        if self.interval <= 0:
            outgoing = list(queue)
            queue.clear()
            self.mark()
            return outgoing
        if not self.due():
            return []
        self.mark()
        return [queue.pop(0)]
