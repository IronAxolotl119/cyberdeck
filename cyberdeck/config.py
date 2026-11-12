"""config.py — load the theme and layout settings from config/.

The look of the deck is not hard-coded in the UI. Colors live in
config/theme.ini and layout text/sizes live in config/layout.ini.
This module reads those INI files (Python's built-in configparser)
and hands simple objects to the UI.

If a file or a key is missing, the defaults in this file are used
so a typo cannot prevent the deck from starting. See CUSTOMIZING.md
for what each setting means.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    """The project folder (the one that contains config/ and plugins/)."""
    # cyberdeck/config.py -> parent of the package directory
    return Path(__file__).resolve().parent.parent


def config_dir(override: str | Path | None = None) -> Path:
    """Where INI files are read from. CYBERDECK_CONFIG_DIR can override this."""
    if override:
        return Path(override)
    env = os.environ.get("CYBERDECK_CONFIG_DIR")
    if env:
        return Path(env)
    return repo_root() / "config"


# --- theme (colors) -------------------------------------------------

# Default palette if config/theme.ini is missing or a key is left out.
# Names must be curses color names (see CUSTOMIZING.md).
DEFAULT_THEME = {
    "background": "black",
    "frame": "cyan",
    "title": "cyan",
    "status": "white",
    "text": "green",
    "prompt": "yellow",
    "warn": "yellow",
    "alert": "red",
    "ok": "green",
    "dim": "white",
}


@dataclass(frozen=True, slots=True)
class Theme:
    """Color names used by the full-screen UI. Values are strings, not curses ids."""

    background: str = DEFAULT_THEME["background"]
    frame: str = DEFAULT_THEME["frame"]
    title: str = DEFAULT_THEME["title"]
    status: str = DEFAULT_THEME["status"]
    text: str = DEFAULT_THEME["text"]
    prompt: str = DEFAULT_THEME["prompt"]
    warn: str = DEFAULT_THEME["warn"]
    alert: str = DEFAULT_THEME["alert"]
    ok: str = DEFAULT_THEME["ok"]
    dim: str = DEFAULT_THEME["dim"]

    def color(self, name: str) -> str:
        """Look up a role such as 'text' or 'alert', falling back to 'text'."""
        return getattr(self, name, self.text)


def load_theme(directory: str | Path | None = None) -> Theme:
    """Read config/theme.ini and merge it over the built-in defaults."""
    values = dict(DEFAULT_THEME)
    path = config_dir(directory) / "theme.ini"
    parsed = _read_ini(path)
    if parsed and parsed.has_section("colors"):
        for key in DEFAULT_THEME:
            if parsed.has_option("colors", key):
                values[key] = parsed.get("colors", key).strip() or values[key]
    return Theme(**values)


# --- layout (labels and sizes) --------------------------------------

@dataclass(frozen=True, slots=True)
class Layout:
    """Labels and sizes for the full-screen UI."""

    title: str = "CYBERDECK / MAIN"
    prompt: str = "> "
    clock_format: str = "%H:%M:%S"
    min_cols: int = 40
    min_rows: int = 8
    max_output_lines: int = 500
    # auto = box-drawing characters when stdout looks like UTF-8
    use_unicode: str = "auto"


def load_layout(directory: str | Path | None = None) -> Layout:
    """Read config/layout.ini and merge it over the built-in defaults."""
    path = config_dir(directory) / "layout.ini"
    parsed = _read_ini(path)
    section = parsed["layout"] if parsed and parsed.has_section("layout") else {}

    def _str(key: str, default: str) -> str:
        raw = section.get(key, default) if section else default
        raw = (raw or default)
        # Allow "quoted" values so a trailing space in the prompt is kept.
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            return raw[1:-1]
        return raw.strip() or default

    def _int(key: str, default: int) -> int:
        raw = section.get(key, "") if section else ""
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    return Layout(
        title=_str("title", Layout.title),
        prompt=_str("prompt", Layout.prompt),
        clock_format=_str("clock_format", Layout.clock_format),
        min_cols=_int("min_cols", Layout.min_cols),
        min_rows=_int("min_rows", Layout.min_rows),
        max_output_lines=_int("max_output_lines", Layout.max_output_lines),
        use_unicode=_str("use_unicode", Layout.use_unicode).lower(),
    )


def _read_ini(path: Path) -> configparser.ConfigParser | None:
    """Load an INI file, or return None if it is missing or unreadable."""
    if not path.is_file():
        return None
    # interpolation=None so values like %H:%M:%S are left alone.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    return parser
