"""petcare.py — look after PULSE from the deck: rest, sleep, evolve, avatar.

Commands added to `pet` (all work headless too):

  pet rest               a quick rest: refills some energy right away
  pet sleep [on|off]     put PULSE to sleep (energy refills over time and it
                         wakes up by itself when full, with a 'woke' event)
                         or wake it up. Plain `pet sleep` means on.
  pet evolve             how close PULSE is to evolving, and the forms it can
                         pick from. When it is ready, the full-screen deck
                         opens a picker; locked forms are greyed out with the
                         reason ("needs 3+ unique discoveries").
  pet evolve <species>   pick that form (pet.evolve)
  pet avatar             PULSE's animated face. Full screen: a popup that
                         plays the animation (or the colour sprite from an
                         older companion). Headless: the frames as text.

Every reply is read through petdata.py, so a companion that sends less
(or does not know a message yet) gets a polite one-line explanation
instead of a crash.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from cyberdeck.activity import Activity, Row
from cyberdeck.commands import PET_ACTIONS, PET_COMPLETERS, CompanionError, run_pet_hooks
from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_PET_AVATAR,
    TYPE_PET_EVOLVE,
    TYPE_PET_REST,
    TYPE_PET_SLEEP,
    TYPE_PET_STATUS,
)

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


def ask(ctx: "Context", typ: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Send one request. Returns (reply payload, "") or (None, a line saying why not)."""
    try:
        reply = ctx.companion.request(typ, payload)
    except CompanionError as exc:
        return None, f"could not reach PULSE: {exc}"
    body = reply.payload or {}
    if reply.type == TYPE_ERROR:
        if body.get("code") == "unknown_type":
            return None, f"this companion does not support {typ} yet"
        return None, f"PULSE says no: {body.get('message') or body.get('code') or 'error'}"
    ctx.services.pop("pet_cache", None)   # the pet pane re-reads soon
    return body, ""


def remember_asleep(ctx: "Context", pet: Any) -> None:
    """Tell the alert watcher (status bar zZ) about a fresh pet object."""
    if isinstance(pet, dict) and isinstance(pet.get("asleep"), bool):
        watcher = ctx.services.get("petwatch")
        if watcher is not None:
            watcher.asleep = pet["asleep"]


def _energy(pet: Any) -> str:
    energy = pet.get("energy") if isinstance(pet, dict) else None
    return f"energy {energy}" if energy is not None else "energy ?"


# --- rest and sleep -------------------------------------------------------------

def pet_rest(ctx: "Context", args: list[str]) -> list[str]:
    del args
    body, error = ask(ctx, TYPE_PET_REST, {})
    if body is None:
        return [error]
    pet = body.get("pet") or {}
    remember_asleep(ctx, pet)
    run_pet_hooks(ctx, "rest", body)
    name = pet.get("name", "PULSE")
    if body.get("rested") is False:
        return [f"{name} can't rest right now ({body.get('reason', 'not now')}) — {_energy(pet)}"]
    delta = body.get("energy_delta")
    gain = f"+{delta}" if isinstance(delta, int) else "up"
    return [f"{name} takes a breather. energy {gain} → {_energy(pet).split()[-1]}"]


def pet_sleep(ctx: "Context", args: list[str]) -> list[str]:
    word = args[1].lower() if len(args) > 1 else "on"
    if word not in {"on", "off", "yes", "no", "wake"}:
        return ["usage: pet sleep [on|off]"]
    on = word in {"on", "yes"}
    body, error = ask(ctx, TYPE_PET_SLEEP, {"on": on})
    if body is None:
        return [error]
    pet = body.get("pet") or {}
    asleep = body.get("asleep", pet.get("asleep"))
    if isinstance(asleep, bool):
        pet = dict(pet, asleep=asleep)
    remember_asleep(ctx, pet)
    run_pet_hooks(ctx, "sleep", body)
    name = pet.get("name", "PULSE")
    if on and asleep is False and body.get("reason"):
        return [f"{name} isn't sleepy ({body['reason']}) — {_energy(pet)}"]
    if asleep:
        return [f"{name} curls up and falls asleep. zZ  ({_energy(pet)}; it wakes up when full — `pet sleep off` to wake it)"]
    return [f"{name} is awake. ({_energy(pet)})"]


