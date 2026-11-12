"""Draw the main deck's (text, role) rows with Rich, in the dashboard's theme.

In plain English: the original deck (cyberdeck/) never draws anything
itself. Its panes (PULSE face, sky, music, net, todo) and its full-screen
modes (dungeon, typing hack, hack show, boot, launcher) return rows like

    [[("HP ", "dim"), ("12/20", "ok")], [("> ", "prompt"), ("map", "text")]]

where each role is a colour name. The curses UI paints those with
config/theme.ini; this file paints them with the active Rich theme from
config/dash.ini, so one theme colours the whole interface.

RowsView is a tiny Rich renderable: Rich tells it how much room it has
(width x height) right before drawing, and it asks the deck for exactly
that many rows. That is how a deck pane fills a dashboard tile neatly.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

# Deck role -> dashboard theme field. Unknown roles fall back to "text".
ROLE_TO_THEME = {
    "text": "text", "info": "text", "status": "text",
    "title": "accent", "frame": "border", "prompt": "accent2",
    "warn": "warn", "alert": "bad", "ok": "good", "dim": "dim",
}
# Roles that are drawn bold, like in the curses UI.
BOLD_ROLES = {"title", "prompt", "warn", "alert", "ok"}


def role_style(role: str, theme) -> str:
    """Turn a deck role ("ok", "alert", "rgb:ffcc00:-") into a Rich style."""
    if role.startswith("rgb:"):
        # Pictures such as PULSE's sprite use exact colours: rgb:FG:BG ('-' = none).
        _, fg, bg = (role.split(":") + ["-", "-"])[:3]
        style = f"#{fg}" if fg and fg != "-" else theme.text
        if bg and bg != "-":
            style += f" on #{bg}"
        return style
    colour = getattr(theme, ROLE_TO_THEME.get(role, "text"), theme.text)
    return f"bold {colour}" if role in BOLD_ROLES else colour


def row_text(row, theme, width: int | None = None) -> Text:
    """One deck row -> one Rich Text line (cropped, never wrapped)."""
    t = Text(no_wrap=True, overflow="crop", end="")
    for piece, role in row:
        t.append(str(piece), style=role_style(str(role), theme))
    if width is not None:
        t.truncate(width)
    return t


def rows_text(rows, theme, width: int | None = None) -> Text:
    """Many deck rows -> one multi-line Rich Text."""
    out = Text(no_wrap=True, overflow="crop")
    for i, row in enumerate(rows):
        if i:
            out.append("\n")
        out.append_text(row_text(row, theme, width))
    return out


class RowsView:
    """Ask a deck render function for rows that fit the space Rich gives us.

    fn(width, height) -> list of rows. Errors become a red line instead of
    crashing the whole screen (one broken pane must not kill the deck).
    """

    def __init__(self, fn: Callable[[int, int], list], theme, min_height: int = 3) -> None:
        self.fn, self.theme, self.min_height = fn, theme, min_height

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = max(1, options.max_width)
        height = options.height or options.max_height or self.min_height
        try:
            rows = self.fn(width, max(1, height))
        except Exception as exc:  # noqa: BLE001 — show the problem in the tile
            rows = [[(f"error: {exc}", "alert")]]
        for row in list(rows)[:height]:
            line = row_text(row, self.theme, width)
            line.end = "\n"
            yield line
