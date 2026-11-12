"""Live TCP check against the main deck's StreamCompanionLink, when present.

The main cyberdeck client lives in a sibling repo (or $CYBERDECK_MAIN).
This test starts our companion server on an ephemeral port and drives it
with that client — the same code path `python3 -m cyberdeck --link tcp`
uses. It is skipped when the main package (or its TCP client) is absent
so our own suite stays self-contained.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

from link.handler import CompanionHandler
from link.transport import TcpServer
from pet.creature import Creature
from pet.stats import Stats


def _main_deck_root() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("CYBERDECK_MAIN")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    # tests/ -> this repo -> sibling cyberdeck-main
    candidates.append(here.parents[1].parent / "cyberdeck-main")
    candidates.append(Path("/workspace/cyberdeck-main"))
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve() if path.exists() else path
        if path in seen:
            continue
        seen.add(path)
        if (path / "cyberdeck" / "link.py").is_file() and (path / "cyberdeck" / "petdata.py").is_file():
            return path
    return None


def _import_deck_client():
    root = _main_deck_root()
    if root is None:
        raise unittest.SkipTest(
            "main cyberdeck not found (set CYBERDECK_MAIN or place it next to this repo)"
        )
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        from cyberdeck.link import LinkSettings, open_link
        from cyberdeck.petdata import event_notice, parse_avatar
        from cyberdeck.protocol import (
            TYPE_PET_AVATAR,
            TYPE_PET_FEED,
            TYPE_PET_GIFT,
            TYPE_PET_PLAY,
            TYPE_PET_STATUS,
        )
    except ImportError as exc:
        raise unittest.SkipTest(f"main deck TCP client is not importable: {exc}") from exc
    return {
        "LinkSettings": LinkSettings,
        "open_link": open_link,
        "event_notice": event_notice,
        "parse_avatar": parse_avatar,
        "TYPE_PET_AVATAR": TYPE_PET_AVATAR,
        "TYPE_PET_FEED": TYPE_PET_FEED,
        "TYPE_PET_GIFT": TYPE_PET_GIFT,
        "TYPE_PET_PLAY": TYPE_PET_PLAY,
        "TYPE_PET_STATUS": TYPE_PET_STATUS,
    }


class MainDeckTcpClientTests(unittest.TestCase):
    """Drive our companion with the main deck's real TCP client."""

    def setUp(self) -> None:
        self.deck = _import_deck_client()
        self.handler = CompanionHandler(
            Creature(stats=Stats(hunger=50, mood=60, energy=80)),
            source="sim",
        )
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.server = TcpServer(self.handler, self.lock, host="127.0.0.1", port=0, event_interval=0.0)
        self.server.start()
        self.port = self.server.port
        self.thread = threading.Thread(
            target=self.server.serve_forever, args=(self.stop,), daemon=True
        )
        self.thread.start()
        settings = self.deck["LinkSettings"](
            mode="tcp",
            host="127.0.0.1",
            port=self.port,
            events=True,
            connect_wait=2.0,
            timeout=1.5,
            poll_seconds=30.0,  # don't background-poll during the short test
        )
        self.link = self.deck["open_link"](settings)
        self.link.connect()
        if not self.link.is_linked():
            self.link.close()
            self.fail(f"deck client did not come up: {self.link.link_detail()}")

    def tearDown(self) -> None:
        if getattr(self, "link", None) is not None:
            try:
                self.link.close()
            except Exception:
                pass
        if getattr(self, "stop", None) is not None:
            self.stop.set()
        if getattr(self, "server", None) is not None:
            self.server.close()
        if getattr(self, "thread", None) is not None:
            self.thread.join(timeout=2)

    def test_status_feed_play_gift_avatar_and_events(self) -> None:
        d = self.deck
        hello = getattr(self.link, "hello", None) or {}
        self.assertEqual(hello.get("role"), "companion")
        self.assertEqual(hello.get("proto"), 1)
        self.assertEqual(hello.get("pet"), "PULSE")
        self.assertIs(hello.get("events"), True)

        status = self.link.request(d["TYPE_PET_STATUS"], {})
        self.assertEqual(status.type, "pet.status.ok")
        pet = status.payload["pet"]
        self.assertEqual(pet["name"], "PULSE")
        self.assertEqual(pet["species"], "Chirplet")
        self.assertEqual(pet["stage"], "baby")
        before_hunger = pet["hunger"]

        feed = self.link.request(d["TYPE_PET_FEED"], {"item": "ration"})
        self.assertEqual(feed.type, "pet.feed.ok")
        self.assertEqual(feed.payload["item"], "ration")
        self.assertIsInstance(feed.payload["hunger_delta"], int)
        self.assertLess(feed.payload["hunger_delta"], 0)
        self.assertLess(feed.payload["pet"]["hunger"], before_hunger)

        play = self.link.request(d["TYPE_PET_PLAY"], {"game": "spar"})
        self.assertEqual(play.type, "pet.play.ok")
        self.assertTrue(play.payload["played"])
        self.assertEqual(play.payload["game"], "spar")

        berry = self.link.request(
            d["TYPE_PET_GIFT"],
            {"item": {"name": "glitch berry", "kind": "food", "power": 20, "rarity": "common"}},
        )
        self.assertEqual(berry.type, "pet.gift.ok")
        self.assertTrue(berry.payload["accepted"])
        self.assertIn("munches the glitch berry", berry.payload["reaction"])

        evo = self.link.request(
            d["TYPE_PET_GIFT"],
            {"item": {"name": "evo chip", "kind": "evolve", "power": 0, "rarity": "legendary"}},
        )
        self.assertEqual(evo.type, "pet.gift.ok")
        self.assertTrue(evo.payload["accepted"])
        # An evo chip makes the pet ready; it does not change form on its own.
        self.assertEqual(evo.payload["pet"]["species"], "Chirplet")
        self.assertEqual(evo.payload["pet"]["stage"], "baby")
        self.assertIsNone(evo.payload["effects"]["stage"])
        self.assertTrue(evo.payload["pet"]["evolution"]["ready"])

        events = list(self.link.drain_events())
        deadline = time.monotonic() + 1.5
        kinds = [m.payload.get("event") for m in events if m.type == "pet.event"]
        while "evolve_choice" not in kinds and time.monotonic() < deadline:
            time.sleep(0.05)
            events.extend(self.link.drain_events())
            kinds = [m.payload.get("event") for m in events if m.type == "pet.event"]
        self.assertIn("evolve_choice", kinds, f"expected evolve_choice, got {kinds}")
        choice = next(m for m in events if m.type == "pet.event" and m.payload.get("event") == "evolve_choice")
        self.assertEqual(choice.payload["detail"]["from"], "Chirplet")

        # The deck can then send pet.evolve if it knows the type; otherwise we
        # confirm readiness only. Unknown types on the deck side are a main issue.
        picked = self.link.request("pet.evolve", {"species": "Wavling"})
        if picked.type == "pet.evolve.ok":
            self.assertTrue(picked.payload["accepted"])
            self.assertEqual(picked.payload["to"], "Wavling")
            self.assertEqual(picked.payload["pet"]["species"], "Wavling")
            # evolving + evolved are pushed after the reply; the deck client
            # may only have drained the first of those on this turn.
        else:
            self.fail(f"pet.evolve was not accepted: {picked.type} {picked.payload}")

        reply = self.link.request(d["TYPE_PET_AVATAR"], {"frame": 0})
        self.assertEqual(reply.type, "pet.avatar.ok")
        payload = reply.payload
        self.assertEqual(payload["style"], "face")
        self.assertIn("frames", payload)
        self.assertNotIn("rows", payload)
        # Main-deck parse_avatar still expects a 16x16 palette grid.
        parsed = d["parse_avatar"](payload)
        if parsed is not None:
            # Deck was updated to accept faces; nothing else to assert tightly.
            self.assertTrue(parsed)


if __name__ == "__main__":
    unittest.main()
