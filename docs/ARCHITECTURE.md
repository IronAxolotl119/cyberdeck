# Architecture walkthrough

This is a plain-English map of how the main cyberdeck program is put
together, and what happens after you press a key.

You do not need to read this to *use* the deck. It is here so you can
find the right file when you want to change something. For "how do I
change colors or add a command?", see [CUSTOMIZING.md](../CUSTOMIZING.md)
in the project root. For the two-board message format, see
[PROTOCOL.md](PROTOCOL.md).

## What this program is

Two Raspberry Pi Zero 2 W boards are planned:

- **Main board** — this project. A custom full-screen terminal UI
  (not the Raspberry Pi desktop). You type commands here.
- **Companion board** — a separate project, later. It hosts a
  Digimon-style pet. This board talks to it with a small JSON
  protocol.

Right now there is no Pi and no second board. The program runs in a
normal Linux terminal and *simulates* both the sensors and the pet.
The seams are already in place so those simulations can be swapped
for real hardware later.

## One interface, two front ends

The deck's **brain** is the `cyberdeck/` package: the command registry,
every feature (notes, dungeon, weather, ISS, music, GPIO, SSH…), the
companion link and the sim pet. It never draws anything. Commands
return text lines; full-screen modes are `Activity` objects that return
rows of `(text, role)` pieces; side panes do the same. Anything a
command wants from the screen (clear, quit, open a mode, run ssh) is
left on the `Context` as a request.

Three front ends read that brain:

```text
                     cyberdeck/  (commands, features, link, sim pet)
                    /                 |                     \
 cyberdeck_dash/ (Rich, default)   tui.py (--classic)   headless.py (--headless/--script)
   DeckBridge + Dashboard          curses DeckUI         plain text
```

`python3 -m cyberdeck` starts the **Rich interface** (`cyberdeck_dash/`):

- `cyberdeck_dash/unified.py` builds a `DeckBridge` around the deck's
  Context and registry, then a `Dashboard` with that bridge, and runs
  the Rich Live loop (keys, GPIO buttons, redraw; it pauses the screen
  for ssh).
- `deck_bridge.py` is `tui.DeckUI` without curses: the console log,
  the line being typed, history and Tab completion (reusing
  `history.py` and `completion.py`), the current Activity, background
  job results, service ticks, deck notices, pop-ups and badges.
- `shared_link.py` makes the deck's companion link the **only** link.
  `TeeCompanion` copies each pushed `pet.event` to the deck's features
  and to the dashboard's PULSE panel; `SharedLink` gives the dashboard
  its small request/callback API on top of the same connection.
- `ui/deck_panels.py` holds the deck tiles (console, dungeon, PULSE
  combat, loot, notes, todo, fun & tools, sky, music, net, output) and
  `ui/rows.py` paints deck rows with the Rich theme, so one theme
  colours everything.
- Keys: `Dashboard.handle_key()` sends a key to an open Activity
  first, then to `keys.ini` hotkeys, then to the focused deck tile
  (the Console takes all typing), and only then to the dashboard keys.

`--classic` starts the curses UI (`tui.py`) exactly as before, and
`--headless` / `--script` are unchanged. If Rich is not installed, the
default falls back to `--classic`.

## Folder map

