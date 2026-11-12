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
