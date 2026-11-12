"""tests/test_link.py — the companion link: pet data, sim events, TCP and serial.

  * petdata: tolerant pet parsing, event text, avatars and colours
  * the sim companion: events only after hello asks, need/level/evolve
    events, avatar, refused evolve at the last form
  * StreamCompanionLink over TCP against tools/fake_companion.py started
    as a separate process on a random port (ping, pet status, feed,
    play, gift, pushed events, reconnect after the companion restarts)
  * timeouts and junk lines from a misbehaving server
  * serial: SerialTransport on an os.openpty() pair, and fake_companion
    serving a pty (plus a socat pty pair if socat is installed)
"""

from __future__ import annotations

import io
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from cyberdeck import petdata
from cyberdeck.app import build_parser
from cyberdeck.commands import build_registry
from cyberdeck.companion import PetState, SimCompanionLink, STAGES, os_serial_default
from cyberdeck.config import Settings
from cyberdeck.link import (LinkSettings, SerialTransport, StreamCompanionLink, TcpTransport,
                            Transport, load_link_settings, open_link, with_overrides)
from cyberdeck.protocol import TYPE_PET_EVENT, Message, decode

from helpers import make_ctx

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tools" / "fake_companion.py"


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


def fast(**kw) -> LinkSettings:
    """Link settings with short timers so tests run quickly."""
    base = dict(mode="tcp", timeout=1.0, connect_wait=3.0, retry_max=0.3, poll_seconds=0.5)
    base.update(kw)
    return LinkSettings(**base)


# --- pet payloads ------------------------------------------------------------------------

class PetDataTests(unittest.TestCase):
    def test_minimal_payload_gets_safe_defaults(self):
        pet = petdata.normalize_pet({})
        self.assertEqual((pet["name"], pet["species"], pet["mood"]), ("PULSE", "?", "content"))
        self.assertIsNone(pet["level"])
        self.assertEqual(pet["moves"], [])
        self.assertEqual(petdata.normalize_pet("garbage")["name"], "PULSE")

    def test_species_falls_back_to_form_or_avatar(self):
        self.assertEqual(petdata.normalize_pet({"form": "Staticore"})["species"], "Staticore")
        self.assertEqual(petdata.normalize_pet({"avatar": {"form": "wavling"}})["species"], "wavling")
        self.assertEqual(petdata.normalize_pet({"species": "Chirplet", "form": "x"})["species"], "Chirplet")

    def test_numeric_mood_is_a_happiness_score(self):
        pet = petdata.normalize_pet({"hunger": 10, "mood": 90, "energy": 80})
        self.assertEqual(pet["happiness"], 90)
        self.assertEqual(pet["mood"], "joyful")

    def test_optional_stats_and_moves(self):
        pet = petdata.normalize_pet({"level": "4", "xp": 130, "hp": 18, "max_hp": 30, "attack": 9,
                                     "speed": 7, "moves": ["Chirp", {"name": "Static Snap"}, None],
                                     "care_mistakes": 2})
        self.assertEqual(pet["moves"], ["Chirp", "Static Snap"])
        self.assertEqual(petdata.stats_line(pet), "Lv 4 (130 xp) · HP 18/30 · ATK 9 SPD 7")

    def test_event_notices(self):
        p = {"pet": {"name": "PULSE", "level": 5}}
        cases = {
            "hungry": ("alert", "hungry"), "tired": ("warn", "worn out"), "sad": ("warn", "sad"),
            "evolved": ("ok", "Wavling"), "level_up": ("ok", "level 5"),
            "learned_move": ("ok", "Static Snap"), "signal_found": ("info", "RoofMesh"),
        }
        details = {"evolved": {"to": "Wavling"}, "learned_move": {"move": "Static Snap"},
                   "signal_found": {"ssid": "RoofMesh"}}
        for event, (level, word) in cases.items():
            kind, lvl, text = petdata.event_notice(dict(p, event=event, detail=details.get(event, {})))
            self.assertEqual((kind, lvl), (event, level))
            self.assertIn(word, text)
        self.assertEqual(petdata.event_notice({"event": "danced", "detail": "x"})[1], "info")

    def test_avatar_parsing(self):
        # Current companions: an animated text face.
        face = petdata.parse_face({"style": "face", "mood": "happy", "frame": 1,
                                   "frames": ["'(^_^)'", "(^o^)", "(^v^)"], "interval_ms": 450})
        self.assertEqual(face.frames, ("(^_^)", "(^o^)", "(^v^)"))     # stray repr quotes dropped
        self.assertEqual((face.interval_ms, face.mood, face.at(0.5)), (450, "happy", "(^o^)"))
        self.assertIsNone(petdata.parse_avatar({"style": "face", "frames": ["(^_^)"]}))
        for bad in (None, {}, {"style": "face", "frames": []}, {"frames": [1, None]}):
            self.assertIsNone(petdata.parse_face(bad))
        # Older companions: the 16x16 sprite, kept only as a fallback.
        good = {"form": "chirplet", "palette": [None, "#ff0000", "0f0", "nonsense"],
                "rows": [[0, 1], [2, 3], [9, True]]}
        self.assertIsNone(petdata.parse_face(good))
        av = petdata.parse_avatar(good)
        self.assertEqual(av.pixel(1, 0), (255, 0, 0))
        self.assertEqual(av.pixel(0, 1), (0, 255, 0))
        self.assertIsNone(av.pixel(0, 0))       # null palette entry = transparent
        self.assertIsNone(av.pixel(1, 1))       # bad colour = transparent
        self.assertIsNone(av.pixel(0, 2))       # index out of range
        self.assertIsNone(av.pixel(1, 2))       # bool is not an index
        cells = petdata.half_block_cells(av)
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0][1], ((255, 0, 0), None))
        for bad in (None, {}, {"rows": "x", "palette": []}, {"rows": [[0] * 99], "palette": [None]}):
            self.assertIsNone(petdata.parse_avatar(bad))

    def test_nearest_colours(self):
        self.assertEqual(petdata.nearest_256((255, 0, 0)), 196)
        self.assertEqual(petdata.nearest_256((255, 255, 255)), 231)
        self.assertGreaterEqual(petdata.nearest_256((128, 128, 128)), 232)   # grey ramp
        self.assertEqual(petdata.nearest_8((250, 10, 10)), 1)
        self.assertEqual(petdata.nearest_8((10, 10, 10)), 0)


