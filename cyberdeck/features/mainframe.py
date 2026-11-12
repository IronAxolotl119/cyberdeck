"""mainframe.py — `hack`: a dramatic, totally fake "hack the mainframe" show.

For showing off to friends. Nothing is actually hacked — it is an
animation. Type `hack` (or `hack pentagon` to name your own target):

  1. Code and hex dumps scroll up the left side of the screen.
  2. Stage bars fill on the right: tracing route, bypassing firewall,
     cracking encryption, injecting payload…
  3. A big flashing ACCESS GRANTED banner.

Press any key to leave at any time. Length and default target are in
config/fx.ini [mainframe].
"""

from __future__ import annotations

import random
from collections import deque
from typing import TYPE_CHECKING

from cyberdeck.activity import Activity, Row
from cyberdeck.config import Settings
from cyberdeck.fx import banner, fake_code_line, glitch, hex_dump_line, progress

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# (label, start, end) as fractions of the run.
HACK_STAGES = [
    ("TRACING ROUTE", 0.00, 0.18),
    ("BYPASSING FIREWALL", 0.12, 0.38),
    ("CRACKING RSA-4096", 0.30, 0.66),
    ("SPOOFING CREDENTIALS", 0.55, 0.78),
    ("INJECTING PAYLOAD", 0.70, 0.92),
    ("ESCALATING PRIVILEGES", 0.85, 1.00),
]


class MainframeActivity(Activity):
    """Scrolling code + stage bars + ACCESS GRANTED. Any key exits."""

    title = "HACK"
    tick = 0.05

    def __init__(self, target: str = "GIBSON MAINFRAME", duration: float = 12.0, unicode: bool = True,
                 seed: int | None = None) -> None:
        super().__init__()
        self.target = target.upper()
        self.duration = max(2.0, duration)
        self.unicode = unicode
        self.rng = random.Random(seed)
        self.code: deque[str] = deque(maxlen=200)
        self.t0: float | None = None
        self.elapsed = 0.0
        self._address = self.rng.randint(0, 0xFFFF) * 16

    def on_key(self, key: str) -> None:
        self.done = True

    def update(self, now: float) -> None:
        if self.t0 is None:
            self.t0 = now
        self.elapsed = now - self.t0
        if self.elapsed < self.duration:
            # A few new lines per frame; faster during "cracking".
            for _ in range(self.rng.randint(1, 3)):
                if self.rng.random() < 0.3:
                    self.code.append(hex_dump_line(self.rng, self._address))
                    self._address += 12
                else:
                    self.code.append(fake_code_line(self.rng))

    @property
    def fraction(self) -> float:
        return min(1.0, self.elapsed / self.duration)

    def render(self, width: int, height: int) -> list[Row]:
        if self.fraction >= 1.0:
            return self._granted(width, height)
        side_w = min(40, max(26, width // 3))
        code_w = max(10, width - side_w - 3)
        rows: list[Row] = []
        header = f" >> TARGET: {self.target}  ::  CONNECTION {'█' if int(self.elapsed * 3) % 2 else ' '}"
        rows.append([(header[:width], "alert")])
        visible = list(self.code)[-(height - 2):]
        side = self._side_panel(side_w)
        for i in range(height - 2):
            code = visible[i] if i < len(visible) else ""
            row: Row = [(code[:code_w].ljust(code_w), "ok"), (" │ " if self.unicode else " | ", "dim")]
            row.extend(side[i] if i < len(side) else [])
            rows.append(row)
        rows.append([(" any key to abort ".ljust(width)[:width], "dim")])
        return rows

    def _side_panel(self, width: int) -> list[Row]:
        bar_w = max(8, width - 6)
        rows: list[Row] = [[("INTRUSION STATUS", "title")], []]
        for label, start, end in HACK_STAGES:
            part = max(0.0, min(1.0, (self.fraction - start) / (end - start)))
            role = "ok" if part >= 1 else "prompt" if part > 0 else "dim"
            status = "DONE" if part >= 1 else f"{int(part * 100):>3}%"
            rows.append([(label[: width - 6].ljust(width - 5), role), (status, role)])
            rows.append([(progress(part, bar_w, self.unicode), role)])
        rows.append([])
        rows.append([("trace risk ", "dim"), (progress(self.rng.random() * 0.4, 10, self.unicode), "alert")])
        return rows

    def _granted(self, width: int, height: int) -> list[Row]:
        big = banner("ACCESS GRANTED", self.unicode)
        if len(big[0]) > width:  # small screen: two lines of big text
            big = banner("ACCESS", self.unicode) + [""] + banner("GRANTED", self.unicode)
        flash = int(self.elapsed * 3) % 2 == 0
        jitter = 0.08 if int(self.elapsed * 7) % 5 == 0 else 0.0
        rows: list[Row] = []
        top = max(0, (height - len(big) - 4) // 2)
        rows.extend([[]] * top)
        for line in big:
            pad = max(0, (width - len(line)) // 2)
            rows.append([(" " * pad + glitch(line, self.rng, jitter, self.unicode), "ok" if flash else "title")])
        rows.append([])
        msg = f"welcome to {self.target}, operator."
        rows.append([(msg.center(width)[:width], "text")])
        rows.append([("press any key".center(width)[:width], "dim")])
        return rows[:height]

    def headless(self) -> list[str]:
        lines = [f">> TARGET: {self.target}"]
        for label, _start, _end in HACK_STAGES:
            lines.append(f"{label:<24} {progress(1.0, 20, False)} DONE")
        return lines + banner("ACCESS GRANTED", False)


def register(reg: "Registry") -> None:
    @reg.command("hack", "Dramatic fake 'hack the mainframe' show (any key exits).",
                 usage="hack [target name]", aliases=("mainframe",))
    def cmd_hack(ctx: "Context", args: list[str]) -> list[str]:
        s = Settings.load("fx.ini", ctx.config_dir)
        target = " ".join(args) or s.get_str("mainframe", "target", "GIBSON MAINFRAME")
        ctx.activity = MainframeActivity(target, s.get_float("mainframe", "duration", 12.0),
                                         unicode=ctx.services.get("unicode", True))
        return []
