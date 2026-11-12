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
| Pet alert thresholds, alert sound| `config/pet.ini` |
| GPIO pin names, buzzer type      | `config/hardware.ini` |
| Companion link (sim / tcp / serial) | `config/hardware.ini` `[link]` (see docs/TESTING_TWO_BOARDS.md) |
| Songs for the buzzer             | `songs/*.txt` |
| Boot animation, visualizer, games| `config/fx.ini` |
| The deck's sassy status lines    | `config/moods.ini` |
| Typing-challenge phrases         | `text/hack_phrases.txt` |
| Your location, units, ISS options| `config/world.ini` |
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

## PULSE alerts and the pet log

While the full-screen deck is open it checks on PULSE every
`poll_seconds`. When PULSE is hungry, bored, or tired the ticker line
flashes a message, a `! HUNGRY` badge appears on the status bar, and the
buzzer chirps (for hunger). On a narrow screen the bar makes room for the
badge by shortening, then hiding, the less important readings (memory
first, the companion link last); the priorities live in
`cyberdeck/statusline.py` if you want a different order. `config/pet.ini`:

```ini
[alerts]
enabled = yes
poll_seconds = 10
hungry_at = 75        # hunger at or above
bored_at = 35         # happiness at or below
tired_at = 15         # energy at or below
repeat_minutes = 10   # remind again while it lasts (0 = once)
sound = beep          # none, beep, or a song name such as alert
```

`pet log` shows evolutions, alerts, recoveries, mood changes, feeds,
plays, and gifts (saved in `data/pet_log.txt`). `pet alerts` checks
right now. In sim mode, `pet sim hunger 90`, `pet sim stage champion`,
`pet sim ready yes` (ready to evolve now) or `pet sim discoveries 3`
let you try alerts and evolutions without waiting. To speed up the sim
pet's evolution clock, start the deck with `CYBERDECK_EVOLUTION_SPEED=200`
(`tools/fake_companion.py --evolution-speed 200` does the same; the
real companion has `--evolution-speed` too).

### Rest, sleep, and evolving

- `pet rest` refills some energy right away; `pet sleep` (or
  `pet sleep on`) puts PULSE to sleep until its energy is full — zZ shows
  in the pet pane and on the status bar — and `pet sleep off` wakes it.
- `pet evolve` shows how close PULSE is to its next stage and which forms
  it could pick (locked ones say why). When it is ready the full-screen
  deck opens a picker; `pet evolve <species>` picks directly (Tab
  completes the names).
- `pet avatar` plays PULSE's face animation in a pop-up (headless: the
  frames as text).

### Evolution pop-ups

When PULSE is close to evolving, ready to pick a form, evolving, and
evolved, a box pops up over the deck. Any key closes it — and that key
still goes to the prompt, so it never eats what you type. `config/pet.ini`:

```ini
[popups]
enabled = yes   # no = only the ticker line and the pet log
seconds = 10    # close by itself after this long (0 = wait for a key)
max_queue = 6   # several at once wait in line; older ones beyond this are dropped
```

## GPIO: LEDs, buttons, buzzer

```text
gpio                      backend + named pins and their state
gpio led on|off|toggle    the "led" pin (or: gpio led on 22)
gpio set 23 on            any pin
gpio read [button]        read an input (button pins use the internal pull-up)
gpio beep [hz] [ms]       short beep
gpio sim press button     sim mode: pretend the button is held down
```

Name your pins in `config/hardware.ini` `[pins]` (BCM numbers, e.g.
`fan = 23`) and use the names anywhere a pin is expected. `[gpio]
backend = auto` uses pretend pins in sim mode and, on the Pi, gpiozero
(then `/sys/class/gpio`). A simple wiring sketch for the defaults:

```text
GPIO17 ──[330Ω]──▶|── GND        LED (long leg toward the resistor/GPIO side)
GPIO27 ──[button]── GND          button (internal pull-up, pressed = HIGH reading)
GPIO18 ──(+)buzzer(−)── GND      passive piezo buzzer; set [buzzer] type = active
                                 for a buzzer that beeps on its own
```

## Songs for the buzzer

`music list`, `music play <song>`, `music stop`, `music now`,
`music show <song>` (a little piano roll). Songs are text files in
`songs/`; the file name is the song name:

```text
title: Ode to Joy
tempo: 132              quarter notes per minute
octave: 4               default octave (C4 = middle C)
length: 4               default note length
# comment lines start with #
E4 E4 F4 G4 | G4 F4 E4 D4 | C4 C4 D4 E4 | E4/4. D4/8 D4/2
```

A note is a letter `A`–`G` (or `R` for a rest), an optional `#` or `b`,
an optional octave, an optional `/length` (`1 2 4 8 16 32`), and an
optional `.` for dotted. Bar lines `|` are ignored. Add a `music` panel
to `[panes]` to see what is playing. Rename or add `alert.txt` and set
`sound = alert` in pet.ini to give PULSE its own alert tune.

## Fun stuff