```text
cyberdeck-main/
  config/               INI files you can edit (colors, layout, keys, …)
  plugins/              your extra commands; auto-loaded at startup
  data/                 files the deck writes (history, notes…); not in git
  cyberdeck/            the Python package (`python -m cyberdeck`)
    app.py              flags, startup, choose Rich UI, --classic, or headless
    tui.py              the classic curses interface (--classic)
    jobs.py             run slow commands on a thread; "fetching…" placeholder
    statusline.py       fit the status readings + link label into the bar
    popups.py           boxed evolution pop-ups: queue, timeout, never eats keys
    headless.py         run commands as plain text (for scripts/tests)
    commands.py         command registry, Context, core commands
    features/           one module per bigger feature, each with register()
      core_ux.py        history / menu / view / layout / keys commands
      notes.py          note + todo commands, plain-text stores, todo panel
      wifi.py           wifi status / scan / live meter (nmcli, iw, sim)
      netcheck.py       net hosts / ping / port / check (TCP + system ping)
      netinfo.py        net info screen, rx/tx RateMeter, net panel
      ssh.py            ssh shortcuts (runs ssh with curses suspended)
      hardware.py       gpio command + shared Hardware (backend, pin names, buzzer lock)
      music.py          song parser, background MusicPlayer, music panel
      petwatch.py       PULSE alerts, pet log, pet sim (hooks into `pet`)
      petcare.py        pet rest / sleep / evolve (+ picker) / avatar
      boot.py           boot animation Activity (+ `boot` command)
      visualizer.py     ticker-line bars, driven by the music player or idle sim
      moods.py          moods.ini rules -> sassy ticker lines
      mainframe.py      `hack` show Activity
      typing_game.py    `typehack` rounds, scoring, high scores
      dungeon.py        roguelike rules (Game), PULSE as an ally (PulseAlly), loot bag, gifts, `loot`
      weather.py        weather / sun / moon commands, ASCII icons, sky panel
      iss.py            ISS passes, position, TLE fallback, pass alerts
    link.py             companion link over TCP or UART: background thread, ids, events, reconnect
    petdata.py          tolerant pet parsing: needs, asleep, evolution, faces, combat stats, event text
    netcache.py         cached web downloads with offline fallback (data/cache/)
    world.py            world.ini settings, timezone display, background refresher
    astro.py            sun and moon maths, sidereal time, look angles
    orbit.py            TLE parsing, orbit propagation, pass finder
    fx.py               shared effects: block-letter banner, glitch, bars, fake code
    gpio.py             GPIO backends: SimGpio, GpiozeroGpio, SysfsGpio
    activity.py         base class for full-screen modes + the launcher menu
    panes.py            side panels, tabs, and the screen-layout maths
    history.py          Up/Down history saved to data/history.txt
    completion.py       Tab completion
    keys.py             key names, keys.ini hotkeys and launcher entries
    plugins.py          loads files from plugins/
    config.py           reads the INI files (theme, layout, Settings helper)
    protocol.py         encode/decode newline-delimited JSON messages
    sensors.py          CPU temp / battery / wifi (sim + Pi stub)
    companion.py        simulated pet (PetState) + the CompanionLink interface
    __main__.py         hook for `python -m cyberdeck`
  tools/fake_companion.py  reference pet server (TCP or serial) for testing
  songs/                chiptunes for the buzzer (plain text)
  text/                 editable text: typing phrases, fallback ISS orbit
  docs/                 protocol, architecture, UI snapshot
  tests/                unit tests (stdlib unittest)
  cyberdeck_dash/       the Rich interface (`python -m cyberdeck`; alias `python -m cyberdeck_dash`)
    unified.py          builds Dashboard + DeckBridge, Rich Live loop, --snapshot
    deck_bridge.py      the deck minus curses: console, history, completion, activities, jobs
    shared_link.py      the deck's one companion link, shared with the dashboard
    app.py              the Dashboard: tabs, scanners, alerts, key routing (incl. deck tabs)
    ui/                 screen layout, panels, deck panels, rows -> Rich text
    scanners/, rules.py, journal.py, field.py, diag.py …  awareness side (see DASHBOARD.md)
```

## How data flows from a keypress to the screen

Here is the path of a command such as `pet feed`:

1. **You type.** In full-screen mode, `tui.py` is in a loop. Each
   key is added to a line at the bottom of the screen. (In headless
   mode, the line comes from stdin or `--script` instead. From here
   on the path is the same.)

2. **You press Enter.** The TUI (or headless runner) takes the
   finished line and calls `Registry.dispatch()` in `commands.py`.

3. **The registry picks a handler.** `dispatch` splits the line into
   a name and arguments (`pet` + `["feed"]`), looks up that name,
   and calls the handler function with a shared `Context`. The
   context holds the sensors, the companion link, and the current
   mode. Built-in handlers live in `build_registry()`; user ones
   come from `plugins/`.

4. **The command talks to the companion (when it needs to).**
   `pet feed` does not change the pet itself. It calls
   `ctx.companion.request("pet.feed", {"item": "ration"})`.
   That goes through the `CompanionLink` interface in
   `companion.py`.

