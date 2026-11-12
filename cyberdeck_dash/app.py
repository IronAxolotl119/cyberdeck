"""The Dashboard: state, scan scheduling, key handling, and wiring.

In plain English: this is the conductor. It owns the scanners, the rule
trackers, the journal, field mode, the pet adapter and the checklist,
and connects them through the event bus:

    scanner -> tracker (rules.py) -> "finding" -> journal, pet XP,
                                                 activity mood, alert filter
                                                       -> "alert" -> ticker/LED

It knows nothing about drawing; ui/screen.py reads its state each frame.
run() (in __main__.py) calls step() in a loop and hands it the keys.
Tests call step(now) directly with a fake clock and threaded=False.
"""

from __future__ import annotations

import json
import queue
import random
import threading
import time
from collections import deque
from typing import Any

from .config import DashConfig
from .diag import Diagnostics
from .events import (HIGH, TOPIC_ALERT, TOPIC_FINDING, TOPIC_LOG, TOPIC_PET_EVENT, TOPIC_PET_REACTION,
                     TOPIC_SCAN_REQUEST, EventBus, Finding)
from .field import AlertFilter
from .journal import Journal
from .pet import CompanionLink, PetAdapter
from .ratelimit import Cooldown
from .rules import BtTracker, LanTracker, RfidTracker, WifiTracker, XpRules, activity_mood, move_scanner
from .scanners import ScanResult, Scanner, build_scanners
from .scanners.rfid import SimRfidScanner
from .sysinfo import SystemInfo

SCANNERS = ("wifi", "bt", "lan", "rfid")
# Panel key -> config section for its scanner interval.
SECTION = {"wifi": "wifi", "bt": "bluetooth", "lan": "lan", "rfid": "rfid"}


class ScanWorker(threading.Thread):
    """Runs one scanner on its own thread so a slow scan never freezes the UI."""

    def __init__(self, scanner: Scanner, interval_fn, out: "queue.Queue[ScanResult]") -> None:
        super().__init__(daemon=True, name=f"scan-{scanner.name}")
        self.scanner, self.interval_fn, self.out = scanner, interval_fn, out
        self.wake = threading.Event()
        self.stopped = threading.Event()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.out.put(self.scanner.scan())
            except Exception as exc:  # a broken backend shows in its panel
                self.out.put(ScanResult(self.scanner.name, error=str(exc)[:60], simulated=self.scanner.simulated))
            self.wake.wait(self.interval_fn())
            self.wake.clear()


