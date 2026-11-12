"""Start the interface:  python3 -m cyberdeck_dash [options]

In plain English: this used to start a separate dashboard program. The
dashboard and the main deck are now ONE interface, so this is simply an
alias for `python3 -m cyberdeck` (see cyberdeck/app.py), kept so older
instructions still work. Every flag is the same, for example:

    python3 -m cyberdeck_dash --theme amber
    python3 -m cyberdeck_dash --snapshot docs/dashboard --tab dungeon --keys enter
"""

from __future__ import annotations

from cyberdeck.app import main as deck_main


def main(argv=None) -> int:
    return deck_main(argv, prog="python3 -m cyberdeck_dash")


if __name__ == "__main__":
    raise SystemExit(main())