5. **The link uses the protocol.** A `Message` is built in
   `protocol.py` (type, unique id, payload) and encoded as one line
   of JSON. In simulation mode, `SimCompanionLink` does not write
   to a serial port: it ticks the fake pet's needs forward, runs
   the same handler a real companion would, and returns a reply
   `Message` with the *same id*. With `--link tcp` or `--link serial`,
   `StreamCompanionLink` writes the line to the wire and waits for the
   reply with that id. A background thread reads the wire.

6. **The command turns the reply into text.** The handler reads
   the `pet` object out of the reply payload and formats a few
   lines (name, mood, hunger bars, and so on).

7. **The screen (or stdout) shows those lines.** The TUI appends
   them to the output pane and redraws. Headless mode prints them.
   The status bar is redrawn from `ctx.sensors.read()` and
   `ctx.companion.link_label()` on a timer, even if you are not
   typing, so the clock keeps ticking.

`quit` and `clear` are slightly different: they set a flag on the
context (`should_quit` / `should_clear`) and the UI or headless
runner acts on it after the handler returns.

```text
 key / --script
        │
        ▼
  tui.py  or  headless.py
        │  dispatch(line, ctx)
        ▼
  commands.py  (Registry → handler)
        │  ctx.companion.request(...)
        ▼
  companion.py / link.py  (SimCompanionLink or StreamCompanionLink)
        │  Message.encode / decode
        ▼
  protocol.py   ←── (later) UART to the other Pi
        │
        ▼
  handler returns text lines
        │
        ▼
  output pane  or  stdout
```

## Keys that are not plain typing

The prompt loop in `tui.py` turns every curses keypress into a simple
name with `keys.key_name()` (`"f2"`, `"ctrl+p"`, `"up"`, `"a"`), then:

1. **Hotkeys first.** If the name is in `config/keys.ini` `[hotkeys]`,
   its action runs. An action is either a command line (handled exactly
   like typing it and pressing Enter) or a `:ui action`.
2. **Editing keys.** Enter, Tab, Backspace, arrows, Home/End, PgUp/PgDn,
   Ctrl-A/E/U/L.
   - Up/Down ask `History` (history.py) for the previous/next line.
   - Tab asks `complete_line()` (completion.py), which looks at command
     names, each command's `subcommands`, and its optional `completer`.
3. **Anything printable** is inserted at the cursor.

## How a command asks the screen to do something

Commands never import curses. Instead they set a field on `Context`
and `tui.py` acts on it right after the command returns
(`DeckUI._after_command`):

| Field            | Meaning                                         | Headless fallback |
|------------------|-------------------------------------------------|-------------------|
| `should_clear`   | wipe the output pane                            | ignored |
| `ui_action`      | `:launcher`, `:tab pet`, `:layout split`…       | ignored |
| `activity`       | run a full-screen `Activity` (menu, game, effect) | prints `activity.headless()` |
| `external`       | argv to run with curses suspended (ssh)         | runs it directly |
| `notify()`       | queue a message for the ticker line             | (not shown) |

An **Activity** (activity.py) is a small object with `on_key()`,
`update()`, and `render()`; render returns rows of `(text, role)`
pieces. `DeckUI._run_activity` loops: update → paint → wait for a key.
Popups (like the launcher) set `overlay = True` and are drawn in a box
over the normal screen. Because activities never touch curses, tests
drive them with fake key names and read `render()` as text.

## Screen layout: split panes and tabs

`panes.compute_layout(rows, cols, settings)` returns a `ScreenLayout`
with the row/column of every area: status row, ticker row, dividers,
the output area, and the side panels with their title rows. `tui.py`
only paints what it is told. In split mode, panels named in
`[panes] side` are stacked on the right. In tabs mode, the top divider
becomes a tab bar. Narrow terminals fall back to tabs automatically.

Panels are registered by name with `panes.register_pane(name, title,
fn)`; `fn(ctx, width, height)` returns rows. Built in: `pet` and
`system`. The pet panel reads the companion at most every
`pet_refresh` seconds.

Long-lived helpers (history, later the GPIO backend, music player, pet
watcher) live in `ctx.services`. If one has a `ui_tick(ctx, now)`
method, the TUI calls it every frame, which is how background features
update without threads in the UI.

## Network features and the outside world

The network helpers are written so they can never hang or crash the
deck:

