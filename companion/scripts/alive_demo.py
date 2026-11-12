#!/usr/bin/env python3
"""Fast-forward the companion with a fake clock and print what the pet says.

    python3 scripts/alive_demo.py --hours 30 --seed 7

Nothing here touches the network or /sys: the clock, RNG, CPU temperature,
and weather are all simulated, so the same seed always tells the same story.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discovery.simulated import SimulatedScanner  # noqa: E402
from link.handler import CompanionHandler  # noqa: E402
from link.protocol import Message  # noqa: E402
from pet.creature import Creature  # noqa: E402
from pet.life import Life  # noqa: E402
from pet.world import World  # noqa: E402


class FakeClock:
    def __init__(self, start: float) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", default="2026-10-30T08:00", help="local start time (ISO)")
    ap.add_argument("--step", type=float, default=15.0, help="seconds per tick")
    args = ap.parse_args(argv)

    start = datetime.fromisoformat(args.start).timestamp()
    clock = FakeClock(start)
    rng = random.Random(args.seed)
    pet = Creature(name="PULSE", life=Life.new(random.Random(args.seed), start))
    weather_codes = iter([0, 61, 3, 95, 0] * 50)
    world = World(temp_reader=lambda: None, lat=33.45, lon=-112.07,
                  weather_fetch=lambda lat, lon: {"weathercode": next(weather_codes), "temperature": 21},
                  threaded=False)
    handler = CompanionHandler(pet, scanner=SimulatedScanner(seed=args.seed), clock=clock, world=world)
    handler.rng = rng
    handler.events_enabled = True

    def send(typ: str, payload: dict | None = None) -> Message:
        return handler.handle(Message.request(typ, payload or {}))

    send("hello", {"role": "main", "proto": 1, "events": True})
    steps = int(args.hours * 3600 / args.step)
    foods = ["byte biscuit", "voltberry", "cache cookie", "chili chip", "ration"]
    for i in range(steps):
        clock.t += args.step
        handler.tick(dt=args.step / 3600.0 * 3)  # 3x sim speed for hunger/energy
        hour = datetime.fromtimestamp(clock.t).hour
        # A caretaker who shows up a few times a day.
        if i % 240 == 0 and 7 <= hour <= 22:
            r = send("pet.feed", {"item": rng.choice(foods)})
            print(f"  >> feed: {r.payload.get('say')}")
            r = send("pet.play", {"game": rng.choice(["fetch", "race", "spar"])})
            print(f"  >> play: {r.payload.get('say')}")
        if i % 700 == 350:
            r = send("pet.praise")
            print(f"  >> praise: {r.payload.get('say')}")
        if i % 1500 == 900:
            handler.link_changed(False)
        if i % 1500 == 960:
            send("hello", {"role": "main", "proto": 1, "events": True})
        for ev in handler.drain_events():
            kind = ev.payload["event"]
            d = ev.payload["detail"]
            stamp = datetime.fromtimestamp(clock.t).strftime("%a %H:%M")
            text = d.get("text") or d.get("story") or d.get("say") or ""
            print(f"[{stamp}] {kind:16} {text}")
        if pet.life.rival.get("active") and rng.random() < 0.2:
            r = send("pet.rival", {"action": "fight"})
            print(f"  >> rival fight: {r.payload.get('result')} — {r.payload.get('say')}")
    print()
    print("diary entries:", len(pet.life.diary))
    print("shelf:", [t["name"] for t in pet.life.shelf][:8])
    print("bond tier:", pet.life.bond_tier(), " networks:", len(pet.life.networks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
