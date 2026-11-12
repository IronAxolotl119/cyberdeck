"""boot.py — the startup animation: glitchy logo and fake decrypting bars.

Shown once when the full-screen deck starts (not in headless mode).
Any key skips it. Turn it off, or change its length and glitchiness,
in config/fx.ini [boot]; `--no-boot` skips it for one run. The `boot`
command replays it any time.

The animation is a timeline over `duration` seconds:
  0%  - 35%   the logo resolves out of glitch noise
  15% - 90%   fake boot log lines appear, and "decrypting" bars fill
  90% - 100%  "ACCESS GRANTED" / ready line, then it closes itself

BootActivity.render() only depends on elapsed time and a seeded random
generator, so tests can render any moment of the animation as text.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cyberdeck.activity import Activity, Row
from cyberdeck.config import Settings
from cyberdeck.fx import banner, glitch, progress

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# Fake boot log lines. Edit freely — they are just for show.
BOOT_LINES = [
    "[ OK ] mounting /dev/neural0",
    "[ OK ] spinning up flux capacitors",
    "[ OK ] loading PULSE companion link",
    "[ OK ] calibrating sarcasm module",
    "[WARN] coffee level low",
    "[ OK ] handshaking with the grid",
    "[ OK ] reticulating splines",
    "[ OK ] deck online",
]

# Names of the fake "decrypting" stages and when each starts/ends (0..1).
STAGES = [
    ("DECRYPTING KERNEL", 0.15, 0.45),
    ("DECRYPTING USER SPACE", 0.35, 0.70),
    ("VERIFYING SIGNATURES", 0.55, 0.88),
]


@dataclass(frozen=True)
class BootSettings:
    enabled: bool = True
    duration: float = 3.5
    glitch: float = 0.35
    title: str = "CYBERDECK"
    jingle: str = "boot_jingle"


def load_boot_settings(settings: Settings | None = None) -> BootSettings:
    s = settings or Settings.load("fx.ini")
    d = BootSettings()
    return BootSettings(
        enabled=s.get_bool("boot", "enabled", d.enabled),
        duration=max(0.5, s.get_float("boot", "duration", d.duration)),
        glitch=max(0.0, min(1.0, s.get_float("boot", "glitch", d.glitch))),
        title=s.get_str("boot", "title", d.title),
        jingle=s.get_str("boot", "jingle", d.jingle),
    )


class BootActivity(Activity):
    """Full-screen boot animation. Any key skips."""

    title = "BOOT"
    tick = 0.06

    def __init__(self, settings: BootSettings, unicode: bool = True, seed: int | None = None,
                 on_start=None) -> None:
        super().__init__()
        self.s = settings
        self.unicode = unicode
        self.rng = random.Random(seed)
        self.t0: float | None = None
        self.elapsed = 0.0
        self.on_start = on_start  # e.g. start the boot jingle

    def start(self, now: float) -> None:
        self.t0 = now
        if self.on_start:
            try:
                self.on_start()
            except Exception:
                pass

    def on_key(self, key: str) -> None:
        self.done = True  # any key skips

    def update(self, now: float) -> None:
        if self.t0 is None:
            self.t0 = now
        self.elapsed = now - self.t0
        if self.elapsed >= self.s.duration:
            self.done = True

    def render(self, width: int, height: int) -> list[Row]:
        frac = min(1.0, self.elapsed / self.s.duration)
        rows: list[Row] = [[]]
        # The logo: heavy glitch at first, settling down by 35%.
        noise = self.s.glitch * max(0.0, 1.0 - frac / 0.35)
        logo = banner(self.s.title, self.unicode)
        pad = max(0, (width - len(logo[0])) // 2)
        for line in logo:
            rows.append([(" " * pad + glitch(line, self.rng, noise, self.unicode), "title")])
        rows.append([])
        # Boot log lines appear one by one between 15% and 90%.
        shown = int(len(BOOT_LINES) * max(0.0, min(1.0, (frac - 0.15) / 0.75)))
        for line in BOOT_LINES[:shown]:
            role = "warn" if "WARN" in line else "ok"
            rows.append([("  ", "dim"), (glitch(line, self.rng, noise * 0.3, self.unicode), role)])
        rows.append([])
        # The "decrypting…" bars.
        bar_w = max(10, min(40, width - 36))
        for name, start, end in STAGES:
            part = max(0.0, min(1.0, (frac - start) / (end - start)))
            if frac < start:
                continue
            dots = "." * (1 + int(self.elapsed * 4) % 3) if part < 1 else "   "
            pct = f"{int(part * 100):>3}%"
            rows.append([(f"  {name}{dots:<3} ".ljust(28), "text"), (progress(part, bar_w, self.unicode), "ok" if part >= 1 else "prompt"),
                         (f" {pct}", "dim")])
        if frac >= 0.9:
            rows.append([])
            rows.append([("  >> SYSTEM READY. WELCOME BACK, OPERATOR.", "title")])
        rows.append([])
        rows.append([("  (any key to skip)", "dim")])
        # Centre vertically using the final size, so lines do not jump as they appear.
        full = 1 + 5 + 1 + len(BOOT_LINES) + 1 + len(STAGES) + 2 + 2
        top = max(0, (height - full) // 2)
        return ([[]] * top + rows)[:height]

    def headless(self) -> list[str]:
        return ["(boot animation runs in the full-screen UI)"]


def make_boot(ctx: "Context", force: bool = False) -> BootActivity | None:
    """The boot activity if enabled (or forced), with the jingle hooked up."""
    settings = load_boot_settings(Settings.load("fx.ini", ctx.config_dir))
    if not settings.enabled and not force:
        return None

    def jingle() -> None:
        if settings.jingle.lower() in {"", "none", "off", "no"}:
            return
        from cyberdeck.features.music import get_player, load_song

        get_player(ctx).play(load_song(ctx, settings.jingle), quiet=True)

    unicode = ctx.services.get("unicode", True)
    return BootActivity(settings, unicode=unicode, on_start=jingle)


def register(reg: "Registry") -> None:
    @reg.command("boot", "Replay the boot animation.")
    def cmd_boot(ctx: "Context", args: list[str]) -> list[str]:
        del args
        ctx.activity = make_boot(ctx, force=True)
        return []