- External tools (`nmcli`, `iw`, `ping`, `ip`, `ssh`) are found with
  `shutil.which` and run with timeouts. If a tool is missing or not
  permitted, a fallback is used (sim scan, current link only, TCP ping).
- Their output is parsed by small `parse_*` functions that take text,
  so tests feed them captured samples.
- `net check` uses a thread pool so slow hosts are checked in parallel.
- Commands that wait on the network (`weather`, `iss` and `iss refresh`,
  `net check`, `net ping`, `net port`, `ping <host>`) never block the
  screen. They hand their work to `jobs.in_background()`, which:
  * in the full-screen UI, starts a thread and returns a one-line
    placeholder (`weather: fetching…  [job 1]`). Every frame,
    `tui._collect_jobs()` picks up finished jobs, swaps the placeholder
    for the real lines, and flashes "weather ready" on the ticker. The
    same command typed again while it runs says "still fetching";
  * in headless mode (and the tests), just runs the work and returns
    its lines, so scripts get results in order.
  `weather` with a fresh cache answers straight away, and the sky panel
  keeps refreshing in the background as before (`world.SkyRefresher`).
- Traffic rates come from two `/proc/net/dev` samples (`RateMeter`).
- `ssh` sets `ctx.external`; `tui.py` calls `curses.endwin()`, runs
  ssh in the real terminal, then redraws. A failed ssh waits for Enter
  so the error stays readable.

### Weather, sun, moon, ISS

These follow the same rules: the maths and parsing are plain functions
with no I/O, and the network is optional.

- **netcache.fetch(url, max_age)** gives back a cached copy if it is fresh
  enough; otherwise it downloads (urllib, with a timeout). If the
  download fails it gives back the old copy marked `stale`, or `offline` if
  nothing was saved. It never raises for network trouble.
- **world.SkyRefresher** is a service with `ui_tick`. Every 30 s it
  checks its jobs (weather every `refresh_minutes`, the TLE every
  `refresh_hours`) and runs any that are due on a daemon thread. Panels
  only read the cache, so drawing never waits on the network. The
  `weather` and `iss` commands themselves may wait up to `timeout`
  seconds when the cache is old.
- **astro.py**: the sunrise equation (agrees with Open-Meteo to within
  about a minute), the moon's phase from a known new moon and the mean
  synodic month (within about 2 h), GMST, and the conversion from
  Earth-fixed coordinates to elevation and azimuth.
- **orbit.py** propagates the TLE with Keplerian motion plus J2 secular
  drift and the TLE's drag term, then rotates into the Earth frame.
  It was checked against a live tracker (within about 10 km) and
  Heavens-Above (pass times within about 5 s). A 48 h scan is
  `find_passes` stepping every 30 s and bisecting each horizon
  crossing. The scan is memoised for 10 minutes. A pass is *visible*
  when the sun is more than 6° below your horizon and the station is
  outside Earth's shadow (cylinder model).
- Times are kept in UTC internally and shown in `[location] timezone`
  with `zoneinfo`. If that zone is missing, the device's own zone is used.

## The companion link

`app.py` picks the link from `--link` (or `CYBERDECK_LINK`, or
`[link] mode` in hardware.ini):

- `sim`: `SimCompanionLink`, the pet inside this process.
- `tcp` / `serial`: `link.StreamCompanionLink` over a `TcpTransport`
  or `SerialTransport`.

`StreamCompanionLink` runs one daemon thread that owns the wire. It
handles connect and hello (with backoff), reads and splits lines,
matches replies to waiting requests by `id`, queues `pet.event`
pushes, and polls `pet.status` (which is also the heartbeat). Callers
write under a lock and wait on an Event for at most `timeout`. If the
link is down, a request fails at once with `CompanionError`.

The UI never waits on the wire for drawing:

- `cached_pet()` uses `pet_snapshot()`, the link's latest pet.
- `PetWatcher` drains pushed events every 0.5 s.
- The avatar (an animated text face, or an older companion's sprite)
  is fetched on a short background thread when the mood changes; the
  face's frames are cycled locally every `interval_ms`.

Only a command you type (`pet feed`) can wait, at most `timeout` (1.5 s).

## Pet alerts, GPIO, and music

- **Extending `pet`.** `commands.py` keeps two small hook tables:
  `PET_ACTIONS` (extra `pet <action>` handlers — `log`, `alerts`, `sim`,
  later `gift`) and `PET_HOOKS` (functions told about every feed, play,
  and gift reply — the pet log uses this). Features add to them in their
  `register()` instead of editing the `pet` command.
- **Background work without threads in the UI.** `features.UI_STARTUP`
  lists functions run once when the TUI starts; `petwatch` uses it to
  create the `PetWatcher` service. The TUI calls every service's
  `ui_tick(ctx, now)` each frame; the watcher only polls the companion
  every `poll_seconds`. `PetWatcher.check()` is pure logic (pet dict +
  time → events), so alert rules are unit-tested.
- **Status badges.** Any service with `status_badge()` can put a short
  flashing label on the right of the status bar (`! HUNGRY`, or
  `zZ ASLEEP` while PULSE sleeps).
- **Pop-ups that never steal input.** Evolution events (`evolve_soon`,
  `evolve_choice`, `evolving`, `evolved`) become `popups.Popup`s in a
  `PopupQueue` service. Unlike an Activity, a pop-up is not modal: the TUI
  draws the current one last in `_draw()`, `_handle_key()` dismisses it on
  any key and then handles that key as usual, and `active(now)` closes it
  after `[popups] seconds` and shows the next one. `evolving` animates
  (the frame rate goes up only while it is on screen).
- **Evolution is the player's choice.** The companion reports
  `evolution {progress, ready, choices}` in `pet.status`; the pet pane and
  `pet status` draw a progress bar, and `pet evolve` opens `EvolvePicker`
  (locked forms grey, with their hint, and skipped by the cursor), which
  runs `pet evolve <species>`.
- **Fitting the status bar.** `statusline.status_items()` turns a sensor
  reading and the link label into items, each with a priority and
  shorter forms (`CPU 17%` → `C17%`, `DECK-NET -53dBm` → `-53dBm` →
  `W-53`, `LINK UP · TCP 7777` → `LINK UP`). `fit()` shortens the
  least important item first, then drops items (memory, CPU, wifi,
  temperature, battery), so the link label is the last to be touched.
  The badge itself is capped at a third of the width.
- **GPIO.** `gpio.py` mirrors `sensors.py`: one `GpioBackend` interface,
  a sim backend that records everything, and real backends imported
  lazily so a laptop never needs gpiozero. `features/hardware.py` holds
  the single shared `Hardware` service with pin names and a
  `buzzer_lock`, so an alert beep never talks over a song.
- **Music** is the only feature with its own thread: `MusicPlayer`
  plays notes via `gpio.tone()` in a daemon thread so the prompt stays
  live. It exposes `level()` and `pitch()` for the visualizer and reports
  "finished" through `ui_tick`. In headless mode `music play` blocks
  until the song ends.

## PULSE in the dungeon

`dungeon.PulseAlly` wraps `ctx.companion.request`. At the start of a run
it sends `pet.combat.stats`; after each of your turns `Game.end_round()`
gives PULSE a turn if a monster is within two steps. With
`pulse_moves = ask` the game pauses (`Game.awaiting`) until you pick a
move; with `auto` it sends `pet.combat.action` without a move and the pet
chooses. The pet decides hits and damage; the deck only applies the
damage to the monster. Monsters next to you go for PULSE with
`pulse_target_chance` (`pet.combat.hit`), repair kits go to whoever is
more hurt (`pet.combat.hit {heal}`), and `Game.finish()` sends
`pet.combat.end` once. Any link error or unknown-message reply turns the
ally off for the rest of the run, so the game always carries on solo.
Combat requests happen on the UI thread; on the sim they are instant and
over TCP/UART each is one short round trip (at most the link timeout).

## The fun layer

All four full-screen modes (boot, hack, typehack, dungeon) are
`Activity` subclasses. Each keeps its *rules* separate from drawing:

- `BootActivity` and `MainframeActivity` render purely from elapsed
  time and a seeded `random.Random`, so a test can render "second 2.0"
  and read the text.
- `TypingRound` holds one phrase's keystrokes; `summarize()` turns
  rounds into WPM / accuracy / score; `HighScores` is a small JSON file.
- `dungeon.Game` is the whole roguelike (map generation, movement,
  bump-to-fight, monster steps, loot rolls) with no drawing code.
  `DungeonActivity` only turns `Game.tile()` into coloured rows.

Startup order in the TUI: `app.py` builds the boot activity (unless
`--no-boot` or `[boot] enabled = no`) and passes it to `run_tui`, which
runs it before the prompt appears. The boot jingle plays through the
same `MusicPlayer` as `music play`.

The ticker line is assembled each frame in `tui._ticker_text()`:
a fresh notice (pet alert, song finished…) wins; otherwise the
`Moods` service's line (after the first few seconds, which show key
hints). The right side is `Visualizer.render()`; while it is enabled
the TUI redraws every `frame_ms` instead of every 250 ms.

