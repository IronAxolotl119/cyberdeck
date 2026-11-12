"""The discovery journal: a shared, timestamped log of every find.

In plain English: each Finding becomes one line of JSON in
data/discoveries.jsonl (one object per line, easy to grep or load).
The main deck or the companion can read the same file later. The
journal also keeps the most recent lines in memory for the Journal panel.

Line format (stable, other programs may rely on it):
  {"ts": "2026-11-12T13:05:09-07:00", "epoch": 1794..., "source": "dash",
   "kind": "wifi", "event": "new", "id": "aa:bb:..", "label": "SSID",
   "interest": 1, "detail": {...}}
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .events import Finding


class Journal:
    def __init__(self, path: Path | None, keep: int = 300) -> None:
        self.path = path
        self.entries: deque[dict[str, Any]] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self.load()

    # --- reading -------------------------------------------------------------
    def load(self) -> None:
        """Pull the tail of an existing journal into memory (bad lines skipped)."""
        if not self.path or not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        """Newest first."""
        with self._lock:
            return list(self.entries)[-n:][::-1]

    # --- writing -------------------------------------------------------------
    @staticmethod
    def entry_for(finding: Finding) -> dict[str, Any]:
        stamp = datetime.fromtimestamp(finding.ts).astimezone().isoformat(timespec="seconds")
        return {
            "ts": stamp,
            "epoch": round(finding.ts, 3),
            "source": "dash",
            "kind": finding.kind,
            "event": finding.event,
            "id": finding.id,
            "label": finding.label,
            "interest": finding.interest,
            "detail": finding.detail,
        }

    def record(self, finding: Finding) -> dict[str, Any]:
        entry = self.entry_for(finding)
        with self._lock:
            self.entries.append(entry)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass  # read-only SD card etc.: keep going in memory
        return entry
