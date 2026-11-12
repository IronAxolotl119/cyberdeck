"""keys.py — key names, hotkeys, and the launcher menu list.

Two jobs:

1. Turn whatever curses gives us for a keypress (a character, or a
   number for special keys like F5) into one simple lowercase name:
   "a", "enter", "up", "f5", "ctrl+p", "esc"… Every screen in the deck
   (prompt, launcher, games) works with these names, so none of them
   needs to know curses key numbers.

2. Read config/keys.ini:
     [hotkeys]   key name = what to run   (F2 = :launcher, ctrl+p = pet)
     [launcher]  label    = what to run   (the popup menu entries)

   "What to run" is either a normal command line (`pet feed`) or a UI
   action starting with a colon. UI actions are listed in
   UI_ACTIONS below and documented in CUSTOMIZING.md.
"""

from __future__ import annotations

import curses
import re

from cyberdeck.config import Settings

# UI actions a hotkey or launcher entry can trigger (besides commands).
UI_ACTIONS = {
    ":launcher": "open the launcher menu",
    ":tab next": "show the next tab / view",
    ":tab prev": "show the previous tab / view",
    ":tab <name>": "jump to a tab: output, pet, system, net…",
    ":layout toggle": "switch between split panes and tabs",
    ":layout split": "side-by-side panes",
    ":layout tabs": "one view at a time",
    ":clear": "clear the output pane",
    ":scroll up": "scroll the output up a page",
    ":scroll down": "scroll the output down a page",
    ":quit": "leave the deck",
}

# Ctrl combos the deck (or the terminal) already uses. They cannot be hotkeys.
RESERVED = {
    "ctrl+c": "quit / interrupt",
    "ctrl+d": "quit on empty line",
    "ctrl+h": "backspace on many terminals",
    "ctrl+i": "same code as Tab",
    "ctrl+j": "same code as Enter",
    "ctrl+m": "same code as Enter",
    "ctrl+s": "terminal flow control (freezes output)",
    "ctrl+q": "terminal flow control",
    "ctrl+z": "suspends the program",
    "ctrl+a": "jump to start of line",
    "ctrl+e": "jump to end of line",
    "ctrl+l": "clear the output pane",
    "ctrl+u": "clear the prompt line",
}

# Default hotkeys used when keys.ini is missing or empty.
DEFAULT_HOTKEYS = {
    "f1": "help",
    "f2": ":launcher",
    "f3": "pet status",
    "f4": ":tab next",
    "f5": "status",
    "f6": ":layout toggle",
    "ctrl+o": ":launcher",
    "ctrl+p": "pet status",
    "ctrl+t": ":tab next",
}


def _special_keys() -> dict[int, str]:
    """curses key numbers -> names. Built once at import."""
    table = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right",
        curses.KEY_HOME: "home",
        curses.KEY_END: "end",
        curses.KEY_PPAGE: "pgup",
        curses.KEY_NPAGE: "pgdn",
        curses.KEY_DC: "delete",
        curses.KEY_IC: "insert",
        curses.KEY_BACKSPACE: "backspace",
        curses.KEY_ENTER: "enter",
        curses.KEY_RESIZE: "resize",
        curses.KEY_BTAB: "shift+tab",
    }
    for n in range(1, 13):
        table[curses.KEY_F0 + n] = f"f{n}"
    return table


SPECIAL_KEYS = _special_keys()


def key_name(key: int | str) -> str:
    """Normalise a curses get_wch() result into a simple key name."""
    if isinstance(key, int):
        if key in SPECIAL_KEYS:
            return SPECIAL_KEYS[key]
        if 0 <= key < 256:
            return key_name(chr(key))
        return f"key{key}"
    if key in ("\n", "\r"):
        return "enter"
    if key == "\t":
        return "tab"
    if key in ("\x7f", "\b"):
        return "backspace"
    if key == "\x1b":
        return "esc"
    if len(key) == 1 and 1 <= ord(key) <= 26:
        return "ctrl+" + chr(ord(key) + 96)
    return key


def normalize_key_spec(spec: str) -> str | None:
    """Turn a key written in keys.ini into a key name, or None if invalid.

    Accepted spellings: F5, f5, ctrl+p, Ctrl-P, C-p, ^P, esc, tab…
    """
    text = spec.strip().lower().replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"f([1-9]|1[0-2])", text):
        return text
    match = re.fullmatch(r"(?:ctrl[+-]|c-|\^)([a-z])", text)
    if match:
        return "ctrl+" + match.group(1)
    if text in {"esc", "insert", "delete", "home", "end", "pgup", "pgdn"}:
        return text
    return None


def load_hotkeys(settings: Settings | None = None) -> tuple[dict[str, str], list[str]]:
    """Read [hotkeys] from keys.ini. Returns (key name -> action, warnings)."""
    settings = settings or Settings.load("keys.ini")
    raw = settings.section("hotkeys")
    if not raw:
        return dict(DEFAULT_HOTKEYS), []
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for spec, action in raw.items():
        name = normalize_key_spec(spec)
        if name is None:
            warnings.append(f"keys.ini: cannot understand key '{spec}'")
            continue
        if name in RESERVED:
            warnings.append(f"keys.ini: {name} is reserved ({RESERVED[name]})")
            continue
        if action.strip():
            mapping[name] = action.strip()
    return mapping, warnings


def load_launcher(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Read [launcher] from keys.ini as (label, action) pairs, in file order."""
    settings = settings or Settings.load("keys.ini")
    return [(label, action) for label, action in settings.section("launcher").items() if action.strip()]
