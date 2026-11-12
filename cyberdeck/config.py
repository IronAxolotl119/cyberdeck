"""config.py — load the theme and layout settings from config/.

The look of the deck is not hard-coded in the UI. Colors live in
config/theme.ini and layout text/sizes live in config/layout.ini.
This module reads those INI files (Python's built-in configparser)
and hands simple objects to the UI.

Newer features (hotkeys, hosts, pet alerts, hardware pins, effects,
weather…) each have their own INI file in config/. They all go
through the small Settings helper at the bottom of this file, which
reads a file once and returns typed values with safe defaults.

data_dir() is where the deck saves things it writes at runtime:
command history, notes, todos, the pet log, high scores, and the
network cache. By default that is ./data (ignored by git).

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


def data_dir(override: str | Path | None = None) -> Path:
    """Folder for files the deck writes (history, notes, scores, cache).

    CYBERDECK_DATA_DIR can override it. The folder is created on demand.
    """
    if override:
        path = Path(override)
    else:
        env = os.environ.get("CYBERDECK_DATA_DIR")
        path = Path(env) if env else repo_root() / "data"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only disk: features that save will report their own error
    return path


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


# Files that failed to parse, with the reason. The UI shows these as
# notices at startup so a typo in an INI file is easy to spot.
CONFIG_ERRORS: dict[str, str] = {}


def _new_parser() -> configparser.ConfigParser:
    """A parser with the deck's INI rules.

    * interpolation=None so values like %H:%M:%S are left alone.
    * only "=" separates key and value, so labels may contain ":".
    * keys keep their case (host names, song names, F-keys).
    * a repeated key keeps the last value instead of failing.
    """
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    return parser


def _read_ini(path: Path) -> configparser.ConfigParser | None:
    """Load an INI file, or return None if it is missing or unreadable."""
    if not path.is_file():
        return None
    parser = _new_parser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        CONFIG_ERRORS[path.name] = str(exc).splitlines()[0]
        return None
    CONFIG_ERRORS.pop(path.name, None)
    return parser


# --- generic settings for feature INI files -------------------------

class Settings:
    """Typed, forgiving access to one INI file in config/.

    Example:
        keys = Settings.load("keys.ini")
        keys.get_int("launcher", "width", 40)

    A missing file, section, or key returns the default you pass in,
    and a value that is not a valid number/bool also returns the
    default, so a typo never stops the deck from starting.
    """

    def __init__(self, parser: configparser.ConfigParser | None, name: str = "") -> None:
        self._parser = parser
        self.name = name

    @classmethod
    def load(cls, filename: str, directory: str | Path | None = None) -> "Settings":
        """Read config/<filename> (or the override directory)."""
        return cls(_read_ini(config_dir(directory) / filename), filename)

    @classmethod
    def from_text(cls, text: str) -> "Settings":
        """Build settings from an INI string (handy in tests)."""
        parser = _new_parser()
        parser.read_string(text)
        return cls(parser, "<text>")

    def has_section(self, section: str) -> bool:
        return bool(self._parser and self._parser.has_section(section))

    def section(self, section: str) -> dict[str, str]:
        """All key/value pairs of a section, in file order ({} if missing)."""
        if not self.has_section(section):
            return {}
        return {k: _unquote(v) for k, v in self._parser.items(section)}  # type: ignore[union-attr]

    def get_str(self, section: str, key: str, default: str = "") -> str:
        value = self.section(section).get(key)
        return default if value is None or value == "" else value

    def get_int(self, section: str, key: str, default: int) -> int:
        try:
            return int(self.get_str(section, key, str(default)))
        except ValueError:
            return default

    def get_float(self, section: str, key: str, default: float) -> float:
        try:
            return float(self.get_str(section, key, str(default)))
        except ValueError:
            return default

    def get_bool(self, section: str, key: str, default: bool) -> bool:
        raw = self.get_str(section, key, "").lower()
        if raw in {"1", "yes", "true", "on"}:
            return True
        if raw in {"0", "no", "false", "off"}:
            return False
        return default

    def get_list(self, section: str, key: str, default: list[str] | None = None) -> list[str]:
        """Comma-separated values, trimmed, empties dropped."""
        raw = self.get_str(section, key, "")
        if not raw:
            return list(default or [])
        return [part.strip() for part in raw.split(",") if part.strip()]


def _unquote(raw: str) -> str:
    """Strip one pair of matching quotes so values can keep edge spaces."""
    raw = raw.strip() if raw else ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return raw[1:-1]
    return raw
