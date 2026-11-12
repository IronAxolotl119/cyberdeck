"""activity.py — the shape of a full-screen "mode" (games, effects, menus).

Some features take over the whole screen for a while: the boot
animation, the launcher menu, the hacking game, the dungeon, the
"hack the mainframe" show, the live wifi meter… Each of these is an
Activity.

An Activity never touches curses. It only:
  * receives key names ("up", "a", "enter", "esc") in on_key(),
  * moves time forward in update(now),
  * returns what to show from render(width, height) as a list of rows,
    where each row is a list of (text, role) pieces. role is a theme
    color name: "text", "title", "warn", "alert", "ok", "dim", "prompt".

The TUI (tui.py) runs the loop and paints those rows with your theme.
Because there is no curses inside, every Activity can be unit-tested
by feeding it keys and reading render() as plain text.

To make a new full-screen mode: subclass Activity, then have a command
set `ctx.activity = MyActivity(...)`. In headless mode, headless() is
called instead so scripts still get a useful text result.
"""

from __future__ import annotations

from dataclasses import dataclass

# One row on screen: a list of (text, role) pieces drawn left to right.
Row = list[tuple[str, str]]


class Activity:
    """Base class for full-screen modes. Override what you need."""

    # Shown by the TUI when it needs a name (e.g. in error messages).
    title = "activity"
    # Seconds between frames when no key is pressed (animation speed).
    tick = 0.1
    # True = draw as a centred popup over the normal deck screen.
    overlay = False
    # Popup size when overlay is True (columns, rows).
    overlay_size = (40, 12)

    def __init__(self) -> None:
        self.done = False
        # Optional command line to run after the activity closes (the launcher uses it).
        self.result_command: str | None = None
        # Optional text to put at the prompt for the user to finish typing.
        self.result_prompt: str | None = None
        # Lines to print in the output pane after closing.
        self.result_lines: list[str] = []

    def start(self, now: float) -> None:
        """Called once, right before the first frame."""

    def on_key(self, key: str) -> None:
        """Handle one key name. Set self.done = True to leave."""
        if key in {"esc", "q"}:
            self.done = True

    def update(self, now: float) -> None:
        """Advance animations. Called every frame."""

    def render(self, width: int, height: int) -> list[Row]:
        """Return up to `height` rows that fit in `width` columns."""
        return [[(self.title, "title")]]

    def headless(self) -> list[str]:
        """Text shown instead when there is no full-screen UI."""
        return [f"{self.title}: needs the full-screen UI (run without --headless)"]


def rows_to_text(rows: list[Row]) -> list[str]:
    """Flatten rendered rows into plain strings (used by tests and headless)."""
    return ["".join(text for text, _role in row) for row in rows]


@dataclass
class MenuItem:
    """One launcher entry: what you see and what it runs."""

    label: str
    action: str


class MenuActivity(Activity):
    """The launcher popup: arrow keys to move, Enter to run, Esc to close.

    Typing letters filters the list (type "net" to see only network
    entries). An action that ends with "..." is not run straight away;
    it is put on the prompt so you can type the rest (e.g. "note add ...").
    """

    title = "LAUNCHER"
    overlay = True
    tick = 0.5

    def __init__(self, items: list[MenuItem], width: int = 44, height: int = 16) -> None:
        super().__init__()
        self.items = items
        self.filter = ""
        self.index = 0
        self.overlay_size = (width, height)

    def visible(self) -> list[MenuItem]:
        """Items that match what has been typed so far."""
        needle = self.filter.lower()
        if not needle:
            return list(self.items)
        return [item for item in self.items if needle in item.label.lower() or needle in item.action.lower()]

    def on_key(self, key: str) -> None:
        items = self.visible()
        if key in {"esc", "ctrl+o", "f2"}:
            self.done = True
        elif key in {"up", "ctrl+p", "shift+tab"}:
            self.index = (self.index - 1) % max(1, len(items))
        elif key in {"down", "ctrl+n", "tab"}:
            self.index = (self.index + 1) % max(1, len(items))
        elif key == "home":
            self.index = 0
        elif key == "end":
            self.index = max(0, len(items) - 1)
        elif key == "backspace":
            self.filter = self.filter[:-1]
            self.index = 0
        elif key == "enter":
            if items:
                self.choose(items[min(self.index, len(items) - 1)])
        elif len(key) == 1 and key.isprintable():
            self.filter += key
            self.index = 0

    def choose(self, item: MenuItem) -> None:
        """Remember the picked action and close."""
        action = item.action.strip()
        if action.endswith("..."):
            self.result_prompt = action[:-3].rstrip() + " "
        else:
            self.result_command = action
        self.done = True

    def render(self, width: int, height: int) -> list[Row]:
        rows: list[Row] = []
        hint = f"filter: {self.filter}_" if self.filter else "type to filter · ↑↓ · Enter · Esc"
        rows.append([(hint[:width], "dim")])
        items = self.visible()
        room = max(1, height - 1)
        # Scroll the list so the highlighted entry stays visible.
        top = max(0, min(self.index - room + 1, len(items) - room)) if self.index >= room else 0
        for i, item in enumerate(items[top : top + room], start=top):
            marker = "▶ " if i == self.index else "  "
            text = f"{marker}{item.label}"
            rows.append([(text[:width].ljust(width), "prompt" if i == self.index else "text")])
        if not items:
            rows.append([("(nothing matches)", "warn")])
        return rows

    def headless(self) -> list[str]:
        return ["launcher entries:"] + [f"  {item.label:<20} {item.action}" for item in self.items]
