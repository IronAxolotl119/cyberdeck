# CYBERDECK//DASH: the Rich dashboard

A full-screen, cyberdeck-styled terminal dashboard for the **main** Pi
Zero 2 W. It is a separate program from the classic deck
(`python3 -m cyberdeck`) and lives in `cyberdeck_dash/`.

It is **simulation-first**: on any Linux/macOS computer every radio is
simulated with realistic, changing data (including the occasional
anomaly). On the Pi, `--mode real` switches each scanner to its real
backend, with hooks for the built-in Wi-Fi/Bluetooth radios, the
MFRC522 RFID reader and GPIO buttons.

> **Awareness tools only.** Everything here listens passively to your
> own surroundings and your own subnet, for personal, educational use.
> Nothing joins, probes, deauths or attacks anyone's network or device.
> A flag ("evil twin", "unknown tag") is a hint to look closer, not
> proof. These are **not skimmer detectors**.

![dashboard](dashboard.png)

Plain-text snapshot (100x30, captured from a live tmux run): [dashboard_snapshot.txt](dashboard_snapshot.txt)

## Run it

```bash
pip install -r requirements-dash.txt        # just Rich
python3 -m cyberdeck_dash                   # sim mode, any computer
python3 -m cyberdeck_dash --theme amber     # neon | amber | matrix (t cycles live)
python3 -m cyberdeck_dash --no-companion    # local pet only
python3 -m cyberdeck_dash --mode real       # on the Pi: real backends from config/dash.ini
python3 -m cyberdeck_dash --snapshot docs/dashboard --seed 7   # headless: write .txt + .svg
```

With the PULSE companion (same computer, two terminals):

```bash
cd companion && python3 companion.py --tcp 127.0.0.1:7777   # window 1
python3 -m cyberdeck_dash                                   # window 2 (LINK ◆ lights up)
```

Tests: `python3 -m unittest discover -s tests/dash -t .`
(the full repo suite `python3 -m unittest discover -s tests` includes them).

## Screen layout

```
header   ▓▒░ CYBERDECK//DASH ░▒▓   SIM  FIELD ○  LINK ◆  ⚠ 3        12:05:09
tabs     ◢ AWARENESS & DIAGNOSTICS ◣   Deck & PULSE
body     grid of panel tiles (focused one glows), or one panel full-screen
footer   ⟫ latest alert / log line
         key hints
```

Panels are grouped into **tabs** (set in `[tabs]` / `[tab.*]` of
`config/dash.ini`):

| Tab | Panels |
|-----|--------|
| **Awareness & Diagnostics** | Wi-Fi, Bluetooth, LAN map, RFID/NFC, Journal, Diagnostics, Checklist |
| **Deck & PULSE** | Field mode, PULSE, System |

## Keys (and buttons)

| Key | Does |
|-----|------|
| `[` `]` (or `g`) | switch tab |
| `1`-`9` | focus a panel in the current tab |
| `←` `→` `tab` / `↑` `↓` | move focus |
| `enter` / `z` | full-screen view of the focused panel, `esc` back |
| `m` | main menu (grouped by tab, with ping / DNS items) |
| `s` | scan now (focused scanner, or all) |
| `u` / `v` | PULSE uses the selected move (runs a fresh scan) / next move |
| `f` | field mode on/off |
| `p` / `d` | ping a host / DNS lookup (Diagnostics) |
| `r` / `n` | sim: "tap" an RFID tag / add a note to the last tag |
| `space` / `x` | tick a checklist item / reset the list |
| `t` | next theme |
| `?` | key help |
| `q` | quit |

## Panel guide

**Awareness & Diagnostics**

- **Wi-Fi** (`rules.WifiTracker`): every network by BSSID with signal,
  security and a sparkline of its RSSI history. Logs new networks and
  names, open networks, sudden signal jumps (`rssi_jump_db`), and
  networks that disappear or come back. Highlights:
  `TWIN` your own SSID (from `[wifi] trusted`) coming from an unlisted
  radio, or an open copy of an encrypted network's name;
  `BAIT` names matching `suspicious_patterns` ("free wifi", "guest", ...);
  `OPEN` no encryption.
- **Bluetooth** (`rules.BtTracker`): unique MACs, names, RSSI and how
  long each has been around. A **new** device that stays at least
  `dwell_minutes` raises one HIGH alert. Your own devices go in `known`.
  Many phones rotate their MAC, so treat it as a hint.
- **LAN map** (`rules.LanTracker`): hosts on your own subnet from the
  ARP table (`/proc/net/arp`), plus an optional, rate-limited ping
  sweep (`ping_sweep = yes`). The sweep refuses anything but a private
  /24 or smaller. New hosts and an IP whose MAC changed are flagged.
- **RFID/NFC** (`rules.RfidTracker`): last UID read, tags currently on
  the reader, the inventory of known UIDs with notes (`data/tags.json`),
  an **unknown-tag alert**, and a presence log (how long each tag was
  held and how many reads). `n` attaches a note to the last tag.
