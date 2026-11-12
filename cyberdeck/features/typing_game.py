"""typing_game.py — `typehack`: a typing-speed challenge styled as hacking.

  typehack            play (also `typing`)
  typehack scores     the high-score table
  typehack reset      wipe the high scores

How a run works: you get `rounds` phrases (from text/hack_phrases.txt).
Type each one exactly; a TRACE bar counts down `time_limit` seconds per
phrase. Correct letters show green, mistakes red. Backspace fixes
mistakes, but every wrong keystroke still costs accuracy. Enter skips a
phrase (it counts as failed). Esc quits.

At the end you get WPM (words per minute, 1 word = 5 characters typed
correctly), accuracy, and a score = WPM × accuracy. ≥ 90% accuracy and
no failed rounds = ACCESS GRANTED. Scores are saved to
data/typing_scores.json (top 10).

TypingRound and score maths are pure, so they are unit-tested by
"typing" into them with fake timestamps.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cyberdeck.activity import Activity, Row
from cyberdeck.config import Settings, repo_root
from cyberdeck.fx import banner, progress

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

FALLBACK_PHRASES = ["hack the planet", "the gibson is protected by four layers of ice"]


# --- one phrase ---------------------------------------------------------------

@dataclass
class TypingRound:
    """Typing one target phrase."""

    target: str
    time_limit: float = 30.0
    typed: str = ""
    keystrokes: int = 0
    mistakes: int = 0
    started: float | None = None
    finished: float | None = None
    failed: bool = False

    def key(self, key: str, now: float) -> None:
        """Feed one key name ('a', 'backspace', 'enter', …)."""
        if self.finished is not None:
            return
        if self.started is None:
            self.started = now  # the clock starts at the first key
        if key == "backspace":
            self.typed = self.typed[:-1]
            return
        if key == "enter":  # give up on this phrase
            self.failed = True
            self.finished = now
            return
        ch = " " if key == "space" else key
        if len(ch) != 1 or not ch.isprintable():
            return
        self.keystrokes += 1
        if len(self.typed) >= len(self.target) or ch != self.target[len(self.typed)]:
            self.mistakes += 1
        self.typed += ch
        if self.typed == self.target:
            self.finished = now

    def check_time(self, now: float) -> None:
        """Fail the round once the trace timer runs out."""
        if self.finished is None and self.started is not None and now - self.started >= self.time_limit:
            self.failed = True
            self.finished = now

    def elapsed(self, now: float) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or now) - self.started

    def correct_chars(self) -> int:
        """Characters matching the target, counted from the start."""
        n = 0
        for a, b in zip(self.typed, self.target):
            if a != b:
                break
            n += 1
        return n

    @property
    def done(self) -> bool:
        return self.finished is not None


def wpm(correct_chars: int, seconds: float) -> float:
    """Standard WPM: 5 characters = 1 word."""
    return 0.0 if seconds <= 0 else (correct_chars / 5) / (seconds / 60)


def accuracy(keystrokes: int, mistakes: int) -> float:
    return 1.0 if keystrokes == 0 else max(0.0, (keystrokes - mistakes) / keystrokes)


@dataclass
class RunResult:
    wpm: float
    accuracy: float
    failed_rounds: int
    rounds: int

    @property
    def score(self) -> int:
        return int(round(self.wpm * self.accuracy * (1 - 0.25 * self.failed_rounds / max(1, self.rounds))))

    @property
    def granted(self) -> bool:
        return self.accuracy >= 0.9 and self.failed_rounds == 0


def summarize(rounds: list[TypingRound]) -> RunResult:
    """Combine all rounds into one result."""
    chars = sum(r.correct_chars() for r in rounds)
    seconds = sum(r.elapsed(r.finished or 0) for r in rounds if r.started is not None)
    keys = sum(r.keystrokes for r in rounds)
    bad = sum(r.mistakes for r in rounds)
    return RunResult(wpm(chars, seconds), accuracy(keys, bad), sum(r.failed for r in rounds), len(rounds))


# --- high scores ------------------------------------------------------------------

@dataclass
class ScoreEntry:
    name: str
    score: int
    wpm: float
    accuracy: float
    date: str


@dataclass
class HighScores:
    path: Path
    keep: int = 10
    entries: list[ScoreEntry] = field(default_factory=list)

    def load(self) -> "HighScores":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [ScoreEntry(**e) for e in data][: self.keep]
        except (OSError, ValueError, TypeError):
            self.entries = []
        return self

    def add(self, entry: ScoreEntry) -> int | None:
        """Insert and save. Returns the 1-based rank, or None if it did not place."""
        self.entries.append(entry)
        self.entries.sort(key=lambda e: e.score, reverse=True)
        rank = self.entries.index(entry) + 1
        self.entries = self.entries[: self.keep]
        self.save()
        return rank if rank <= self.keep else None

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(e) for e in self.entries], indent=1), encoding="utf-8")
        except OSError:
            pass

    def table(self) -> list[str]:
        if not self.entries:
            return ["(no high scores yet — run `typehack`)"]
        lines = ["  #  NAME        SCORE   WPM   ACC   DATE"]
        for i, e in enumerate(self.entries, 1):
            lines.append(f" {i:>2}  {e.name[:10]:<10} {e.score:>6} {e.wpm:>5.0f} {e.accuracy * 100:>4.0f}%  {e.date}")
        return lines


# --- the full-screen game -------------------------------------------------------------

class TypingActivity(Activity):
    """Rounds of phrases with a trace timer, then a results screen."""

    title = "TYPEHACK"
    tick = 0.1

    def __init__(self, phrases: list[str], scores: HighScores, player: str = "OPERATOR",
                 rounds: int = 3, time_limit: float = 30.0, unicode: bool = True, seed: int | None = None,
                 clock=time.time) -> None:
        super().__init__()
        rng = random.Random(seed)
        pool = phrases or FALLBACK_PHRASES
        picks = rng.sample(pool, min(rounds, len(pool)))
        self.rounds = [TypingRound(p, time_limit) for p in picks]
        self.index = 0
        self.scores = scores
        self.player = player
        self.unicode = unicode
        self.clock = clock
        self.now = clock()
        self.result: RunResult | None = None
        self.rank: int | None = None

    @property
    def current(self) -> TypingRound | None:
        return self.rounds[self.index] if self.index < len(self.rounds) else None

    def on_key(self, key: str) -> None:
        if key == "esc":
            self.done = True
            return
        if self.result is not None:  # results screen: any key closes
            self.done = True
            return
        if key == " ":
            key = "space"
        rnd = self.current
        if rnd is not None:
            rnd.key(key, self.clock())
            self._advance()

    def update(self, now: float) -> None:
        self.now = now
        rnd = self.current
        if rnd is not None:
            rnd.check_time(now)
            self._advance()

    def _advance(self) -> None:
        rnd = self.current
        if rnd is not None and rnd.done:
            self.index += 1
            if self.index >= len(self.rounds):
                self._finish()

    def _finish(self) -> None:
        self.result = summarize(self.rounds)
        entry = ScoreEntry(self.player, self.result.score, round(self.result.wpm, 1),
                           round(self.result.accuracy, 3), time.strftime("%Y-%m-%d"))
        self.rank = self.scores.add(entry)
        verdict = "ACCESS GRANTED" if self.result.granted else "INTRUSION DETECTED"
        self.result_lines = [f"typehack: {verdict} — {self.result.wpm:.0f} wpm, "
                             f"{self.result.accuracy * 100:.0f}% accuracy, score {self.result.score}"
                             + (f" (#{self.rank} on the board!)" if self.rank else "")]

    def render(self, width: int, height: int) -> list[Row]:
        if self.result is not None:
            return self._results(width, height)
        rnd = self.current
        assert rnd is not None
        rows: list[Row] = [[(f" TYPEHACK :: node {self.index + 1}/{len(self.rounds)}", "title")], []]
        rows.append([("  type the command exactly:", "dim")])
        rows.append([])
        # Target with typed characters coloured: green right, red wrong, grey to come.
        line: Row = [("  ", "dim")]
        for i, ch in enumerate(rnd.target):
            if i < len(rnd.typed):
                line.append((ch if rnd.typed[i] == ch else (rnd.typed[i] if rnd.typed[i] != " " else "_"),
                             "ok" if rnd.typed[i] == ch else "alert"))
            elif i == len(rnd.typed):
                line.append((ch, "prompt"))
            else:
                line.append((ch, "dim"))
        rows.append(line)
        if len(rnd.typed) > len(rnd.target):
            rows.append([("  " + " " * len(rnd.target), "dim"), (rnd.typed[len(rnd.target):], "alert")])
        rows.append([("  > " + rnd.typed + "_", "text")])
        rows.append([])
        left = max(0.0, rnd.time_limit - rnd.elapsed(self.now))
        frac = left / rnd.time_limit if rnd.time_limit else 0
        role = "ok" if frac > 0.5 else "warn" if frac > 0.2 else "alert"
        rows.append([("  TRACE ", role), (progress(1 - frac, max(10, min(40, width - 24)), self.unicode), role),
                     (f" {left:4.1f}s", role)])
        live = wpm(rnd.correct_chars(), rnd.elapsed(self.now))
        rows.append([(f"  {live:4.0f} wpm   {accuracy(rnd.keystrokes, rnd.mistakes) * 100:3.0f}% acc", "text")])
        rows.append([])
        hint = "timer starts on your first key · Backspace fixes · Enter skips · Esc quits"
        rows.append([("  " + hint, "dim")])
        return rows[:height]

    def _results(self, width: int, height: int) -> list[Row]:
        r = self.result
        assert r is not None
        verdict = "GRANTED" if r.granted else "DENIED"
        rows: list[Row] = [[]]
        for line in banner(verdict, self.unicode):
            rows.append([(line.center(width)[:width], "ok" if r.granted else "alert")])
        rows.append([])
        rows.append([(f"{r.wpm:.0f} WPM   ·   {r.accuracy * 100:.0f}% ACCURACY   ·   SCORE {r.score}".center(width)[:width], "title")])
        if r.failed_rounds:
            rows.append([(f"{r.failed_rounds} node(s) traced".center(width)[:width], "warn")])
        if self.rank:
            rows.append([(f"NEW HIGH SCORE  #{self.rank}".center(width)[:width], "prompt")])
        rows.append([])
        table = self.scores.table()[:8]
        table_w = max(len(line) for line in table)
        for line in table:  # pad first so the columns stay lined up when centred
            rows.append([(line.ljust(table_w).center(width)[:width], "text")])
        rows.append([])
        rows.append([("any key to return".center(width)[:width], "dim")])
        return rows[:height]


def load_phrases(ctx: "Context") -> list[str]:
    rel = Settings.load("fx.ini", ctx.config_dir).get_str("typing", "phrases", "text/hack_phrases.txt")
    path = Path(rel) if Path(rel).is_absolute() else repo_root() / rel
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return list(FALLBACK_PHRASES)
    return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")] or list(FALLBACK_PHRASES)


def get_scores(ctx: "Context") -> HighScores:
    return HighScores(ctx.data_path("typing_scores.json")).load()


def register(reg: "Registry") -> None:
    @reg.command("typehack", "Typing-speed hacking challenge with high scores.",
                 usage="typehack [scores|reset]", aliases=("typing",), subcommands=("scores", "reset"))
    def cmd_typehack(ctx: "Context", args: list[str]) -> list[str]:
        scores = get_scores(ctx)
        if args and args[0] == "scores":
            return scores.table()
        if args and args[0] == "reset":
            scores.entries = []
            scores.save()
            return ["high scores wiped."]
        s = Settings.load("fx.ini", ctx.config_dir)
        activity = TypingActivity(load_phrases(ctx), scores, s.get_str("typing", "player", "OPERATOR"),
                                  s.get_int("typing", "rounds", 3), s.get_float("typing", "time_limit", 30.0),
                                  unicode=ctx.services.get("unicode", True))
        if not ctx.interactive:
            return ["typehack needs the full-screen UI. high scores:"] + scores.table()
        ctx.activity = activity
        return []