# --- evolution ------------------------------------------------------------------

def evolution_status(ctx: "Context") -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    """(evolution info, pet, error) from a fresh pet.status."""
    from cyberdeck.petdata import evolution_info

    body, error = ask(ctx, TYPE_PET_STATUS, {})
    if body is None:
        return None, {}, error
    pet = body.get("pet") or {}
    evolution = evolution_info(body.get("evolution") or pet.get("evolution"))
    if evolution is not None:
        ctx.services["pet_evolution"] = evolution
    return evolution, pet, ""


def pet_evolve(ctx: "Context", args: list[str]) -> list[str]:
    from cyberdeck.petdata import choice_line, evolution_line

    if len(args) > 1:
        species = " ".join(args[1:])
        body, error = ask(ctx, TYPE_PET_EVOLVE, {"species": species})
        if body is None:
            return [error]
        run_pet_hooks(ctx, "evolve", body)
        if body.get("accepted") is False:
            return [body.get("reaction") or f"PULSE did not evolve ({body.get('reason', 'refused')})",
                    f"  reason: {body.get('reason', '?')}"]
        return [body.get("reaction") or f"PULSE evolved: {body.get('from', '?')} → {body.get('to', species)}!"]
    evolution, pet, error = evolution_status(ctx)
    if error:
        return [error]
    name = pet.get("name", "PULSE")
    if evolution is None:
        return [f"this companion does not report evolution progress for {name}"]
    lines = [f"{name} ({pet.get('species', '?')}, {pet.get('stage', '?')})  {evolution_line(evolution)}"]
    choices = evolution["choices"]
    for choice in choices:
        lines.append(("  ✓ " if choice["available"] else "  × ") + choice_line(choice))
    if evolution["ready"] and any(c["available"] for c in choices):
        if ctx.interactive:
            ctx.activity = EvolvePicker(name, choices)
        lines.append("pick one: pet evolve <species>")
    elif evolution["ready"]:
        lines.append("ready, but every form is still locked — see the hints above")
    return lines


def species_completer(ctx: "Context", rest: list[str]) -> list[str]:
    """`pet evolve <Tab>`: the forms PULSE can pick (from the last status)."""
    if rest:
        return []
    evolution = ctx.services.get("pet_evolution")
    if evolution is None and ctx.companion.is_linked():
        evolution, _pet, _err = evolution_status(ctx)
    return [c["species"] for c in (evolution or {}).get("choices", []) if c["available"]]


class EvolvePicker(Activity):
    """Popup list of next forms. Locked ones are grey, show why, and are skipped."""

    title = "EVOLVE"
    overlay = True
    tick = 0.5

    def __init__(self, name: str, choices: list[dict[str, Any]]) -> None:
        super().__init__()
        self.name = name
        self.choices = choices
        self.index = next((i for i, c in enumerate(choices) if c["available"]), 0)
        self.overlay_size = (58, min(18, 6 + 2 * len(choices)))

    def _step(self, direction: int) -> None:
        for _ in range(len(self.choices)):
            self.index = (self.index + direction) % len(self.choices)
            if self.choices[self.index]["available"]:
                return

    def on_key(self, key: str) -> None:
        if key in {"esc", "q"}:
            self.done = True
        elif key in {"up", "k", "shift+tab"}:
            self._step(-1)
        elif key in {"down", "j", "tab"}:
            self._step(1)
        elif key == "enter" and self.choices and self.choices[self.index]["available"]:
            self.result_command = f"pet evolve {self.choices[self.index]['species']}"
            self.done = True

    def render(self, width: int, height: int) -> list[Row]:
        rows: list[Row] = [[(f"{self.name} is ready! pick a form:"[:width], "title")], []]
        for i, choice in enumerate(self.choices):
            stage = f" ({choice['stage']})" if choice.get("stage") else ""
            if choice["available"]:
                mark = "▶ " if i == self.index else "  "
                rows.append([(f"{mark}{choice['species']}{stage}"[:width].ljust(width), "prompt" if i == self.index else "text")])
                if choice.get("hint"):
                    rows.append([(f"    {choice['hint']}"[:width], "dim")])
            else:
                rows.append([(f"  {choice['species']}{stage}  [locked]"[:width], "dim")])
                rows.append([(f"    {choice.get('hint') or 'not available yet'}"[:width], "dim")])
        rows.append([("↑↓ choose · Enter evolve · Esc later"[:width], "dim")])
        return rows[:height]

    def headless(self) -> list[str]:
        return []   # the command already listed the choices