- **Journal** (`journal.Journal`): every finding, timestamped, appended
  to `data/discoveries.jsonl`, one JSON object per line:
  `{"ts","epoch","source":"dash","kind","event","id","label","interest","detail"}`.
  The main deck or the companion can read the same file later.
- **Diagnostics** (`diag.Diagnostics`): interface traffic (rx/tx rates
  and totals from `/proc/net/dev`) with IP addresses, plus `p` ping a
  host (`ping -c 3`) and `d` DNS lookup (`socket.getaddrinfo`). Both
  run on a background thread. Simulated in sim mode (`[diag] backend`).
- **Checklist**: reminders from `[checklist] items`.

**Deck & PULSE**

- **Field mode** (`field.AlertFilter`): `f` (or the field button)
  speeds scans up to `[field] scan_interval` and only alerts on HIGH
  findings. Every alert passes a per-item cooldown and a token-bucket
  rate limit (`max_alerts_per_minute`, `burst`). Manual scans have
  their own cooldown. Everything still goes to the journal.
- **PULSE**: name, species, level, XP, energy, environment mood and
  buff (and, when linked, the companion's `busy` 0–1 meter), the
  selected move (with rank / bonus when `pet.combat.stats` has them),
  recently learned or strengthened moves, the last discovery reaction,
  and alerts pushed by the companion (`pet.event`, including
  `move_strengthened`). `LINK` = live companion, `LOCAL` =
  built-in stand-in.
- **System**: CPU, memory, temperature (`~` = simulated off-Pi), load,
  uptime, disk, and each scanner's backend and schedule.

## How it fits together

```
ScanWorker threads ─► ScanResult ─► rules.*Tracker ─► Finding
                                                        │ EventBus "finding"
               ┌───────────────┬──────────────┬─────────┴──────────┐
            Journal      PetAdapter (XP)   activity → mood   AlertFilter
           (JSONL)       TCP or local pet                        │ "alert"
                                                         ticker · alert LED
```

| File | Role |
|------|------|
| `cyberdeck_dash/__main__.py` | command line, Rich Live loop, `--snapshot` |
| `app.py` | the Dashboard: state, scheduling, keys, bus wiring |
| `events.py` | `Finding`, interest levels, `EventBus` |
| `rules.py` | Wi-Fi / BT / LAN / RFID trackers, XP + move rules, mood |
| `scanners/` | `base.py` interface + Sim/Real backends per radio |
| `pet.py` | companion TCP client, local pet, `pet.discovery` (see companion/docs/DISCOVERY.md) |
| `field.py`, `ratelimit.py` | alert filter, cooldown, token bucket |
| `journal.py` | shared discovery journal |
| `diag.py` | ping / DNS / interface stats |
| `sysinfo.py` | CPU / memory / temperature from /proc and /sys |
| `inputs.py` | non-blocking keyboard, gpiozero buttons, alert LED |
| `ui/theme.py`, `ui/panels.py`, `ui/screen.py` | look, panels, layout |

## PULSE tie-ins (Digimon side)

When the companion is linked, **it** decides XP and moves. The
dashboard sends `pet.discovery` for each first sighting and treats
`pet.discovery.ok` as the source of truth (XP, reaction, learned or
strengthened move, level-up). The full message is specified in
[companion/docs/DISCOVERY.md](../companion/docs/DISCOVERY.md). The
`[xp]` rules below are used only when the companion is unlinked, or
when it answers `unknown_type`.

- **First sightings earn XP**: `[xp]` sets the local-fallback base per
  kind (`wifi`, `bt`, `lan`, `rfid`) plus `interest_bonus` per level
  above low. The companion's table is the same (wifi 4/7/10, bt and
  lan 3/6/9, rfid 8/11/14). Repeats of the same kind+id come back with
  XP 0 and an "already logged" reaction. A sleeping PULSE still records
  the find, at half XP and with no move changes.
- **Moves**: each first sighting has `learn_chance × interest` to teach
  PULSE a move from that scanner's pool (`rules.MOVE_POOL`), or to
  strengthen one it already knows (rank capped at 5, +2 power per rank).
- **Use move** (`u`, or the use-move button) runs a fresh scan of the
  scanner the move belongs to (`Signal Sniff` → Wi-Fi, `Tag Bite` →
  RFID, companion moves are matched by keyword, otherwise all).
- **Mood/buffs**: while linked, the PULSE panel shows the companion's
  `environment` (`busy` 0–1 and `buff`: calm, curious, busy airwaves,
  or bored). Offline, the dashboard's own activity window is used:
  calm (energy regen), curious, buzzing (XP ×1.25), alert (2+
  high-interest finds).

## Hardware hooks (real mode)

Set `mode = real` in `[dash]` or pass `--mode real`. Each section's
`backend` picks the real implementation; missing tools or libraries
show an error in that panel instead of crashing.

