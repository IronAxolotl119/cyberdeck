"""headless.py — run commands without the full-screen UI.

This mode is for tests, scripts, and any time you do not have a real
terminal (or do not want curses). It prints command output as plain
text and exits when it sees `quit` or the end of the input.

Use it like:

    python -m cyberdeck --headless
    python -m cyberdeck --headless --script 'status' --script 'ping'
    printf 'help\\npet status\\nquit\\n' | python -m cyberdeck --headless
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import TextIO

from cyberdeck.commands import Context, Registry


def run_headless(
    ctx: Context,
    registry: Registry,
    commands: Iterable[str] | None = None,
    out: TextIO = sys.stdout,
) -> int:
    """Run a list of command lines (or stdin if commands is None).

    Returns an exit code: 0 on a normal quit, 0 on end-of-input too.
    """
    stream: Iterable[str]
    if commands is None:
        stream = _stdin_lines()
    else:
        stream = commands

    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        print(f"> {line}", file=out)
        output = registry.dispatch(line, ctx)
        if ctx.should_clear:
            ctx.should_clear = False
        for piece in output:
            print(piece, file=out)
        if ctx.should_quit:
            return 0
    return 0


def _stdin_lines() -> Iterable[str]:
    """Yield lines from stdin until EOF. Works with pipes and interactive use."""
    try:
        for line in sys.stdin:
            yield line
    except KeyboardInterrupt:
        return
