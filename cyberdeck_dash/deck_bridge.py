"""The bridge between the Rich interface and the main deck's brain.

In plain English: everything the original deck can do (the command
registry, the dungeon, notes and todo, typing hack, weather, ISS, music,
GPIO, SSH, plugins, background jobs, pet alerts…) lives in cyberdeck/
and never draws anything itself. The curses UI (cyberdeck/tui.py) was
one way to show it. This DeckBridge is the other: it does the same jobs
as tui.DeckUI, minus the drawing, so the Rich dashboard can show them.

What it keeps:
  * the console: output log, the line being typed, cursor, scrollback,
    command history (Up/Down, saved in data/history.txt) and Tab
    completion, all reused from cyberdeck/ (history.py, completion.py).
  * the "activity" slot: a full-screen mode such as the dungeon. While
    one is open, every key goes to it and the screen shows its rows.
  * requests that commands leave on the Context: clear, quit, a UI
    action like ":tab sky" or ":launcher", an external program (ssh).
  * per-frame housekeeping the curses loop used to do: service ticks
    (pet alerts, music, visualizer), finished background jobs, notices.

The Dashboard (app.py) owns one of these when started from
`python3 -m cyberdeck`, and asks it things each frame. Tests drive it
directly: bridge.run_line("note add milk") and read bridge.lines.
"""

from __future__ import annotations

import textwrap
import time
from collections import deque
from typing import Any, Iterable

from cyberdeck.activity import Activity, MenuActivity
from cyberdeck.commands import Context, Registry
from cyberdeck.completion import complete_line
from cyberdeck.config import Settings
from cyberdeck.features.core_ux import get_history, launcher_items
from cyberdeck.keys import load_hotkeys

from .shared_link import tee