| Section | Real backends | Notes |
|---------|---------------|-------|
| `[wifi]` | `nmcli` (normal user), `iw` (needs root) | listen-only scan list |
| `[bluetooth]` | `bluetoothctl`, `bleak` (`pip install bleak`) | passive adverts |
| `[lan]` | `proc` (+ `ping_sweep = yes`) | own private subnet only |
| `[rfid]` | `mfrc522` (`pip install mfrc522 spidev`) | enable SPI in raspi-config |
| `[diag]` | `auto` / `real` / `sim` | ping, getaddrinfo, /proc/net/dev |
| `[buttons]` | `gpiozero` | BCM pins, pull-up, press = key |

MFRC522 wiring (SPI0): SDA→GPIO8 (CE0, pin 24), SCK→GPIO11 (pin 23),
MOSI→GPIO10 (pin 19), MISO→GPIO9 (pin 21), RST→GPIO25 (pin 22),
GND→GND, 3.3V→3.3V (**not 5 V**).

Buttons (`[buttons]`, wire each between the pin and GND): `scan`=17,
`use_move`=27, `field`=22, `next_panel`=23, optional joystick
`joy_up/down/left/right/press`, optional `alert_led`. Each press acts
exactly like its key (`s`, `u`, `f`, `tab`, arrows, `enter`), so
everything is testable on a keyboard first.

## Customizing

Everything is in `config/dash.ini`: themes (`[theme.NAME]`, any
`#rrggbb`), tabs and which panels they hold, scan intervals, thresholds
(`rssi_jump_db`, `dwell_minutes`, cooldowns, rate limits), suspicious
name patterns, your trusted networks and known devices, checklist items,
XP numbers, the companion address and the button pins.

## Add a panel

1. In `cyberdeck_dash/ui/panels.py` write a function that reads the
   Dashboard and returns a Rich renderable:
   ```python
   def render_uptime(dash, big: bool):
       return Text(f"up {dash.sysinfo.get('uptime', 0) / 3600:.1f} h", style=dash.theme.accent)
   ```
2. Register it: `PANELS["uptime"] = ("UPTIME", "⏱", render_uptime, None)`.
3. Add `uptime` to a tab's `panels =` list in `config/dash.ini`.

## Add a scanner

1. In `cyberdeck_dash/scanners/` subclass `Scanner`, set `name`, and
   return a `ScanResult` of records from `scan()` (a `Sim` class and a
   real class, importing optional libraries inside the method).
2. Pick it in `scanners/__init__.py:build_scanners()` from a config key.
3. Add a tracker in `rules.py` that turns records into `Finding`s, and
   route it in `Dashboard.handle_result()`. The journal, XP, mood and
   alerts then work automatically through the event bus.

## Protocol: `pet.discovery`

The dashboard reuses the existing newline-JSON protocol
(`docs/PROTOCOL.md`, `cyberdeck/protocol.py`) over TCP
`127.0.0.1:7777`: `hello` with `"events": true` and `"client":
"dash"` (the companion ignores `client` and can serve the deck and the
dashboard at the same time), `pet.status` polling every `[companion]
poll` seconds, and `pet.event` pushes shown as alerts (including the
new `move_strengthened` `{move, rank, bonus}` event).

`pet.discovery` is **implemented** on both sides. The authoritative
spec is [companion/docs/DISCOVERY.md](../companion/docs/DISCOVERY.md).
A short copy of the shape:

### `pet.discovery` → `pet.discovery.ok`

Sent once per **first sighting** (new network, device, host or tag).

```json
{"type":"pet.discovery","id":"…","payload":{"kind":"rfid","id":"de:ad:10:ca","interest":3,"event":"unknown_tag","label":"unknown tag"}}
{"type":"pet.discovery.ok","id":"…","payload":{"xp":14,"level":4,"level_up":false,"learned_move":"Tag Bite","strengthened_move":null,"reaction":"PULSE learned Tag Bite from unknown tag!","pet":{…pet object, including environment…}}}
```

| Request field | Meaning |
|---------------|---------|
| `kind` | `wifi`, `bt`, `lan`, `rfid` |
| `id` | BSSID, MAC, IP or tag UID |
| `interest` | 1 low, 2 medium, 3 high (clamped; default 1) |
| `event`, `label` | optional: which rule fired, a display name |

| Reply field | Meaning |
|-------------|---------|
| `xp` | XP awarded (0 on a repeat) |
| `learned_move` / `strengthened_move` | move name or `null` |
| `reaction` | one line shown on the ticker and in the journal |
| `level`, `level_up`, `pet` | refresh the PULSE panel (`pet` includes `environment`) |

The companion decides XP and moves itself. Repeats of the same
kind+id are not an error: the reply is still `pet.discovery.ok` with
`xp: 0` and an "already logged" reaction. An `error` with
`unknown_type` makes the dashboard stop sending it and fall back to
its local XP rules, so older companions keep working and `proto`
stays `1`. Other errors (timeout, `bad_payload`) do not invent local
XP.
