"""Loads config/dash.ini into simple Python objects.

In plain English: every tunable number, colour, and list the dashboard
uses lives in config/dash.ini. This file reads that file, fills in a
sensible default for anything missing, and hands the rest of the code
a DashConfig object. If the file does not exist at all, the dashboard
still runs on defaults.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

# The repo root is one level above this package; config/ lives there.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "config" / "dash.ini"

# --- Built-in defaults ------------------------------------------------------
# Used for any section/key missing from dash.ini. Kept as strings so they
# go through exactly the same parsing as values from the file.
DEFAULTS: dict[str, dict[str, str]] = {
    "dash": {"refresh_hz": "2", "theme": "neon", "mode": "sim", "start_panel": "wifi", "glitch": "1",
             "title": "CYBERDECK//MAIN"},
    "tabs": {"order": "awareness, deck, console, dungeon, notes, fun"},
    "tab.awareness": {"title": "Awareness & Diagnostics", "short": "Awareness",
                      "panels": "wifi, bt, lan, rfid, journal, diag, checklist", "columns": "3"},
    "tab.deck": {"title": "Deck & PULSE", "short": "PULSE", "panels": "field, pet, system", "columns": "3",
                 "detail": "yes"},
    # The main deck's features (need `python3 -m cyberdeck`; hidden otherwise).
    "tab.console": {"title": "Console", "panels": "console, avatar", "columns": "2", "ratios": "7, 3",
                    "detail": "yes"},
    "tab.dungeon": {"title": "Dungeon", "panels": "dungeon, combat, loot", "columns": "3", "ratios": "4, 4, 3",
                    "detail": "yes"},
    "tab.notes": {"title": "Notes & Todo", "short": "Notes", "panels": "notes, todo", "columns": "2",
                  "detail": "yes"},
    "tab.fun": {"title": "Fun & Tools", "short": "Fun", "panels": "launcher, sky, music, net, output",
                "columns": "3", "detail": "yes"},
    "diag": {"backend": "auto", "ping_default": "192.168.1.1", "dns_default": "github.com"},
    "theme.neon": {
        "accent": "#00f0ff", "accent2": "#ff2bd6", "text": "#c8d3f5", "dim": "#5a6688",
        "good": "#39ff88", "warn": "#ffd23f", "bad": "#ff3860", "border": "#2b3a67",
        "focus": "#ff2bd6", "title_bg": "#0d0221",
    },
    "wifi": {
        "backend": "sim", "interface": "wlan0", "interval": "8", "rssi_jump_db": "15",
        "gone_after_scans": "3", "history_len": "40",
        "suspicious_patterns": "free wifi, free_wifi, freewifi, free public, public wifi, guest, hotspot, login",
        "trusted": "",
    },
    "bluetooth": {"backend": "sim", "interval": "10", "listen": "5", "dwell_minutes": "10",
                  "forget_after": "300", "known": ""},
    "lan": {"backend": "sim", "interval": "20", "ping_sweep": "no", "subnet": "auto",
            "pings_per_second": "10", "sweep_cooldown": "600"},
    "rfid": {"backend": "sim", "interval": "1", "absent_after": "4", "inventory": "data/tags.json"},
    "journal": {"path": "data/discoveries.jsonl", "keep": "300"},
    "field": {"scan_interval": "4", "alert_interest": "3", "normal_alert_interest": "2", "cooldown": "120",
              "max_alerts_per_minute": "6", "burst": "3", "manual_scan_cooldown": "3"},
    "checklist": {"items": "Phone charged, Badge / keys on me, Deck battery above 50%"},
    "companion": {"enabled": "yes", "host": "127.0.0.1", "port": "7777", "timeout": "1.5", "poll": "3"},
    "xp": {"wifi": "4", "bt": "3", "lan": "3", "rfid": "8", "interest_bonus": "3",
           "learn_chance": "0.12", "activity_window": "300", "busy_at": "8"},
    "buttons": {"backend": "keyboard", "scan": "17", "use_move": "27", "field": "22", "next_panel": "23",
                "joy_up": "", "joy_down": "", "joy_left": "", "joy_right": "", "joy_press": "", "alert_led": ""},
}


def split_list(value: str, sep: str = ",") -> list[str]:
    """'a, b ,c' -> ['a', 'b', 'c'] (empty items dropped)."""
    return [part.strip() for part in value.split(sep) if part.strip()]


@dataclass
class Theme:
    """One colour scheme. Field names match the keys in [theme.*]."""

    name: str
    accent: str
    accent2: str
    text: str
    dim: str
    good: str
    warn: str
    bad: str
    border: str
    focus: str
    title_bg: str


@dataclass
class DashConfig:
    """Everything from dash.ini, already parsed into the right types."""

    raw: configparser.ConfigParser
    themes: dict[str, Theme] = field(default_factory=dict)

    # --- tiny typed getters so callers never touch raw strings ---------------
    def get(self, section: str, key: str) -> str:
        if self.raw.has_option(section, key):
            return self.raw.get(section, key).strip()
        return DEFAULTS.get(section, {}).get(key, "")

    def int(self, section: str, key: str) -> int:
        try:
            return int(float(self.get(section, key)))
        except ValueError:
            return int(float(DEFAULTS[section][key]))

    def float(self, section: str, key: str) -> float:
        try:
            return float(self.get(section, key))
        except ValueError:
            return float(DEFAULTS[section][key])

    def bool(self, section: str, key: str) -> bool:
        return self.get(section, key).lower() in ("1", "yes", "true", "on")

    def list(self, section: str, key: str) -> list[str]:
        return split_list(self.get(section, key))

    def path(self, section: str, key: str) -> Path:
        p = Path(self.get(section, key))
        return p if p.is_absolute() else REPO_ROOT / p

    # --- convenience views ---------------------------------------------------
    def tab_detail(self, title: str) -> bool:
        """True if that tab's tiles show their full (detailed) view."""
        for key in self.list("tabs", "order"):
            sec = f"tab.{key}"
            if self.raw.has_section(sec) and self.raw.get(sec, "title", fallback=key).strip() == title:
                return self.raw.get(sec, "detail", fallback="no").strip().lower() in ("1", "yes", "true", "on")
        return False

    def _tab_section(self, title: str) -> str | None:
        for key in self.list("tabs", "order"):
            sec = f"tab.{key}"
            if self.raw.has_section(sec) and self.raw.get(sec, "title", fallback=key).strip() == title:
                return sec
        return None

    def tab_ratios(self, title: str) -> list[int]:
        """Relative tile widths in a row, e.g. 'ratios = 7, 3' (empty = all equal)."""
        sec = self._tab_section(title)
        if not sec:
            return []
        return [int(x) for x in split_list(self.raw.get(sec, "ratios", fallback="")) if x.isdigit() and int(x) > 0]

    def tab_short(self, title: str) -> str:
        """Short name for the tab bar when the tab is not the current one."""
        sec = self._tab_section(title)
        short = self.raw.get(sec, "short", fallback="").strip() if sec else ""
        return short or title

    def tabs(self) -> list[tuple[str, list[str], int]]:
        """[(title, panel keys, columns)] in [tabs] order."""
        out = []
        for key in self.list("tabs", "order"):
            sec = f"tab.{key}"
            if not self.raw.has_section(sec):
                continue
            panels = split_list(self.raw.get(sec, "panels", fallback=""))
            if panels:
                cols = self.raw.get(sec, "columns", fallback="3").strip()
                out.append((self.raw.get(sec, "title", fallback=key).strip(), panels,
                            int(cols) if cols.isdigit() else 3))
        return out or [("Awareness & Diagnostics", ["wifi", "bt", "lan", "rfid", "journal", "diag"], 3)]

    def theme(self, name: str | None = None) -> Theme:
        name = name or self.get("dash", "theme")
        return self.themes.get(name) or next(iter(self.themes.values()))

    def trusted_wifi(self) -> dict[str, set[str]]:
        """[wifi] trusted = SSID = bssid|bssid, SSID2 = ... -> {ssid: {bssids}}"""
        out: dict[str, set[str]] = {}
        for item in self.list("wifi", "trusted"):
            if "=" not in item:
                continue
            ssid, bssids = item.split("=", 1)
            out[ssid.strip()] = {b.strip().lower() for b in bssids.split("|") if b.strip()}
        return out


def load_config(path: str | Path | None = None) -> DashConfig:
    """Read dash.ini (or the given path). Missing file = all defaults."""
    raw = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    raw.optionxform = str  # keep key case (SSIDs in [wifi] trusted)
    raw.read_dict(DEFAULTS)
    target = Path(path) if path else DEFAULT_PATH
    if target.exists():
        raw.read(target, encoding="utf-8")
    cfg = DashConfig(raw=raw)
    # Collect every [theme.NAME] section into a Theme object.
    for section in raw.sections():
        if section.startswith("theme."):
            name = section.split(".", 1)[1]
            base = DEFAULTS["theme.neon"]
            values = {k: raw.get(section, k, fallback=base[k]) for k in base}
            cfg.themes[name] = Theme(name=name, **values)
    return cfg
