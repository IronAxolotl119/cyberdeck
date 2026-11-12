"""petwatch.py — PULSE alerts and the pet event log.

While the full-screen deck is running, a PetWatcher asks the companion
how PULSE is doing every few seconds and:

  * raises an ALERT when PULSE is hungry, bored, or tired — the ticker
    line flashes, a badge appears on the status bar, and (optionally)
    the buzzer chirps. It reminds you again every `repeat_minutes`
    while the problem lasts.
  * writes a line to data/pet_log.txt for every evolution, mood change,
    alert, recovery, feed, play, and gift.

Commands added to `pet`:
  pet log [n]          the last n log lines (default 15)
  pet log clear        empty the log
  pet alerts           check right now and show the alert state
  pet sim <stat> <n>   sim mode only: set hunger / happiness / energy /
                       stage / discoveries / level, or `ready yes` to
                       make PULSE ready to evolve, to try alerts and
                       evolutions quickly

Thresholds live in config/pet.ini. check() is pure (pet dict + time in,
events out), so the alert rules are unit-tested.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cyberdeck.commands import PET_ACTIONS, PET_HOOKS
from cyberdeck.config import Settings
from cyberdeck.protocol import TYPE_PET_STATUS

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


@dataclass(frozen=True)
class AlertSettings:
    """config/pet.ini [alerts] and [log]."""

    enabled: bool = True
    poll_seconds: float = 10.0
    hungry_at: int = 75       # alert when hunger is at or above this
    bored_at: int = 35        # alert when happiness is at or below this
    tired_at: int = 15        # alert when energy is at or below this
    repeat_minutes: float = 10.0
    sound: str = "beep"       # none, beep, or the name of a song in songs/
    log_max: int = 2000       # lines kept in data/pet_log.txt


def load_alert_settings(settings: Settings | None = None) -> AlertSettings:
    s = settings or Settings.load("pet.ini")
    d = AlertSettings()
    return AlertSettings(
        enabled=s.get_bool("alerts", "enabled", d.enabled),
        poll_seconds=max(1.0, s.get_float("alerts", "poll_seconds", d.poll_seconds)),
        hungry_at=s.get_int("alerts", "hungry_at", d.hungry_at),
        bored_at=s.get_int("alerts", "bored_at", d.bored_at),
        tired_at=s.get_int("alerts", "tired_at", d.tired_at),
        repeat_minutes=s.get_float("alerts", "repeat_minutes", d.repeat_minutes),
        sound=s.get_str("alerts", "sound", d.sound).lower(),
        log_max=s.get_int("log", "max_lines", d.log_max),
    )


@dataclass(frozen=True)
class PetEvent:
    """Something worth telling you about."""

    kind: str     # evolve, hungry, bored, tired, recovered, mood, feed, play, gift
    level: str    # alert, warn, ok, info, quiet (quiet = log only)
    text: str


# The three needs we watch: (name, payload key, test, message).
CONDITIONS = (
    ("hungry", "hunger", lambda v, s: v >= s.hungry_at, "{name} is hungry! (hunger {v}) — try `pet feed`"),
    ("bored", "happiness", lambda v, s: v <= s.bored_at, "{name} is bored (happiness {v}) — try `pet play`"),
    ("tired", "energy", lambda v, s: v <= s.tired_at, "{name} is worn out (energy {v}) — let it rest"),
)


# Pushed event names -> the deck's own alert names (see CONDITIONS).
PUSHED_TO_LOCAL = {"hungry": "hungry", "sad": "bored", "tired": "tired"}


class PetLog:
    """data/pet_log.txt: 'YYYY-MM-DD HH:MM:SS | kind | text' per line."""

    def __init__(self, path: Path, max_lines: int = 2000) -> None:
        self.path = path
        self.max_lines = max(10, max_lines)

    def add(self, kind: str, text: str, when: float | None = None) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} | {kind:<9} | {text}\n")
            # Trim now and then so the file cannot grow forever.
            if self.path.stat().st_size > self.max_lines * 120:
                lines = self.read()
                self.path.write_text("".join(l + "\n" for l in lines[-self.max_lines:]), encoding="utf-8")
        except OSError:
            pass

    def read(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def clear(self) -> None:
        try:
            self.path.write_text("", encoding="utf-8")
        except OSError:
            pass


@dataclass
class PetWatcher:
    """Polls the pet, turns changes into events, logs and announces them."""

    settings: AlertSettings
    log: PetLog
    latest: dict[str, Any] | None = None
    active: set[str] = field(default_factory=set)          # alerts currently true
    _last_alert: dict[str, float] = field(default_factory=dict)
    _next_poll: float = 0.0
    _next_push_check: float = 0.0
    asleep: bool = False                                    # zZ on the status bar

    def check(self, pet: dict[str, Any], now: float) -> list[PetEvent]:
        """Compare a fresh pet payload with the previous one. Pure logic."""
        events: list[PetEvent] = []
        name = pet.get("name", "PULSE")
        before = self.latest
        if before:
            if pet.get("stage") != before.get("stage"):
                events.append(PetEvent("evolve", "ok", f"{name} evolved: {before.get('stage')} → {pet.get('stage')}!"))
            if pet.get("mood") != before.get("mood"):
                events.append(PetEvent("mood", "quiet", f"mood {before.get('mood')} → {pet.get('mood')}"))
        repeat = self.settings.repeat_minutes * 60
        for kind, key, test, message in CONDITIONS:
            value = pet.get(key)
            if value is None:
                continue
            if test(value, self.settings):
                last = self._last_alert.get(kind)
                if kind not in self.active or (repeat > 0 and last is not None and now - last >= repeat):
                    level = "alert" if kind == "hungry" else "warn"
                    events.append(PetEvent(kind, level, message.format(name=name, v=value)))
                    self._last_alert[kind] = now
                self.active.add(kind)
            elif kind in self.active:
                self.active.discard(kind)
                events.append(PetEvent("recovered", "info", f"{name} is no longer {kind}"))
        if isinstance(pet.get("asleep"), bool):
            self.asleep = pet["asleep"]
        self.latest = pet
        return events

    def poll(self, ctx: "Context", now: float) -> list[PetEvent]:
        """Check the newest pet (the link's background copy, or ask)."""
        pet = ctx.companion.pet_snapshot()
        if pet is None:
            try:
                reply = ctx.companion.request(TYPE_PET_STATUS, {})
            except Exception:
                return []   # link trouble just means no events
            pet = (reply.payload or {}).get("pet")
        return self.check(pet, now) if pet else []

    def pushed(self, ctx: "Context", now: float) -> list[PetEvent]:
        """Events the companion pushed (pet.event) since last time.

        Evolution news also goes to the pop-up queue (full-screen deck only).

        Each pushed snapshot also refreshes the pet panel, and need events
        count as already-announced so the deck's own threshold check does
        not say the same thing twice.
        """
        from cyberdeck.petdata import event_notice
        from cyberdeck.popups import POPUP_KINDS, get_popups, popup_for_event

        events: list[PetEvent] = []
        for message in ctx.companion.drain_events():
            payload = message.payload or {}
            kind, level, text = event_notice(payload)
            events.append(PetEvent(kind, level, text))
            local = PUSHED_TO_LOCAL.get(kind)
            if local:
                self.active.add(local)
                self._last_alert[local] = now
            if ctx.interactive and kind in POPUP_KINDS:
                popup = popup_for_event(payload)
                if popup is not None:
                    get_popups(ctx).push(popup)
            if kind == "woke":
                self.asleep = False
            pet = payload.get("pet")
            if isinstance(pet, dict):
                if isinstance(pet.get("asleep"), bool):
                    self.asleep = pet["asleep"]
                ctx.services["pet_cache"] = {"at": time.monotonic(), "pet": pet}
                if self.latest is not None:
                    # Remember the new stage/mood so check() does not re-announce them.
                    self.latest = dict(self.latest, stage=pet.get("stage"), mood=pet.get("mood"))
        return events

    def handle(self, ctx: "Context", events: list[PetEvent], now: float) -> None:
        """Log every event; announce the important ones."""
        for event in events:
            self.log.add(event.kind, event.text, now)
            if event.level != "quiet":
                ctx.notify(event.text, event.level)
            if event.level == "alert":
                self._sound(ctx)

    def ui_tick(self, ctx: "Context", now: float) -> None:
        """Called by the TUI every frame: pushed events now, polls every poll_seconds."""
        if not self.settings.enabled:
            return
        if now >= self._next_push_check:
            self._next_push_check = now + 0.5
            self.handle(ctx, self.pushed(ctx, now), now)
        if now < self._next_poll:
            return
        self._next_poll = now + self.settings.poll_seconds
        self.handle(ctx, self.poll(ctx, now), now)

    def status_badge(self) -> tuple[str, str] | None:
        """Short text for the status bar: alerts, and zZ while PULSE sleeps."""
        if not self.active:
            return ("zZ ASLEEP", "dim") if self.asleep else None
        text = "! " + "/".join(sorted(self.active)).upper()
        return (text + " zZ" if self.asleep else text), "alert"

    def _sound(self, ctx: "Context") -> None:
        sound = self.settings.sound
        if sound in {"", "none", "off", "no"}:
            return
        try:
            if sound == "beep":
                from cyberdeck.features.hardware import get_hardware

                import threading
                threading.Thread(target=get_hardware(ctx).beep, args=(1320.0, 90.0), daemon=True).start()
            else:
                from cyberdeck.features.music import get_player, load_song

                get_player(ctx).play(load_song(ctx, sound))
        except Exception:
            pass  # a missing song or busy buzzer must not break alerts


def get_watcher(ctx: "Context") -> PetWatcher:
    """The shared watcher. Creating it is what switches alerts on in the TUI."""
    def build() -> PetWatcher:
        settings = load_alert_settings(Settings.load("pet.ini", ctx.config_dir))
        return PetWatcher(settings, PetLog(ctx.data_path("pet_log.txt"), settings.log_max))
    return ctx.service("petwatch", build)


# --- pet subcommands --------------------------------------------------------

def pet_log(ctx: "Context", args: list[str]) -> list[str]:
    log = get_watcher(ctx).log
    if len(args) > 1 and args[1] == "clear":
        log.clear()
        return ["pet log cleared."]
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 15
    lines = log.read()[-count:]
    return lines or ["(the pet log is empty — events appear while the deck runs)"]


def pet_alerts(ctx: "Context", args: list[str]) -> list[str]:
    del args
    watcher = get_watcher(ctx)
    now = time.time()
    events = watcher.poll(ctx, now)
    for event in events:
        watcher.log.add(event.kind, event.text, now)
    s = watcher.settings
    lines = [f"alerts {'on' if s.enabled else 'off'}: hungry ≥{s.hungry_at}  bored ≤{s.bored_at}  "
             f"tired ≤{s.tired_at}  every {s.poll_seconds:.0f}s, repeat {s.repeat_minutes:g} min, sound {s.sound}"]
    lines += [f"  {e.text}" for e in events if e.level != "quiet"]
    lines.append("active: " + (", ".join(sorted(watcher.active)) or "none — PULSE is fine"))
    return lines


SIM_STATS = {"hunger": "hunger", "happiness": "happiness", "happy": "happiness", "energy": "energy"}


def pet_sim(ctx: "Context", args: list[str]) -> list[str]:
    pet = getattr(ctx.companion, "pet", None)
    if pet is None:
        return ["pet sim only works with the simulated companion (--mode sim)"]
    if len(args) < 3:
        return ["usage: pet sim hunger|happiness|energy <0-100> · stage <name> · ready yes|no · "
                "discoveries|level|care_mistakes <n>"]
    stat, value = args[1].lower(), args[2]
    if stat == "ready" and hasattr(pet, "stage_hours"):
        pet.stage_hours = 10_000.0 if value in {"yes", "on", "1"} else 0.0
        ctx.services.pop("pet_cache", None)
        return [f"sim: evolution {'ready' if pet.stage_hours else 'reset'} — evolve_choice is pushed on the next tick"]
    if stat in {"discoveries", "level", "care_mistakes"} and value.isdigit():
        setattr(pet, stat, int(value))
        return [f"sim: {stat} = {value}"]
    if stat == "stage":
        pet._evolve_to(value) if hasattr(pet, "_evolve_to") else setattr(pet, "stage", value)
        ctx.services.pop("pet_cache", None)
        return [f"sim: stage = {value}"]
    if stat not in SIM_STATS or not value.isdigit():
        return ["usage: pet sim hunger|happiness|energy <0-100>"]
    setattr(pet, SIM_STATS[stat], float(max(0, min(100, int(value)))))
    ctx.services.pop("pet_cache", None)
    return [f"sim: {SIM_STATS[stat]} = {value}"]


def log_pet_action(ctx: "Context", action: str, payload: dict) -> None:
    """PET_HOOKS listener: write feeds, plays, and gifts to the log."""
    if action == "feed":
        text = f"fed {payload.get('item', '?')} (hunger {payload.get('hunger_delta', '?')})"
    elif action == "play":
        text = (f"played {payload.get('game', '?')}" if payload.get("played", True)
                else f"refused to play ({payload.get('reason', '?')})")
    elif action == "rest":
        text = (f"rested (energy {payload.get('energy_delta', '?'):+})" if isinstance(payload.get("energy_delta"), int)
                and payload.get("rested", True) else f"could not rest ({payload.get('reason', '?')})")
    elif action == "sleep":
        text = "fell asleep" if payload.get("asleep") else "woke up"
    elif action == "evolve":
        text = (f"evolved {payload.get('from', '?')} → {payload.get('to', '?')}" if payload.get("accepted", True)
                else f"evolve refused: {payload.get('reason', '?')}")
    elif action == "combat":
        text = (f"dungeon run {payload.get('result', '?')}: +{payload.get('xp_gained', 0)} xp"
                + (f", {payload['level_ups']} level-up(s)" if payload.get("level_ups") else ""))
    elif action == "gift":
        item = payload.get("item") or {}
        verb = "got a gift" if payload.get("accepted", True) else "turned down a gift"
        text = f"{verb}: {item.get('name', '?')} — {payload.get('reaction', '')}".rstrip(" —")
    else:
        text = action
    get_watcher(ctx).log.add(action, text)


def register(reg: "Registry") -> None:
    """Hook into the pet command (no new top-level command)."""
    del reg
    from cyberdeck.features import UI_STARTUP

    PET_ACTIONS.update({"log": pet_log, "alerts": pet_alerts, "sim": pet_sim})
    if log_pet_action not in PET_HOOKS:
        PET_HOOKS.append(log_pet_action)
    if get_watcher not in UI_STARTUP:
        UI_STARTUP.append(get_watcher)  # start background alerts with the UI


def headless_event_lines(ctx: "Context") -> list[str]:
    """For headless mode: pushed events as '[pet] …' lines (also logged)."""
    watcher = get_watcher(ctx)
    now = time.time()
    events = watcher.pushed(ctx, now)
    for event in events:
        watcher.log.add(event.kind, event.text, now)
    return [f"[pet {event.kind}] {event.text}" for event in events]
