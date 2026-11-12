"""__main__.py — entry point for `python -m cyberdeck`.

Python looks for this file when you run the package as a module.
It just hands off to app.main(), which parses flags and starts
either the full-screen UI or headless/script mode.
"""

from cyberdeck.app import main

raise SystemExit(main())