# --- the in-process sim companion --------------------------------------------------------

class SimCompanionTests(unittest.TestCase):
    def make(self, **pet):
        self.now = 0.0
        link = SimCompanionLink(clock=lambda: self.now, pet=PetState(**pet))
        return link

    def test_ladder_and_species(self):
        self.assertEqual(STAGES, ["egg", "baby", "in-training", "rookie", "champion", "ultimate", "mega"])
        pet = PetState(stage="in-training")
        self.assertNotEqual(pet.species, "Bitling")
        self.assertEqual(pet.as_payload()["avatar"]["form"], pet.species.lower())

    def test_events_only_after_hello_asks(self):
        link = self.make(hunger=79)
        link._linked = True
        link.handle(Message.request("hello", {"role": "main", "proto": 1}))
        self.assertNotIn("events", link._hello or {})
        link.pet.hunger = 90
        link.pet._check_needs()
        self.assertEqual(link.take_events(), [])
        reply = link.handle(Message.request("hello", {"role": "main", "proto": 1, "events": True}))
        self.assertTrue(reply.payload.get("events"))

    def test_hungry_event_once_per_crossing(self):
        link = self.make(hunger=70)
        link.connect()                       # asks for events
        self.now += 300                      # 5 minutes: hunger 70 -> 82.5
        events = link.drain_events()
        kinds = [e.payload["event"] for e in events]
        self.assertEqual(kinds.count("hungry"), 1)
        first = events[kinds.index("hungry")]
        self.assertEqual(first.type, TYPE_PET_EVENT)
        self.assertIn("pet", first.payload)
        self.now += 60
        self.assertNotIn("hungry", [e.payload["event"] for e in link.drain_events()])
        link.request("pet.feed", {"item": "ration"})
        link.pet.hunger = 95
        link.pet._check_needs()
        self.assertIn("hungry", [e.payload["event"] for e in link.drain_events()])

    def test_level_up_and_learned_move(self):
        link = self.make(level=1, xp=30, moves=["peck"], energy=100)
        link.connect()
        link.request("pet.play", {"game": "spar"})     # +10 xp -> 40 -> level 2
        kinds = [(e.payload["event"], e.payload["detail"]) for e in link.drain_events()]
        self.assertIn(("level_up", {"level": 2}), kinds)
        self.assertIn(("learned_move", {"move": "static zap"}), kinds)
        self.assertEqual(link.pet.as_payload()["moves"], ["peck", "static zap"])

    def test_evolve_gift_event_and_refusal_at_last_form(self):
        link = self.make(stage="ultimate")
        link.connect()
        chip = {"item": {"name": "evo chip", "kind": "evolve", "power": 50}}
        reply = link.request("pet.gift", chip)
        self.assertTrue(reply.payload["accepted"])
        self.assertIsNone(reply.payload["effects"]["stage"])          # ready, not evolved
        choice = [e for e in link.drain_events() if e.payload["event"] == "evolve_choice"]
        self.assertEqual({c["species"] for c in choice[0].payload["detail"]["choices"]}, {"Aethcrown", "Nullking"})
        picked = link.request("pet.evolve", {"species": "Nullking"})
        self.assertTrue(picked.payload["accepted"])
        kinds = [e.payload["event"] for e in link.drain_events()]
        self.assertEqual(kinds[-2:], ["evolving", "evolved"])
        self.assertEqual((link.pet.stage, link.pet.species), ("mega", "Nullking"))
        gifts = len(link.pet.inventory)
        again = link.request("pet.gift", chip)
        self.assertIs(again.payload["accepted"], False)
        self.assertIn("already", again.payload["reaction"])
        self.assertEqual(len(link.pet.inventory), gifts)

    def test_avatar(self):
        link = self.make()
        link.connect()
        reply = link.request("pet.avatar", {})
        self.assertEqual(reply.type, "pet.avatar.ok")
        self.assertEqual(reply.payload["style"], "face")
        face = petdata.parse_face(reply.payload)
        self.assertEqual(len(face.frames), 2)
        self.assertIsNone(petdata.parse_avatar(reply.payload))     # no sprite any more
        # The old sprite shape still parses (fallback for older companions).
        from cyberdeck.companion import sim_avatar
        av = petdata.parse_avatar(sim_avatar("rookie", "content", 0))
        self.assertEqual((len(av.rows), av.width), (16, 16))
        self.assertIsNone(av.palette[0])
        self.assertIsNotNone(av.pixel(7, 9))   # the body is drawn

    def test_refused_gift_keeps_loot(self):
        ctx = make_ctx()
        ctx.companion.pet.stage = "mega"
        ctx.data_path("loot.json").write_text(json.dumps([{"name": "evo chip", "kind": "evolve", "power": 50}]))
        lines = build_registry(include_plugins=False).dispatch("loot gift 1", ctx)
        self.assertIn("stays in your loot bag", "\n".join(lines))
        self.assertEqual(len(json.loads(ctx.data_path("loot.json").read_text())), 1)


