"""notes.py — quick notes and a to-do list, saved as plain text.

  note add <text>      save a note (with the date and time)
  note list            show all notes (also just `note`)
  note find <word>     notes containing a word
  note del <n>         delete note number n

  todo add <text>      add a task
  todo list            show tasks (also just `todo`)
  todo done <n>        tick a task off       todo undo <n>  untick it
  todo del <n>         delete a task         todo clear     remove ticked tasks

Files (in the data folder, see CUSTOMIZING.md):
  notes.txt   one note per line:   2026-11-12 10:40 | buy more jumper wires
  todo.txt    one task per line:   [ ] solder the buzzer   /   [x] flash the SD card

They are ordinary text files, so you can also edit them in nano, copy
them to a laptop, or sync them. The deck re-reads them every time.

It also adds a "todo" panel you can put in [panes] side/tabs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from cyberdeck.activity import Row
from cyberdeck.panes import register_pane

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


# --- storage ------------------------------------------------------------

class LineFile:
    """A text file treated as a list of non-empty lines."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> list[str]:
        try:
            return [line.rstrip("\n") for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            return []

    def write(self, lines: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


class NoteStore(LineFile):
    """Notes: 'YYYY-MM-DD HH:MM | text' per line."""

    def add(self, text: str, when: float | None = None) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
        line = f"{stamp} | {text.strip()}"
        self.write(self.read() + [line])
        return line

    def delete(self, number: int) -> str:
        lines = self.read()
        if not 1 <= number <= len(lines):
            raise IndexError(f"no note #{number} (there are {len(lines)})")
        removed = lines.pop(number - 1)
        self.write(lines)
        return removed


class TodoStore(LineFile):
    """Tasks: '[ ] text' (open) or '[x] text' (done) per line."""

    @staticmethod
    def parse(line: str) -> tuple[bool, str]:
        """Return (done, text). Lines without a box count as open tasks."""
        if line[:3].lower() == "[x]":
            return True, line[3:].strip()
        if line[:3] == "[ ]":
            return False, line[3:].strip()
        return False, line.strip()

    def items(self) -> list[tuple[bool, str]]:
        return [self.parse(line) for line in self.read()]

    def _save(self, items: list[tuple[bool, str]]) -> None:
        self.write([f"[{'x' if done else ' '}] {text}" for done, text in items])

    def add(self, text: str) -> int:
        items = self.items() + [(False, text.strip())]
        self._save(items)
        return len(items)

    def set_done(self, number: int, done: bool = True) -> str:
        items = self.items()
        self._check(number, items)
        items[number - 1] = (done, items[number - 1][1])
        self._save(items)
        return items[number - 1][1]

    def delete(self, number: int) -> str:
        items = self.items()
        self._check(number, items)
        removed = items.pop(number - 1)
        self._save(items)
        return removed[1]

    def clear_done(self) -> int:
        items = self.items()
        keep = [item for item in items if not item[0]]
        self._save(keep)
        return len(items) - len(keep)

    @staticmethod
    def _check(number: int, items: list) -> None:
        if not 1 <= number <= len(items):
            raise IndexError(f"no task #{number} (there are {len(items)})")


def notes(ctx: "Context") -> NoteStore:
    return NoteStore(ctx.data_path("notes.txt"))


def todos(ctx: "Context") -> TodoStore:
    return TodoStore(ctx.data_path("todo.txt"))


# --- commands -------------------------------------------------------------

def _number(args: list[str]) -> int:
    """The task/note number argument, with a friendly error."""
    if len(args) < 2 or not args[1].isdigit():
        raise ValueError(f"usage: {args[0] if args else ''} <number>")
    return int(args[1])


def register(reg: "Registry") -> None:
    """Add the note and todo commands."""

    @reg.command("note", "Plain-text notes: add, list, find, del.", usage="note [add|list|find|del] ...",
                 aliases=("notes",), subcommands=("add", "list", "find", "del"))
    def cmd_note(ctx: "Context", args: list[str]) -> list[str]:
        store = notes(ctx)
        action = args[0].lower() if args else "list"
        try:
            if action == "add":
                if len(args) < 2:
                    return ["usage: note add <text>"]
                return [f"saved: {store.add(' '.join(args[1:]))}"]
            if action == "del":
                return [f"deleted: {store.delete(_number(args))}"]
            if action == "find":
                word = " ".join(args[1:]).lower()
                hits = [(i, n) for i, n in enumerate(store.read(), 1) if word in n.lower()]
                return [f"{i:>3}. {n}" for i, n in hits] or [f"no notes contain '{word}'"]
            if action == "list":
                lines = store.read()
                return [f"{i:>3}. {n}" for i, n in enumerate(lines, 1)] or ["(no notes yet — try: note add <text>)"]
        except (IndexError, ValueError) as exc:
            return [str(exc)]
        # `note something` with no verb is treated as `note add something`.
        return [f"saved: {store.add(' '.join(args))}"]

    @reg.command("todo", "To-do list: add, list, done, undo, del, clear.", usage="todo [add|list|done|undo|del|clear] ...",
                 aliases=("todos",), subcommands=("add", "list", "done", "undo", "del", "clear"))
    def cmd_todo(ctx: "Context", args: list[str]) -> list[str]:
        store = todos(ctx)
        action = args[0].lower() if args else "list"
        try:
            if action == "add":
                if len(args) < 2:
                    return ["usage: todo add <text>"]
                n = store.add(" ".join(args[1:]))
                return [f"added #{n}: {' '.join(args[1:])}"]
            if action == "done":
                return [f"done [x] {store.set_done(_number(args), True)}"]
            if action == "undo":
                return [f"reopened: {store.set_done(_number(args), False)}"]
            if action == "del":
                return [f"deleted: {store.delete(_number(args))}"]
            if action == "clear":
                return [f"removed {store.clear_done()} finished task(s)"]
            if action == "list":
                return format_todos(store.items()) or ["(nothing to do — try: todo add <text>)"]
        except (IndexError, ValueError) as exc:
            return [str(exc)]
        return [f"unknown todo action '{action}' (add, list, done, undo, del, clear)"]

    register_pane("todo", "TODO", todo_pane)


def format_todos(items: list[tuple[bool, str]]) -> list[str]:
    """Numbered task lines with a tick box, plus a summary."""
    if not items:
        return []
    lines = [f"{i:>3}. [{'x' if done else ' '}] {text}" for i, (done, text) in enumerate(items, 1)]
    open_count = sum(1 for done, _ in items if not done)
    return lines + [f"     {open_count} open · {len(items) - open_count} done"]


def todo_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """Open tasks first, for the side panel."""
    items = todos(ctx).items()
    if not items:
        return [[("nothing to do", "dim")]]
    rows: list[Row] = []
    for i, (done, text) in enumerate(items, 1):
        if not done:
            rows.append([(f"{i:>2} ", "dim"), ("[ ] ", "warn"), (text, "text")])
    for i, (done, text) in enumerate(items, 1):
        if done:
            rows.append([(f"{i:>2} ", "dim"), ("[x] ", "ok"), (text, "dim")])
    return rows[:height]
