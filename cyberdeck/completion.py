"""completion.py — what happens when you press Tab at the prompt.

Tab looks at the word under the cursor (always the last word for
simplicity) and offers words that start with it:

  * first word   -> command names and aliases   (`pe` -> `pet`)
  * later words  -> that command's subcommands  (`pet f` -> `pet feed`)
                    plus anything its completer() returns
                    (song names, ssh hosts, note numbers…)

If only one word matches, it is filled in with a trailing space. If
several match, the shared beginning is filled in and the choices are
returned so the UI can print them.

No curses here, so this is unit-tested directly.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # imported only for type hints, avoids an import cycle
    from cyberdeck.commands import Context, Registry


def complete_line(line: str, registry: "Registry", ctx: "Context | None" = None) -> tuple[str, list[str]]:
    """Return (new_line, choices). choices is empty when nothing matched."""
    # Split into finished words and the partial word being typed.
    ends_with_space = line.endswith(" ")
    words = line.split()
    if ends_with_space or not words:
        done, partial = words, ""
    else:
        done, partial = words[:-1], words[-1]

    candidates = _candidates(done, registry, ctx)
    matches = sorted({c for c in candidates if c.lower().startswith(partial.lower())})
    if not matches:
        return line, []

    prefix = " ".join(done)
    joiner = " " if prefix else ""
    if len(matches) == 1:
        return f"{prefix}{joiner}{matches[0]} ", matches
    common = os.path.commonprefix([m.lower() for m in matches])
    # Keep the user's own text if the shared prefix is not longer.
    filled = matches[0][: len(common)] if len(common) > len(partial) else partial
    return f"{prefix}{joiner}{filled}", matches


def _candidates(done: list[str], registry: "Registry", ctx: "Context | None") -> list[str]:
    """Words that are valid at this position."""
    if not done:
        return registry.all_names()
    command = registry.get(done[0])
    if command is None:
        return []
    out: list[str] = []
    if len(done) == 1:
        out.extend(command.subcommands)
    if command.completer is not None:
        try:
            out.extend(command.completer(ctx, done[1:]))  # type: ignore[arg-type]
        except Exception:  # a broken completer must never break typing
            pass
    return [str(word) for word in out]
