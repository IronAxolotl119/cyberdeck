# Cyberdeck Pet

A simulation-first, Digimon-style virtual pet designed to eventually live on a
Raspberry Pi Zero 2 W (512 MB RAM). This repository is the foundation: a small,
standard-library-only Python 3 project you can run and test on a regular
computer before any hardware is involved.

The creature grows from a baby into an in-training form, then branches through
rookie, champion, ultimate, and mega. Care, combat stats, and how many unique
signals it has discovered all steer the forks. It also "discovers" the world by
scanning for nearby signals. For now those scans are fake and fully seeded, so
the game is deterministic and safe to test. Later, the same `Scanner` interface
can be backed by real Wi-Fi and Bluetooth lookups on the Pi.

No Digimon names or official evolution lines are used. The species here
are original to this project:

```text
Chirplet (baby)
  -> Wavling (in-training)
       -> Aethling (rookie, well cared for)
            -> Luminode (champion) -> Aurorion (ultimate) -> Aethernova (mega)
            -> Hexwave  (champion, many discoveries) -> Resonarch -> Signumega
       -> Staticore (rookie, neglected or trained hard)
            -> Voltshard (champion) -> Stormbit (ultimate) -> Nullstorm (mega)
            -> Jammeron  (champion, many discoveries) -> Blackout -> Voidstatic
```

## Goals

- Keep the **core** on the Python standard library only. The Pi Zero 2 W is
  tight on memory, so heavy frameworks do not belong in the simulation,
  save system, or discovery logic.
- Hide anything hardware-shaped behind **small interfaces**. `Scanner` is one example (a real radio driver can be dropped in later).
  The pet's avatar is a small text face, not a pixel framebuffer, so a later
  UI can just print or place the current frame string.
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
│   ├── evolution.py     Species table, time gates, and branch rules
│   ├── combat.py        Dungeon combat helpers (stats, action, hit, end)
│   └── creature.py      Creature class: tick, feed, train, rest, sleep, serialize
├── discovery/           How the pet notices the world around it
│   ├── base.py          Signal + abstract Scanner interface
│   ├── simulated.py     Seeded fake Wi-Fi / Bluetooth scans (no real radio)
│   ├── effects.py       Turns signals into XP, mood, or learned moves
│   └── sighting.py      Dashboard pet.discovery: XP, ranks, environment
├── avatar/              Kaomoji-style text faces (no images)
│   └── faces.py         Moods, 2–4 animation frames, stage accents, banners
├── link/                Talks to the main cyberdeck (docs/PROTOCOL.md)
│   ├── protocol.py      NDJSON encode/decode, buffer splitting, errors
│   ├── handler.py       Maps protocol messages onto our Creature
│   ├── events.py        pet.event coalescing and pacing
│   └── transport.py     TCP server and UART (termios) server
├── companion.py         Live tick loop + link server (the pet-board entry)
├── scripts/link_smoke.py  Tiny scripted client for a running companion
├── docs/faces/sheet.txt Catalog of every mood and stage accent
├── docs/DUNGEON_INTEGRATION.md  Rest, sleep, combat, and evolution protocol
├── docs/DISCOVERY.md           pet.discovery, move ranks, environment mood
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

# Same run, but draw the creature's text face each tick
python3 main.py --ticks 50 --seed 42 --avatar

# Plain-ASCII stage accents (mega uses * instead of «»)
python3 main.py --ticks 50 --seed 42 --avatar --ascii

# Faster evolution (gates halved). 0.5 is slower (gates doubled).
python3 main.py --ticks 80 --seed 42 --evolution-speed 2

# Print every mood and stage accent, write docs/faces/sheet.txt, and exit
python3 main.py --show-faces

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
| `--avatar` | off | Draw the current text face each tick |
| `--ascii` | off | Use plain-ASCII stage accents |
| `--show-faces` | off | Print the catalog, write `docs/faces/sheet.txt`, and exit |
| `--evolution-speed` | `1.0` | Multiply evolution rate (2.0 = twice as fast, gates halved) |
| `--evolution-config` | none | Optional `evolution.json` overlaying the default gates |

