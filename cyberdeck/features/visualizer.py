"""visualizer.py — the audio-style bars on the right of the ticker line.

   ▂▄▇█▆▃▁▂▅▇▅▃

It is purely for looks. When nothing is playing it idles with a gentle
"sim-driven" wobble (it breathes a little faster when the CPU is
busy). When `music play` is running, the bars jump on every note and
the peak sits where the note's pitch is: low notes on the left, high
notes on the right.

Settings: config/fx.ini [visualizer] (enabled, width, frame_ms, idle).
The `viz on|off` command toggles it while the deck runs.

Visualizer.levels() is pure maths over time + an injected music source,
so tests check it without curses.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

from cyberdeck.config import Settings
from cyberdeck.fx import level_char

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


class Visualizer:
    """Keeps a row of bar levels (0..1) moving over time."""

    def __init__(self, width: int = 12, idle: float = 0.35, frame_ms: int = 150,
                 enabled: bool = True, seed: int | None = None) -> None:
        self.width = max(3, width)
        self.idle = max(0.0, min(1.0, idle))
        self.frame_ms = frame_ms
        self.enabled = enabled
        self.rng = random.Random(seed)
        self.values = [0.0] * self.width
        self._last = 0.0
        # Something with level(now) and pitch(); the music player, when playing.
        self.source: Any = None
        self.cpu: float | None = None  # 0..100, nudges the idle speed

    def levels(self, now: float) -> list[float]:
        """Advance the bars to `now` and return them."""
        dt = min(0.5, max(0.0, now - self._last)) if self._last else 0.1
        self._last = now
        playing = self.source is not None and getattr(self.source, "playing", False)
        if playing:
            level = self.source.level(now)
            centre = self.source.pitch() * (self.width - 1)
            for i in range(self.width):
                # A bump around the note's pitch, plus a little sparkle.
                shape = math.exp(-((i - centre) ** 2) / (2 * (self.width / 5) ** 2))
                target = level * (0.35 + 0.65 * shape) + self.rng.uniform(0, 0.15) * level
                self.values[i] = max(target, self.values[i] - dt * 2.5)
        else:
            speed = 1.0 + (self.cpu or 0) / 50
            for i in range(self.width):
                wave = 0.5 + 0.5 * math.sin(now * 2.2 * speed + i * 0.7)
                target = self.idle * (0.25 + 0.75 * wave) * self.rng.uniform(0.6, 1.0)
                self.values[i] += (target - self.values[i]) * min(1.0, dt * 6)
        self.values = [max(0.0, min(1.0, v)) for v in self.values]
        return list(self.values)

    def render(self, now: float, unicode: bool = True) -> str:
        """The bars as a string, for the ticker line."""
        return "".join(level_char(v, unicode) for v in self.levels(now))

    def ui_tick(self, ctx: "Context", now: float) -> None:
        """Hook up to the music player and the CPU reading each frame."""
        self.source = ctx.services.get("music")
        try:
            self.cpu = ctx.sensors.read().cpu_percent
        except Exception:
            self.cpu = None


def get_visualizer(ctx: "Context") -> Visualizer:
    def build() -> Visualizer:
        s = Settings.load("fx.ini", ctx.config_dir)
        return Visualizer(
            width=s.get_int("visualizer", "width", 12),
            idle=s.get_float("visualizer", "idle", 0.35),
            frame_ms=s.get_int("visualizer", "frame_ms", 150),
            enabled=s.get_bool("visualizer", "enabled", True),
        )
    return ctx.service("visualizer", build)


def register(reg: "Registry") -> None:
    from cyberdeck.features import UI_STARTUP

    if get_visualizer not in UI_STARTUP:
        UI_STARTUP.append(get_visualizer)

    @reg.command("viz", "Turn the status-line visualizer on or off.", usage="viz [on|off]",
                 subcommands=("on", "off"))
    def cmd_viz(ctx: "Context", args: list[str]) -> list[str]:
        viz = get_visualizer(ctx)
        if args and args[0].lower() in {"on", "off"}:
            viz.enabled = args[0].lower() == "on"
        elif not args:
            viz.enabled = not viz.enabled
        else:
            return ["usage: viz [on|off]"]
        return [f"visualizer {'on' if viz.enabled else 'off'}"]