# --- avatar ---------------------------------------------------------------------

def pet_avatar(ctx: "Context", args: list[str]) -> list[str]:
    from cyberdeck.petdata import DEFAULT_FACES, parse_avatar, parse_face

    del args
    body, error = ask(ctx, TYPE_PET_AVATAR, {})
    face = parse_face(body) if body else None
    sprite = parse_avatar(body) if body and face is None else None
    if face is None and sprite is None:
        # No picture from the companion: fall back to a face for its mood.
        status, status_error = ask(ctx, TYPE_PET_STATUS, {})
        mood = ((status or {}).get("pet") or {}).get("mood", "content")
        from cyberdeck.petdata import Face

        face = Face((DEFAULT_FACES.get(mood, "(o_o)"),), 1000, mood)
        if error or status_error:
            note = error or status_error
        else:
            note = "the companion sent no picture; showing a default face"
    else:
        note = ""
    if ctx.interactive:
        ctx.activity = AvatarView(face, sprite)
        return [note] if note else []
    lines = [note] if note else []
    if face is not None:
        lines.append(f"PULSE · {face.mood or 'face'} · {len(face.frames)} frame(s) every {face.interval_ms} ms")
        lines += [f"  frame {i}: {text}" for i, text in enumerate(face.frames, 1)]
    else:
        lines.append(f"PULSE · {sprite.form or 'sprite'} · {sprite.width}x{len(sprite.rows)} (older companion sprite)")
        lines += sprite_text(sprite)
    return lines


# Light to dark, for drawing a colour sprite with plain characters.
_SHADES = " .:-=+*#%@"


def sprite_text(sprite: Any) -> list[str]:
    """An older companion's colour sprite as a character grid (2 chars per pixel)."""
    out = []
    for y in range(len(sprite.rows)):
        line = ""
        for x in range(sprite.width):
            rgb = sprite.pixel(x, y)
            if rgb is None:
                line += "  "
            else:
                lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
                line += _SHADES[1 + min(len(_SHADES) - 2, int(lum * (len(_SHADES) - 1)))] * 2
        out.append(line.rstrip())
    return out


class AvatarView(Activity):
    """Popup that plays PULSE's face animation (any key closes it)."""

    title = "PULSE"
    overlay = True
    tick = 0.1

    def __init__(self, face: Any, sprite: Any = None) -> None:
        super().__init__()
        self.face = face
        self.sprite = sprite
        self.now = time.time()
        self.overlay_size = (36, 14) if sprite is not None else (30, 7)

    def update(self, now: float) -> None:
        self.now = now

    def on_key(self, key: str) -> None:
        if key != "resize":
            self.done = True

    def render(self, width: int, height: int) -> list[Row]:
        if self.face is not None:
            text = self.face.at(self.now)
            rows: list[Row] = [[], [(text.center(width)[:width], "title")], []]
            rows.append([((self.face.mood or "").center(width)[:width], "dim")])
            rows.append([("any key to close".center(width)[:width], "dim")])
            return rows[:height]
        from cyberdeck.panes import sprite_rows

        rows = sprite_rows(self.sprite, width) or [[(line[:width], "text")] for line in sprite_text(self.sprite)]
        return rows[:height]

    def headless(self) -> list[str]:
        return []


def register(reg: "Registry") -> None:
    """Hook rest / sleep / evolve / avatar into `pet` (no new top-level command)."""
    del reg
    PET_ACTIONS.update({"rest": pet_rest, "sleep": pet_sleep, "evolve": pet_evolve, "avatar": pet_avatar})
    PET_COMPLETERS.update({"sleep": lambda ctx, rest: ["on", "off"] if not rest else [],
                           "evolve": species_completer})