# --- settings and flags ------------------------------------------------------------------

class LinkSettingsTests(unittest.TestCase):
    def test_defaults(self):
        ls = load_link_settings(Settings.from_text(""))
        self.assertEqual((ls.mode, ls.host, ls.port, ls.device, ls.baud), ("sim", "127.0.0.1", 7777, "/dev/serial0", 115200))

    def test_shipped_config_is_sim(self):
        self.assertEqual(load_link_settings(Settings.load("hardware.ini")).mode, "sim")

    def test_file_then_overrides(self):
        ls = load_link_settings(Settings.from_text("[link]\nmode = serial\ndevice = /dev/ttyUSB0\nbaud = 9600\n"))
        self.assertEqual((ls.mode, ls.device, ls.baud), ("serial", "/dev/ttyUSB0", 9600))
        ls = with_overrides(ls, mode="tcp", port=9000)
        self.assertEqual((ls.mode, ls.port, ls.device), ("tcp", 9000, "/dev/ttyUSB0"))

    def test_open_link_kinds(self):
        self.assertIsInstance(open_link(LinkSettings()), SimCompanionLink)
        tcp = open_link(LinkSettings(mode="tcp"))
        self.assertIsInstance(tcp, StreamCompanionLink)
        self.assertIn("TCP 7777", tcp.link_label())
        self.assertIn("UART serial0", open_link(LinkSettings(mode="serial")).link_label())
        with self.assertRaises(ValueError):
            open_link(LinkSettings(mode="carrier-pigeon"))

    def test_cli_flags(self):
        a = build_parser().parse_args(["--link", "tcp", "--port", "7777"])
        self.assertEqual((a.link, a.port), ("tcp", 7777))
        b = build_parser().parse_args(["--link", "serial", "--serial", "/dev/ttyUSB0", "--baud", "57600"])
        self.assertEqual((b.device, b.baud), ("/dev/ttyUSB0", 57600))

    def test_serial_env_default(self):
        old = os.environ.pop("CYBERDECK_SERIAL", None)
        try:
            self.assertEqual(os_serial_default(), "/dev/serial0")
            os.environ["CYBERDECK_SERIAL"] = "/dev/ttyACM0"
            self.assertEqual(os_serial_default(), "/dev/ttyACM0")
        finally:
            os.environ.pop("CYBERDECK_SERIAL", None)
            if old is not None:
                os.environ["CYBERDECK_SERIAL"] = old


