# Customizing the deck

This project is meant to be edited. You can change how it looks and
add your own commands without touching the core Python unless you
want to. Restart the deck after you change a file.

## Where things live

| What you want to change          | Where to edit |
|----------------------------------|---------------|
| Colors                           | `config/theme.ini` |
| Title, prompt, clock, min size   | `config/layout.ini` |
| Add a command of your own        | `plugins/` (copy `_template.py`) |
| Built-in commands (help, pet…)   | `cyberdeck/commands.py` |
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

`run` should return a list of strings (the lines to print). The
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

## What you usually should not need to change

- `tui.py` — only if you want a different screen layout (extra
  panes, a second status line, and so on). Prefer `layout.ini`
  first.
- `app.py` — only if you are adding a new command-line flag.
- `protocol.py` — only if the companion board needs a new message
  type. Update `docs/PROTOCOL.md` in the same change.