Avatars are small kaomoji-style text faces (about 1–3 lines), not images
or pixel grids. Each mood has 2–4 frames that blink, glance, or bounce a
little, and a stage accent wraps the face (none for baby, `~...~` for
in-training, `[...]` rookie, `{...}` champion, `<...>` ultimate, `«...»`
mega; `--ascii` uses `*` for mega). Faces change with mood and stats:
happy, content, hungry, tired, sleeping, sad, angry, hurt, fainted,
excited (close to evolution), and an evolving animation. The life engine
adds sick, pouting (tantrum), party, dreaming, love, nervous, suspicious,
hot, and lonely faces. It also adds short animations (idle actions, dances,
sneezes, and hiccups) and holiday costumes. `pet.avatar.ok`
returns `{style:"face", mood, frame, frames, interval_ms}`; the pet object
carries `{style:"face", mood, frame, text}`.

To review the faces without running a simulation:

```bash
python3 main.py --show-faces
cat docs/faces/sheet.txt
```

### Evolution speed and player choice

The pet **never auto-evolves**. When the age / level / XP gates are met,
`pet.status.evolution` becomes `{next_stage, progress, ready: true,
choices:[{species, stage, hint, available}]}` and a one-shot `evolve_choice`
event is pushed (`evolve_soon` still fires first, at about 90% of the
gates). The deck (or a player) then sends `pet.evolve {species}`. An
evolve-chip gift skips the time gates and emits `evolve_choice`, but does
not change form on its own.

Default gates (hours of age, level, and XP needed to leave that stage)
live in `DEFAULT_EVOLUTION_GATES` in `pet/evolution.py` and can be
overlaid by an optional project-root `evolution.json`:

| Evolving from | min age (hours) | min level | min XP |
|---------------|-----------------|-----------|--------|
| baby          | 36              | 4         | 40     |
| in-training   | 100             | 8         | 140    |
| rookie        | 220             | 13        | 320    |
| champion      | 400             | 19        | 600    |
| ultimate      | 620             | 26        | 960    |

`--evolution-speed` is a multiplier: `2.0` halves every number (twice as
fast; level is at least 2), `0.5` doubles them. The headless `main.py`
auto-picks the first available choice so a long sim still grows; the live
`companion.py` waits for `pet.evolve`. In `--avatar` mode the companion
prints a boxed list of choices (it does not block on stdin, because the
server has to keep listening).

See `docs/DUNGEON_INTEGRATION.md` for rest, sleep, dungeon combat, and the
full evolution message flow.

## How to run the tests

Still from this directory, using only the standard library:

```bash
python3 -m unittest discover -s tests -v
```

All tests should pass. They cover time passing (`tick`), rest and sleep,
dungeon combat, player-chosen evolution through mega (care, combat, and
discovery forks), text-face avatars, learning moves from levels and from
discovery events, a save/load round trip, protocol encode/decode, every
link handler type and error code, event gating, coalescing and pacing of
`pet.event` pushes, a TCP round trip including two clients at once, and
(when `socat` is installed) a UART exchange over a PTY pair. They also cover
every aliveness feature with a fake clock and a seeded RNG, including a
test that the no-repeat picker cycles through its whole pool. If a checkout of the main cyberdeck is next to this repo (or
`CYBERDECK_MAIN` points at one) and that tree has the TCP client, one extra
test drives our companion with the deck's own `StreamCompanionLink`.

## Linking with the main deck

The main cyberdeck and this companion speak the same newline-delimited JSON
protocol described in the main repo's `docs/PROTOCOL.md` (reference:
`cyberdeck/protocol.py`). The main board sends requests; this process replies
with the same `id`. Run this side first, then point the deck at the link.

On this repo (the pet board, or a regular computer for testing):

```bash
# TCP, default 127.0.0.1:7777. Autosave is optional.
# Several decks can connect at once. --event-interval paces pet.event
# pushes (default 0.5s; 0 sends immediately).
python3 companion.py --tcp 127.0.0.1:7777 --save ./save.json
python3 companion.py --tcp 127.0.0.1:7777 --event-interval 0.5

# Same, with a terminal face, evolution popups, and a slower/faster clock
python3 companion.py --tcp 127.0.0.1:7777 --avatar --ascii --tick-seconds 1 --dt 0.05 --evolution-speed 1

# UART on a Pi (115200 8N1 raw, no flow control). Path defaults to
# $CYBERDECK_SERIAL or /dev/serial0.
python3 companion.py --serial
python3 companion.py --serial /dev/serial0
```

