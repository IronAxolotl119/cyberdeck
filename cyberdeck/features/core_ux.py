"""core_ux.py — commands for the deck's own interface.

  history [n] | history clear   show or wipe saved command history
  menu                          open the launcher popup (also F2 / Ctrl-O)
  view <name|next|prev>         switch tab / view (output, pet, system…)
  layout split|tabs|toggle      side-by-side panes or one view at a time
  keys                          list your hotkeys from config/keys.ini

The UI-only verbs (menu, view, layout) do not draw anything here.
They set ctx.ui_action, and tui.py carries it out. That keeps this
file free of curses, so it works in headless mode and in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberdeck.activity import MenuActivity, MenuItem
from cyberdeck.config import Settings
from cyberdeck.history import History
from cyberdeck.keys import UI_ACTIONS, load_hotkeys, load_launcher

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


def get_history(ctx: "Context") -> History:
    """The shared History object (created on first use)."""
    size = Settings.load("layout.ini", ctx.config_dir).get_int("history", "size", 500)
    return ctx.service("history", lambda: History(ctx.data_path("history.txt"), size))


def launcher_items(ctx: "Context", reg: "Registry") -> list[MenuItem]:
    """Entries for the launcher: keys.ini [launcher], or every command."""
    configured = load_launcher(Settings.load("keys.ini", ctx.config_dir))
    if configured:
        return [MenuItem(label, action) for label, action in configured]
    return [MenuItem(f"{c.name:<10} {c.summary}", c.name) for c in reg.all()]


def register(reg: "Registry") -> None:
    """Add the interface commands to the registry."""

    @reg.command("history", "Show recent commands, or clear them.", usage="history [n|clear]",
                 subcommands=("clear",))
    def cmd_history(ctx: "Context", args: list[str]) -> list[str]:
        hist = get_history(ctx)
        if args and args[0].lower() == "clear":
            hist.clear()
            return ["history cleared."]
        count = int(args[0]) if args and args[0].isdigit() else 20
        recent = hist.recent(count)
        if not recent:
            return ["(no history yet)"]
        start = len(hist.entries) - len(recent) + 1
        return [f"{start + i:>4}  {line}" for i, line in enumerate(recent)]

    @reg.command("menu", "Open the launcher menu (F2 / Ctrl-O).", aliases=("launcher",))
    def cmd_menu(ctx: "Context", args: list[str]) -> list[str]:
        del args
        if not ctx.interactive:
            return MenuActivity(launcher_items(ctx, reg)).headless()
        ctx.ui_action = ":launcher"
        return []

    @reg.command("view", "Switch tab/view: output, pet, system, next…", usage="view <name|next|prev>",
                 aliases=("tab",), completer=lambda ctx, args: _view_names())
    def cmd_view(ctx: "Context", args: list[str]) -> list[str]:
        target = args[0].lower() if args else "next"
        if target not in {"next", "prev", "output"} and target not in _view_names():
            return [f"no such view: {target}  (try: {', '.join(_view_names())})"]
        ctx.ui_action = f":tab {target}"
        return [] if ctx.interactive else [f"(view {target} applies in the full-screen UI)"]

    @reg.command("layout", "Split panes or tabs.", usage="layout <split|tabs|toggle>",
                 subcommands=("split", "tabs", "toggle"))
    def cmd_layout(ctx: "Context", args: list[str]) -> list[str]:
        target = args[0].lower() if args else "toggle"
        if target not in {"split", "tabs", "toggle"}:
            return ["usage: layout <split|tabs|toggle>"]
        ctx.ui_action = f":layout {target}"
        return [] if ctx.interactive else [f"(layout {target} applies in the full-screen UI)"]

    @reg.command("keys", "List hotkeys and UI actions from config/keys.ini.", aliases=("hotkeys",))
    def cmd_keys(ctx: "Context", args: list[str]) -> list[str]:
        del args
        mapping, warnings = load_hotkeys(Settings.load("keys.ini", ctx.config_dir))
        lines = ["hotkeys (edit config/keys.ini):"]
        lines += [f"  {key:<8} {action}" for key, action in mapping.items()]
        lines += ["built-in: Tab complete · ↑↓ history · PgUp/PgDn scroll · Ctrl-L clear · Ctrl-U wipe line"]
        lines += ["UI actions you can bind:"] + [f"  {a:<16} {d}" for a, d in UI_ACTIONS.items()]
        return lines + warnings


def _view_names() -> list[str]:
    from cyberdeck.panes import PANES

    return ["output", "next", "prev", *PANES.keys()]