| Command | What it does |
|---------|--------------|
| `hack [target]` | Fake "hack the mainframe" show: scrolling code, stage bars, ACCESS GRANTED. Any key exits. |
| `typehack` | Typing-speed challenge with a trace timer; `typehack scores` for the board. |
| `dungeon` | ASCII roguelike with PULSE at your side. Arrows/WASD move, `>` stairs, `g` gifts loot to PULSE, `i` bag, `q` quit. On PULSE's turn: `1`-`9` pick its move, space = let it choose, `.` = hold back. |
| `loot`, `loot gift <n>`, `pet gift <n>` | Your saved loot; gifts go to PULSE with the `pet.gift` message. |
| `loot add <item>` | Put a loot item in the bag without crawling (for scripts and trying gifts), e.g. `loot add evo chip`. |
| `boot` | Replay the boot animation. |
| `viz [on\|off]` | The animated bars on the ticker line (they dance to `music play`). |
| `mood` | What the deck is feeling right now. |

`config/fx.ini` has a section for each:

- `[boot]` `enabled`, `duration`, `glitch` (0–1), `title` (big logo
  text), `jingle` (a song or `none`). `--no-boot` skips it once.
- `[visualizer]` `enabled`, `width`, `idle` (liveliness), `frame_ms`
  (bigger is lighter on the Pi's CPU).
- `[moods]` `enabled`, `file`, `rotate_seconds`.
- `[mainframe]` `target`, `duration`.
- `[typing]` `phrases` file, `rounds`, `time_limit`, `player` name.
- `[dungeon]` `width`, `height`, `hp`; `pulse` (yes = PULSE joins with
  its real stats from the companion; no link = solo), `pulse_moves`
  (`ask` = you pick its move each round, `auto` = it picks), and
  `pulse_target_chance` (0–1, how often a monster next to you goes for
  PULSE instead). The HUD shows PULSE's HP and energy.

### Give the deck a personality (moods.ini)

`config/moods.ini` is a list of moods, checked top to bottom; the first
one whose `when` is true is shown on the ticker line:

```ini
[battery_low]
when = battery < 20        # battery / temp / cpu / mem / wifi with < <= > >=
role = warn                # color: dim text ok warn alert title
messages =
    running on fumes here. {bat}% left.
    i'd love a charger. no pressure.
```

`when` can also be `charging`, `pet_alert` (PULSE needs you), or
`always` (keep one of these last as the default). Placeholders:
`{bat} {temp} {cpu} {mem} {wifi} {ssid} {host}`. Type `mood reload`
after editing.

### Typing phrases

`text/hack_phrases.txt`: one phrase per line, `#` for comments. Scores
are saved in `data/typing_scores.json`.

## Weather, sun, moon, and the ISS

| Command | What it does |
|---------|--------------|
| `weather` (or `wx`), `weather refresh` | Conditions with an ASCII icon, a forecast, the sun clock, the moon. |
| `sun`, `sun tomorrow` | Sunrise, solar noon, sunset, day length, and a bar showing where the sun is. |
| `moon` | The phase drawn in ASCII, % lit, next full and new moon. |
| `iss`, `iss visible` | Next passes over you; `*` marks ones you can see (sunlit station, dark sky). |
| `iss now`, `iss tle`, `iss refresh` | Where it is now; the orbit data in use; download fresh orbit data. |

Add `sky` to `side` or `tabs` under `[panes]` in `config/layout.ini`
for a small panel with temperature, the sun clock, the moon, and the
next ISS pass. (It is in the default side column.)

Everything is set in `config/world.ini`:

- `[location]` `name`, `latitude`, `longitude` (south and west are
  negative), `timezone` (e.g. `America/Phoenix`, or `local` to use the
  Pi's own clock setting). Phoenix is the default.
- `[network]` `offline = yes` never goes online, and the deck uses only
  what is cached plus its own maths. `background_refresh` keeps data
  fresh while the deck is open. `timeout` is how long to wait for a
  website.
- `[weather]` `units` (`fahrenheit` or `celsius`), `wind_units`
  (`mph` or `kmh`), `refresh_minutes`, and `forecast_days` (1–7).
- `[iss]` `tle_url` takes any satellite: change `CATNR=25544` to
  another catalog number (Hubble is 20580), then `iss refresh`.
  `refresh_hours`, `warn_after_days`, `search_hours`, `min_elevation`
  (10° clears most rooftops; use 20–30 if you have mountains or
  buildings around), `show`, and `alert_minutes` (a ticker notice
  before a visible pass; 0 turns it off).

**Offline behaviour.** Downloads are cached in `data/cache/`. With no
network, the deck shows the last download and says how old it is. Sun
and moon times are always worked out on the deck, so they need no
network. For the ISS, a copy of the orbit data ships in
`text/iss_fallback.tle`, so a brand-new deck can still predict passes.
That copy is only accurate for a week or two, and the deck warns you
when it is too old.

Weather data: [Open-Meteo](https://open-meteo.com) (free, no key).
Orbit data: [Celestrak](https://celestrak.org).

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
names so far: `pet`, `system`, `net`, `todo`, `music`. Roles are the
theme names: `text`, `title`, `dim`, `ok`, `warn`, `alert`, `prompt`.

## What you usually should not need to change

- `tui.py` — only if you want a different screen layout (extra
  panes, a second status line, and so on). Prefer `layout.ini`
  first.
- `app.py` — only if you are adding a new command-line flag.
- `protocol.py` — only if the companion board needs a new message
  type. Update `docs/PROTOCOL.md` in the same change.