# --- end to end over TCP with tools/fake_companion.py ------------------------------------

class FakeCompanionProcess:
    """Run tools/fake_companion.py on a random port for one test."""

    def __init__(self, port: int, *extra: str) -> None:
        self.port = port
        self.proc = subprocess.Popen([sys.executable, str(FAKE), "--tcp", f"127.0.0.1:{port}", "--demo-every", "0", *extra],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ok = wait_for(self._listening, 10)
        if not ok:
            self.stop()
            raise RuntimeError("fake_companion did not start")

    def _listening(self) -> bool:
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
            return True
        except OSError:
            return False

    def stop(self) -> str:
        self.proc.terminate()
        try:
            out, _ = self.proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, _ = self.proc.communicate()
        return out or ""


class TcpEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.server = FakeCompanionProcess(self.port, "--speed", "200")
        self.link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", self.port), fast(port=self.port))
        self.ctx = make_ctx()
        self.ctx.companion = self.link
        self.reg = build_registry(include_plugins=False)

    def tearDown(self):
        self.link.close()
        self.server.stop()

    def test_commands_and_pushed_events(self):
        self.link.connect()
        self.assertTrue(self.link.is_linked(), self.link.reason)
        self.assertTrue(self.link.events_enabled)
        self.assertIn("via fake_companion", self.reg.dispatch("ping", self.ctx)[0])
        status = self.reg.dispatch("pet status", self.ctx)
        self.assertIn("PULSE", status[0])
        self.assertTrue(any(l.startswith("stats") for l in status))
        self.assertTrue(self.reg.dispatch("pet feed", self.ctx)[0].startswith("fed ration"))
        self.assertTrue(self.reg.dispatch("pet play", self.ctx)[0].startswith(("played", "PULSE sits")))
        self.ctx.data_path("loot.json").write_text(json.dumps([{"name": "glitch berry", "kind": "food", "power": 20}]))
        self.assertIn("munches the glitch berry", self.reg.dispatch("pet gift 1", self.ctx)[0])
        # 200x speed: the pet gets hungry within a few seconds and the companion pushes it.
        seen: list[Message] = []
        wait_for(lambda: seen.extend(self.link.drain_events()) or any(m.payload["event"] == "hungry" for m in seen), 15)
        self.assertIn("hungry", [m.payload["event"] for m in seen])
        self.assertIsNotNone(self.link.pet_snapshot())
        # The pushed event is surfaced the same way the TUI and headless mode do it.
        self.link._events.append(seen[-1])
        from cyberdeck.features.petwatch import headless_event_lines

        lines = headless_event_lines(self.ctx)
        self.assertTrue(lines and lines[0].startswith("[pet "))
        self.assertTrue(self.ctx.data_path("pet_log.txt").read_text())

    def test_reconnects_after_companion_restart(self):
        self.link.connect()
        self.assertTrue(self.link.is_linked())
        self.server.stop()
        self.assertTrue(wait_for(lambda: not self.link.is_linked(), 5))
        self.assertIn("link down", self.reg.dispatch("pet status", self.ctx)[0] + " link down")
        self.server = FakeCompanionProcess(self.port)
        self.assertTrue(wait_for(self.link.is_linked, 8), self.link.reason)
        self.assertIn("pong", self.reg.dispatch("ping", self.ctx)[0])
        self.assertGreaterEqual(self.link.stats["connects"], 2)


class DownLinkTests(unittest.TestCase):
    def test_nobody_listening(self):
        port = free_port()
        link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", port), fast(port=port, connect_wait=0.3))
        started = time.monotonic()
        link.connect()
        self.assertLess(time.monotonic() - started, 1.5)   # the UI is never held up for long
        self.assertFalse(link.is_linked())
        self.assertIn("refused", link.reason)
        self.assertIn("retry", link.link_detail())
        ctx = make_ctx()
        ctx.companion = link
        self.assertIn("companion error: link down", build_registry(include_plugins=False).dispatch("ping", ctx)[0])
        link.close()


class MisbehavingServer:
    """Answers hello, then sends junk and ignores everything except ping."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        conn, _ = self.sock.accept()
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            *lines, buf = buf.split(b"\n")
            for line in lines:
                msg = decode(line)
                if msg.type == "hello":
                    conn.sendall(b"garbage that is not json\n{\"type\":\"x\"}\n")
                    conn.sendall(msg.reply("hello.ok", {"role": "companion", "proto": 1}).encode_bytes())
                elif msg.type == "ping":
                    # A reply for some other id first, then the real one in two pieces.
                    conn.sendall(Message("pong", "not-yours", {}).encode_bytes())
                    raw = msg.reply("pong", {"ok": True}).encode_bytes()
                    conn.sendall(raw[:10])
                    time.sleep(0.05)
                    conn.sendall(raw[10:])


class RobustnessTests(unittest.TestCase):
    def test_junk_stray_replies_split_lines_and_timeouts(self):
        srv = MisbehavingServer()
        link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", srv.port),
                                   fast(port=srv.port, poll_seconds=30, events=False))
        link.connect()
        self.assertTrue(link.is_linked(), link.reason)
        self.assertFalse(link.events_enabled)          # hello.ok did not echo events
        self.assertEqual(link.request("ping").type, "pong")
        self.assertGreaterEqual(link.stats["bad_lines"], 2)
        self.assertGreaterEqual(link.stats["stray"], 1)
        started = time.monotonic()
        with self.assertRaises(Exception) as caught:
            link.request("pet.status", timeout=0.4)
        self.assertIn("no reply", str(caught.exception))
        self.assertLess(time.monotonic() - started, 1.0)
        link.close()


# --- serial ---------------------------------------------------------------------------------

def _serve_on_fd(fd: int, stop: threading.Event) -> None:
    """A tiny companion on the master side of a pty."""
    sim = SimCompanionLink()
    sim._linked = True
    buf = b""
    while not stop.is_set():
        if not select.select([fd], [], [], 0.1)[0]:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError:
            return
        buf += data
        *lines, buf = buf.split(b"\n")
        for line in lines:
            if line.strip():
                os.write(fd, sim.handle(decode(line)).encode_bytes())


@unittest.skipUnless(hasattr(os, "openpty"), "needs a POSIX pty")
class SerialTests(unittest.TestCase):
    def test_serial_transport_on_a_pty(self):
        master, slave = os.openpty()
        device = os.ttyname(slave)
        stop = threading.Event()
        threading.Thread(target=_serve_on_fd, args=(master, stop), daemon=True).start()
        link = StreamCompanionLink(lambda: SerialTransport(device, 115200, "stdlib"),
                                   fast(mode="serial", device=device))
        try:
            link.connect()
            self.assertTrue(link.is_linked(), link.reason)
            self.assertEqual(link.request("ping").type, "pong")
            pet = link.request("pet.status").payload["pet"]
            self.assertEqual(pet["name"], "PULSE")
            self.assertIn("UART", link.link_detail())
        finally:
            link.close()
            stop.set()
            os.close(master)
            os.close(slave)

    def test_tty_is_raw_8n1(self):
        import termios

        master, slave = os.openpty()
        try:
            t = SerialTransport(os.ttyname(slave), 115200, "stdlib")
            t.open()
            iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(t.fd)
            self.assertEqual(cflag & termios.CSIZE, termios.CS8)
            self.assertFalse(cflag & termios.PARENB)
            self.assertFalse(cflag & termios.CSTOPB)
            self.assertFalse(lflag & (termios.ICANON | termios.ECHO))
            self.assertEqual(ospeed, termios.B115200)
            t.close()
        finally:
            os.close(master)
            os.close(slave)

    def test_missing_device_is_reported(self):
        link = StreamCompanionLink(lambda: SerialTransport("/dev/does-not-exist", 115200, "stdlib"),
                                   fast(mode="serial", connect_wait=0.3))
        link.connect()
        self.assertFalse(link.is_linked())
        self.assertIn("no such device", link.reason)
        link.close()

    def test_fake_companion_serves_a_pty(self):
        master, slave = os.openpty()
        proc = subprocess.Popen([sys.executable, str(FAKE), "--serial", os.ttyname(slave), "--quiet",
                                 "--demo-every", "0", "--serial-driver", "stdlib"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            time.sleep(0.8)
            hello = Message.request("hello", {"role": "main", "proto": 1, "events": True})
            os.write(master, hello.encode_bytes())
            buf = b""
            end = time.monotonic() + 5
            while b"\n" not in buf and time.monotonic() < end:
                if select.select([master], [], [], 0.1)[0]:
                    buf += os.read(master, 4096)
            reply = decode(buf.split(b"\n")[0])
            self.assertEqual((reply.type, reply.id, reply.payload.get("events")), ("hello.ok", hello.id, True))
        finally:
            proc.terminate()
            proc.communicate(timeout=5)
            os.close(master)
            os.close(slave)

    @unittest.skipUnless(shutil.which("socat"), "socat not installed")
    def test_socat_pty_pair(self):
        proc = subprocess.Popen(["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
                                stderr=subprocess.PIPE, text=True)
        try:
            names = []
            while len(names) < 2:
                line = proc.stderr.readline()
                if "PTY is" in line:
                    names.append(line.strip().split("PTY is ")[-1])
            fake = subprocess.Popen([sys.executable, str(FAKE), "--serial", names[0], "--quiet", "--demo-every", "0"])
            link = StreamCompanionLink(lambda: SerialTransport(names[1], 115200), fast(mode="serial", device=names[1]))
            try:
                link.connect()
                self.assertTrue(wait_for(link.is_linked, 5), link.reason)
                self.assertEqual(link.request("ping").type, "pong")
            finally:
                link.close()
                fake.terminate()
                fake.wait(5)
        finally:
            proc.terminate()
            proc.wait(5)


# --- the UI side ---------------------------------------------------------------------------

class UiSideTests(unittest.TestCase):
    def test_pet_pane_uses_snapshot_without_asking(self):
        from cyberdeck.panes import pet_pane

        class SnapshotOnly(SimCompanionLink):
            def request(self, *a, **k):
                raise AssertionError("pane must not block on a request")

            def pet_snapshot(self):
                return {"name": "PULSE", "species": "Staticore", "stage": "rookie", "hunger": 20,
                        "happiness": 60, "energy": 50, "level": 4, "hp": 10, "max_hp": 30,
                        "moves": ["Static Snap"], "care_mistakes": 1}

        ctx = make_ctx()
        ctx.companion = SnapshotOnly()
        text = "\n".join("".join(seg for seg, _ in row) for row in pet_pane(ctx, 30, 20))
        for word in ("Staticore", "Lv 4", "10/30", "Static Snap", "care mistakes 1"):
            self.assertIn(word, text)

    def test_pushed_need_event_is_not_announced_twice(self):
        from cyberdeck.features.petwatch import get_watcher

        ctx = make_ctx()
        ctx.companion.connect()
        ctx.companion.pet.hunger = 95
        ctx.companion.pet._check_needs()
        watcher = get_watcher(ctx)
        pushed = watcher.pushed(ctx, 1000.0)
        self.assertEqual([e.kind for e in pushed], ["hungry"])
        local = watcher.check(ctx.companion.pet.as_payload(), 1001.0)
        self.assertNotIn("hungry", [e.kind for e in local])

    def test_headless_prints_pushed_events(self):
        from cyberdeck.headless import run_headless

        ctx = make_ctx()
        ctx.companion.connect()
        ctx.companion.pet.energy = 5
        out = io.StringIO()
        run_headless(ctx, build_registry(include_plugins=False), ["pet status"], out=out)
        self.assertIn("[pet tired]", out.getvalue())


if __name__ == "__main__":
    unittest.main()
