"""tui.py — the full-screen terminal interface.

The screen, top to bottom:

  ┌─ TITLE ─────────────────────────── clock ─┐   frame + title + clock
  │ temp  cpu  mem  battery  wifi  link        │   status line
  │ notices / deck mood         ▁▃▅▇ visualizer│   ticker line (optional)
  ├──────────────────────┬─ PULSE ────────────┤
  │ command output       │ pet pane            │   body: split panes…
  │                      ├─ SYSTEM ───────────┤   …or tabs (one view)
  │                      │ system pane         │
  ├──────────────────────┴────────────────────┤
  │ > your command                             │   prompt
  └─ F1 help  F2 menu  F4 tab ────────────────┘   hotkey hints

What lives where:
  * Colors: config/theme.ini. Labels and sizes: config/layout.ini.
  * Pane arrangement ([panes] in layout.ini) is worked out by
    panes.compute_layout(); the panes themselves are in panes.py and
    the feature modules.
  * Hotkeys and launcher entries: config/keys.ini (keys.py).
  * History (Up/Down) is history.py; Tab completion is completion.py.
  * Full-screen modes (games, effects, menus) are Activity objects
    (activity.py). This file just runs them and paints their rows.

This is the only file in the project that imports curses for drawing.
If the terminal has no color, or is very small, the UI simplifies
itself instead of crashing. Press Ctrl-C or type `quit` to leave.
"""

from __future__ import annotations

import curses
import subprocess
import sys
import textwrap
import time
from collections import deque
from typing import Iterable

from cyberdeck.activity import Activity, MenuActivity, Row
from cyberdeck.commands import Context, Registry
from cyberdeck.completion import complete_line
from cyberdeck.config import Layout, Settings, Theme
from cyberdeck.features.core_ux import get_history, launcher_items
from cyberdeck.keys import key_name, load_hotkeys
from cyberdeck.panes import PANES, ScreenLayout, compute_layout, load_pane_settings

# Standard curses color names accepted in config/theme.ini.
# "default" means "use the terminal's own color" (pair -1).
COLOR_NAMES = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
    "default": -1,
}

# ASCII fallback when the terminal cannot draw Unicode box characters.
ASCII_BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
             "ltee": "+", "rtee": "+", "ttee": "+", "btee": "+"}
UNICODE_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
               "ltee": "├", "rtee": "┤", "ttee": "┬", "btee": "┴"}

# How long a notice stays on the ticker line (seconds).
NOTICE_SECONDS = 6.0


def wants_unicode(layout: Layout) -> bool:
    """Decide whether to use box-drawing characters."""
    setting = (layout.use_unicode or "auto").lower()
    if setting in {"yes", "true", "1", "on"}:
        return True
    if setting in {"no", "false", "0", "off", "ascii"}:
        return False
    encoding = sys.stdout.encoding or ""
    return "utf" in encoding.lower()


# Color pair ids we assign once at startup. 0 is reserved by curses.
PAIR_IDS = {"frame": 1, "title": 2, "status": 3, "text": 4, "prompt": 5,
            "warn": 6, "alert": 7, "ok": 8, "dim": 9}


