"""jobs.py — run slow commands (network fetches) without freezing the screen.

In the full-screen UI, a command such as `weather` or `net check` calls

    return in_background(ctx, "weather", "weather", lambda: weather_lines(...))

which starts the work on a thread and immediately returns one line:

    weather: fetching…  [job 3]

The TUI checks for finished jobs every frame (finished_jobs) and swaps
that placeholder line for the real result, or adds the result at the end
if the log was cleared meanwhile. A short notice also appears on the
ticker line.

In headless/script mode (ctx.interactive is False) there is no screen to
freeze, so the work simply runs right away and returns its lines; scripts
and tests see the same output as before.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from cyberdeck.commands import Context

_ids = itertools.count(1)


@dataclass
class Job:
    key: str                      # one job per key at a time (e.g. "weather")
    title: str
    placeholder: str              # the line shown until the result arrives
    lines: list[str] = field(default_factory=list)
    failed: bool = False


class Jobs:
    """Running and finished background jobs (one shared instance per deck)."""

    def __init__(self) -> None:
        self.running: dict[str, Job] = {}
        self.done: deque[Job] = deque()
        self._lock = threading.Lock()

    def start(self, key: str, title: str, work: Callable[[], list[str]]) -> Job | None:
        """Start work on a thread; None if a job with this key is still running."""
        with self._lock:
            if key in self.running:
                return None
            job = Job(key, title, f"{title}: fetching…  [job {next(_ids)}]")
            self.running[key] = job

        def run() -> None:
            try:
                job.lines = list(work() or [])
            except Exception as exc:  # report it in the log, never crash the deck
                job.lines, job.failed = [f"{title} failed: {exc}"], True
            with self._lock:
                self.running.pop(key, None)
                self.done.append(job)

        threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
        return job

    def finished(self) -> list[Job]:
        """Jobs that completed since the last call (oldest first)."""
        out = []
        with self._lock:
            while self.done:
                out.append(self.done.popleft())
        return out


def get_jobs(ctx: "Context") -> Jobs:
    return ctx.service("jobs", Jobs)


def in_background(ctx: "Context", key: str, title: str, work: Callable[[], list[str]]) -> list[str]:
    """Run work off the UI thread in the TUI; run it directly in headless mode."""
    if not ctx.interactive:
        return list(work() or [])
    job = get_jobs(ctx).start(key, title, work)
    if job is None:
        return [f"{title}: still fetching — the result will appear here"]
    return [job.placeholder]


def place_results(log: list[str], job: Job) -> bool:
    """Swap the job's placeholder in the log for its lines. False if it is gone."""
    for i in range(len(log) - 1, -1, -1):
        if log[i] == job.placeholder:
            log[i:i + 1] = job.lines or [f"{job.title}: (no output)"]
            return True
    return False