**Gifts to PULSE.** `dungeon.gift()` sends a `pet.gift` request with
the item, checks for an `error` reply, then calls `run_pet_hooks()` so
the pet log records it. The sim companion's `PetState.gift()` applies
the effect by item kind (see docs/PROTOCOL.md). Failed deliveries put
the item back in the loot bag.

## What each module does

### `app.py`

Startup only. Parses `--mode`, `--headless`, `--script`, `--classic`,
the Rich options (`--theme`, `--tab`, `--snapshot`…) and friends, opens
hardware, loads config and commands, then hands off to the Rich
interface (`cyberdeck_dash/unified.py`), the curses TUI (`--classic`),
or the headless runner. Environment variables `CYBERDECK_MODE` and
`CYBERDECK_LINK` and `CYBERDECK_SERIAL` are also read here. `--link`,
`--host`, `--port`, `--device`/`--serial` and `--baud` override
`[link]` in hardware.ini.

### `cyberdeck_dash/` (the Rich interface)

See "One interface, two front ends" above and
[DASHBOARD.md](DASHBOARD.md) for every file. The awareness side
(scanners, rules, journal, field mode) is described there too.

### `tui.py`

The classic visible deck (`--classic`). A status bar, an output log, and a prompt, drawn
with curses. It knows almost nothing about pets or sensors except
how to display them. Colors and labels come from `config.py`.

### `headless.py`

The same commands, without curses. Used by tests and by
`python -m cyberdeck --headless --script 'ping'`.

### `commands.py`

The registry and the built-in commands: `help`, `status`, `clear`,
`quit`, `sys`, `pet`, `ping`. Each command is a small function that
returns text lines. Adding a built-in means adding one of those
functions. Adding a *user* command should be done in `plugins/`
instead.

### `plugins.py` and `plugins/`

At startup, every `plugins/*.py` file that does not start with `_`
is imported and registered. The contract is just `NAME` plus
`run(ctx, args)`. `_template.py` is skipped on purpose so it can
be copied, not executed.

### `config.py`, `config/theme.ini`, `config/layout.ini`

INI files for colors and for titles, prompt text, and minimum
terminal size. Missing keys fall back to defaults inside `config.py`.

### `protocol.py`

Packs and unpacks messages. This is the only place that should
build or parse the JSON on the wire.

### `sensors.py`

`SystemSensors.read()` returns a snapshot (temp, battery, wifi…).
`SimSensors` wanders realistically. `PiSensors` is a stub that
already tries a few common Linux files and returns `None` for
what is not there yet.

### `companion.py`

`CompanionLink.request()` is how commands talk to the pet.
`SimCompanionLink` plus `PetState` is the fake companion (needs
change over time; feed and play change them immediately).
The sim pet also pushes `pet.event`s (when hello asks), levels up,
learns moves, and draws an avatar. The real links are in `link.py`
(see "The companion link" above), and `link.open_link()` picks one.

## Hardware modes

`--mode` picks the sensors and `--link` picks the companion. They are
independent:

| Flag | Choices | Default |
|------|---------|---------|
| `--mode` (or `CYBERDECK_MODE`) | `sim` = `SimSensors`, `real` = `PiSensors` | `sim` |
| `--link` (or `CYBERDECK_LINK`, `[link] mode`) | `sim`, `tcp`, `serial`, `auto` (tcp if the companion is listening at start-up, else sim) | `auto` |

On two Pis: `python3 -m cyberdeck --mode real --link serial`.

## Tests

`tests/` uses the standard library `unittest` runner. They cover
the protocol, the command registry, and the simulated companion —
the parts that must stay stable as the UI and hardware change.
