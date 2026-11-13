"""service.py — LcdService: the background worker that runs the LCD.

In plain English: the rest of the deck only ever calls a few quick,
non-blocking methods on this object:

    lcd.notify(text, title=…, priority=…)   something happened
    lcd.add_blurb(text)                     PULSE said something
    lcd.set_pet(pet) / set_link(…) / set_battery(…) / set_field_mode(…)

and a background thread (start()) does the slow part about 4 times a
second: pick what to show (notification queue, else the idle rotation),
scroll long lines, blink the backlight, and write only the characters
that changed to the real LCD over I2C. A slow or missing screen can
never freeze the interface.

ui_tick(ctx, now) is called by the deck every frame (both the Rich
interface and --classic) and feeds PULSE's status, the link state and
the battery in. In tests and docs snapshots (threaded=False) ui_tick
also advances the screen with the fake clock, so pictures are repeatable.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .backends import I2cLcd, LcdHardwareError, SimScreen
from .messages import ALERT, INFO, MANUAL, NOTICE, PRIORITY_NAMES, LcdMessage, NotifyQueue
from .pages import BlurbPool, Screen, blurb_page, clock_page, layout_message, lines_at, load_blurbs, pulse_page, \
    screen_seconds
from .settings import LcdSettings, parse_address
from .text import G, to_lcd

# ---------------------------------------------------------------------------
# What each event looks like on the screen: (line 1 title, priority).
# The title is LCD text: {glyph} tokens work. Line 2 is the event's text.
# ---------------------------------------------------------------------------
PET_TITLES = {
    "hungry": ("{food}{name} HUNGRY", NOTICE),
    "tired": ("{bolt}{name} TIRED", NOTICE),
    "bored": ("{heart}{name} BORED", NOTICE),
    "sad": ("{heart}{name} SAD", NOTICE),
    "evolve_soon": ("{diamond}EVOLVING SOON", NOTICE),
    "evolve_choice": ("{bell}PICK EVOLUTION", ALERT),
    "evolving": ("{diamond}EVOLVING!", NOTICE),
    "evolved": ("{diamond}EVOLVED!", NOTICE),
    "level_up": ("{diamond}LEVEL UP!", NOTICE),
    "learned_move": ("{diamond}NEW MOVE!", NOTICE),
    "move_strengthened": ("{diamond}MOVE UP!", NOTICE),
    "woke": ("{face}{name} AWAKE", INFO),
    "signal_found": ("{wifi}SIGNAL FOUND", INFO),
    # The companion's "life" events (companion/docs/ALIVE.md). Each one
    # carries a ready-made `say` line; the ones listed here also pop up,
    # every other one (speech, idle, dream, greeting…) just becomes a blurb.
    "ask": ("{bell}{name} ASKS", NOTICE),
    "sick": ("{heart}{name} IS SICK", NOTICE),
    "tantrum": ("{bell}TANTRUM!", NOTICE),
    "treasure": ("{diamond}TREASURE!", NOTICE),
    "adventure_return": ("{face}{name} IS BACK", NOTICE),
    "rival_appears": ("{bell}RIVAL!", NOTICE),
    "birthday": ("{heart}BIRTHDAY!", NOTICE),
    "hatchday": ("{heart}HATCHDAY!", NOTICE),
    "egg_hatched": ("{diamond}EGG HATCHED!", NOTICE),
    "suspicious": ("{wifi}{name} IS WARY", NOTICE),
}

ALERT_TITLES = {
    "evil_twin": "EVIL TWIN?", "unknown_tag": "UNKNOWN TAG", "open_network": "OPEN WIFI",
    "suspicious_name": "BAIT NAME?", "rssi_jump": "SIGNAL JUMP", "new_host": "NEW LAN HOST",
    "mac_changed": "MAC CHANGED", "long_stay": "BT LINGERING", "new": "NEW", "new_ssid": "NEW WIFI",
}
KIND_GLYPH = {"wifi": "{wifi}", "bt": "{wifi}", "lan": "{wifi}", "rfid": "{bell}"}


class LcdService:
    """Owns the screen (sim copy + optional real LCD) and its thread."""

    def __init__(self, settings: LcdSettings | None = None, threaded: bool = True,
                 blurbs: dict[str, list[str]] | None = None, base_dir: str | Path | None = None,
                 chirp: Callable[[], None] | None = None, hardware=None, mirror: str | Path | None = None,
                 rng=None) -> None:
        self.s = s = settings or LcdSettings()
        self.threaded = threaded
        self.enabled = s.backend != "none"
        self.screen = SimScreen(s.cols, s.rows, mirror or s.sim_mirror or None)
        self.hw = hardware
        if self.hw is None and s.backend == "i2c":
            self.hw = I2cLcd(s.cols, s.rows, parse_address(s.address), s.port, s.driver, s.charmap, s.expander,
                             [a for a in (parse_address(c) for c in s.candidates) if a is not None])
        self.hw_error = ""
        self.chirp = chirp
        self.queue = NotifyQueue(s.dedupe_seconds, s.max_per_minute, s.burst, s.max_queue, s.field_min_priority,
                                 s.quiet_enabled, s.quiet_start, s.quiet_end, s.quiet_allow_priority)
        if blurbs is None:
            path = Path(s.blurb_file)
            if not path.is_absolute():
                path = Path(base_dir or Path.cwd()) / path
            blurbs = load_blurbs(path)
        self.blurbs = BlurbPool(blurbs, s.reaction_minutes, rng)

        # --- what the screen knows about the world (fed by ui_tick) ---------
        self.pet: dict[str, Any] = {}
        self.link_text = "LINK ?"
        self.linked: bool | None = None
        self.battery: float | None = None
        self._battery_low_sent = False

        # --- what is on screen right now ---------------------------------------
        self.current: LcdMessage | None = None
        self.current_screen: Screen | None = None
        self.shown_at = 0.0
        self.until = 0.0
        self.page_index = -1
        self.page: Screen | None = None
        self.page_at = 0.0
        self.page_until = 0.0
        self.flash_until = 0.0
        self.manual_light = s.backlight if s.backlight in ("on", "off") else "auto"
        self.last_activity = 0.0
        self.shown: deque[tuple[float, str, str]] = deque(maxlen=20)   # (when, kind, text) actually displayed
        self._written: list[str | None] = [None] * s.rows
        self._light: bool | None = None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_feed = 0.0
        self._next_battery = 0.0
        self._next_status_ask = 0.0

    # =====================================================================
    # lifecycle
    # =====================================================================
    def start(self) -> None:
        """Open the real LCD (if any) and start the thread (threaded mode)."""
        if not self.enabled or not self.threaded or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="lcd", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._close_hw()

    def _open_hw(self) -> None:
        if self.hw is None or getattr(self.hw, "_started", False):
            return
        try:
            self.hw.start()
            self.hw._started = True
            self.hw_error = ""
        except (LcdHardwareError, OSError, ImportError) as exc:
            self.hw_error = str(exc)
            self.hw = None              # keep going on the sim copy only
        except Exception as exc:  # noqa: BLE001 — any driver surprise
            self.hw_error = f"{type(exc).__name__}: {exc}"
            self.hw = None

    def _close_hw(self) -> None:
        if self.hw is not None and getattr(self.hw, "_started", False):
            try:
                self.hw.close()
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        self._open_hw()
        period = 1.0 / self.s.fps
        while not self._stop.is_set():
            try:
                self.tick(time.time())
            except Exception as exc:  # noqa: BLE001 — never let the LCD thread die silently
                self.hw_error = f"{type(exc).__name__}: {exc}"
                self.hw = None
            self._stop.wait(period)

    # =====================================================================
    # inputs (quick, thread-safe, never block)
    # =====================================================================
    def notify(self, text: str, title: str = "", priority: int = NOTICE, kind: str = "info", key: str = "",
               hold: float | None = None, now: float | None = None) -> str:
        """Queue a notification. Returns 'ok' or why it was skipped."""
        if not self.enabled:
            return "off"
        now = time.time() if now is None else now
        msg = LcdMessage(str(text), str(title), priority, kind, key, hold)
        with self._lock:
            return self.queue.push(msg, now)

    def say(self, text: str, seconds: float | None = None, now: float | None = None) -> str:
        """`lcd say`: show this now, past every filter."""
        self.interrupt_manual()
        return self.notify(text, "", MANUAL, "say", hold=seconds, now=now)

    def interrupt_manual(self) -> None:
        """A new `lcd say` / `lcd test` replaces the previous one right away."""
        with self._lock:
            self.queue.items = [m for m in self.queue.items if m.priority < MANUAL]
            if self.current is not None and self.current.priority >= MANUAL:
                self.until = 0.0

    def add_blurb(self, text: str, now: float | None = None) -> None:
        """A PULSE line for the idle rotation (reactions, event text)."""
        with self._lock:
            self.blurbs.add_reaction(text, time.time() if now is None else now)

    def set_pet(self, pet: dict | None) -> None:
        if isinstance(pet, dict) and pet:
            with self._lock:
                self.pet = dict(pet)

    def set_field_mode(self, on: bool) -> None:
        self.queue.field_mode = bool(on)

    def set_backlight(self, mode: str) -> None:
        """'on', 'off' or 'auto' (auto = on, flashes, quiet hours / dim rules)."""
        with self._lock:
            self.manual_light = mode if mode in ("on", "off", "auto") else "auto"

    def set_link(self, linked: bool, label: str = "", now: float | None = None) -> None:
        """Link up / down: notify on a change (not on the first reading)."""
        now = time.time() if now is None else now
        self.link_text = ("LINK " + ("UP" if linked else "DOWN")) if not label else label
        if self.linked is not None and linked != self.linked and self.s.link_changes:
            if linked:
                self.notify("companion connected", "{wifi}LINK UP", NOTICE, "link", "link:up", now=now)
            else:
                self.notify("PULSE is out of reach", "{bell}LINK DOWN", ALERT, "link", "link:down", now=now)
        self.linked = linked

    def set_battery(self, percent: float | None, now: float | None = None) -> None:
        self.battery = percent
        if percent is None or not self.s.battery_low:
            return
        if percent <= self.s.battery_low and not self._battery_low_sent:
            self._battery_low_sent = True
            self.notify(f"deck battery {percent:.0f}% - plug in soon", "{bolt}BATTERY LOW", ALERT, "battery",
                        "battery:low", now=now)
        elif percent >= self.s.battery_low + 5:
            self._battery_low_sent = False

    # --- higher-level feeds ---------------------------------------------------
    def pet_event(self, kind: str, text: str, payload: dict | None = None, now: float | None = None) -> str:
        """A PULSE event (pushed pet.event or the deck's own need alert)."""
        kind = (kind or "").lower()
        payload = payload or {}
        detail = payload.get("detail") or {}
        if isinstance(payload.get("pet"), dict):
            self.set_pet(payload["pet"])
        name = str((payload.get("pet") or {}).get("name") or self.pet.get("name") or "PULSE")
        say = detail.get("say") or payload.get("say")
        if isinstance(say, str) and say.strip():
            text = say                       # the companion's own ready-made line
        move = detail.get("move") or detail.get("name")
        if kind == "move_strengthened" and move:
            text = f"{name}'s {move} is now rank {detail.get('rank', '?')}" + \
                   (f" (+{detail['bonus']} power)" if detail.get("bonus") is not None else "")
        self.add_blurb(text, now)
        if kind not in self.s.pet_events or kind not in PET_TITLES:
            return "skip"
        title, priority = PET_TITLES[kind]
        key_bit = (move or detail.get("level") or detail.get("to") or detail.get("form") or detail.get("kind")
                   or detail.get("rival") or detail.get("place") or "")
        return self.notify(text, title.replace("{name}", to_lcd(name)[:7]), priority, "pet",
                           f"pet:{kind}:{key_bit}", now=now)

    def discovery_reaction(self, res: dict, now: float | None = None) -> None:
        """A pet.discovery.ok reply: the reaction joins the blurbs; big news pops up."""
        reaction = str(res.get("reaction") or "")
        if isinstance(res.get("pet"), dict):
            self.set_pet(res["pet"])
        if reaction and "already logged" not in reaction.lower():
            self.add_blurb(reaction, now)
        if not self.s.reactions:
            return
        name = str(self.pet.get("name") or "PULSE")
        if res.get("learned_move"):
            self.notify(f"{name} learned {res['learned_move']}!", "{diamond}NEW MOVE!", NOTICE, "pet",
                        f"pet:learned_move:{res['learned_move']}", now=now)
        if res.get("strengthened_move"):
            self.notify(f"{res['strengthened_move']} got stronger!", "{diamond}MOVE UP!", NOTICE, "pet",
                        f"pet:move_strengthened:{res['strengthened_move']}", now=now)
        if res.get("level_up"):
            self.notify(f"{name} reached level {res.get('level', '?')}!", "{diamond}LEVEL UP!", NOTICE, "pet",
                        f"pet:level_up:{res.get('level', '')}", now=now)

    def battle_say(self, text: str, foe: str = "", now: float | None = None) -> str:
        """PULSE's newest battle line: a blurb, and a pop-up rate-limited like other notices."""
        text = str(text or "").strip()
        if not text:
            return "skip"
        self.add_blurb(text, now)
        if not self.s.battle:
            return "skip"
        title = "{bell}VS " + to_lcd(str(foe or "FOE")).upper()[:12]
        with self._lock:                 # only the newest battle line waits in the queue
            self.queue.items = [m for m in self.queue.items if not m.key.startswith("battle:")]
        return self.notify(text, title, NOTICE, "pet", f"battle:{text}", now=now)

    def awareness_alert(self, kind: str, event: str, label: str, ident: str = "", interest: int = 2,
                        now: float | None = None) -> str:
        """An Awareness & Diagnostics alert (already passed the AlertFilter)."""
        if not self.s.awareness_alerts:
            return "skip"
        name = ALERT_TITLES.get(event, event.replace("_", " ").upper())
        if event == "new":
            name = f"NEW {kind.upper()}"
        title = KIND_GLYPH.get(kind, "{bell}") + name
        if kind == "lan" and ident:
            body = f"{ident} {label}".strip()   # IP first, then the MAC
        elif not label or label.lower() in name.lower() or label.lower().startswith("unknown"):
            body = ident or label           # e.g. UNKNOWN TAG / de:ad:10:42
        else:
            body = label if not ident or ident == label else f"{label} {ident}"
        priority = ALERT if interest >= 3 else NOTICE if interest == 2 else INFO
        return self.notify(body, title, priority, "alert", f"alert:{kind}:{event}:{ident or label}", now=now)

    # =====================================================================
    # ui_tick: called by the deck each frame (UI thread) — cheap feeding
    # =====================================================================
    def ui_tick(self, ctx, now: float) -> None:
        if not self.enabled:
            return
        if now >= self._next_feed:
            self._next_feed = now + 0.5
            self._feed_from(ctx, now)
        if not self.threaded:
            self.tick(now)

    def _feed_from(self, ctx, now: float) -> None:
        link = getattr(ctx, "companion", None)
        if link is not None:
            try:
                self.set_link(bool(link.is_linked()), "", now)
            except Exception:  # noqa: BLE001
                pass
            pet = None
            try:
                pet = link.pet_snapshot()
            except Exception:  # noqa: BLE001
                pet = None
            cache = (getattr(ctx, "services", {}) or {}).get("pet_cache")
            if pet is None and isinstance(cache, dict):
                pet = cache.get("pet")
            if pet is None and now >= self._next_status_ask and _is_sim(link):
                # The in-process sim pet answers instantly; real links are
                # never asked from here (pet_snapshot covers them).
                self._next_status_ask = now + 5
                try:
                    pet = (link.request("pet.status", {}).payload or {}).get("pet")
                except Exception:  # noqa: BLE001
                    pet = None
            self.set_pet(pet)
        sensors = getattr(ctx, "sensors", None)
        if sensors is not None and now >= self._next_battery:
            self._next_battery = now + 30
            try:
                self.set_battery(sensors.read().battery_percent, now)
            except Exception:  # noqa: BLE001
                pass

    # =====================================================================
    # tick: decide what is on the glass (LCD thread, or ui_tick in tests)
    # =====================================================================
    def tick(self, now: float) -> list[str]:
        with self._lock:
            s = self.s
            # 1) a notification that has had its time goes away
            if self.current is not None and now >= self.until:
                self.current = None
                self.current_screen = None
                self.page_until = 0.0            # start a fresh idle page
            # 2) a waiting notification shows if nothing is up, or it outranks the current one
            nxt = self.queue.peek()
            if nxt is not None and (self.current is None or nxt.priority > self.current.priority):
                cut = self.current
                if cut is not None and not getattr(cut, "requeued", False) and \
                        now - self.shown_at < (self.until - self.shown_at) / 2:
                    cut.requeued = True          # interrupted early: it gets one more turn afterwards
                    self.queue.items.append(cut)
                self._show(self.queue.pop(), now)
            # 3) idle rotation
            if self.current is None and now >= self.page_until:
                self._next_page(now)
            if self.current is not None:
                lines = lines_at(self.current_screen, now - self.shown_at, s.cols, s.rows, s.fps, s.scroll_pause)
            else:
                lines = lines_at(self.page, now - self.page_at, s.cols, s.rows, s.fps, s.scroll_pause)
            light = self._backlight(now)
        self._write(lines, light)
        return lines

    def _show(self, msg: LcdMessage, now: float) -> None:
        s = self.s
        self.current = msg
        self.current_screen = layout_message(msg, s.cols, s.rows)
        self.shown_at = now
        base = msg.hold if msg.hold is not None else (s.alert_hold_seconds if msg.priority >= ALERT else s.hold_seconds)
        self.until = now + screen_seconds(self.current_screen, s.cols, s.fps, s.scroll_pause, base)
        self.last_activity = now
        self.shown.appendleft((now, msg.kind, msg.preview()))
        quiet = self.queue.quiet(now) and msg.priority < MANUAL
        want_flash = msg.flash if msg.flash is not None else msg.priority >= NOTICE
        if s.flash and want_flash and not quiet and s.flash_times:
            self.flash_until = now + s.flash_times * 0.5
        if s.chirp and self.chirp and msg.priority >= s.chirp_priority and not quiet:
            try:
                self.chirp()
            except Exception:  # noqa: BLE001 — a busy buzzer must not break the LCD
                pass

    def _next_page(self, now: float) -> None:
        s = self.s
        order = [p for p in s.pages if p in ("pulse", "blurb", "clock")] or ["clock"]
        self.page_index = (self.page_index + 1) % len(order)
        kind = order[self.page_index]
        if kind == "pulse":
            screen = pulse_page(self.pet, s.cols, s.rows)
        elif kind == "blurb":
            screen = blurb_page(self.blurbs.next(self.pet, now), self.pet, s.cols, s.rows)
        else:
            screen = clock_page(now, self.link_text, self.battery, s.clock_format, s.cols, s.rows)
        self.page, self.page_at = screen, now
        self.page_until = now + screen_seconds(screen, s.cols, s.fps, s.scroll_pause, s.rotate_seconds)

    def _backlight(self, now: float) -> bool:
        if self.manual_light == "off":
            return False
        if now < self.flash_until:
            # Blink: off for the first half of every half-second.
            return int((self.flash_until - now) * 4) % 2 == 0
        if self.manual_light == "on":
            return True
        if self.current is not None:
            return True
        if self.queue.quiet(now):
            return False
        if self.s.dim_after_minutes and now - self.last_activity > self.s.dim_after_minutes * 60:
            return False
        return True

    def _write(self, lines: list[str], light: bool) -> None:
        """Push only what changed, to the sim copy and the real LCD."""
        for row, text in enumerate(lines):
            if text != self._written[row]:
                self._written[row] = text
                self.screen.write_line(row, text)
                if self.hw is not None and getattr(self.hw, "_started", False):
                    try:
                        self.hw.write_line(row, text)
                    except OSError as exc:      # loose wire: keep the sim going, report it
                        self.hw_error = f"write failed: {exc}"
        if light != self._light:
            self._light = light
            self.screen.set_backlight(light)
            if self.hw is not None and getattr(self.hw, "_started", False):
                try:
                    self.hw.set_backlight(light)
                except OSError as exc:
                    self.hw_error = f"backlight failed: {exc}"

    # =====================================================================
    # reading (status command, tile, tests)
    # =====================================================================
    def backend_text(self) -> str:
        if not self.enabled:
            return "none (switched off in config/lcd.ini)"
        if self.s.backend == "sim":
            return "sim (pretend 16x2 on screen)"
        if self.hw is not None:
            return self.hw.describe()
        return f"i2c FAILED - showing the sim copy only: {self.hw_error}"

    def status_lines(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        q = self.queue
        lines = [f"lcd: {self.backend_text()}",
                 f"  {self.s.cols}x{self.s.rows} · backlight {self.manual_light}"
                 f"{' (now ' + ('on' if self.screen.backlight else 'off') + ')'}"
                 f" · field mode {'on' if q.field_mode else 'off'}"
                 f" · quiet hours {(q.quiet_start + '-' + q.quiet_end) if q.quiet_enabled else 'off'}"
                 f"{' (quiet now)' if q.quiet(now) else ''}"]
        if self.hw_error and self.s.backend == "i2c":
            lines.append(f"  hardware: {self.hw_error}")
        lines += self.screen.render(ascii_only=False)
        showing = (f"notification ({PRIORITY_NAMES.get(self.current.priority, '?')})" if self.current
                   else f"idle page: {self.page.name if self.page else '-'}")
        counts = ", ".join(f"{k} {v}" for k, v in sorted(q.counts.items()))
        lines.append(f"  showing {showing} · {len(q)} waiting" + (f" · {counts}" if counts else ""))
        recent = [f"  {time.strftime('%H:%M:%S', time.localtime(t))} {d:<5} {txt}" for t, d, txt in list(q.history)[:5]]
        if recent:
            lines.append("  recent (decision · text):")
            lines += recent
        return lines


def _is_sim(link) -> bool:
    from cyberdeck.companion import SimCompanionLink

    inner = getattr(link, "inner", link)
    return isinstance(inner, SimCompanionLink)


