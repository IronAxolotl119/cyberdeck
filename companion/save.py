"""JSON save and load for a Creature.

The path is the caller's choice: pass any filename you like, or let main.py
set it from the --save / --load flags. The file is a single JSON object, the
same shape as Creature.to_dict(), with a small header so a later version of
the game can notice an old save.
"""

from __future__ import annotations

import json
from pathlib import Path

from pet.creature import Creature


# 2 adds the creature's "life" block (bond, memory, diary, ...). Version-1
# saves still load; their pet simply gets a fresh life with default values.
SAVE_VERSION = 2


def save_creature(creature: Creature, path: str | Path) -> Path:
    """Write the creature (and a version tag) to path. Parents are created."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "save_version": SAVE_VERSION,
        "creature": creature.to_dict(),
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def load_creature(path: str | Path) -> Creature:
    """Read a file written by save_creature and return the Creature.

    Raises FileNotFoundError if the path is missing, and ValueError if the
    JSON is not a recognized save.
    """
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Save file {source} is not a JSON object.")

    # Accept both a wrapped save ({"save_version", "creature": {...}}) and a
    # bare Creature.to_dict() snapshot, so a hand-edited file still loads.
    if "creature" in raw:
        data = raw["creature"]
    elif "stats" in raw:
        data = raw
    else:
        raise ValueError(f"Save file {source} has no 'creature' or 'stats' key.")
    if not isinstance(data, dict):
        raise ValueError(f"Save file {source} has a malformed creature record.")
    return Creature.from_dict(data)
