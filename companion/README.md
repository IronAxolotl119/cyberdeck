# Cyberdeck Pet

A simulation-first, Digimon-style virtual pet designed to eventually live on a
Raspberry Pi Zero 2 W (512 MB RAM). This repository is the foundation: a small,
standard-library-only Python 3 project you can run and test on a regular
computer before any hardware is involved.

The creature grows from a baby into an in-training form, then branches into one
of two rookie forms depending on how you care for it and how its stats develop.
It also "discovers" the world by scanning for nearby signals. For now those
scans are fake and fully seeded, so the game is deterministic and safe to test.
Later, the same `Scanner` interface can be backed by real Wi-Fi and Bluetooth
lookups on the Pi.

No Digimon names or official evolution lines are used. The species here
(Chirplet, Wavling, Aethling, and Staticore) are original to this project.

## Goals

- Keep the **core** on the Python standard library only. The Pi Zero 2 W is
  tight on memory, so heavy frameworks do not belong in the simulation,
  save system, or discovery logic.
- Hide anything hardware-shaped behind **small interfaces**. `Scanner` is one example (a real radio driver can be dropped in later).
  `Renderer` is another: `TerminalRenderer` stands in today; a future SPI,
  e-ink, or OLED driver can consume the same 2D pixel array.
- Learn by reading. The code is commented for a beginner who is comfortable
  with Python basics (functions, classes, modules) and is learning how to
  structure a small game.

## Folder layout

```
cyberdeck-pet/
├── main.py              Headless text simulator (the program you run)
├── save.py              JSON save and load, path is configurable
├── pet/                 The creature itself
│   ├── stats.py         HP, attack, hunger, mood, XP, care mistakes, ...
│   ├── moves.py         Move dataclass and a small shared move library
│   ├── evolution.py     Data-driven species table and evolution rules
│   └── creature.py      Creature class: tick, feed, train, rest, serialize
├── discovery/           How the pet notices the world around it
│   ├── base.py          Signal + abstract Scanner interface
│   ├── simulated.py     Seeded fake Wi-Fi / Bluetooth scans (no real radio)
│   └── effects.py       Turns signals into XP, mood, or learned moves
├── avatar/              16x16 text-grid sprites and a Renderer interface
│   ├── sprites.py       Palettes, idle frames, and mood face-overlays
│   └── renderer.py      ANSI half-blocks, ASCII fallback, optional rich
├── link/                Talks to the main cyberdeck (docs/PROTOCOL.md)
│   ├── protocol.py      NDJSON encode/decode, buffer splitting, errors
│   ├── handler.py       Maps hello/ping/feed/play/gift onto our Creature
│   └── transport.py     TCP server and UART (termios) server
├── companion.py         Live tick loop + link server (the pet-board entry)
├── scripts/link_smoke.py  Tiny scripted client for a running companion
├── docs/sprites/        Pre-rendered catalogs (no images)
│   ├── sheet.ansi       Truecolor half-block sheet (raw ANSI)
│   └── sheet.txt        Plain-ASCII sheet
├── tests/               unittest (standard library) coverage
├── README.md
└── .gitignore
```

## How to run

You need Python 3.10+ (the code uses type hints like `list[str]`). No packages
need to be installed.

From this directory:

```bash
# 50 simulated hours, reproducible with seed 42
python3 main.py --ticks 50 --seed 42

# Same run, but draw the creature's 16x16 avatar each tick
python3 main.py --ticks 50 --seed 42 --avatar

# Plain-ASCII avatar (no color, good for a basic terminal or a log file)
python3 main.py --ticks 50 --seed 42 --avatar --ascii

# Print every form and mood side by side, write docs/sprites/sheet.ansi
# and docs/sprites/sheet.txt, then exit
python3 main.py --show-sprites
python3 main.py --show-sprites --ascii

# Optional: write a save file at the end (or load one at the start)
python3 main.py --ticks 20 --seed 42 --save ./save.json
python3 main.py --ticks 20 --seed 42 --save ./save.json --load ./save.json
```

Useful flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--ticks` | `50` | How many simulated hours to run |
| `--seed` | `42` | RNG seed for fake scans (same seed = same "airwaves") |
| `--name` | `Pulse` | Your creature's nickname |
| `--save` | none | JSON path to write after the run |
| `--load` | none | JSON path to read before the run |
| `--avatar` | off | Draw the current form's sprite each tick |
| `--ascii` | off | Use a plain-ASCII sprite instead of color half-blocks |
| `--show-sprites` | off | Print the catalog, write `docs/sprites/sheet.*`, and exit |
| `--rich` | off | Use the optional `rich` package for sprites, if installed |

Avatars are original 16×16 pixel art stored as text grids, not image files.
`TerminalRenderer` packs two pixel rows into one terminal row with the `▀`
half-block character and ANSI truecolor, so a sprite is 16 columns by 8
rows. `--ascii` falls back to a luminance ramp (`#` for the outline). Pixel
data is always a plain 2D array of RGB values, so a later SPI, e-ink, or
OLED renderer can reuse it unchanged. The core does not depend on `rich`;
`--rich` is only a nicer path when that library happens to be installed.

To review the art without running a simulation:

```bash
python3 main.py --show-sprites --ascii
cat docs/sprites/sheet.txt
# In a truecolor terminal:
cat docs/sprites/sheet.ansi
```

## How to run the tests

Still from this directory, using only the standard library:

```bash
python3 -m unittest discover -s tests -v
```

All tests should pass. They cover time passing (`tick`), both rookie evolution
branches, learning moves from levels and from discovery events, a save/load
round trip, protocol encode/decode, every link handler type and error code,
event gating, a TCP round trip, and (when `socat` is installed) a UART
exchange over a PTY pair.

## Linking with the main deck

The main cyberdeck and this companion speak the same newline-delimited JSON
protocol described in the main repo's `docs/PROTOCOL.md` (reference:
`cyberdeck/protocol.py`). The main board sends requests; this process replies
with the same `id`. Run this side first, then point the deck at the link.

On this repo (the pet board, or a regular computer for testing):

```bash
# TCP, default 127.0.0.1:7777. Autosave is optional.
python3 companion.py --tcp 127.0.0.1:7777 --save ./save.json

# Same, with a terminal avatar and a slower/faster clock
python3 companion.py --tcp 127.0.0.1:7777 --avatar --ascii --tick-seconds 1 --dt 0.05

# UART on a Pi (115200 8N1 raw, no flow control). Path defaults to
# $CYBERDECK_SERIAL or /dev/serial0.
python3 companion.py --serial
python3 companion.py --serial /dev/serial0
```

`--tick-seconds` is the wall-clock interval between ticks; `--dt` is how many
*simulated hours* each tick advances (default 0.05, about three minutes).
The live companion does not auto-feed: the main deck takes care of PULSE
through `pet.feed`, `pet.play`, and `pet.gift`.

The main deck's UART client is still a stub in the current scaffold, and it
has no TCP client yet. Until that lands, a scripted client in this repo can
exercise a running server (it will use the main repo's encoder if
`/workspace/cyberdeck-main` is importable):

```bash
# Terminal A
python3 companion.py --tcp 127.0.0.1:7777

# Terminal B
python3 scripts/link_smoke.py 127.0.0.1:7777
```

When the deck's serial link is implemented, start this process with
`--serial` (or `--serial /dev/ttyAMA0` if you want the deck's default path)
and start the deck with `--mode real`. Both sides honor `CYBERDECK_SERIAL`.

A note on units: the protocol reports hunger as a *need* (0 = full, 100 =
starving), so feeding lowers it. Internally this sim stores the opposite
meter (0 = starving, 100 = stuffed). `link/handler.py` is the only place
that conversion happens. Species names stay ours (Chirplet, Wavling,
Aethling, Staticore) and stages are `baby` / `in-training` / `rookie`. An
`evolve` gift advances one of those forms using our care/mood branch rules;
it is rejected (`accepted: false`) at a final rookie form.

The pet object in status / feed / play / gift / event replies includes the
optional extras the main deck understands: `level`, `xp`, `hp`, `max_hp`,
`attack`, `defense`, `speed`, `moves`, `care_mistakes`, and
`avatar: {form, mood, frame}` (`mood` here is the protocol word:
content / playful / joyful / hungry / tired / grumpy).

`pet.avatar` asks for the 16×16 sprite itself and is answered with
`pet.avatar.ok`:

```json
{"type":"pet.avatar","id":"...","payload":{"frame":0,"mood":"playful"}}
{"type":"pet.avatar.ok","id":"...","payload":{
  "form":"chirplet","mood":"playful","frame":0,"width":16,"height":16,
  "palette":[null,"#121018","#ffd440"],
  "rows":[[0,0,...],[...]]
}}
```

`frame` (0 or 1) and `mood` in the request are optional overrides. Index 0 of
`palette` is transparent (`null`); later entries are `"#rrggbb"`. `rows` is
16 lists of 16 palette indexes.

If the main deck's `hello` payload includes `"events": true`, we echo that
in `hello.ok` and may then push `{type:"pet.event", id:<new>,
payload:{event, detail, pet}}` for hungry / tired / sad / evolved /
level_up / learned_move / signal_found. `detail` is a dict (empty for the
need events; `from`/`to`/`form`/`stage`/`from_stage` for evolved; `level`
for level_up; `move` for learned_move; `ssid`/`name`/`kind`/`rssi` for
signal_found). Without that flag we stay request/reply only.

## Roadmap

1. **Core simulation** (this repo). Stats, a tick loop, one evolution line with
   a two-way rookie branch, fake Wi-Fi/Bluetooth scans, JSON saves, and a
   protocol link to the main deck.
2. **Custom UI from scratch.** A simple, hand-drawn interface that talks
   directly to a small screen. No Raspberry Pi desktop environment and no
   full-window toolkit sitting on top of one — the Pi should boot into the pet,
   not into a general-purpose desktop.
3. **Real Wi-Fi and Bluetooth scanning.** A new `Scanner` implementation that
   calls the Pi's radios, swapped in behind the existing interface. The pet
   already knows how to turn a `Signal` into an in-game event.
4. **Hardware buttons and display.** Physical inputs mapped to feed / train /
   rest / scan, and the custom UI shown on a real panel.

The TCP/UART *link* is implemented now; it does not scan real Wi-Fi or drive
GPIO itself. Real radios, buttons, and a custom display still come later.
