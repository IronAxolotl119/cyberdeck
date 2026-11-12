# CYBERDECK / MAIN

Simulation-first scaffold for the main Raspberry Pi Zero 2 W
cyberdeck: a custom full-screen terminal UI (not the Pi OS desktop)
that talks to a companion board running a Digimon-style pet.

Everything runs on a normal Linux terminal today. No Pi is required.
Python 3 and the standard library (including curses) are enough.

## Run it

From this directory:

```bash
# Full-screen UI (sim sensors + sim companion pet)
python3 -m cyberdeck

# Headless: type commands, or pipe them in
python3 -m cyberdeck --headless
printf 'status\nping\npet status\nquit\n' | python3 -m cyberdeck --headless

# Headless, one-shot scripts (repeat --script as needed)
python3 -m cyberdeck --headless --script 'help' --script 'ping' --script 'pet status'
```

`--mode sim` is the default. You can also set `CYBERDECK_MODE=sim`.
`--mode real` selects the Pi sensors and the UART companion link
(`--link serial`, default `/dev/serial0`). `--link sim|tcp|serial`
picks the companion link on its own; see "Deck + pet on one computer"
below.

```bash
python3 -m cyberdeck --help
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Layout

```text
config/theme.ini        colors for the full-screen UI
config/layout.ini       title, prompt, clock, minimum size
plugins/hello.py        example user command (auto-loaded)
plugins/_template.py    copy this to add your own command
cyberdeck/              the Python package
docs/PROTOCOL.md        dual-board message protocol
docs/ARCHITECTURE.md    how modules fit together
docs/screenshot.txt     captured text snapshot of the UI
docs/TESTING_TWO_BOARDS.md  running deck + pet together (TCP, serial, two Pis)
tools/fake_companion.py stand-in pet server for testing the link
companion/              the companion board's own app (the real pet; its own README)
CUSTOMIZING.md          how to change colors, layout, and commands
tests/                  unit tests
```

## Deck + pet on one computer

The real pet lives in `companion/`, the companion board's app. It is
maintained separately; see [companion/README.md](companion/README.md).
To run both together, open two terminals in this directory:

```bash
cd companion && python3 companion.py --tcp 127.0.0.1:7777   # terminal 1: the pet
python3 -m cyberdeck --link tcp                             # terminal 2: the deck
```

The deck connects to `127.0.0.1:7777` by default (change it with
`--host` / `--port` or `[link]` in `config/hardware.ini`). The status
line shows `LINK UP · TCP 7777` once they are talking. Start them in
either order; the deck keeps retrying.

No companion checkout handy? `tools/fake_companion.py` is a stand-in
that speaks the same protocol:

```bash
python3 tools/fake_companion.py          # terminal 1: fake pet (TCP 127.0.0.1:7777)
python3 -m cyberdeck --link tcp          # terminal 2: the deck
```

Rest, sleep, evolution and dungeon combat are specified by the companion
in [companion/docs/DUNGEON_INTEGRATION.md](companion/docs/DUNGEON_INTEGRATION.md)
(summarised in [docs/PROTOCOL.md](docs/PROTOCOL.md)). Add
`--evolution-speed 50` to `companion.py` to see evolution sooner.

The companion's own tests run from its folder:
`cd companion && python3 -m unittest discover -s tests -v`.

Serial and two-Pi setups: [docs/TESTING_TWO_BOARDS.md](docs/TESTING_TWO_BOARDS.md).

## What it can do

Type `help` for everything, or press F2 for the launcher menu.

- **Core:** split panes or tabs (`view`, `layout`, F4/F6), launcher
  (F2), hotkeys (`keys`), history (Up/Down, `history`), Tab completion.
- **Productivity and network:** `note`, `todo`, `wifi scan`/`wifi meter`,
  `net info`, `ping`, `net port`, `net check`, `ssh <name>`.
- **Pet and hardware:** `pet` (feed/play/rest/sleep/evolve/avatar/log/
  alerts/gift), an animated face and evolution progress in the pet pane,
  evolution pop-ups, hunger and boredom alerts, `gpio led|set|read|beep`,
  `music play <song>` on the buzzer.
- **Fun:** boot animation, ticker visualizer, deck moods, `hack`,
  `typehack`, `dungeon` (PULSE fights at your side with its real stats;
  loot can be gifted to it; `loot add <item>` for scripts).
- **Outside world:** `weather`, `sun`, `moon`, `iss` (passes over you,
  computed on the deck), plus a `sky` panel.

## Customize

This scaffold is meant to be edited. Start with
[CUSTOMIZING.md](CUSTOMIZING.md): colors and layout are INI files,
and new commands are just Python files dropped into `plugins/`.

## Roadmap

Next steps after this scaffold:

1. **Real UART link** — done in `cyberdeck/link.py`; still to verify on
   two real Pis (docs/TESTING_TWO_BOARDS.md part C).
2. **Pi deployment** — run this package on the main Pi Zero 2 W,
   point `--mode real` at the live sysfs sensors and the serial
   port, and confirm the curses UI on the deck's own screen.
3. **Boot-to-UI** — start the deck interface automatically on boot
   (a systemd service or getty) instead of a graphical desktop.

## Protocol (short)

Boards exchange newline-delimited JSON objects with three keys:
`type`, `id`, and `payload`. Requests and replies share the same
`id`. Current types: `hello`, `ping`, `pet.status`, `pet.feed`,
`pet.play`, plus their `*.ok` replies and `error`. Full rules are
in [docs/PROTOCOL.md](docs/PROTOCOL.md).
