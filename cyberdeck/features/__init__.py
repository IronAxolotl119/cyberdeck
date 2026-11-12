"""features/ — the deck's bigger built-in features, one module each.

commands.py keeps only the core commands (help, status, pet, ping…).
Everything else lives here so each feature is easy to find, read, and
switch off. Every module has a register(registry) function that adds
its commands.

To turn a feature off, remove its name from FEATURES below (or add
`disabled = name, name` under [features] in config/layout.ini).
To add a feature of your own, prefer a file in plugins/ — but a new
module here works too: write register(reg) and list it below.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyberdeck.commands import Registry

# Loaded in this order. Each name is a module in this folder.
FEATURES = [
    "core_ux",      # history, launcher menu, views/tabs, hotkey list
    "notes",        # note / todo (plain text files in data/)
    "wifi",         # wifi status, scan, live signal meter
    "netcheck",     # net hosts / ping / port / check (homelab)
    "netinfo",      # net info screen and the live "net" panel
    "ssh",          # ssh shortcuts from hosts.ini
    "hardware",     # gpio led / set / read / beep (sim, gpiozero, sysfs)
    "music",        # chiptune player for the buzzer, songs/ folder
    "petwatch",     # PULSE alerts, pet log, pet sim
    "boot",         # boot animation (glitch logo, decrypting bars)
    "visualizer",   # animated bars on the ticker line
    "moods",        # sassy status lines from moods.ini
    "mainframe",    # `hack`: the dramatic fake hacking show
    "typing_game",  # `typehack`: typing-speed challenge + high scores
    "dungeon",      # ASCII roguelike + loot gifts to PULSE
]


# Functions a feature wants called once when the full-screen UI starts,
# as fn(ctx). Used to start background helpers (pet alerts, visualizer…).
UI_STARTUP: list = []


def register_features(reg: "Registry") -> list[str]:
    """Import every enabled feature module and let it add its commands."""
    from cyberdeck.config import Settings

    disabled = set(Settings.load("layout.ini").get_list("features", "disabled"))
    loaded: list[str] = []
    for name in FEATURES:
        if name in disabled:
            continue
        try:
            module = importlib.import_module(f"cyberdeck.features.{name}")
            module.register(reg)
            loaded.append(name)
        except Exception as exc:  # one broken feature must not stop the deck
            print(f"cyberdeck: feature {name} failed to load: {exc}", file=sys.stderr)
    return loaded
