"""Unit tests for pet.event coalescing and wall-clock pacing."""

from __future__ import annotations

import unittest

from link.events import (
    DEFAULT_EVENT_INTERVAL,
    EventPacer,
    enqueue_event,
    event_kind,
)
from link.protocol import TYPE_PET_EVENT, Message, new_id


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _event(kind: str, **detail: object) -> Message:
    return Message(type=TYPE_PET_EVENT, id=new_id(), payload={"event": kind, "detail": dict(detail), "pet": {}})


class EnqueueTests(unittest.TestCase):
    def test_duplicate_need_events_are_coalesced(self) -> None:
        queue: list[Message] = []
        first = _event("hungry", n=1)
        second = _event("hungry", n=2)
        enqueue_event(queue, first)
        enqueue_event(queue, second)
        enqueue_event(queue, _event("tired"))
        enqueue_event(queue, _event("hungry", n=3))
        kinds = [event_kind(m) for m in queue]
        self.assertEqual(kinds, ["hungry", "tired"])
        self.assertEqual(queue[0].payload["detail"]["n"], 3)

    def test_growth_events_are_never_dropped(self) -> None:
        queue: list[Message] = []
        enqueue_event(queue, _event("evolved", to="Wavling"))
        enqueue_event(queue, _event("level_up", level=2))
        enqueue_event(queue, _event("learned_move", move="Chirp"))
        enqueue_event(queue, _event("evolved", to="Aethling"))
        enqueue_event(queue, _event("hungry"))
        enqueue_event(queue, _event("hungry"))
        kinds = [event_kind(m) for m in queue]
        self.assertEqual(kinds, ["evolved", "level_up", "learned_move", "evolved", "hungry"])


class PacerTests(unittest.TestCase):
    def test_default_interval_is_half_a_second(self) -> None:
        self.assertEqual(DEFAULT_EVENT_INTERVAL, 0.5)

    def test_at_most_one_event_per_interval(self) -> None:
        clock = FakeClock()
        pacer = EventPacer(interval=0.5, clock=clock)
        queue = [_event("evolved"), _event("level_up"), _event("learned_move")]
        first = pacer.take(queue)
        self.assertEqual([event_kind(m) for m in first], ["evolved"])
        # Still inside the window: nothing else goes out.
        clock.advance(0.4)
        self.assertEqual(pacer.take(queue), [])
        self.assertEqual(len(queue), 2)
        clock.advance(0.1)
        second = pacer.take(queue)
        self.assertEqual([event_kind(m) for m in second], ["level_up"])
        clock.advance(0.5)
        third = pacer.take(queue)
        self.assertEqual([event_kind(m) for m in third], ["learned_move"])
        self.assertEqual(queue, [])

    def test_zero_interval_flushes_everything(self) -> None:
        pacer = EventPacer(interval=0.0, clock=FakeClock())
        queue = [_event("hungry"), _event("evolved")]
        sent = pacer.take(queue)
        self.assertEqual([event_kind(m) for m in sent], ["hungry", "evolved"])
        self.assertEqual(queue, [])


if __name__ == "__main__":
    unittest.main()
