"""history.py — remember the commands you typed, across restarts.

Press Up / Down at the prompt to walk back and forward through earlier
commands. The list is saved to data/history.txt (one command per line)
so it survives a reboot of the deck.

Small rules, like a normal shell:
  * Blank lines are not saved.
  * Typing the same command twice in a row is saved only once.
  * A line that starts with a space is NOT saved (handy for secrets).
  * Only the newest `limit` entries are kept (layout.ini [history] size).

This file has no curses code, so it is fully unit-tested.
"""

from __future__ import annotations

from pathlib import Path


class History:
    """A list of past commands plus a cursor for Up/Down browsing."""

    def __init__(self, path: Path | None = None, limit: int = 500) -> None:
        self.path = path
        self.limit = max(1, limit)
        self.entries: list[str] = []
        # None = not browsing. Otherwise an index into entries
        # (len(entries) means "back at the line you were typing").
        self._index: int | None = None
        self._draft = ""  # what you had typed before pressing Up
        self.load()

    # --- saving and loading -----------------------------------------

    def load(self) -> None:
        """Read the history file if there is one. Errors leave it empty."""
        if not self.path or not self.path.is_file():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        self.entries = [line for line in lines if line.strip()][-self.limit :]

    def save(self) -> None:
        """Write the history file. A read-only disk is silently ignored."""
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.entries) + ("\n" if self.entries else ""), encoding="utf-8")
        except OSError:
            pass

    def add(self, line: str) -> None:
        """Record a submitted command and stop browsing."""
        self._index = None
        self._draft = ""
        if not line.strip() or line.startswith(" "):
            return
        line = line.rstrip()  # Tab completion leaves a trailing space
        if self.entries and self.entries[-1] == line:
            return
        self.entries.append(line)
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]
        self.save()

    def clear(self) -> None:
        """Forget everything (and empty the file)."""
        self.entries = []
        self._index = None
        self.save()

    # --- Up / Down browsing -------------------------------------------

    def previous(self, current: str) -> str:
        """Up arrow: return the older command to show at the prompt."""
        if not self.entries:
            return current
        if self._index is None:
            self._draft = current
            self._index = len(self.entries)
        self._index = max(0, self._index - 1)
        return self.entries[self._index]

    def next(self, current: str) -> str:
        """Down arrow: return the newer command (or your unfinished draft)."""
        if self._index is None:
            return current
        self._index += 1
        if self._index >= len(self.entries):
            self._index = None
            return self._draft
        return self.entries[self._index]

    def recent(self, count: int = 20) -> list[str]:
        """The newest `count` entries, oldest first."""
        return self.entries[-count:] if count > 0 else []