class Dashboard:
    def __init__(self, cfg: DashConfig, mode: str | None = None, seed: int | None = None,
                 threaded: bool = True, companion: bool | None = None, journal_path=None) -> None:
        self.cfg = cfg
        self.mode = mode or cfg.get("dash", "mode")
        self.rng = random.Random(seed)
        self.threaded = threaded
        self.bus = EventBus()

        # --- scanners + their rule trackers -----------------------------------
        self.scanners = build_scanners(cfg, self.mode, self.rng)
        self.wifi = WifiTracker(cfg.int("wifi", "rssi_jump_db"), cfg.int("wifi", "gone_after_scans"),
                                cfg.int("wifi", "history_len"), cfg.list("wifi", "suspicious_patterns"),
                                cfg.trusted_wifi())
        self.bt = BtTracker(cfg.float("bluetooth", "dwell_minutes") * 60, cfg.float("bluetooth", "forget_after"),
                            cfg.list("bluetooth", "known"))
        self.lan = LanTracker()
        self.tags_path = cfg.path("rfid", "inventory")
        self.rfid = RfidTracker(self._load_tags(), cfg.float("rfid", "absent_after"))
        self.last_scan: dict[str, float] = {}
        self.scan_errors: dict[str, str] = {}
        self.scan_counts: dict[str, int] = {k: 0 for k in SCANNERS}
        self.next_due: dict[str, float] = {}
        self.manual = Cooldown(cfg.float("field", "manual_scan_cooldown"))

        # --- journal, alerts, field mode --------------------------------------
        jpath = journal_path if journal_path is not None else cfg.path("journal", "path")
        self.journal = Journal(jpath or None, cfg.int("journal", "keep"))
        self.filter = AlertFilter(cfg.int("field", "alert_interest"), cfg.int("field", "normal_alert_interest"),
                                  cfg.float("field", "cooldown"), cfg.float("field", "max_alerts_per_minute"),
                                  cfg.int("field", "burst"))
        self.alerts: deque[Finding] = deque(maxlen=30)
        self.ticker: deque[tuple[float, str, str]] = deque(maxlen=12)   # (ts, style-key, text)
        self.activity: deque[tuple[float, int]] = deque(maxlen=500)     # (ts, interest)

        # --- PULSE ------------------------------------------------------------------
        xp = XpRules({k: cfg.int("xp", k) for k in SCANNERS}, cfg.int("xp", "interest_bonus"),
                     cfg.float("xp", "learn_chance"))
        use_link = cfg.bool("companion", "enabled") if companion is None else companion
        self.link = (CompanionLink(cfg.get("companion", "host"), cfg.int("companion", "port"),
                                   cfg.float("companion", "timeout"), cfg.float("companion", "poll"))
                     if use_link else None)
        self.pet = PetAdapter(xp, self.link, random.Random(self.rng.random()))
        self.pet.on_reaction = lambda res: self.bus.publish(TOPIC_PET_REACTION, res)
        self.pet.on_event = lambda ev: self.bus.publish(TOPIC_PET_EVENT, ev)

        # --- UI state -----------------------------------------------------------------
        # Tabs ("groups"): [(title, [panel keys], columns)]. The first is
        # "Awareness & Diagnostics"; focus is an index inside the current tab.
        self.tabs = cfg.tabs()
        self.tab = 0
        self.focus = 0
        start = cfg.get("dash", "start_panel")
        for ti, (_, keys, _) in enumerate(self.tabs):
            if start in keys:
                self.tab, self.focus = ti, keys.index(start)
                break
        self.zoomed = False
        self.menu_open = False
        self.menu_index = 0
        self.show_help = False
        self.theme_names = list(cfg.themes)
        self.theme_name = cfg.get("dash", "theme") if cfg.get("dash", "theme") in cfg.themes else self.theme_names[0]
        self.checklist = [[item, False] for item in cfg.list("checklist", "items")]
        self.check_index = 0
        self.prompt: dict[str, Any] | None = None     # active text entry (RFID note)
        self.running = True
        diag_backend = cfg.get("diag", "backend").lower()
        diag_sim = self.mode == "sim" if diag_backend == "auto" else diag_backend == "sim"
        self.diag = Diagnostics(simulated=diag_sim, rng=random.Random(self.rng.random()))
        self.sys = SystemInfo(random.Random(self.rng.random()))
        self.sysinfo: dict = {}
        self._last_slow = 0.0
        self.led = None
        self.now = time.time()          # the step's clock; panels read this
        self.screen_width: int | None = None  # set by the runner; small screens drop extras
        self._baseline: set[str] = set()  # scanners still on their first scan

        # --- wiring: who listens to what -------------------------------------------
        self.bus.subscribe(TOPIC_FINDING, self._on_finding)
        self.bus.subscribe(TOPIC_ALERT, self._on_alert)
        self.bus.subscribe(TOPIC_PET_REACTION, self._on_reaction)
        self.bus.subscribe(TOPIC_PET_EVENT, self._on_pet_event)
        self.bus.subscribe(TOPIC_SCAN_REQUEST, self._on_scan_request)
        self.bus.subscribe(TOPIC_LOG, lambda text: self.log(text))

        self._results: "queue.Queue[ScanResult]" = queue.Queue()
        self.workers: dict[str, ScanWorker] = {}

    # =====================================================================
    # lifecycle
    # =====================================================================
    def start(self) -> None:
        if self.link:
            self.link.start()
        if self.threaded:
            for name, sc in self.scanners.items():
                w = ScanWorker(sc, lambda n=name: self.interval(n), self._results)
                self.workers[name] = w
                w.start()
        self.log(f"deck online · {self.mode.upper()} mode · {len(self.journal.entries)} journal lines loaded")

    def stop(self) -> None:
        for w in self.workers.values():
            w.stopped.set()
            w.wake.set()
        if self.link:
            self.link.stop()

    @property
    def panels(self) -> list[str]:
        """Panel keys of the current tab."""
        return self.tabs[self.tab][1]

    @property
    def tab_title(self) -> str:
        return self.tabs[self.tab][0]

    @property
    def columns(self) -> int:
        return max(1, self.tabs[self.tab][2])

    def switch_tab(self, step: int = 1) -> None:
        self.tab = (self.tab + step) % len(self.tabs)
        self.focus, self.zoomed = 0, False

    def go_to_panel(self, key: str, zoom: bool = True) -> None:
        for ti, (_, keys, _) in enumerate(self.tabs):
            if key in keys:
                self.tab, self.focus, self.zoomed = ti, keys.index(key), zoom
                return

    @property
    def theme(self):
        return self.cfg.theme(self.theme_name)

    def interval(self, name: str) -> float:
        """Seconds between scans; field mode speeds up the radio scanners."""
        base = self.cfg.float(SECTION[name], "interval")
        if self.filter.field_mode and name != "rfid":
            return min(base, self.cfg.float("field", "scan_interval"))
        return base

    # =====================================================================
    # the per-frame step
    # =====================================================================
    def step(self, now: float | None = None, keys: list[str] | None = None) -> None:
        now = time.time() if now is None else now
        self.now = now
        for key in keys or []:
            self.handle_key(key, now)
        if not self.threaded:
            for name, sc in self.scanners.items():
                if now >= self.next_due.get(name, 0):
                    self.next_due[name] = now + self.interval(name)
                    self._results.put(sc.scan())
        while not self._results.empty():
            self.handle_result(self._results.get_nowait(), now)
        self.pet.drain()
        if now - self._last_slow >= 1.0:          # once a second is plenty
            self._last_slow = now
            self.sysinfo = self.sys.read()
            self.diag.refresh_ifaces(now)
            self._update_mood(now)
            self.pet.tick()

    def handle_result(self, res: ScanResult, now: float) -> None:
        name = res.scanner
        self.last_scan[name] = now
        self.scan_counts[name] = self.scan_counts.get(name, 0) + 1
        self.scan_errors[name] = res.error
        if res.error:
            return
        # The very first scan just learns the baseline: it is journaled and
        # earns XP, but only HIGH findings alert (no flood at start-up).
        first_scan = self.scan_counts[name] == 1
        if first_scan:
            self._baseline.add(name)
        if name == "wifi":
            findings = self.wifi.update(res.items, now)
        elif name == "bt":
            findings = self.bt.update(res.items, now)
        elif name == "lan":
            findings = self.lan.update(res.items, now)
        elif name == "rfid":
            findings = self.rfid.update([t.uid for t in res.items], now)
        else:
            findings = []
        for f in findings:
            self.bus.publish(TOPIC_FINDING, f)
        self._baseline.discard(name)

    # =====================================================================
    # bus handlers
    # =====================================================================
    def _on_finding(self, f: Finding) -> None:
        self.journal.record(f)
        self.activity.append((f.ts, f.interest))
        self.pet.on_finding(f)
        if f.kind in self._baseline and f.interest < HIGH:
            return
        if self.filter.check(f, f.ts):
            self.bus.publish(TOPIC_ALERT, f)

    def _on_alert(self, f: Finding) -> None:
        self.alerts.appendleft(f)
        self.log(f"ALERT {f.kind.upper()} {f.event.replace('_', ' ')}: {f.label}", "bad" if f.interest >= HIGH else "warn")
        if self.led:
            self.led.blink()

    def _on_reaction(self, res: dict) -> None:
        """Show a pet.discovery result: XP, reaction, moves, level-up.

        The companion reply (or the local fallback) is already in `res`.
        The ticker, the journal and the PULSE panel all read from it.
        """
        reaction = res.get("reaction") or "PULSE reacts"
        self.log(reaction, "accent2")
        detail = {
            "xp": res.get("xp", 0),
            "level": res.get("level"),
            "level_up": bool(res.get("level_up")),
            "learned_move": res.get("learned_move"),
            "strengthened_move": res.get("strengthened_move"),
            "reaction": reaction,
            "source": res.get("source"),
        }
        # One journal line for the discovery itself (XP + reaction).
        interest = 2 if (res.get("level_up") or res.get("learned_move") or res.get("strengthened_move")) else 1
        self.journal.record(Finding("pet", "discovery", "pulse", reaction, interest, detail))
        if res.get("learned_move"):
            self.journal.record(Finding("pet", "learned_move", res["learned_move"], res["learned_move"], 2,
                                        {"source": res.get("source")}))
        if res.get("strengthened_move"):
            self.journal.record(Finding("pet", "move_strengthened", res["strengthened_move"],
                                        res["strengthened_move"], 2, {"source": res.get("source")}))
        if res.get("level_up"):
            level = res.get("level", "?")
            self.journal.record(Finding("pet", "level_up", "pulse", f"level {level}", 2,
                                        {"level": level, "source": res.get("source")}))
            if "level" not in reaction.lower():
                self.log(f"◆ PULSE reached level {level}!", "accent2")

    def _on_pet_event(self, ev: dict) -> None:
        detail = ev.get("detail") or {}
        name = (ev.get("pet") or {}).get("name", "PULSE")
        move = detail.get("move") or detail.get("name")
        rank, bonus = detail.get("rank"), detail.get("bonus")
        strengthened = f"{name} strengthened {move}"
        if rank is not None:
            strengthened += f" (rank {rank}" + (f", +{bonus}" if bonus is not None else "") + ")"
        strengthened += "!"
        text = {
            "hungry": f"{name} is hungry!", "tired": f"{name} is tired", "sad": f"{name} feels sad",
            "evolved": f"{name} evolved into {detail.get('to') or detail.get('form') or '???'}!",
            "level_up": f"{name} reached level {detail.get('level', '?')}!",
            "learned_move": f"{name} learned {move}!",
            "move_strengthened": strengthened,
            "signal_found": f"{name} sniffed out {detail.get('ssid') or detail.get('name') or 'a signal'}",
        }.get(ev.get("event"), f"{name}: {detail.get('text') or ev.get('event')}")
        self.log("◆ " + text, "accent2")
        short = (move or detail.get("ssid") or detail.get("to") or detail.get("level") or text)
        self.journal.record(Finding("pet", str(ev.get("event")), name, str(short), 2, {"detail": detail, "text": text}))

    def _on_scan_request(self, name: str) -> None:
        for n in (SCANNERS if name == "all" else (name,)):
            if n not in self.scanners:
                continue
            if self.threaded and n in self.workers:
                self.workers[n].wake.set()
            else:
                self.next_due[n] = 0

    def _update_mood(self, now: float) -> None:
        window = self.cfg.float("xp", "activity_window")
        recent = [i for t, i in self.activity if now - t <= window]
        mood, buff = activity_mood(len(recent), sum(1 for i in recent if i >= HIGH), self.cfg.int("xp", "busy_at"))
        self.pet.set_environment(mood, buff)

    def log(self, text: str, style: str = "text") -> None:
        self.ticker.appendleft((self.now, style, text))

    # =====================================================================
    # actions (keys and GPIO buttons land here)
    # =====================================================================
    def scan_now(self, name: str = "all", now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.manual.ready(f"scan:{name}", now):
            self.log(f"scan cooling down ({self.manual.remaining(f'scan:{name}', now):.0f}s)", "dim")
            return False
        self.bus.publish(TOPIC_SCAN_REQUEST, name)
        self.log(f"manual scan → {name}", "accent")
        return True

    def use_move(self, now: float | None = None) -> str | None:
        move = self.pet.selected_move()
        if not move:
            return None
        target = move_scanner(move)
        if self.scan_now(target, now):
            self.log(self.pet.use_move(move) + f"  ⟶ fresh {target} scan", "accent2")
        return move

    def toggle_field(self) -> None:
        self.filter.field_mode = not self.filter.field_mode
        state = "ON · fast scans, HIGH alerts only" if self.filter.field_mode else "OFF"
        self.log(f"FIELD MODE {state}", "good" if self.filter.field_mode else "dim")
        self._on_scan_request("all")

    def tap_tag(self) -> None:
        sc = self.scanners.get("rfid")
        if isinstance(sc, SimRfidScanner):
            uid = sc.tap()
            self.log(f"(sim) tag held on reader: {uid}", "dim")
            self._on_scan_request("rfid")
        else:
            self.log("hold a tag on the reader", "dim")

    def save_note(self, uid: str, text: str) -> None:
        self.rfid.note(uid, text)
        try:
            self.tags_path.parent.mkdir(parents=True, exist_ok=True)
            self.tags_path.write_text(json.dumps(self.rfid.known, indent=2) + "\n")
        except OSError:
            pass
        self.log(f"tag {uid} saved: {text}", "good")

    def _load_tags(self) -> dict[str, str]:
        try:
            return json.loads(self.tags_path.read_text())
        except (OSError, ValueError):
            return {"04:a2:19:7f": "front door fob", "93:5e:01:c4": "transit card"}

    # --- menu ------------------------------------------------------------------
    def menu_items(self) -> list[tuple[str, str]]:
        """(label, action) pairs for the main menu, grouped by tab.
        Rows whose action is "" are group headings (not selectable)."""
        from .ui.panels import PANELS  # local import: ui depends on app, not vice versa
        items: list[tuple[str, str]] = []
        for title, keys, _ in self.tabs:
            items.append((f"── {title.upper()} ──", ""))
            for k in keys:
                items.append((f"   {PANELS[k][1] if k in PANELS else '•'} {PANELS[k][0] if k in PANELS else k}",
                              f"panel:{k}"))
            if "diag" in keys:
                items += [("   ⇢ ping a host…", "ping"), ("   ⇢ DNS lookup…", "dns"),
                          ("   ⇢ interface stats", "panel:diag")]
        items += [("── DECK ACTIONS ──", ""), ("   s  scan everything", "scan"), ("   u  use move", "move"),
                  ("   f  field mode on/off", "field"), ("   t  next theme", "theme"), ("   ?  help", "help"),
                  ("   q  quit", "quit")]
        return items

    def _menu_move(self, step: int) -> None:
        items = self.menu_items()
        for _ in range(len(items)):
            self.menu_index = (self.menu_index + step) % len(items)
            if items[self.menu_index][1]:
                return

    def _run_menu(self, action: str, now: float) -> None:
        self.menu_open = False
        if not action:
            return
        if action.startswith("panel:"):
            self.go_to_panel(action.split(":", 1)[1])
        elif action in ("ping", "dns"):
            self.go_to_panel("diag")
            self.open_prompt(action)
        else:
            self.handle_key({"scan": "s", "move": "u", "field": "f", "theme": "t", "help": "?", "quit": "q"}[action], now)

    # --- text prompts (RFID note, ping target, DNS name) -----------------------
    def open_prompt(self, kind: str) -> None:
        if kind == "note":
            uid = self.rfid.last_uid
            if not uid:
                self.log("no tag read yet: tap one first (r in sim)", "dim")
                return
            self.prompt = {"kind": "note", "uid": uid, "label": f"note for {uid}", "text": self.rfid.known.get(uid, "")}
        elif kind == "ping":
            self.prompt = {"kind": "ping", "label": "ping host", "text": self.cfg.get("diag", "ping_default")}
        elif kind == "dns":
            self.prompt = {"kind": "dns", "label": "DNS lookup", "text": self.cfg.get("diag", "dns_default")}

    def _submit_prompt(self, p: dict) -> None:
        text = p["text"].strip()
        if not text:
            return
        if p["kind"] == "note":
            self.save_note(p["uid"], text)
        else:
            self.log(self.diag.start(p["kind"], text), "accent")

    # --- keys --------------------------------------------------------------------
    def handle_key(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.prompt is not None:             # typing a note
            p = self.prompt
            if key == "enter":
                self.prompt = None
                self._submit_prompt(p)
            elif key == "esc":
                self.prompt = None
            elif key == "backspace":
                p["text"] = p["text"][:-1]
            elif len(key) == 1 and key.isprintable():
                p["text"] += key
            return
        if self.menu_open:
            items = self.menu_items()
            if key in ("up", "k"):
                self._menu_move(-1)
            elif key in ("down", "j", "tab"):
                self._menu_move(1)
            elif key == "enter":
                self._run_menu(items[self.menu_index][1], now)
            elif key in ("esc", "m"):
                self.menu_open = False
            elif key == "q":
                self.running = False
            return
        panel = self.panels[self.focus]
        if key == "q":
            self.running = False
        elif key.isdigit() and 1 <= int(key) <= len(self.panels):
            idx = int(key) - 1
            self.zoomed = self.zoomed and idx == self.focus
            self.focus = idx
        elif key in ("tab", "right", "l"):
            self.focus = (self.focus + 1) % len(self.panels)
        elif key in ("left", "h"):
            self.focus = (self.focus - 1) % len(self.panels)
        elif key in ("up", "down") and panel == "checklist" and self.checklist:
            step = -1 if key == "up" else 1
            self.check_index = (self.check_index + step) % len(self.checklist)
        elif key in ("[", "]", "g"):
            self.switch_tab(-1 if key == "[" else 1)
        elif key in ("up", "down"):
            cols = self.columns
            self.focus = (self.focus + (-cols if key == "up" else cols)) % len(self.panels)
        elif key in ("enter", "z"):
            self.zoomed = not self.zoomed
        elif key == "esc":
            self.zoomed = False
            self.show_help = False
        elif key == "m":
            self.menu_open, self.menu_index = True, 0
            self._menu_move(1)
        elif key == "s":
            self.scan_now(panel if panel in SCANNERS else "all", now)
        elif key == "u":
            self.use_move(now)
        elif key == "v":
            mv = self.pet.next_move()
            self.log(f"move selected: {mv}", "accent2")
        elif key == "f":
            self.toggle_field()
        elif key == "t":
            i = self.theme_names.index(self.theme_name)
            self.theme_name = self.theme_names[(i + 1) % len(self.theme_names)]
            self.log(f"theme → {self.theme_name}", "accent")
        elif key == "?":
            self.show_help = not self.show_help
        elif key == " " and panel == "checklist" and self.checklist:
            item = self.checklist[self.check_index]
            item[1] = not item[1]
        elif key == "x" and panel == "checklist":
            for item in self.checklist:
                item[1] = False
        elif key == "r":
            self.tap_tag()
        elif key == "n":
            self.open_prompt("note")
        elif key == "p":
            self.open_prompt("ping")
        elif key == "d":
            self.open_prompt("dns")
