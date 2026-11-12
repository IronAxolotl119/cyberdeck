"""tui.py — the full-screen terminal interface.

This draws three areas on the screen:

  1. A status bar at the top (title, clock, temp, battery, wifi, link).
  2. A scrolling output pane in the middle (command results).
  3. A command prompt at the bottom, where you type.

It uses Python's built-in curses library. Colors come from
config/theme.ini and labels/sizes from config/layout.ini, so you can
restyle the deck without editing this file.

If the terminal has no color, or is very small, the UI simplifies
itself instead of crashing. Press Ctrl-C or type `quit` to leave.
"""

from __future__ import annotations

import curses
import curses.ascii
import sys
import time
from collections import deque
from typing import Iterable

from cyberdeck.commands import Context, Registry
from cyberdeck.config import Layout, Theme

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
ASCII_BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "ltee": "+", "rtee": "+"}
UNICODE_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│", "ltee": "├", "rtee": "┤"}


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
PAIR_FRAME = 1
PAIR_TITLE = 2
PAIR_STATUS = 3
PAIR_TEXT = 4
PAIR_PROMPT = 5
PAIR_WARN = 6
PAIR_ALERT = 7
PAIR_OK = 8
PAIR_DIM = 9


class DeckUI:
    """The interactive curses application. run() blocks until the user quits."""

    def __init__(
        self,
        ctx: Context,
        registry: Registry,
        theme: Theme,
        layout: Layout,
    ) -> None:
        self.ctx = ctx
        self.registry = registry
        self.theme = theme
        self.layout = layout
        self.lines: deque[str] = deque(maxlen=layout.max_output_lines)
        self.scroll = 0  # how many lines up from the bottom we are looking
        self.input = ""
        self.cursor = 0
        self.history: list[str] = []
        self.history_index: int | None = None
        self.attrs: dict[str, int] = {}
        self.box = ASCII_BOX
        self._has_color = False

    def run(self, stdscr) -> None:
        """curses.wrapper calls this with a ready screen. This is the main loop."""
        self._setup(stdscr)
        self._banner()
        # timeout lets us refresh the clock even when the user is not typing
        stdscr.timeout(250)
        while not self.ctx.should_quit:
            self._draw(stdscr)
            self._handle_key(stdscr)

    def append(self, text: str | Iterable[str]) -> None:
        """Add one or more lines to the output pane and jump to the bottom."""
        if isinstance(text, str):
            pieces = text.splitlines() or [text]
        else:
            pieces = list(text)
        for piece in pieces:
            self.lines.append(piece)
        self.scroll = 0

    def _setup(self, stdscr) -> None:
        """Hide the hardware cursor style we do not want and load colors."""
        curses.curs_set(1)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        self.box = UNICODE_BOX if wants_unicode(self.layout) else ASCII_BOX
        self.attrs = self._init_colors()

    def _init_colors(self) -> dict[str, int]:
        """Build curses attributes from the theme, or plain bold/reverse if no color."""
        if not curses.has_colors():
            self._has_color = False
            return {
                "frame": curses.A_NORMAL,
                "title": curses.A_BOLD,
                "status": curses.A_REVERSE,
                "text": curses.A_NORMAL,
                "prompt": curses.A_BOLD,
                "warn": curses.A_BOLD,
                "alert": curses.A_BOLD,
                "ok": curses.A_BOLD,
                "dim": curses.A_DIM if hasattr(curses, "A_DIM") else curses.A_NORMAL,
            }
        self._has_color = True
        bg = COLOR_NAMES.get(self.theme.background.lower(), curses.COLOR_BLACK)

        def pair(pid: int, fg_name: str) -> int:
            fg = COLOR_NAMES.get(fg_name.lower(), curses.COLOR_WHITE)
            try:
                curses.init_pair(pid, fg, bg)
            except curses.error:
                return curses.A_NORMAL
            return curses.color_pair(pid)

        return {
            "frame": pair(PAIR_FRAME, self.theme.frame),
            "title": pair(PAIR_TITLE, self.theme.title) | curses.A_BOLD,
            "status": pair(PAIR_STATUS, self.theme.status),
            "text": pair(PAIR_TEXT, self.theme.text),
            "prompt": pair(PAIR_PROMPT, self.theme.prompt) | curses.A_BOLD,
            "warn": pair(PAIR_WARN, self.theme.warn) | curses.A_BOLD,
            "alert": pair(PAIR_ALERT, self.theme.alert) | curses.A_BOLD,
            "ok": pair(PAIR_OK, self.theme.ok) | curses.A_BOLD,
            "dim": pair(PAIR_DIM, self.theme.dim),
        }

    def _banner(self) -> None:
        """A short greeting so the output pane is not empty on first launch."""
        link = self.ctx.companion.link_label()
        self.append(
            [
                f"deck online  mode={self.ctx.mode}",
                f"companion: {link}",
                "type 'help' for commands, 'quit' to leave",
            ]
        )

    def _draw(self, stdscr) -> None:
        """Redraw the whole screen from the current state."""
        stdscr.erase()
        rows, cols = stdscr.getmaxyx()
        if rows < self.layout.min_rows or cols < self.layout.min_cols:
            _safe_add(stdscr, 0, 0, "terminal too small — resize me", self.attrs.get("alert", 0))
            stdscr.refresh()
            return

        # Row layout: top frame+title, status, divider, output, divider, prompt, bottom frame.
        self._draw_frame(stdscr, rows, cols)
        self._draw_header(stdscr, cols)
        self._draw_output(stdscr, rows, cols)
        self._draw_prompt(stdscr, rows, cols)
        stdscr.refresh()

    def _draw_frame(self, stdscr, rows: int, cols: int) -> None:
        """Draw the outer box and the two horizontal dividers."""
        b = self.box
        attr = self.attrs["frame"]
        top = b["tl"] + b["h"] * (cols - 2) + b["tr"]
        mid = b["ltee"] + b["h"] * (cols - 2) + b["rtee"]
        bot = b["bl"] + b["h"] * (cols - 2) + b["br"]
        _safe_add(stdscr, 0, 0, top, attr)
        _safe_add(stdscr, 2, 0, mid, attr)          # under the status line
        _safe_add(stdscr, rows - 3, 0, mid, attr)   # above the prompt
        _safe_add(stdscr, rows - 1, 0, bot, attr)
        for y in range(1, rows - 1):
            if y in {2, rows - 3}:
                continue
            _safe_add(stdscr, y, 0, b["v"], attr)
            _safe_add(stdscr, y, cols - 1, b["v"], attr)

    def _draw_header(self, stdscr, cols: int) -> None:
        """Title + clock on row 1, sensor/link summary on row 2."""
        reading = self.ctx.sensors.read()
        clock = time.strftime(self.layout.clock_format)
        title = f" {self.layout.title} "
        right = f" {clock} "
        # Put the title on the left and the clock on the right of the top edge.
        _safe_add(stdscr, 0, 2, title[: max(0, cols - 4)], self.attrs["title"])
        clock_x = max(2, cols - len(right) - 2)
        if clock_x > len(title) + 2:
            _safe_add(stdscr, 0, clock_x, right, self.attrs["title"])

        # Status line: pick a color if temp is hot or battery is low.
        temp = reading.temp_label()
        cpu = f"CPU {reading.cpu_percent:.0f}%" if reading.cpu_percent is not None else "CPU --"
        mem = f"MEM {reading.mem_percent:.0f}%" if reading.mem_percent is not None else "MEM --"
        bat = reading.battery_label()
        wifi = reading.wifi_label()
        link = self.ctx.companion.link_label()
        status = f" {temp}  {cpu}  {mem}  {bat}  {wifi}  {link}"
        attr = self.attrs["status"]
        if reading.cpu_temp_c is not None and reading.cpu_temp_c >= 70:
            attr = self.attrs["alert"]
        elif reading.battery_percent is not None and reading.battery_percent <= 15:
            attr = self.attrs["warn"]
        elif self.ctx.companion.is_linked():
            attr = self.attrs["ok"]
        _safe_add(stdscr, 1, 1, status[: max(0, cols - 2)].ljust(max(0, cols - 2)), attr)

    def _draw_output(self, stdscr, rows: int, cols: int) -> None:
        """Fill the middle pane with the visible slice of the log."""
        top = 3
        bottom = rows - 4  # last output row (inclusive)
        height = bottom - top + 1
        if height <= 0:
            return
        visible = list(self.lines)
        # scroll=0 means "pinned to the end"
        end = len(visible) - self.scroll
        start = max(0, end - height)
        end = max(start, end)
        window = visible[start:end]
        inner = max(0, cols - 4)
        attr = self.attrs["text"]
        for i in range(height):
            line = window[i] if i < len(window) else ""
            _safe_add(stdscr, top + i, 2, line[:inner].ljust(inner), attr)

    def _draw_prompt(self, stdscr, rows: int, cols: int) -> None:
        """Draw the command line and place the cursor in the right spot."""
        y = rows - 2
        prefix = self.layout.prompt
        inner = max(0, cols - 4)
        # If the typed text is long, keep the cursor visible by sliding a window.
        room = max(1, inner - len(prefix))
        if self.cursor < 0:
            self.cursor = 0
        if self.cursor > len(self.input):
            self.cursor = len(self.input)
        left = 0
        if self.cursor > room - 1:
            left = self.cursor - (room - 1)
        shown = self.input[left : left + room]
        text = (prefix + shown)[:inner]
        _safe_add(stdscr, y, 2, text.ljust(inner), self.attrs["prompt"])
        cursor_x = 2 + len(prefix) + (self.cursor - left)
        try:
            stdscr.move(y, min(cursor_x, cols - 2))
        except curses.error:
            pass

    def _handle_key(self, stdscr) -> None:
        """Wait briefly for a key and update input / scroll / commands."""
        try:
            key = stdscr.get_wch()
        except curses.error:
            return  # timeout: the next loop will just redraw the clock
        if isinstance(key, str) and key == "":
            return

        # Ctrl-C or Ctrl-D on an empty line leaves the app.
        if key in (curses.ascii.ETX, curses.ascii.EOT) and not self.input:
            self.ctx.should_quit = True
            return
        if key == curses.ascii.EOT:
            return
        if key == curses.ascii.FF:  # Ctrl-L
            self.lines.clear()
            self.scroll = 0
            return
        if key in (curses.KEY_RESIZE,):
            return  # the next draw picks up the new size
        if key in (curses.KEY_ENTER, "\n", "\r"):
            self._submit()
            return
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if self.cursor > 0:
                self.input = self.input[: self.cursor - 1] + self.input[self.cursor :]
                self.cursor -= 1
            return
        if key == curses.KEY_DC:
            self.input = self.input[: self.cursor] + self.input[self.cursor + 1 :]
            return
        if key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
            return
        if key == curses.KEY_RIGHT:
            self.cursor = min(len(self.input), self.cursor + 1)
            return
        if key in (curses.KEY_HOME, curses.ascii.SOH):
            self.cursor = 0
            return
        if key in (curses.KEY_END, curses.ascii.ENQ):
            self.cursor = len(self.input)
            return
        if key == curses.KEY_PPAGE:
            pane = max(1, stdscr.getmaxyx()[0] - 8)
            max_scroll = max(0, len(self.lines) - 1)
            self.scroll = min(max_scroll, self.scroll + pane)
            return
        if key == curses.KEY_NPAGE:
            pane = max(1, stdscr.getmaxyx()[0] - 8)
            self.scroll = max(0, self.scroll - pane)
            return
        if key == curses.KEY_UP:
            self._history(-1)
            return
        if key == curses.KEY_DOWN:
            self._history(1)
            return
        # Printable characters are appended at the cursor.
        if isinstance(key, str) and key.isprintable():
            self.input = self.input[: self.cursor] + key + self.input[self.cursor :]
            self.cursor += 1

    def _history(self, step: int) -> None:
        """Move through previously submitted commands with the arrow keys."""
        if not self.history:
            return
        if self.history_index is None:
            self.history_index = len(self.history)
        self.history_index = max(0, min(len(self.history), self.history_index + step))
        if self.history_index == len(self.history):
            self.input = ""
        else:
            self.input = self.history[self.history_index]
        self.cursor = len(self.input)

    def _submit(self) -> None:
        """Run the current input line as a command and print the result."""
        line = self.input
        self.input = ""
        self.cursor = 0
        self.history_index = None
        if not line.strip():
            return
        self.history.append(line)
        self.append(f"{self.layout.prompt}{line}")
        output = self.registry.dispatch(line, self.ctx)
        if self.ctx.should_clear:
            self.lines.clear()
            self.ctx.should_clear = False
        if output:
            self.append(output)


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


def run_tui(ctx: Context, registry: Registry, theme: Theme, layout: Layout) -> None:
    """Start the full-screen UI and restore the terminal when it exits."""
    ui = DeckUI(ctx, registry, theme, layout)
    curses.wrapper(ui.run)
