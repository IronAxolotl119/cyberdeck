# Customizing the deck

This project is meant to be edited. You can change how it looks and
add your own commands without touching the core Python unless you
want to. Restart the deck after you change a file.

## Where things live

| What you want to change          | Where to edit |
|----------------------------------|---------------|
| Colors                           | `config/theme.ini` |
| Title, prompt, clock, min size   | `config/layout.ini` `[layout]` |
| Split panes vs tabs, side panels | `config/layout.ini` `[panes]` |
| History size                     | `config/layout.ini` `[history]` |
| Switch off a built-in feature    | `config/layout.ini` `[features]` |
| Hotkeys (F-keys, Ctrl combos)    | `config/keys.ini` `[hotkeys]` |
| Homelab hosts and ports to check | `config/hosts.ini` `[hosts]` |
| SSH shortcuts                    | `config/hosts.ini` `[ssh]` |
| Wifi backend, timeouts, ssh opts | `config/network.ini` |
| Launcher menu entries            | `config/keys.ini` `[launcher]` |
| Add a command of your own        | `plugins/` (copy `_template.py`) |
| Built-in commands (help, pet…)   | `cyberdeck/commands.py` |
| Bigger features (one file each)  | `cyberdeck/features/` |
| Side panels / tabs (what they show) | `cyberdeck/panes.py` (or `register_pane` in a plugin) |
| Fake pet behaviour               | `PetState` in `cyberdeck/companion.py` |
| Status-bar sensor values         | `cyberdeck/sensors.py` |
| Message format between boards    | `cyberdeck/protocol.py` and `docs/PROTOCOL.md` |

A map of how keypresses turn into commands and replies is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Change colors

Open `config/theme.ini`. Each key is a role in the UI; each value is
a curses color name:

`black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`,
or `default` (the terminal's own color).

```ini
[colors]
background = black
frame = cyan
title = cyan
status = white
text = green
prompt = yellow
warn = yellow
alert = red
ok = green
dim = white
```

If a color name is typo'd or the terminal has no color, the UI
falls back to plain text (bold / reverse) and still runs.

## Change the layout

Open `config/layout.ini`:

```ini
[layout]
title = CYBERDECK / MAIN
prompt = >
clock_format = %H:%M:%S
min_cols = 40
min_rows = 8
max_output_lines = 500
use_unicode = auto
```

- `title` is the label in the top frame.
- `prompt` is the text before your cursor. Quote it (`prompt = "> "`) if you want a trailing space — INI files otherwise strip those.
- `clock_format` uses Python's `strftime` codes (`%H:%M`, `%I:%M %p`…).
- `use_unicode` can be `auto`, `yes`, or `no`. `no` draws a plain
  `+ - |` box, which is useful on limited terminals.

You can point at a different folder with `--config-dir /path` or
`CYBERDECK_CONFIG_DIR`.

## Split panes or tabs

`config/layout.ini` `[panes]` decides how the middle of the screen is
used:

```ini
[panes]
mode = split            # split = output left, live panels right; tabs = one view at a time
side = pet, system      # right-hand panels for split mode, top to bottom
side_width = 30
min_split_cols = 72     # narrower terminals fall back to tabs automatically
tabs = output, pet, system
ticker = yes            # second status line (notices, deck mood, visualizer)
hints = yes             # hotkey reminders on the bottom border
pet_refresh = 3         # seconds between pet panel updates
```

While the deck runs: **F6** (or `layout toggle`) flips between split
and tabs, **F4 / Ctrl-T** (or `view next`) moves to the next tab, and
`view pet` jumps to a named tab. Pressing F4 in split mode "zooms" into
tabs mode. More panel names (net, todo, music…) become available as
features are added — `view ` + Tab lists them.

## Hotkeys and the launcher menu

`config/keys.ini` has two sections.

`[hotkeys]` maps a key to something to run:

```ini
[hotkeys]
F1 = help
F2 = :launcher
ctrl+p = pet status
F9 = wifi scan
```

- Keys: `F1`…`F12`, `ctrl+<letter>` (also `^P` or `C-p`), `esc`, `insert`.
- Some Ctrl keys are reserved (Ctrl-C/D quit, Ctrl-A/E start/end,
  Ctrl-L clear, Ctrl-U wipe line, plus a few the terminal itself eats).
  The deck shows a notice if you bind one, and `keys` lists warnings.
- The value is any command line, or a **UI action** that starts with
  a colon: `:launcher`, `:tab next`, `:tab prev`, `:tab pet`,
  `:layout toggle|split|tabs`, `:clear`, `:scroll up|down`, `:quit`.

`[launcher]` is the popup menu (F2, Ctrl-O, or `menu`): one
`label = command` per line, shown in order. Arrow keys move, typing
filters, Enter runs, Esc closes. End a command with `...`
(`Add note = note add ...`) to drop it on the prompt so you can type
the rest. Delete the whole section to list every command instead.

Type `keys` at the prompt to see what is active.

## History and Tab completion

- **Up / Down** walk through earlier commands. They are saved in
  `data/history.txt`, so they survive restarts. Start a command with a
  space to keep it out of the history. `history` lists them,
  `history clear` wipes them, `[history] size` sets how many to keep.
- **Tab** completes command names, then subcommands (`pet f` → `pet feed`),
  then things like song or host names. If several match, they are
  printed so you can see the choices.

Runtime files (history, notes, scores, cache) go in `data/` (ignored by
git). Use `--data-dir /path` or `CYBERDECK_DATA_DIR` to put them
somewhere else, such as a USB stick.

## Notes and to-dos

`note add <text>`, `note list`, `note find <word>`, `note del <n>`;
`todo add <text>`, `todo list`, `todo done <n>`, `todo undo <n>`,
`todo del <n>`, `todo clear` (removes ticked tasks). They are saved as
plain text in `data/notes.txt` and `data/todo.txt`:

```text
2026-11-12 10:40 | buy more jumper wires        <- notes.txt
[ ] solder the buzzer                           <- todo.txt
[x] flash the SD card
```

Edit those files by hand if you like; the deck re-reads them each time.
Add `todo` to `[panes] side` or `tabs` to keep your list on screen.

## Your homelab: hosts, checks, and SSH

`config/hosts.ini` ships with **example** addresses — replace them:

```ini
[hosts]
# name = address   ports to test
nas    = 192.168.1.20   22, 80, 445
router = 192.168.1.1    53, 80

[ssh]
# name = what you would type after "ssh"
nas    = pi@192.168.1.20
router = admin@192.168.1.1 -p 2222
```

- `net hosts` lists them, `net check` pings every host and tests its
  ports (in parallel), `net check nas` does one, `ping nas` or
  `net ping 10.0.0.5` pings one, `net port nas 22 8080` tests ports.
- `ssh nas` pauses the deck screen, runs ssh in the terminal, and
  brings the deck back when you log out. `ssh user@host` works without
  a shortcut. Tab completes host and shortcut names.
- Ping uses the system `ping` when allowed, otherwise times a TCP
  connection instead ("via tcp:22"). A *closed* port still means the
  host is up; *filtered* means nothing answered in time.

`config/network.ini` holds the knobs: `[wifi] backend` (`auto`,
`nmcli`, `iw`, `sim`) and `interface`, `[net] ping_timeout`,
`port_timeout`, `workers`, and `[ssh] binary` / `options`.

## Wifi

`wifi` shows the current network, `wifi scan` lists nearby networks
strongest first, and `wifi meter` opens a full-screen live signal
meter (any key to leave). In `--mode sim` the networks are made up.
On the Pi, `nmcli` (NetworkManager) is used when installed; `iw` scans
usually need root, so the deck falls back to the current link.

## Add your own command

1. Copy `plugins/_template.py` to something like `plugins/mood.py`.
   The new file **must not** start with an underscore, or it will
   be ignored (that is how the template stays a template).
2. Set `NAME`, `SUMMARY`, and `USAGE`, then write `run(ctx, args)`.
3. Restart the deck and type `help`. Your command should be listed.

A working example ships as `plugins/hello.py`. Try:

```text
hello
hello sam
```

`run` should return a list of strings (the lines to print).
Optional extras: `SUBCOMMANDS = ("on", "off")` makes Tab offer those
words, and a `complete(ctx, args)` function can return Tab choices that
change at runtime. The
`ctx` object is the same one built-in commands get, so your command
can read sensors or talk to the pet if it wants to. Notes are in
the template.

If a plugin fails to load, the deck prints a warning and continues
with the rest. Start with `--no-plugins` if you need to bypass them.

Plugins are also looked up in `CYBERDECK_PLUGINS_DIR` when that
environment variable is set.

## Adding a built-in instead

If a command should ship with the deck for everyone, add a small
function inside `build_registry()` in `cyberdeck/commands.py`,
using the `@reg.command(...)` decorator. Follow the existing
commands — they all return a list of lines and stay short on
purpose.

## Add your own side panel or tab

A panel is a function that returns rows of `(text, color role)` pieces.
From a plugin file:

```python
from cyberdeck.panes import register_pane

def clock_pane(ctx, width, height):
    import time
    return [[("time ", "dim"), (time.strftime("%H:%M"), "title")]]

register_pane("clock", "CLOCK", clock_pane)
```

Then add `clock` to `side =` or `tabs =` in `[panes]`. Built-in panel
names so far: `pet`, `system`, `net`, `todo`. Roles are the
theme names: `text`, `title`, `dim`, `ok`, `warn`, `alert`, `prompt`.

## What you usually should not need to change

- `tui.py` — only if you want a different screen layout (extra
  panes, a second status line, and so on). Prefer `layout.ini`
  first.
- `app.py` — only if you are adding a new command-line flag.
- `protocol.py` — only if the companion board needs a new message
  type. Update `docs/PROTOCOL.md` in the same change.
