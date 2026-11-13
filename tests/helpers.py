"""tests/helpers.py — small shared pieces for the test files.

make_ctx() builds a simulated deck context whose data folder is a
fresh temporary directory, so tests never write into the real data/.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cyberdeck.commands import Context
from cyberdeck.companion import SimCompanionLink
from cyberdeck.sensors import SimSensors


def make_ctx(config_dir: str | Path | None = None) -> Context:
    """A sim context with a connected companion and a temp data dir."""
    tmp = Path(tempfile.mkdtemp(prefix="cyberdeck-test-"))
    link = SimCompanionLink()
    link.connect()
    return Context(sensors=SimSensors(seed=1), companion=link, mode="sim",
                   data_dir=tmp, config_dir=config_dir)


def write_config(files: dict[str, str]) -> Path:
    """Create a temp config folder holding the given INI files."""
    folder = Path(tempfile.mkdtemp(prefix="cyberdeck-cfg-"))
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def companion_script() -> Path:
    """The real companion program the end-to-end tests start.

    Normally companion/companion.py in this repo. Set CYBERDECK_COMPANION_DIR
    to test against another checkout of the companion instead (for example
    the companion's own repo while a fix is on its way).
    """
    import os

    root = Path(__file__).resolve().parent.parent
    override = os.environ.get("CYBERDECK_COMPANION_DIR", "").strip()
    return (Path(override) if override else root / "companion") / "companion.py"


def companion_battle_data_missing() -> str:
    """Why the real companion can't run battles from this checkout ("" = it can).

    companion/pet/battle.py reads pet/data/roster.json. If that file is not in
    the checkout (for example because a `data/` rule in .gitignore kept it out
    of a commit) every battle message crashes the companion's connection.
    """
    script = companion_script()
    roster = script.parent / "pet" / "data" / "roster.json"
    if (script.parent / "pet" / "battle.py").exists() and not roster.exists():
        return (f"{roster} is missing from the companion checkout, so its battle messages crash; "
                "set CYBERDECK_COMPANION_DIR to a complete companion checkout to run this")
    return ""
