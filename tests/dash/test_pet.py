"""PULSE adapter tests: pet.discovery as source of truth.

Covers the local fallback, how a companion reply is applied (XP,
reaction, moves, level-up, environment), the move_strengthened event,
and a live round-trip against companion/companion.py on an ephemeral
(non-7777) port. No hardware needed.
"""

from __future__ import annotations

import queue
import random
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from cyberdeck.protocol import Message, decode
from cyberdeck_dash.events import HIGH, Finding
from cyberdeck_dash.pet import (MAX_MOVE_RANK, CompanionLink, LocalPet, PetAdapter, apply_discovery,
                                env_mood_from_buff)
from cyberdeck_dash.rules import XpRules


ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(test, seconds: float = 5.0, step: float = 0.05) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(step)
    return False


def finding(kind="wifi", ident="aa:bb:cc:dd:ee:01", event="new", label="cafe-guest",
            interest=2, first=True) -> Finding:
    return Finding(kind, event, ident, label, interest, first=first)


class FakeLink:
    """Stand-in for CompanionLink that records requests and injects replies."""

    def __init__(self) -> None:
        self.online = True
        self.pet: dict = {}
        self.status = "linked fake"
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.sent: list[tuple] = []

    def request(self, typ, payload=None, callback=None) -> None:
        self.sent.append((typ, payload or {}, callback))

    def reply_last(self, typ: str, payload: dict, mid: str = "1") -> None:
        typ_sent, _payload, callback = self.sent[-1]
        assert callback is not None
        self.results.put((callback, Message(typ, mid, payload)))


