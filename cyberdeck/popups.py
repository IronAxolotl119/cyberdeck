"""popups.py — boxed pop-ups over the deck for PULSE's big moments.

When the companion pushes evolve_soon, evolve_choice, evolving, or
evolved, the full-screen deck shows a centred box over everything:

  * it goes away by itself after `seconds` (config/pet.ini [popups]),
    or when you press any key;
  * that key is NOT eaten: it still reaches the prompt (or whatever you
    were typing), so a pop-up can never steal your input;
  * several at once wait in a queue and show one after another;
  * "evolving" plays a little shimmer animation.

Headless mode prints the same events as plain '[pet evolving] …' lines.
PopupQueue is pure logic (time is passed in), so it is unit-tested.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cyberdeck.activity import Row
from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context

# Pushed pet.event kinds that get a pop-up.
POPUP_KINDS = ("evolve_soon", "evolve_choice", "evolving", "evolved")


@dataclass(frozen=True)
class PopupSettings:
    """config/pet.ini [popups]."""

    enabled: bool = True
    seconds: float = 10.0     # auto-dismiss after this long (0 = wait for a key)
    max_queue: int = 6        # older pop-ups are dropped beyond this


def load_popup_settings(settings: Settings) -> PopupSettings:
    d = PopupSettings()
    return PopupSettings(
        enabled=settings.get_bool("popups", "enabled", d.enabled),
        seconds=max(0.0, settings.get_float("popups", "seconds", d.seconds)),
        max_queue=max(1, settings.get_int("popups", "max_queue", d.max_queue)),
    )


@dataclass
class Popup:
    """One box: a title, a few lines, and whether to animate."""

    kind: str
    title: str
    lines: list[str]
    animate: bool = False


def popup_for_event(payload: dict[str, Any]) -> Popup | None:
    """A pet.event payload -> Popup (None for kinds that do not pop up)."""
    from cyberdeck.petdata import choice_line, evolution_choices, normalize_pet

    kind = str(payload.get("event") or "")
    if kind not in POPUP_KINDS:
        return None
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    pet = normalize_pet(payload.get("pet"))
    name = pet["name"]
    if kind == "evolve_soon":
        from cyberdeck.petdata import _eta

        nxt = detail.get("next_stage") or "its next stage"
        return Popup(kind, "EVOLUTION SOON", [f"{name} is close to evolving!",
                                              f"{detail.get('from') or pet['species']} → {nxt}{_eta(detail)}"])
    if kind == "evolve_choice":
        choices = evolution_choices(detail.get("choices"))
        lines = [f"{name} is ready to evolve!"]
        lines += [("✓ " if c["available"] else "× ") + choice_line(c) for c in choices]
        lines.append("type `pet evolve` to choose")
        return Popup(kind, "READY TO EVOLVE", lines)
    if kind == "evolving":
        return Popup(kind, "EVOLVING", [f"{name} is evolving…",
                                        f"{detail.get('from') or pet['species']} → {detail.get('to') or '?'}"],
                     animate=True)
    new = detail.get("to") or detail.get("form") or pet["species"]
    stage = detail.get("stage") or pet["stage"]
    return Popup(kind, "EVOLVED!", [f"{name} evolved into {new}!", f"stage: {stage}"])


@dataclass
class PopupQueue:
    """Pop-ups waiting to be shown, and the one on screen now."""

    settings: PopupSettings = field(default_factory=PopupSettings)
    waiting: deque = field(default_factory=deque)
    current: Popup | None = None
    shown_at: float = 0.0

    def push(self, popup: Popup) -> None:
        if not self.settings.enabled:
            return
        self.waiting.append(popup)
        while len(self.waiting) > self.settings.max_queue:
            self.waiting.popleft()

    def active(self, now: float) -> Popup | None:
        """The pop-up to draw right now (handles time-outs and the queue)."""
        if self.current is not None and self.settings.seconds > 0 and now - self.shown_at >= self.settings.seconds:
            self.current = None
        if self.current is None and self.waiting:
            self.current, self.shown_at = self.waiting.popleft(), now
        return self.current

    def dismiss(self) -> bool:
        """Close the visible pop-up (a key was pressed). True if one was open."""
        was_open = self.current is not None
        self.current = None
        return was_open

    def animating(self) -> bool:
        return self.current is not None and self.current.animate


# Shimmer used for "evolving": a bright spot sweeping along a row of dots.
SPARKS = "·*✦*·"


def render_popup(popup: Popup, now: float, width: int, unicode: bool = True) -> list[Row]:
    """Rows inside the box (the TUI draws the frame and title)."""
    rows: list[Row] = [[]]
    for i, line in enumerate(popup.lines):
        role = "title" if i == 0 else ("dim" if line.startswith("×") else "text")
        rows.append([(line.center(width)[:width], role)])
    if popup.animate:
        phase = int(now * 8)
        spark = SPARKS if unicode else ".*+*."
        line = "".join(spark[(x + phase) % len(spark)] if (x + phase) % 6 < len(spark) else " "
                       for x in range(max(0, width - 4)))
        rows.append([])
        rows.append([(line.center(width)[:width], "prompt")])
    rows.append([])
    rows.append([("any key closes".center(width)[:width], "dim")])
    return rows


def popup_size(popup: Popup) -> tuple[int, int]:
    """(columns, rows) for the box, including its frame."""
    inner = max([len(popup.title) + 4, 22] + [len(line) for line in popup.lines])
    height = len(popup.lines) + 5 + (2 if popup.animate else 0)
    return inner + 6, height


def get_popups(ctx: "Context") -> PopupQueue:
    """The deck's shared pop-up queue (settings from config/pet.ini)."""
    return ctx.service("popups", lambda: PopupQueue(load_popup_settings(Settings.load("pet.ini", ctx.config_dir))))