class DeckUI:
    """The interactive curses application. run() blocks until the user quits."""

    def __init__(self, ctx: Context, registry: Registry, theme: Theme, layout: Layout,
                 startup: list[Activity] | None = None) -> None:
        self.ctx = ctx
        self.registry = registry
        self.theme = theme
        self.layout = layout
        ctx.interactive = True

        # Output log and prompt state.
        self.lines: deque[str] = deque(maxlen=layout.max_output_lines)
        self.scroll = 0  # visual rows up from the bottom of the log
        self.input = ""
        self.cursor = 0
        self.history = get_history(ctx)

        # Panes / tabs (layout.ini [panes]) and hotkeys (keys.ini).
        self.panes = load_pane_settings(Settings.load("layout.ini", ctx.config_dir))
        self.mode = self.panes.mode
        ctx.services["pane_settings"] = self.panes  # panes read pet_refresh from here
        self.tab = 0  # index into the tab list
        self.hotkeys, key_warnings = load_hotkeys(Settings.load("keys.ini", ctx.config_dir))
        for warning in key_warnings:
            ctx.notify(warning, "warn")

        # Ticker line: the current notice and when it expires.
        self.notice: tuple[str, str, float] | None = None
        # Activities to run before the prompt appears (e.g. the boot animation).
        self.startup = list(startup or [])

        self.attrs: dict[str, int] = {}
        self.box = ASCII_BOX
        self.refresh_ms = 250

    # --- main loop ------------------------------------------------------

    def run(self, stdscr) -> None:
        """curses.wrapper calls this with a ready screen. This is the main loop."""
        self._setup(stdscr)
        for activity in self.startup:
            self._run_activity(stdscr, activity)
        self._banner()
        while not self.ctx.should_quit:
            self._tick_services()
            self._draw(stdscr)
            stdscr.timeout(self._frame_ms())
            self._handle_key(stdscr)
            self._after_command(stdscr)

    def append(self, text: str | Iterable[str]) -> None:
        """Add one or more lines to the output pane and jump to the bottom."""
        pieces = (text.splitlines() or [text]) if isinstance(text, str) else list(text)
        for piece in pieces:
            self.lines.append(piece)
        self.scroll = 0

    def _frame_ms(self) -> int:
        """Redraw faster while something animated (like the visualizer) is on."""
        viz = self.ctx.services.get("visualizer")
        if viz is not None and getattr(viz, "enabled", False):
            return max(60, int(getattr(viz, "frame_ms", 150)))
        return self.refresh_ms

    def _tick_services(self) -> None:
        """Let background helpers (pet alerts, music…) do a little work."""
        now = time.time()
        for service in list(self.ctx.services.values()):
            tick = getattr(service, "ui_tick", None)
            if callable(tick):
                try:
                    tick(self.ctx, now)
                except Exception as exc:  # keep the deck alive, report once
                    self.ctx.notify(f"{type(service).__name__}: {exc}", "warn")

    # --- setup ------------------------------------------------------------

    def _setup(self, stdscr) -> None:
        """Load colors and box characters."""
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        self.box = UNICODE_BOX if wants_unicode(self.layout) else ASCII_BOX
        self.attrs = self._init_colors()
        stdscr.keypad(True)

    def _init_colors(self) -> dict[str, int]:
        """Build curses attributes from the theme, or plain bold/reverse if no color."""
        if not curses.has_colors():
            dim = curses.A_DIM if hasattr(curses, "A_DIM") else curses.A_NORMAL
            return {"frame": curses.A_NORMAL, "title": curses.A_BOLD, "status": curses.A_REVERSE,
                    "text": curses.A_NORMAL, "prompt": curses.A_BOLD, "warn": curses.A_BOLD,
                    "alert": curses.A_BOLD | curses.A_REVERSE, "ok": curses.A_BOLD, "dim": dim}
        bg = COLOR_NAMES.get(self.theme.background.lower(), curses.COLOR_BLACK)
        attrs: dict[str, int] = {}
        for role, pid in PAIR_IDS.items():
            fg = COLOR_NAMES.get(self.theme.color(role).lower(), curses.COLOR_WHITE)
            try:
                curses.init_pair(pid, fg, bg)
                attrs[role] = curses.color_pair(pid)
            except curses.error:
                attrs[role] = curses.A_NORMAL
        for role in ("title", "prompt", "warn", "alert", "ok"):
            attrs[role] |= curses.A_BOLD
        return attrs

    def _banner(self) -> None:
        """A short greeting so the output pane is not empty on first launch."""
        from cyberdeck.config import CONFIG_ERRORS

        for name, error in CONFIG_ERRORS.items():
            self.append(f"config/{name} ignored: {error}")
        self.append([
            f"deck online  mode={self.ctx.mode}",
            f"companion: {self.ctx.companion.link_label()}",
            "type 'help' for commands · Tab completes · F2 opens the menu",
        ])

    # --- drawing ----------------------------------------------------------

    def _tabs(self) -> list[str]:
        """Tab names that actually exist (unknown names in layout.ini are skipped)."""
        tabs = [t for t in self.panes.tabs if t == "output" or t in PANES]
        return tabs or ["output"]

    def _draw(self, stdscr) -> None:
        """Redraw the whole screen from the current state."""
        stdscr.erase()
        rows, cols = stdscr.getmaxyx()
        if rows < self.layout.min_rows or cols < self.layout.min_cols:
            _safe_add(stdscr, 0, 0, "terminal too small — resize me", self.attrs.get("alert", 0))
            stdscr.refresh()
            return
        geo = compute_layout(rows, cols, self.panes, self.mode)
        self._draw_frame(stdscr, rows, cols, geo)
        self._draw_header(stdscr, cols, geo)
        self._draw_ticker(stdscr, cols, geo)
        if geo.mode == "split":
            self._draw_log(stdscr, geo.output.top, geo.output.left, geo.output.height, geo.output.width)
            for name, title_row, rect in geo.side:
                self._draw_pane_title(stdscr, geo, title_row, cols, PANES[name].title)
                self._draw_rows(stdscr, rect.top, rect.left, rect.height, rect.width,
                                self._safe_pane(name, rect.width, rect.height))
        else:
            self._draw_tab_bar(stdscr, geo, cols)
            current = self._tabs()[self.tab % len(self._tabs())]
            r = geo.output
            if current == "output":
                self._draw_log(stdscr, r.top, r.left, r.height, r.width)
            else:
                self._draw_rows(stdscr, r.top, r.left, r.height, r.width, self._safe_pane(current, r.width, r.height))
        self._draw_hints(stdscr, rows, cols)
        self._draw_prompt(stdscr, geo, cols)
        stdscr.refresh()

    def _safe_pane(self, name: str, width: int, height: int) -> list[Row]:
        """Render one pane, turning any error into a visible message."""
        try:
            return PANES[name].render(self.ctx, width, height)
        except Exception as exc:
            return [[(f"{name}: {exc}", "alert")]]

    def _draw_frame(self, stdscr, rows: int, cols: int, geo: ScreenLayout) -> None:
        """Outer box, the two horizontal dividers, and the split line."""
        b, attr = self.box, self.attrs["frame"]
        inner = cols - 2
        _safe_add(stdscr, 0, 0, b["tl"] + b["h"] * inner + b["tr"], attr)
        for y in (geo.top_divider, geo.bottom_divider):
            _safe_add(stdscr, y, 0, b["ltee"] + b["h"] * inner + b["rtee"], attr)
        _safe_add(stdscr, rows - 1, 0, b["bl"] + b["h"] * inner + b["br"], attr)
        for y in range(1, rows - 1):
            if y in {geo.top_divider, geo.bottom_divider}:
                continue
            _safe_add(stdscr, y, 0, b["v"], attr)
            _safe_add(stdscr, y, cols - 1, b["v"], attr)
        if geo.divider_col is not None:
            x = geo.divider_col
            _safe_add(stdscr, geo.top_divider, x, b["ttee"], attr)
            _safe_add(stdscr, geo.bottom_divider, x, b["btee"], attr)
            for y in range(geo.body.top, geo.bottom_divider):
                _safe_add(stdscr, y, x, b["v"], attr)

    def _draw_pane_title(self, stdscr, geo: ScreenLayout, row: int, cols: int, title: str) -> None:
        """A '├─ PULSE ────┤' line above a side pane."""
        b, x = self.box, geo.divider_col or 0
        if row != geo.top_divider:
            line = b["ltee"] + b["h"] * (cols - x - 2) + b["rtee"]
            _safe_add(stdscr, row, x, line, self.attrs["frame"])
        _safe_add(stdscr, row, x + 2, f" {title} ", self.attrs["title"])

    def _draw_tab_bar(self, stdscr, geo: ScreenLayout, cols: int) -> None:
        """Tab names on the top divider; the active one is highlighted."""
        x = 2
        for i, name in enumerate(self._tabs()):
            title = "OUTPUT" if name == "output" else PANES[name].title
            label = f" {i + 1}:{title} "
            active = i == self.tab % len(self._tabs())
            attr = (self.attrs["prompt"] | curses.A_REVERSE) if active else self.attrs["dim"]
            if x + len(label) >= cols - 2:
                break
            _safe_add(stdscr, geo.top_divider, x, label, attr)
            x += len(label) + 1

    def _draw_header(self, stdscr, cols: int, geo: ScreenLayout) -> None:
        """Title + clock on the top edge, sensor/link summary below."""
        reading = self.ctx.sensors.read()
        title = f" {self.layout.title} "
        right = f" {time.strftime(self.layout.clock_format)} "
        _safe_add(stdscr, 0, 2, title[: max(0, cols - 4)], self.attrs["title"])
        clock_x = max(2, cols - len(right) - 2)
        if clock_x > len(title) + 2:
            _safe_add(stdscr, 0, clock_x, right, self.attrs["title"])

        cpu = f"CPU {reading.cpu_percent:.0f}%" if reading.cpu_percent is not None else "CPU --"
        mem = f"MEM {reading.mem_percent:.0f}%" if reading.mem_percent is not None else "MEM --"
        status = (f" {reading.temp_label()}  {cpu}  {mem}  {reading.battery_label()}  "
                  f"{reading.wifi_label()}  {self.ctx.companion.link_label()}")
        attr = self.attrs["status"]
        if reading.cpu_temp_c is not None and reading.cpu_temp_c >= 70:
            attr = self.attrs["alert"]
        elif reading.battery_percent is not None and reading.battery_percent <= 15:
            attr = self.attrs["warn"]
        elif self.ctx.companion.is_linked():
            attr = self.attrs["ok"]
        width = max(0, cols - 2)
        _safe_add(stdscr, geo.status_row, 1, status[:width].ljust(width), attr)

    def _draw_ticker(self, stdscr, cols: int, geo: ScreenLayout) -> None:
        """Notices (or the deck's mood) on the left, the visualizer on the right."""
        if geo.ticker_row is None:
            return
        now = time.time()
        width = max(0, cols - 4)
        # Right side: the audio-style visualizer, if that feature is on.
        viz_text = ""
        viz = self.ctx.services.get("visualizer")
        if viz is not None and getattr(viz, "enabled", False):
            try:
                viz_text = viz.render(now, self.box is UNICODE_BOX)
            except Exception:
                viz_text = ""
        left_w = max(0, width - len(viz_text) - (1 if viz_text else 0))
        text, role = self._ticker_text(now)
        attr = self.attrs.get(role, self.attrs["dim"])
        if role == "alert" and int(now * 2) % 2:
            attr |= curses.A_REVERSE  # flash alerts
        _safe_add(stdscr, geo.ticker_row, 2, text[:left_w].ljust(left_w), attr)
        if viz_text:
            _safe_add(stdscr, geo.ticker_row, 2 + left_w + 1, viz_text, self.attrs["ok"])

    def _ticker_text(self, now: float) -> tuple[str, str]:
        """Pick what the ticker line says right now: a notice, a mood, or a hint."""
        # Take the next queued notice once the current one has had its time.
        if self.ctx.notices and (self.notice is None or now >= self.notice[2]):
            level, text = self.ctx.notices.pop(0)
            self.notice = (text, level, now + NOTICE_SECONDS)
        if self.notice and now < self.notice[2]:
            return f"» {self.notice[0]}", self.notice[1]
        self.notice = None
        mood = self.ctx.services.get("moods")
        if mood is not None:
            try:
                line = mood.line(self.ctx.sensors.read(), now)
                if line:
                    return line
            except Exception:
                pass
        return "Tab completes · ↑↓ history · F2 menu · F4 tabs", "dim"

    def _draw_hints(self, stdscr, rows: int, cols: int) -> None:
        """Hotkey reminders on the bottom frame line."""
        if not self.panes.hints:
            return
        parts, seen = [], set()
        for key, action in self.hotkeys.items():
            if action in seen:
                continue  # only the first key for each action
            seen.add(action)
            label = action.lstrip(":").split()[0] if action.startswith(":") else action.split()[0]
            parts.append(f"{key.upper().replace('CTRL+', '^')} {label}")
        text = " " + "  ".join(parts) + " "
        _safe_add(stdscr, rows - 1, 2, text[: max(0, cols - 4)], self.attrs["dim"])

    def _visual_log(self, width: int) -> list[str]:
        """The log wrapped to the pane width (long lines continue below)."""
        out: list[str] = []
        for line in self.lines:
            if len(line) <= width:
                out.append(line)
            else:
                out.extend(textwrap.wrap(line, width, replace_whitespace=False) or [""])
        return out

    def _draw_log(self, stdscr, top: int, left: int, height: int, width: int) -> None:
        """Fill an area with the visible slice of the command log."""
        if height <= 0 or width <= 0:
            return
        visible = self._visual_log(width)
        self.scroll = max(0, min(self.scroll, max(0, len(visible) - height)))
        end = len(visible) - self.scroll
        window = visible[max(0, end - height) : end]
        prompt = self.layout.prompt.strip()
        for i in range(height):
            line = window[i] if i < len(window) else ""
            role = "dim" if prompt and line.startswith(prompt) else "text"
            _safe_add(stdscr, top + i, left, line.ljust(width)[:width], self.attrs[role])
        if self.scroll:
            tag = f" ↑{self.scroll} "
            _safe_add(stdscr, top, left + max(0, width - len(tag)), tag, self.attrs["warn"])

    def _draw_rows(self, stdscr, top: int, left: int, height: int, width: int, rows: list[Row]) -> None:
        """Paint (text, role) rows from a pane or activity into an area."""
        for i in range(max(0, height)):
            x = left
            used = 0
            row = rows[i] if i < len(rows) else []
            for text, role in row:
                room = width - used
                if room <= 0:
                    break
                piece = text[:room]
                _safe_add(stdscr, top + i, x, piece, self.attrs.get(role, self.attrs["text"]))
                x += len(piece)
                used += len(piece)

    def _draw_prompt(self, stdscr, geo: ScreenLayout, cols: int) -> None:
        """Draw the command line and place the cursor in the right spot."""
        y = geo.prompt_row
        prefix = self.layout.prompt
        inner = max(0, cols - 4)
        room = max(1, inner - len(prefix))
        self.cursor = max(0, min(self.cursor, len(self.input)))
        left = self.cursor - (room - 1) if self.cursor > room - 1 else 0
        shown = self.input[left : left + room]
        _safe_add(stdscr, y, 2, (prefix + shown)[:inner].ljust(inner), self.attrs["prompt"])
        try:
            stdscr.move(y, min(2 + len(prefix) + (self.cursor - left), cols - 2))
        except curses.error:
            pass

    # --- keys ---------------------------------------------------------------

    def _handle_key(self, stdscr) -> None:
        """Wait briefly for a key and update input / scroll / commands."""
        try:
            raw = stdscr.get_wch()
        except curses.error:
            return  # timeout: the next loop just redraws the clock
        key = key_name(raw)

        # Hotkeys from keys.ini win over everything except typing.
        if key in self.hotkeys:
            self._run_action(self.hotkeys[key], stdscr)
            return
        if key in ("ctrl+c", "ctrl+d"):
            if not self.input:
                self.ctx.should_quit = True
            return
        handler = {
            "enter": self._submit,
            "tab": self._complete,
            "backspace": self._backspace,
            "delete": self._delete,
            "left": lambda: self._move(-1),
            "right": lambda: self._move(1),
            "home": lambda: self._move_to(0),
            "ctrl+a": lambda: self._move_to(0),
            "end": lambda: self._move_to(len(self.input)),
            "ctrl+e": lambda: self._move_to(len(self.input)),
            "ctrl+u": self._wipe,
            "ctrl+l": self._clear,
            "up": lambda: self._set_input(self.history.previous(self.input)),
            "down": lambda: self._set_input(self.history.next(self.input)),
            "pgup": lambda: self._scroll(stdscr, 1),
            "pgdn": lambda: self._scroll(stdscr, -1),
        }.get(key)
        if handler is not None:
            handler()
            return
        if len(key) == 1 and key.isprintable():
            self.input = self.input[: self.cursor] + key + self.input[self.cursor :]
            self.cursor += 1

    def _set_input(self, text: str) -> None:
        self.input = text
        self.cursor = len(text)

    def _move(self, step: int) -> None:
        self.cursor = max(0, min(len(self.input), self.cursor + step))

    def _move_to(self, pos: int) -> None:
        self.cursor = pos

    def _wipe(self) -> None:
        self._set_input("")

    def _clear(self) -> None:
        self.lines.clear()
        self.scroll = 0

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.input = self.input[: self.cursor - 1] + self.input[self.cursor :]
            self.cursor -= 1

    def _delete(self) -> None:
        self.input = self.input[: self.cursor] + self.input[self.cursor + 1 :]

    def _scroll(self, stdscr, direction: int) -> None:
        page = max(1, stdscr.getmaxyx()[0] - 10)
        self.scroll = max(0, self.scroll + direction * page)

    def _complete(self) -> None:
        """Tab: fill in the word, or list the choices when there are several."""
        head = self.input[: self.cursor]
        new_head, choices = complete_line(head, self.registry, self.ctx)
        if new_head != head:
            self.input = new_head + self.input[self.cursor :]
            self.cursor = len(new_head)
        elif len(choices) > 1:
            self.append("  ".join(choices))

    # --- running commands and actions ------------------------------------

    def _submit(self) -> None:
        """Run the current input line as a command and print the result."""
        line = self.input
        self._set_input("")
        if not line.strip():
            return
        self.history.add(line)
        self._run_command(line)

    def _run_command(self, line: str) -> None:
        """Echo, dispatch, and print a command's output."""
        self.append(f"{self.layout.prompt}{line}")
        output = self.registry.dispatch(line, self.ctx)
        if output:
            self.append(output)

    def _run_action(self, action: str, stdscr) -> None:
        """A hotkey or menu pick: a ':ui action' or a normal command."""
        if action.startswith(":"):
            self._ui_action(action, stdscr)
        else:
            self._run_command(action)
            self._after_command(stdscr)

    def _after_command(self, stdscr) -> None:
        """Carry out anything a command asked the UI to do."""
        ctx = self.ctx
        if ctx.should_clear:
            self._clear()
            ctx.should_clear = False
        if ctx.ui_action:
            action, ctx.ui_action = ctx.ui_action, None
            self._ui_action(action, stdscr)
        if ctx.activity is not None:
            activity, ctx.activity = ctx.activity, None
            self._run_activity(stdscr, activity)
        if ctx.external:
            argv, ctx.external = ctx.external, None
            self._run_external(stdscr, argv)

    def _ui_action(self, action: str, stdscr) -> None:
        """Run a ':verb arg' UI action from a hotkey, the menu, or a command."""
        verb, _, arg = action.lstrip(":").partition(" ")
        verb, arg = verb.lower(), arg.strip().lower()
        tabs = self._tabs()
        if verb == "launcher":
            self._run_activity(stdscr, MenuActivity(launcher_items(self.ctx, self.registry)))
        elif verb == "tab":
            # From split mode, picking a tab "zooms" that view full width.
            if self.mode == "split":
                self.mode = "tabs"
            if arg in ("", "next"):
                self.tab = (self.tab + 1) % len(tabs)
            elif arg == "prev":
                self.tab = (self.tab - 1) % len(tabs)
            elif arg in tabs:
                self.tab = tabs.index(arg)
            elif arg.isdigit() and 1 <= int(arg) <= len(tabs):
                self.tab = int(arg) - 1
            else:
                self.ctx.notify(f"no tab named {arg}", "warn")
        elif verb == "layout":
            if arg in ("split", "tabs"):
                self.mode = arg
            else:
                self.mode = "tabs" if self.mode == "split" else "split"
            self.ctx.notify(f"layout: {self.mode}")
        elif verb == "clear":
            self._clear()
        elif verb == "scroll":
            self._scroll(stdscr, 1 if arg == "up" else -1)
        elif verb == "quit":
            self.ctx.should_quit = True
        else:
            self.ctx.notify(f"unknown UI action :{verb}", "warn")

    def _run_activity(self, stdscr, activity: Activity) -> None:
        """Hand the screen to a full-screen (or popup) Activity until it is done."""
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        activity.start(time.time())
        try:
            while not activity.done and not self.ctx.should_quit:
                activity.update(time.time())
                if activity.done:
                    break
                self._paint_activity(stdscr, activity)
                stdscr.timeout(max(15, int(activity.tick * 1000)))
                try:
                    raw = stdscr.get_wch()
                except curses.error:
                    continue
                key = key_name(raw)
                if key == "ctrl+c":
                    activity.done = True
                elif key != "resize":
                    activity.on_key(key)
        except Exception as exc:  # a crashing game must not kill the deck
            self.append(f"{activity.title} stopped: {exc}")
        finally:
            try:
                curses.curs_set(1)
            except curses.error:
                pass
        # Results the activity left for the prompt / log.
        if activity.result_lines:
            self.append(activity.result_lines)
        if activity.result_prompt:
            self._set_input(activity.result_prompt)
        if activity.result_command:
            self._run_action(activity.result_command, stdscr)

    def _paint_activity(self, stdscr, activity: Activity) -> None:
        """Draw one frame of an activity: full screen, or a framed popup."""
        rows, cols = stdscr.getmaxyx()
        if not activity.overlay:
            stdscr.erase()
            self._draw_rows(stdscr, 0, 0, rows, cols - 1, activity.render(cols - 1, rows))
            stdscr.refresh()
            return
        self._draw(stdscr)
        want_w, want_h = activity.overlay_size
        w, h = min(want_w, cols - 4), min(want_h, rows - 2)
        top, left = max(0, (rows - h) // 2), max(0, (cols - w) // 2)
        b, attr = self.box, self.attrs["frame"]
        _safe_add(stdscr, top, left, b["tl"] + b["h"] * (w - 2) + b["tr"], attr)
        _safe_add(stdscr, top, left + 2, f" {activity.title} ", self.attrs["title"])
        for y in range(top + 1, top + h - 1):
            _safe_add(stdscr, y, left, b["v"] + " " * (w - 2) + b["v"], attr)
        _safe_add(stdscr, top + h - 1, left, b["bl"] + b["h"] * (w - 2) + b["br"], attr)
        self._draw_rows(stdscr, top + 1, left + 2, h - 2, w - 4, activity.render(w - 4, h - 2))
        stdscr.refresh()

    def _run_external(self, stdscr, argv: list[str]) -> None:
        """Leave curses, run a program (like ssh) in the real terminal, come back."""
        curses.def_prog_mode()
        curses.endwin()
        code: int | str
        try:
            print(f"\n[cyberdeck] running: {' '.join(argv)}\n", flush=True)
            code = subprocess.call(argv)
            if code != 0:
                # Keep any error message on screen until it has been read.
                input(f"\n[cyberdeck] {argv[0]} exited with {code} — press Enter to return to the deck ")
        except (OSError, ValueError) as exc:
            code = f"could not start: {exc}"
        except (KeyboardInterrupt, EOFError):
            code = "interrupted"
        finally:
            curses.reset_prog_mode()
            stdscr.clear()
            stdscr.refresh()
        self.append(f"[{argv[0]} exited: {code}]")


def _safe_add(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write text without throwing if we hit the bottom-right corner or a short line."""
    try:
        rows, cols = stdscr.getmaxyx()
        if y < 0 or x < 0 or y >= rows or x >= cols:
            return
        room = cols - x
        if room <= 0:
            return
        stdscr.addstr(y, x, text[:room], attr)
    except curses.error:
        pass


def run_tui(ctx: Context, registry: Registry, theme: Theme, layout: Layout,
            startup: list[Activity] | None = None) -> None:
    """Start the full-screen UI and restore the terminal when it exits."""
    ui = DeckUI(ctx, registry, theme, layout, startup=startup)
    curses.wrapper(ui.run)
