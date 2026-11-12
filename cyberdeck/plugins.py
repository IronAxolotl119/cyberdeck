"""plugins.py — auto-load extra commands from the plugins/ folder.

This is the hook that makes customization easy: drop a .py file in
plugins/, restart the deck, and `help` will list your new command.

Rules, kept deliberately small:
  * Each file should define NAME and a run(ctx, args) function.
  * Files whose names start with an underscore (like _template.py)
    are skipped, so templates and notes are not registered.
  * A plugin that fails to import is reported, but does not stop the
    rest of the deck from starting.

See plugins/_template.py, plugins/hello.py, and CUSTOMIZING.md.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from cyberdeck.commands import Command, Registry
from cyberdeck.config import repo_root


def plugins_dir(override: str | Path | None = None) -> Path:
    """Where user command files live. CYBERDECK_PLUGINS_DIR can override this."""
    if override:
        return Path(override)
    env = os.environ.get("CYBERDECK_PLUGINS_DIR")
    if env:
        return Path(env)
    return repo_root() / "plugins"


def load_user_commands(registry: Registry, directory: str | Path | None = None) -> list[str]:
    """Import plugin files and register each one. Returns the loaded command names."""
    folder = plugins_dir(directory)
    if not folder.is_dir():
        return []
    loaded: list[str] = []
    # Sorted so `help` order is stable from run to run.
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            command = _load_plugin(path)
        except Exception as exc:  # a bad plugin should not take down the deck
            print(f"cyberdeck: could not load plugin {path.name}: {exc}", file=sys.stderr)
            continue
        try:
            registry.register(command)
        except ValueError as exc:
            print(f"cyberdeck: skip plugin {path.name}: {exc}", file=sys.stderr)
            continue
        loaded.append(command.name)
    return loaded


def _load_plugin(path: Path) -> Command:
    """Import one plugin file and turn it into a Command object."""
    spec = importlib.util.spec_from_file_location(f"cyberdeck_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    name = getattr(module, "NAME", None)
    run = getattr(module, "run", None)
    if not name or not callable(run):
        raise ValueError("plugin must define NAME and a run(ctx, args) function")

    aliases = tuple(getattr(module, "ALIASES", ()) or ())
    # Optional: words Tab should offer after the command name.
    subcommands = tuple(getattr(module, "SUBCOMMANDS", ()) or ())
    # Optional: complete(ctx, args) -> list of words, for dynamic Tab completion.
    completer = getattr(module, "complete", None)
    return Command(
        name=str(name),
        handler=run,
        summary=str(getattr(module, "SUMMARY", "") or f"user command '{name}'"),
        usage=str(getattr(module, "USAGE", "") or name),
        aliases=aliases,
        subcommands=subcommands,
        completer=completer if callable(completer) else None,
    )