class MoodFromBuff(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(env_mood_from_buff("busy airwaves"), "buzzing")
        self.assertEqual(env_mood_from_buff("curious: sniffing around"), "curious")
        self.assertEqual(env_mood_from_buff("bored: it's been quiet"), "bored")
        self.assertEqual(env_mood_from_buff("calm"), "calm")
        self.assertEqual(env_mood_from_buff(""), "calm")


class LocalFallback(unittest.TestCase):
    def test_unlinked_first_sighting_awards_local_xp(self):
        pet = LocalPet()
        res = apply_discovery(pet, finding(interest=2), XpRules(), random.Random(0), 1.0)
        self.assertEqual(res["xp"], 7)
        self.assertIn("cafe-guest", res["reaction"])
        self.assertEqual(pet.xp, 7)

    def test_repeat_finding_is_ignored_locally(self):
        adapter = PetAdapter(XpRules(), link=None, rng=random.Random(0))
        reactions = []
        adapter.on_reaction = reactions.append
        adapter.on_finding(finding(first=False))
        self.assertEqual(reactions, [])
        self.assertEqual(adapter.local.xp, 0)

    def test_strengthen_caps_at_five(self):
        pet = LocalPet()
        pet.moves = {"Signal Sniff": 5}
        res = apply_discovery(pet, finding(kind="wifi", interest=3),
                              XpRules(learn_chance=1.0), random.Random(0))
        self.assertLessEqual(pet.moves["Signal Sniff"], MAX_MOVE_RANK)
        if res and res.get("strengthened_move") == "Signal Sniff":
            self.assertEqual(pet.moves["Signal Sniff"], MAX_MOVE_RANK)


class CompanionReplyIsAuthoritative(unittest.TestCase):
    def setUp(self):
        self.link = FakeLink()
        self.adapter = PetAdapter(XpRules(), self.link, random.Random(0))
        self.reactions: list[dict] = []
        self.events: list[dict] = []
        self.adapter.on_reaction = self.reactions.append
        self.adapter.on_event = self.events.append

    def _ok(self, **body):
        body.setdefault("learned_move", None)
        body.setdefault("strengthened_move", None)
        body.setdefault("level_up", False)
        body.setdefault("level", 1)
        body.setdefault("pet", {"name": "PULSE", "level": body["level"], "xp": body.get("xp", 0),
                                "environment": {"busy": 0.125, "buff": "calm"}, "moves": ["Signal Sniff"]})
        return body

    def test_ok_reply_updates_view_and_reaction(self):
        self.adapter.on_finding(finding())
        typ, payload, _cb = self.link.sent[0]
        self.assertEqual(typ, "pet.discovery")
        self.assertEqual(payload, {"kind": "wifi", "id": "aa:bb:cc:dd:ee:01",
                                   "interest": 2, "event": "new", "label": "cafe-guest"})
        pet = {"name": "PULSE", "species": "Chirplet", "level": 1, "xp": 7,
               "environment": {"busy": 0.25, "buff": "curious: sniffing around"},
               "moves": ["Signal Sniff"]}
        self.link.reply_last("pet.discovery.ok", self._ok(
            xp=7, reaction="PULSE logged cafe-guest (+7 XP).", pet=pet))
        self.adapter.drain()
        self.assertEqual(self.reactions[0]["xp"], 7)
        self.assertEqual(self.reactions[0]["source"], "companion")
        self.assertEqual(self.reactions[0]["reaction"], "PULSE logged cafe-guest (+7 XP).")
        self.assertEqual(self.adapter.local.xp, 0)          # local stand-in stays put
        v = self.adapter.view()
        self.assertEqual(v["source"], "LINK")
        self.assertEqual(v["xp"], 7)
        self.assertEqual(v["env_busy"], 0.25)
        self.assertEqual(v["env_mood"], "curious")
        self.assertEqual(v["buff"], "curious: sniffing around")
        self.assertNotIn("xp_next", v)
        # After an ok reply we also ask for combat stats (ranks).
        self.assertEqual(self.link.sent[1][0], "pet.combat.stats")

    def test_learned_and_level_up_are_kept(self):
        self.adapter.on_finding(finding(kind="rfid", ident="de:ad", label="badge", interest=3))
        self.link.reply_last("pet.discovery.ok", self._ok(
            xp=14, level=2, level_up=True, learned_move="Tag Bite",
            reaction="PULSE learned Tag Bite from badge!  LEVEL 2!",
            pet={"name": "PULSE", "level": 2, "xp": 14, "moves": ["Signal Sniff", "Tag Bite"],
                 "environment": {"busy": 0.125, "buff": "calm"}}))
        self.adapter.drain()
        res = self.reactions[0]
        self.assertEqual(res["learned_move"], "Tag Bite")
        self.assertTrue(res["level_up"])
        self.assertEqual(res["level"], 2)
        self.assertIn("+ Tag Bite", list(self.adapter.recent_moves))

    def test_repeat_zero_xp_still_shows_reaction(self):
        self.adapter.on_finding(finding())
        self.link.reply_last("pet.discovery.ok", self._ok(
            xp=0, reaction="PULSE has already logged this wifi signal."))
        self.adapter.drain()
        self.assertEqual(self.reactions[0]["xp"], 0)
        self.assertIn("already", self.reactions[0]["reaction"])
        self.assertEqual(self.adapter.local.xp, 0)

    def test_unknown_type_falls_back_to_local_xp(self):
        self.adapter.on_finding(finding(interest=1, label="home"))
        self.link.reply_last("error", {"code": "unknown_type", "message": "unsupported"})
        self.adapter.drain()
        self.assertIs(self.adapter.discovery_supported, False)
        self.assertEqual(self.reactions[0]["source"], "local")
        self.assertEqual(self.reactions[0]["xp"], 4)
        self.assertEqual(self.adapter.local.xp, 4)
        # A later find must not be sent to the companion again.
        sent = len(self.link.sent)
        self.adapter.on_finding(finding(ident="ff:ff", label="other", interest=1))
        self.assertEqual(len(self.link.sent), sent)
        self.assertEqual(self.adapter.local.xp, 8)

    def test_timeout_does_not_invent_local_xp(self):
        self.adapter.on_finding(finding())
        self.link.reply_last("error", {"code": "timeout", "message": "no reply"})
        self.adapter.drain()
        self.assertEqual(self.reactions, [])
        self.assertEqual(self.adapter.local.xp, 0)
        self.assertIsNone(self.adapter.discovery_supported)   # may retry later

    def test_bad_payload_does_not_invent_local_xp(self):
        self.adapter.on_finding(finding())
        self.link.reply_last("error", {"code": "bad_payload", "message": "missing id"})
        self.adapter.drain()
        self.assertEqual(self.reactions, [])
        self.assertEqual(self.adapter.local.xp, 0)

    def test_move_strengthened_event(self):
        self.link.events.put({
            "event": "move_strengthened",
            "detail": {"move": "Tag Bite", "rank": 2, "bonus": 4},
            "pet": {"name": "PULSE", "level": 1, "environment": {"busy": 0.5, "buff": "curious: sniffing around"}},
        })
        self.adapter.drain()
        self.assertEqual(self.events[0]["event"], "move_strengthened")
        self.assertIn("^ Tag Bite r2 +4", list(self.adapter.recent_moves))
        self.assertEqual(self.link.pet["environment"]["busy"], 0.5)
        self.assertEqual(self.link.sent[-1][0], "pet.combat.stats")

    def test_combat_stats_reply_is_kept(self):
        self.adapter._request_combat_stats()
        self.link.reply_last("pet.combat.stats.ok", {
            "moves": [{"name": "Tag Bite", "power": 16, "rank": 2, "bonus": 4}],
            "level": 1,
        })
        self.adapter.drain()
        self.assertEqual(self.adapter.combat["moves"][0]["rank"], 2)
        self.assertEqual(self.adapter.view()["combat"]["moves"][0]["bonus"], 4)


class DashboardWiresTheReply(unittest.TestCase):
    """The journal, ticker and PULSE panel all show the companion's reply."""

    def test_reaction_lands_in_journal_and_ticker(self):
        from cyberdeck_dash.app import Dashboard
        from cyberdeck_dash.config import load_config

        dash = Dashboard(load_config(), mode="sim", seed=1, threaded=False, companion=False,
                         journal_path=False)
        dash._on_reaction({
            "xp": 14, "level": 2, "level_up": True, "learned_move": "Tag Bite",
            "strengthened_move": None, "reaction": "PULSE learned Tag Bite from badge!",
            "source": "companion",
        })
        events = [e["event"] for e in dash.journal.recent(10)]
        self.assertIn("discovery", events)
        self.assertIn("learned_move", events)
        self.assertIn("level_up", events)
        discovery = next(e for e in dash.journal.recent(10) if e["event"] == "discovery")
        self.assertEqual(discovery["detail"]["xp"], 14)
        self.assertEqual(discovery["detail"]["source"], "companion")
        ticker = [line for _ts, _style, line in dash.ticker]
        self.assertTrue(any("Tag Bite" in line for line in ticker))
        self.assertTrue(any("level 2" in line.lower() for line in ticker))

    def test_move_strengthened_event_text(self):
        from cyberdeck_dash.app import Dashboard
        from cyberdeck_dash.config import load_config

        dash = Dashboard(load_config(), mode="sim", seed=1, threaded=False, companion=False,
                         journal_path=False)
        dash._on_pet_event({"event": "move_strengthened",
                            "detail": {"move": "Beacon Burst", "rank": 1, "bonus": 2},
                            "pet": {"name": "PULSE"}})
        ticker = [line for _ts, _style, line in dash.ticker]
        self.assertTrue(any("Beacon Burst" in line and "rank 1" in line for line in ticker))
        self.assertEqual(dash.journal.recent(1)[0]["event"], "move_strengthened")


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        return True
    except OSError:
        return False


class RealCompanionDiscovery(unittest.TestCase):
    """Round-trip against companion/companion.py on a free (non-7777) port."""

    def setUp(self):
        script = ROOT / "companion" / "companion.py"
        if not script.exists():
            self.skipTest("no companion/ checkout")
        self.port = free_port()
        self.assertNotEqual(self.port, 7777)
        self.proc = subprocess.Popen(
            [sys.executable, str(script), "--tcp", f"127.0.0.1:{self.port}",
             "--event-interval", "0"],
            cwd=str(script.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_for(lambda: _listening(self.port), 10):
            self.proc.terminate()
            self.skipTest("companion did not start")
        self.link = CompanionLink("127.0.0.1", self.port, timeout=1.0, poll=0.3)
        self.adapter = PetAdapter(XpRules(), self.link, random.Random(0))
        self.reactions: list[dict] = []
        self.adapter.on_reaction = self.reactions.append
        self.link.start()
        self.addCleanup(self._stop)
        self.assertTrue(wait_for(lambda: (self.adapter.drain() or self.link.online), 8),
                        f"did not link: {self.link.status}")

    def _stop(self):
        try:
            self.link.stop()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def discover(self, kind: str, ident: str, interest: int = 1, label: str = "",
                 event: str = "new") -> dict:
        before = len(self.reactions)
        self.adapter.on_finding(finding(kind, ident, event, label or ident, interest, first=True))
        ok = wait_for(lambda: (self.adapter.drain() or len(self.reactions) > before), 5)
        self.assertTrue(ok, f"no pet.discovery reply; status={self.link.status}")
        return self.reactions[-1]

    def test_first_sighting_repeat_and_environment(self):
        res = self.discover("wifi", "aa:bb:cc:dd:ee:01", 2, "cafe-guest")
        self.assertEqual(res["source"], "companion")
        self.assertEqual(res["xp"], 7)
        self.assertIn("cafe-guest", res["reaction"])
        self.assertFalse(res.get("level_up"))
        v = self.adapter.view()
        self.assertEqual(v["source"], "LINK")
        self.assertEqual(v["xp"], 7)
        self.assertIsNotNone(v.get("env_busy"))
        self.assertIn("buff", v)
        self.assertIn("environment", self.link.pet)

        again = self.discover("wifi", "aa:bb:cc:dd:ee:01", 3, "cafe-guest")
        self.assertEqual(again["xp"], 0)
        self.assertIn("already", again["reaction"].lower())
        self.assertEqual(self.adapter.view()["xp"], 7)          # unchanged

        other = self.discover("bt", "aa:bb:cc:dd:ee:01", 1, "phone")
        self.assertEqual(other["xp"], 3)
        self.assertGreaterEqual(self.adapter.view()["xp"], 10)

    def test_several_finds_raise_busy(self):
        for i in range(8):
            res = self.discover("lan", f"192.168.1.{10 + i}", 1, f"host-{i}")
            self.assertEqual(res["xp"], 3, res)
        v = self.adapter.view()
        self.assertGreaterEqual(float(v["env_busy"]), 0.99)
        self.assertIn("busy", v["buff"])

    def test_dashboard_shows_companion_xp_in_journal(self):
        from cyberdeck_dash.app import Dashboard
        from cyberdeck_dash.config import load_config

        dash = Dashboard(load_config(), mode="sim", seed=1, threaded=False, companion=False,
                         journal_path=False)
        # Reattach the already-linked adapter so reactions go through the bus.
        dash.link = self.link
        dash.pet = self.adapter
        # Keep collecting reactions (discover() waits on them) and also
        # run the dashboard handlers the real bus would call.
        self.adapter.on_reaction = lambda res: (self.reactions.append(res), dash._on_reaction(res))
        self.adapter.on_event = lambda ev: dash._on_pet_event(ev)
        res = self.discover("rfid", "de:ad:10:ca", 1, "keycard")
        self.assertEqual(res["xp"], 8)
        dash.step(time.time())
        discovery = next(e for e in dash.journal.recent(20) if e["event"] == "discovery")
        self.assertEqual(discovery["detail"]["xp"], 8)
        self.assertEqual(discovery["detail"]["source"], "companion")
        self.assertTrue(any("keycard" in line or "+8" in line or "XP" in line
                            for _ts, _style, line in dash.ticker))


class UnknownTypeServer(unittest.TestCase):
    """An older companion that does not know pet.discovery."""

    def test_fallback_over_tcp(self):
        port = free_port()
        stop = threading.Event()
        seen_hello = {}

        def serve():
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.listen(1)
                sock.settimeout(0.2)
                while not stop.is_set():
                    try:
                        conn, _addr = sock.accept()
                    except socket.timeout:
                        continue
                    with conn:
                        conn.settimeout(0.5)
                        buf = b""
                        try:
                            while not stop.is_set():
                                chunk = conn.recv(65536)
                                if not chunk:
                                    break
                                buf += chunk
                                while b"\n" in buf:
                                    raw, buf = buf.split(b"\n", 1)
                                    if not raw.strip():
                                        continue
                                    msg = decode(raw.decode("utf-8", "replace"))
                                    if msg.type == "hello":
                                        seen_hello["client"] = (msg.payload or {}).get("client")
                                        reply = msg.reply("hello.ok", {"role": "companion", "proto": 1,
                                                                       "events": True, "pet": "PULSE"})
                                    elif msg.type == "pet.status":
                                        reply = msg.reply("pet.status.ok",
                                                          {"pet": {"name": "PULSE", "level": 1, "xp": 0,
                                                                   "moves": []}})
                                    elif msg.type == "pet.discovery":
                                        reply = msg.error("unknown_type", "unsupported message type")
                                    else:
                                        reply = msg.error("unknown_type", "unsupported message type")
                                    conn.sendall(reply.encode_bytes())
                        except OSError:
                            pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: stop.set())
        link = CompanionLink("127.0.0.1", port, timeout=1.0, poll=0.3)
        adapter = PetAdapter(XpRules(), link, random.Random(0))
        reactions = []
        adapter.on_reaction = reactions.append
        link.start()
        self.addCleanup(link.stop)
        self.assertTrue(wait_for(lambda: (adapter.drain() or link.online), 8), link.status)
        adapter.on_finding(finding(interest=1, label="home"))
        self.assertTrue(wait_for(lambda: (adapter.drain() or bool(reactions)), 5), "no fallback")
        self.assertEqual(seen_hello.get("client"), "dash")
        self.assertIs(adapter.discovery_supported, False)
        self.assertEqual(reactions[0]["source"], "local")
        self.assertEqual(reactions[0]["xp"], 4)


if __name__ == "__main__":
    unittest.main()