`--tick-seconds` is the wall-clock interval between ticks; `--dt` is how many
*simulated hours* each tick advances (default 0.05, about three minutes).
The live companion does not auto-feed: the main deck takes care of PULSE
through `pet.feed`, `pet.play`, and `pet.gift`.

The main deck now has a real TCP (and serial) client. Start this companion
first, then point the deck at it. Both sides need Python 3.10 or newer; the
deck is stdlib-only for TCP/headless (curses for the full-screen UI;
`pyserial` is optional for `--link serial`).

```bash
# Terminal A — from this repo
python3 companion.py --tcp 127.0.0.1:7777

# Terminal B — from the main cyberdeck repo
# Seed a loot bag first if you want to try `pet gift`:
#   mkdir -p /tmp/cyberdeck-data
#   printf '%s\n' '[{"name":"glitch berry","kind":"food","power":20,"rarity":"common"},{"name":"evo chip","kind":"evolve","power":0,"rarity":"legendary"}]' > /tmp/cyberdeck-data/loot.json
python3 -m cyberdeck --link tcp --host 127.0.0.1 --port 7777 --headless \
  --data-dir /tmp/cyberdeck-data \
  --script 'link' \
  --script 'pet status' \
  --script 'pet feed ration' \
  --script 'pet play spar' \
  --script 'pet gift 1' \
  --script 'pet gift 1' \
  --script 'pet status' \
  --script 'quit'
```

`--host` and `--port` default to 127.0.0.1 and 7777, so
`python3 -m cyberdeck --link tcp --headless` is enough for a typed session.
Headless mode understands `pet status`, `pet feed [item]`, `pet play [game]`,
and `pet gift <n|name>` (gift needs `data/loot.json`). There is not yet a
headless `pet avatar` command — the TUI fetches `pet.avatar` itself. The
scripted client in this repo is still useful as a fallback:

```bash
python3 scripts/link_smoke.py 127.0.0.1:7777
```

For UART on two boards, start this process with `--serial` (default
`$CYBERDECK_SERIAL` or `/dev/serial0`) and start the deck with
`--link serial`. Both sides honor `CYBERDECK_SERIAL`.

A note on units: the protocol reports hunger as a *need* (0 = full, 100 =
starving), so feeding lowers it. Internally this sim stores the opposite
meter (0 = starving, 100 = stuffed). `link/handler.py` is the only place
that conversion happens. Species names stay ours (see the tree at the top)
and stages are `baby` / `in-training` / `rookie` / `champion` / `ultimate`
/ `mega`. An `evolve` gift (evo chip) skips the time gates and makes the
pet *ready to choose*; it does not change form. It is rejected
(`accepted: false`) only at mega. The player then sends
`pet.evolve {"species":"Wavling"}`, which replies
`pet.evolve.ok {accepted, from, to, reaction}` and is followed by
`evolving` then `evolved` events. Unknown species names are a
`bad_payload` error; a known but locked or unready species returns
`accepted: false` with a reason. The TCP server accepts several clients at
once: each has its own buffer and events flag, replies go only to the
requester, and `pet.event` is pushed to every client that opted in, at
most one event per `--event-interval`. Duplicate hungry / tired / sad
events are coalesced while they are still queued; evolved / level_up /
learned_move / woke / evolve_soon / evolving / evolve_choice are never
dropped.

The pet object in status / feed / play / gift / event replies includes
`level`, `xp`, `hp`, `max_hp`, `attack`, `defense`, `speed`, `moves`,
`care_mistakes`, `asleep`, `fainted`,
`evolution: {next_stage, progress, ready, choices}`, and
`avatar: {style:"face", mood, frame, text}`. Protocol mood words are
content / playful / joyful / hungry / tired / grumpy, plus asleep,
fainted, and excited when those states apply.

`pet.avatar` is answered with `pet.avatar.ok`:

```json
{"type":"pet.avatar","id":"...","payload":{"frame":0,"mood":"playful"}}
{"type":"pet.avatar.ok","id":"...","payload":{
  "style":"face","mood":"happy","frame":0,"interval_ms":450,
  "frames":["(^_^)","(^o^)","(^_^)","(^v^)"]
}}
```

`frame` (a non-negative int) and `mood` in the request are optional
overrides. `mood` in the reply is the face mood (happy, content, hungry,
tired, sleeping, sad, angry, hurt, fainted, excited, evolving).

Other messages this companion speaks, all documented in
`docs/DUNGEON_INTEGRATION.md`:

- `pet.rest` → `pet.rest.ok {rested, energy_delta, pet}` (small instant rest;
  refused with a reason when energy is already full, unless the pet is
  fainted or missing HP)
- `pet.sleep {on: true|false}` → `pet.sleep.ok {asleep, changed, pet}`
  (energy refills each tick, hunger drains slower, HP trickles back; the
  pet will not play, train, or fight; it wakes at full energy and emits
  `woke`)
- `pet.combat.stats` / `pet.combat.action` / `pet.combat.hit` /
  `pet.combat.end` for the dungeon companion API
- `pet.evolve {species}` as described above
- `pet.discovery {kind, id, interest, event?, label?}` → `pet.discovery.ok`
  (PULSE XP and a chance to learn or strengthen a move from a dashboard
  find; see `docs/DISCOVERY.md`)

If the main deck's `hello` payload includes `"events": true`, we echo that
in `hello.ok` and may then push `{type:"pet.event", id:<new>,
payload:{event, detail, pet}}` for hungry / tired / sad / evolved /
level_up / learned_move / move_strengthened / signal_found / woke /
evolve_soon / evolving / evolve_choice. `detail` is a dict (empty for the
need events and woke;
`from`/`to`/`form`/`stage`/`from_stage` for evolved; `from`/`to` for
evolving; `from`/`to_candidates`/`next_stage`/`eta_hours` for evolve_soon;
`from`/`next_stage`/`choices` for evolve_choice; `level` for level_up;
`move` for learned_move; `move`/`rank`/`bonus` for move_strengthened;
`ssid`/`name`/`kind`/`rssi` for signal_found).
A hello may also include `client` (for example `"dash"`); it is ignored.
Without that flag we stay request/reply only.

## It feels alive

The pet does more than tick stats. `pet/life.py` gives it a life of its own.
[docs/ALIVE.md](docs/ALIVE.md) has the full list, the new protocol messages
and events, and the tunables. In short:

- **On its own:**
  - It idles: yawns, chases bugs, dances, hums, and dozes.
  - It asks you for things, and has secret favorite and hated foods.
  - It hiccups and sneezes, dreams about real memories, and goes on short
    adventures. It comes back with a generated story and loot.
- **Remembers:**
  - It greets you based on how long you were gone, and gets lonely when the
    deck disconnects.
  - It learns what time you usually show up.
  - It recognizes networks (home, work, the café) and defends its turf.
  - It keeps a nightly diary and a shelf of trinkets.
- **Has a personality:**
  - A hidden bond (stranger → soulmate) and obedience shaped by praise and
    scolding.
  - Lingering moods, tantrums, nicknames, and boredom if you feed it the same
    snack again and again.
  - Sickness that needs a cure, and jealousy when you use other apps.
  - Mood and bond change how it fights.
- **Knows the real world:**
  - The clock, CPU temperature, how busy the airwaves are, and optional
    weather (`--weather LAT,LON`).
  - Birthdays and monthly hatchdays, plus 15 holidays with costumes.
- **Long term:**
  - A named rival that drops by every few hours.
  - A mega form can lay an egg that starts a new generation, which inherits a
    move, a trait, and memories.

All its words come from about 2,000 lines in `pet/lines/*.lines` (218 pools).
A no-repeat picker is saved with the pet, so a line won't come back until most
of its pool has been used. Babies babble, in-training pets talk simply, and
older forms sound like kids, then teens, then wise elders. Diaries and
adventure stories are assembled from fragments, which gives hundreds of
millions of combinations. You can add lines without touching any code.

```bash
# Fast-forward two simulated days with a fake clock (no network, no deck)
python3 scripts/alive_demo.py --hours 48 --seed 11
# Line counts per category and story combinations
python3 scripts/line_stats.py
```

Companion flags for this:

| Flag | Meaning |
|---|---|
| `--reset` | Start a brand-new pet. If `--save` points at an existing file, it is first copied to `.bak`. |
| `--weather LAT,LON` | Optional real weather from Open-Meteo. It silently does nothing when offline. |
| `--life-pace N` | Make idle life, chatter, adventures, and rivals N× more frequent. Useful for demos. |

## Roadmap

1. **Core simulation** (this repo). Stats, a tick loop, one evolution line with
   a branching line through mega, fake Wi-Fi/Bluetooth scans, JSON saves, and a
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