class DeckBridge:
    """The main deck, minus curses. One per running interface."""

    def __init__(self, ctx: Context, registry: Registry, prompt: str = "> ", max_lines: int = 500,
                 start_services: bool = True) -> None:
        self.ctx = ctx
        self.registry = registry
        ctx.interactive = True                 # background jobs, activities: like the curses UI
        ctx.companion = tee(ctx.companion)     # one link, events copied to deck + dashboard
        self.link = ctx.companion
        self.prompt = prompt

        # --- the console --------------------------------------------------------
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.input = ""
        self.cursor = 0
        self.scroll = 0                        # rows up from the bottom of the log
        self.history = get_history(ctx)
        self.last_output: list[str] = []       # the newest command's lines (Fun tab shows them)

        # --- full-screen mode + things the Dashboard must do for us ---------------
        self.activity: Activity | None = None
        self.external: list[str] | None = None  # argv to run with the screen paused (ssh)
        self.ui_requests: list[str] = []         # ":tab sky", ":layout" … for the Dashboard
        self.closed: list[Activity] = []         # activities that just ended (Dashboard reads them)
        self._failed: set[str] = set()           # services whose ui_tick already broke

        # Hotkeys from config/keys.ini (F1-F12, ctrl+letter) work here too.
        self.hotkeys, warnings = load_hotkeys(Settings.load("keys.ini", ctx.config_dir))
        for w in warnings:
            ctx.notify(w, "warn")

        from cyberdeck.config import CONFIG_ERRORS

        for name, error in CONFIG_ERRORS.items():
            self.append(f"config/{name} ignored: {error}")
        self.append([
            f"deck online  mode={ctx.mode}",
            f"companion: {self.link.link_label()}",
            "type 'help' for commands · Tab completes · ↑↓ history · [ ] other tabs (when the line is empty)",
        ])
        if start_services:
            self.start_services()

    # =====================================================================
    # start-up and per-frame work
    # =====================================================================
    def start_services(self) -> None:
        """Start the background helpers features asked for (pet alerts, weather…)."""
        from cyberdeck.features import UI_STARTUP

        for start in UI_STARTUP:
            try:
                start(self.ctx)
            except Exception as exc:  # noqa: BLE001
                self.ctx.notify(f"startup: {exc}", "warn")

    def step(self, now: float | None = None) -> None:
        """Called once per frame by the Dashboard."""
        now = time.time() if now is None else now
        self.link.pump()
        self._tick_services(now)
        self._collect_jobs()
        if self.activity is not None:
            try:
                self.activity.update(now)
            except Exception as exc:  # noqa: BLE001 — a crashing game must not kill the deck
                self.append(f"{self.activity.title} stopped: {exc}")
                self.activity.done = True
            if self.activity.done:
                self._finish_activity()

    def _tick_services(self, now: float) -> None:
        for name, service in list(self.ctx.services.items()):
            tick = getattr(service, "ui_tick", None)
            if callable(tick) and name not in self._failed:
                try:
                    tick(self.ctx, now)
                except Exception as exc:  # noqa: BLE001 — report once, keep going
                    self._failed.add(name)
                    self.ctx.notify(f"{name} stopped: {exc}", "warn")

    def _collect_jobs(self) -> None:
        """Swap 'weather: fetching… [job 3]' placeholders for the real result."""
        from cyberdeck.jobs import get_jobs, place_results

        for job in get_jobs(self.ctx).finished():
            log = list(self.lines)
            if place_results(log, job):
                self.lines = deque(log, maxlen=self.lines.maxlen)
            else:
                self.append(job.lines)
            self.last_output = list(job.lines)
            self.ctx.notify(f"{job.title} {'failed' if job.failed else 'ready'}", "warn" if job.failed else "info")

    def take_notices(self) -> list[tuple[str, str]]:
        """Deck notices (level, text) for the dashboard ticker, oldest first."""
        out, self.ctx.notices[:] = list(self.ctx.notices), []
        return out

    # =====================================================================
    # running commands
    # =====================================================================
    def append(self, text: str | Iterable[str]) -> None:
        pieces = (text.splitlines() or [text]) if isinstance(text, str) else list(text)
        for piece in pieces:
            self.lines.append(piece)
        self.scroll = 0

    def run_line(self, line: str, remember: bool = True) -> list[str]:
        """Run one command line exactly like the curses prompt does."""
        if not line.strip():
            return []
        if remember:
            self.history.add(line)
        self.append(f"{self.prompt}{line}")
        output = self.registry.dispatch(line, self.ctx) or []
        if output:
            self.append(output)
        self.last_output = [f"{self.prompt}{line}", *output]
        self.after_command()
        return list(output)

    def run_action(self, action: str) -> None:
        """A hotkey, launcher or menu pick: ':ui action' or a command line."""
        if action.startswith(":"):
            self.ui_action(action)
        else:
            self.run_line(action, remember=False)

    def after_command(self) -> None:
        """Do whatever the command asked the UI for (see Context in commands.py)."""
        ctx = self.ctx
        if ctx.should_clear:
            self.clear()
            ctx.should_clear = False
        if ctx.ui_action:
            action, ctx.ui_action = ctx.ui_action, None
            self.ui_action(action)
        if ctx.activity is not None:
            activity, ctx.activity = ctx.activity, None
            self.open_activity(activity)
        if ctx.external:
            self.external, ctx.external = ctx.external, None

    def ui_action(self, action: str) -> None:
        """Handle ':verb arg' actions. Tab/layout ones go to the Dashboard."""
        verb, _, arg = action.lstrip(":").partition(" ")
        verb = verb.lower()
        if verb == "launcher":
            self.open_activity(MenuActivity(launcher_items(self.ctx, self.registry)))
        elif verb == "clear":
            self.clear()
        elif verb == "scroll":
            self.scroll_by(1 if arg.strip().lower() == "up" else -1)
        elif verb == "quit":
            self.ctx.should_quit = True
        else:
            self.ui_requests.append(action)     # :tab …, :layout …

    def run_external(self, argv: list[str], runner=None) -> None:
        """Run a program such as ssh (the Dashboard pauses the screen first)."""
        import subprocess

        runner = runner or subprocess.call
        try:
            code: Any = runner(argv)
        except (OSError, ValueError) as exc:
            code = f"could not start: {exc}"
        except (KeyboardInterrupt, EOFError):
            code = "interrupted"
        self.append(f"[{argv[0]} exited: {code}]")

    # =====================================================================
    # full-screen activities (dungeon, typehack, hack, boot, launcher…)
    # =====================================================================
    def open_activity(self, activity: Activity) -> None:
        if self.activity is not None and not self.activity.done:
            self.activity.done = True
            self._finish_activity()
        try:
            activity.start(time.time())
        except Exception as exc:  # noqa: BLE001
            self.append(f"{activity.title} could not start: {exc}")
            return
        self.activity = activity

    def activity_key(self, key: str) -> None:
        """Send one key to the open activity (ctrl+c always closes it)."""
        act = self.activity
        if act is None:
            return
        try:
            if key == "ctrl+c":
                act.done = True
            else:
                act.on_key(key)
        except Exception as exc:  # noqa: BLE001
            self.append(f"{act.title} stopped: {exc}")
            act.done = True
        if act.done:
            self._finish_activity()

    def _finish_activity(self) -> None:
        act, self.activity = self.activity, None
        if act is None:
            return
        self.closed.append(act)
        if act.result_lines:
            self.append(act.result_lines)
            self.last_output = list(act.result_lines)
        if act.result_prompt:
            self.set_input(act.result_prompt)
            self.ui_requests.append(":tab console")   # go where the half-typed line is
        if act.result_command:
            self.run_action(act.result_command)

    def frame_seconds(self, default: float) -> float:
        """How long the screen may wait for a key: faster while things animate."""
        wait = default
        if self.activity is not None:
            wait = min(wait, max(0.03, float(getattr(self.activity, "tick", 0.1))))
        popups = self.ctx.services.get("popups")
        if popups is not None and getattr(popups, "animating", lambda: False)():
            wait = min(wait, 0.12)
        viz = self.ctx.services.get("visualizer")
        if viz is not None and getattr(viz, "enabled", False):
            wait = min(wait, max(0.06, getattr(viz, "frame_ms", 150) / 1000))
        return wait

    # =====================================================================
    # pop-ups, badges, visualizer (read by the screen)
    # =====================================================================
    def popup(self, now: float | None = None):
        from cyberdeck.popups import get_popups

        return get_popups(self.ctx).active(time.time() if now is None else now)

    def dismiss_popup(self) -> bool:
        from cyberdeck.popups import get_popups

        return get_popups(self.ctx).dismiss()

    def badges(self) -> list[tuple[str, str]]:
        """status_badge() results from services, e.g. ('! HUNGRY', 'alert')."""
        out = []
        for service in list(self.ctx.services.values()):
            badge = getattr(service, "status_badge", None)
            if callable(badge):
                try:
                    result = badge()
                except Exception:  # noqa: BLE001
                    result = None
                if result:
                    out.append(result)
        return out

    def visualizer_text(self, now: float) -> str:
        viz = self.ctx.services.get("visualizer")
        if viz is None or not getattr(viz, "enabled", False):
            return ""
        try:
            return viz.render(now, bool(self.ctx.services.get("unicode", True)))
        except Exception:  # noqa: BLE001
            return ""

    def mood_line(self, now: float) -> tuple[str, str] | None:
        mood = self.ctx.services.get("moods")
        if mood is None:
            return None
        try:
            return mood.line(self.ctx.sensors.read(), now) or None
        except Exception:  # noqa: BLE001
            return None

    # =====================================================================
    # console keys (the Console tab sends every key here)
    # =====================================================================
    def console_key(self, key: str) -> bool:
        """Edit / run the command line. Returns False if the key was not used."""
        handler = {
            "enter": self.submit,
            "tab": self.complete,
            "backspace": self._backspace,
            "delete": self._delete,
            "left": lambda: self._move(-1),
            "right": lambda: self._move(1),
            "home": lambda: self._move_to(0),
            "ctrl+a": lambda: self._move_to(0),
            "end": lambda: self._move_to(len(self.input)),
            "ctrl+e": lambda: self._move_to(len(self.input)),
            "ctrl+u": lambda: self.set_input(""),
            "ctrl+l": self.clear,
            "up": lambda: self.set_input(self.history.previous(self.input)),
            "down": lambda: self.set_input(self.history.next(self.input)),
            "pgup": lambda: self.scroll_by(1),
            "pgdn": lambda: self.scroll_by(-1),
            "esc": lambda: self.set_input(""),
        }.get(key)
        if handler is not None:
            handler()
            return True
        if key == "space":
            key = " "
        if len(key) == 1 and key.isprintable():
            self.input = self.input[: self.cursor] + key + self.input[self.cursor:]
            self.cursor += 1
            return True
        return False

    def submit(self) -> None:
        line = self.input
        self.set_input("")
        if line.strip():
            # A leading space keeps a command out of history (same as curses).
            self.run_line(line, remember=not line.startswith(" "))

    def complete(self) -> None:
        """Tab: fill in the word, or list the choices when there are several."""
        head = self.input[: self.cursor]
        new_head, choices = complete_line(head, self.registry, self.ctx)
        if new_head != head:
            self.input = new_head + self.input[self.cursor:]
            self.cursor = len(new_head)
        elif len(choices) > 1:
            self.append("  ".join(choices))

    def set_input(self, text: str) -> None:
        self.input = text
        self.cursor = len(text)

    def clear(self) -> None:
        self.lines.clear()
        self.scroll = 0

    def scroll_by(self, pages: int, page: int = 10) -> None:
        self.scroll = max(0, self.scroll + pages * page)

    def _move(self, step: int) -> None:
        self.cursor = max(0, min(len(self.input), self.cursor + step))

    def _move_to(self, pos: int) -> None:
        self.cursor = max(0, min(len(self.input), pos))

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.input = self.input[: self.cursor - 1] + self.input[self.cursor:]
            self.cursor -= 1

    def _delete(self) -> None:
        self.input = self.input[: self.cursor] + self.input[self.cursor + 1:]

    def visual_log(self, width: int) -> list[str]:
        """The log wrapped to `width` (long lines continue below)."""
        out: list[str] = []
        for line in self.lines:
            if len(line) <= width:
                out.append(line)
            else:
                out.extend(textwrap.wrap(line, width, replace_whitespace=False) or [""])
        return out

    def log_window(self, width: int, height: int) -> list[str]:
        """The slice of the wrapped log that fits, honouring scrollback."""
        visible = self.visual_log(max(1, width))
        self.scroll = max(0, min(self.scroll, max(0, len(visible) - height)))
        end = len(visible) - self.scroll
        return visible[max(0, end - height): end]
