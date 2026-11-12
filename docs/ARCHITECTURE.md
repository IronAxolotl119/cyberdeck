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

## Folder map

```text
cyberdeck-main/
  config/               INI files you can edit (colors, layout, keys, …)
  plugins/              your extra commands; auto-loaded at startup
  data/                 files the deck writes (history, notes…); not in git
  cyberdeck/            the Python package (`python -m cyberdeck`)
    app.py              flags, startup, choose UI or headless
    tui.py              full-screen curses interface (the only drawing code)
    headless.py         run commands as plain text (for scripts/tests)
    commands.py         command registry, Context, core commands
    features/           one module per bigger feature, each with register()
      core_ux.py        history / menu / view / layout / keys commands
      notes.py          note + todo commands, plain-text stores, todo panel
      wifi.py           wifi status / scan / live meter (nmcli, iw, sim)
      netcheck.py       net hosts / ping / port / check (TCP + system ping)
      netinfo.py        net info screen, rx/tx RateMeter, net panel
      ssh.py            ssh shortcuts (runs ssh with curses suspended)
    activity.py         base class for full-screen modes + the launcher menu
    panes.py            side panels, tabs, and the screen-layout maths
    history.py          Up/Down history saved to data/history.txt
    completion.py       Tab completion
    keys.py             key names, keys.ini hotkeys and launcher entries
    plugins.py          loads files from plugins/
    config.py           reads the INI files (theme, layout, Settings helper)
    protocol.py         encode/decode newline-delimited JSON messages
    sensors.py          CPU temp / battery / wifi (sim + Pi stub)
    companion.py        pet link (sim + UART stub)
    __main__.py         hook for `python -m cyberdeck`
  docs/                 protocol, architecture, UI snapshot
  tests/                unit tests (stdlib unittest)
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
   `Message` with the *same id*. Later, `SerialCompanionLink` will
   write that line to UART and wait for the matching reply.

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
  companion.py  (SimCompanionLink or SerialCompanionLink)
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
  Each check still blocks the prompt until it finishes (worst case
  about one `port_timeout`), which keeps the UI loop simple.
- Traffic rates come from two `/proc/net/dev` samples (`RateMeter`).
- `ssh` sets `ctx.external`; `tui.py` calls `curses.endwin()`, runs
  ssh in the real terminal, then redraws. A failed ssh waits for Enter
  so the error stays readable.

## What each module does

### `app.py`

Startup only. Parses `--mode`, `--headless`, `--script`, and friends,
opens hardware, loads config and commands, then hands off to the TUI
or the headless runner. Environment variables `CYBERDECK_MODE` and
`CYBERDECK_SERIAL` are also read here (and in `open_hardware`).

### `tui.py`

The visible deck. A status bar, an output log, and a prompt, drawn
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
`SerialCompanionLink` is a stub that stays unlinked and explains
itself if you call it. `open_hardware(mode)` picks the pair.

## Hardware modes

| Mode   | How to choose              | Sensors        | Companion              |
|--------|----------------------------|----------------|------------------------|
| `sim`  | default, or `--mode sim`   | `SimSensors`   | `SimCompanionLink`     |
| `real` | `--mode real`              | `PiSensors`    | `SerialCompanionLink`  |

`CYBERDECK_MODE=real` has the same effect as `--mode real`.
Real mode is selectable now so the UI can show a down link; the
UART backend is not implemented yet.

## Tests

`tests/` uses the standard library `unittest` runner. They cover
the protocol, the command registry, and the simulated companion —
the parts that must stay stable as the UI and hardware change.
