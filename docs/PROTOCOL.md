# Dual-board message protocol

This is the language the **main cyberdeck** and the **companion pet board**
speak to each other. Both sides should implement this document the same
way. The Python reference is `cyberdeck/protocol.py`.

The deck can talk to a simulated companion in the same process
(`--link sim`, the default), to a companion program over TCP
(`--link tcp`, same computer or network), or over UART between two Pis
(`--link serial`). The messages are identical on all three.
`tools/fake_companion.py` is a reference server; see
[TESTING_TWO_BOARDS.md](TESTING_TWO_BOARDS.md).

## Transport

- **Framing:** one UTF-8 JSON object per line, ended by `\n`.
  A trailing `\r` is ignored. Empty lines are ignored.
- **TCP:** the companion is the server, default `127.0.0.1:7777`.
  The deck is the client and reconnects on its own (0.5 s, 1 s, 2 s …
  up to `retry_max`) if the companion restarts.
- **Serial:** 115200 baud, 8 data bits, no parity, 1 stop bit (8N1),
  no flow control, raw mode. Device `/dev/serial0` (the Pi's UART
  alias). Override it with `--serial`/`--device`, `CYBERDECK_SERIAL`, or
  `device` under `[link]` in `config/hardware.ini`.
- **Direction:** the main board sends *requests*. The companion
  answers with a *reply* that uses the same `id`. The only exception is
  the optional `pet.event` push (below), which is never answered.
- **Liveness:** while linked, the deck sends `pet.status` every
  `poll_seconds` (3 s). If no reply arrives, the deck treats the link as
  down and reconnects (over serial, it sends `hello` again).

Incoming bytes should be appended to a buffer and split on newlines
(`decode_buffer()` in the Python reference). Keep any leftover
partial line until more data arrives.

## Message shape

Every message is a JSON object with exactly these keys:

| Field     | Type   | Meaning |
|-----------|--------|---------|
| `type`    | string | What this message is (see the table below). |
| `id`      | string | Correlation id. A reply must copy the request's id. |
| `payload` | object | Extra fields. Use `{}` when there is nothing to say. |

Example, compact form (this is what the encoder actually writes):

```json
{"type":"ping","id":"3b1c0e9a7f6d4c2b8a1e0d9c8b7a6f5e","payload":{}}
```

Rules:

- `type` and `id` must be non-empty strings.
- `payload` must be a JSON object, never a list or a bare value.
- Unknown extra top-level keys should be ignored (forward compatible).
- Unknown `type` values should be answered with an `error` reply,
  not a disconnect.

Ids are 32-character hex UUIDs in the Python reference, but any
non-empty string is legal. The only requirement is uniqueness
enough to match a reply to its request.

## Handshake

On connect, the main board sends `hello`. The companion replies
with `hello.ok`. `proto` is the protocol version (currently `1`).
If the versions cannot work together, the companion should reply
with `error` and code `proto_mismatch`.

```json
{"type":"hello","id":"...","payload":{"role":"main","proto":1,"events":true}}
{"type":"hello.ok","id":"...","payload":{"role":"companion","proto":1,"source":"sim","pet":"PULSE","events":true}}
```

`events` is optional. The deck sends `"events": true` to ask for
`pet.event` pushes. A companion that supports them echoes
`"events": true` in `hello.ok` and pushes events only on that
connection. A companion that leaves it out never pushes. The protocol
stays at `proto` 1.

## Message types

| Request       | Reply           | Payload (request)       | Payload (reply) |
|---------------|-----------------|-------------------------|-----------------|
| `hello`       | `hello.ok`      | `role`, `proto`         | `role`, `proto`, optional `source`, `pet` |
| `ping`        | `pong`          | `{}`                    | `ok` (true), optional `source` |
| `pet.status`  | `pet.status.ok` | `{}`                    | `pet` object (see below) |
| `pet.feed`    | `pet.feed.ok`   | `item` (string)         | `item`, `hunger_delta`, `pet` |
| `pet.play`    | `pet.play.ok`   | `game` (string)         | `game`, `played` (bool), optional `reason`, `pet` |
| `pet.gift`    | `pet.gift.ok`   | `item` (object, below)  | `accepted`, `item`, `reaction`, `effects`, `pet` |
| `pet.avatar`  | `pet.avatar.ok` | optional `frame`, `mood`| picture, see below (optional message) |
| *(push)*      | —               |                         | `pet.event`, see below (optional) |
|               | `error`         |                         | `code`, `message` |

The deck always fills in `pet.feed` `item` (default `"ration"`) and
`pet.play` `game` (default `"spar"`). A companion may answer an empty
one with `error` / `bad_payload`.

### Pet object

```json
{
  "name": "PULSE",
  "species": "Chirplet",
  "stage": "baby",
  "hunger": 38,
  "happiness": 72,
  "energy": 80,
  "mood": "playful",
  "age_hours": 11.5,
  "gifts": 3,

  "level": 3, "xp": 95, "hp": 28, "max_hp": 28,
  "attack": 7, "defense": 6, "speed": 8,
  "moves": ["Chirp", "Static Snap"],
  "care_mistakes": 0,
  "avatar": {"form": "chirplet", "mood": "playful", "frame": 0}
}
```

The deck shows whatever `species` (or `form`) the companion sends. Do
not expect any fixed name.

**Stages**, lowest to highest: `egg`, `baby`, `in-training`, `rookie`,
`champion`, `ultimate`, `mega`. A companion may use only some of them.

**Optional fields** (the deck shows them only when present): `level`,
`xp`, `hp`, `max_hp`, `attack`, `defense`, `speed`, `moves` (a list
of names), `care_mistakes`, and `avatar` `{form, mood, frame}`. That
last one says which picture `pet.avatar` would return, so the deck
re-fetches the picture only when it changes. Unknown extra fields are
ignored.

**Needs are need levels:** `hunger` 0 = full, 100 = starving (`pet.feed`
lowers it). If your pet stores *fullness*, send `100 - fullness`.
`happiness` is a 0–100 mood score (100 = delighted). `energy` 100 =
rested. A companion that sends `mood` as a number instead of a word is
also accepted: the deck then treats it as `happiness` and picks a word
itself.

`gifts` (added with `pet.gift`) is how many gifts the pet has kept.
Older readers can ignore it.

Need values (`hunger`, `happiness`, `energy`) are integers 0–100.
`mood` is a short word derived from those needs (`content`,
`playful`, `joyful`, `hungry`, `tired`, `grumpy`, …).

If the pet is too tired to play, `played` is `false` and `reason`
explains why. That is still a successful `pet.play.ok`, not an
`error`.

### Gifts (`pet.gift`)

The main deck sends loot found in the dungeon game to the pet. The
request payload has one `item` object:

| Field    | Type    | Meaning |
|----------|---------|---------|
| `name`   | string  | Required. What the item is called, e.g. `"glitch berry"`. |
| `kind`   | string  | `food`, `toy`, `battery`, `evolve`, or `trinket`. Unknown kinds are treated as `trinket`. |
| `power`  | integer | 0–100, how strong the effect is. Default 10. |
| `rarity` | string  | Optional: `common`, `rare`, `legendary`. Cosmetic. |

What the reference (sim) companion does with each kind:

| kind      | effect |
|-----------|--------|
| `food`    | hunger − power, happiness + 5 |
| `toy`     | happiness + power, energy − 5 |
| `battery` | energy + power |
| `evolve`  | moves one stage up: egg → baby → in-training → rookie → champion → ultimate → mega |
| `trinket` | happiness + 10 (kept as a keepsake) |

The reply says whether the gift was `accepted`, echoes the cleaned-up
`item`, gives a one-line `reaction` to show on screen, and lists
`effects` as deltas (`hunger`, `happiness`, `energy`) plus `stage`
(the new stage, or `null` if it did not change). A real companion may
react differently (refuse food when full, animate, etc.) as long as the
reply has this shape.

```json
{"type":"pet.gift","id":"c3d4...","payload":{"item":{"name":"glitch berry","kind":"food","power":20,"rarity":"common"}}}
{"type":"pet.gift.ok","id":"c3d4...","payload":{"accepted":true,"item":{"name":"glitch berry","kind":"food","power":20},"reaction":"PULSE munches the glitch berry. nom.","effects":{"hunger":-20,"happiness":5,"energy":0,"stage":null},"pet":{...}}}
```

**Refusing a gift.** If the pet is already at its last form, the
companion may answer an `evolve` item with `"accepted": false` and a
`reaction` (still a `pet.gift.ok`, not an error). The same applies to
any other gift it does not want. The deck then shows the reaction and
keeps the item in the loot bag.

```json
{"type":"pet.gift.ok","id":"…","payload":{"accepted":false,"item":{"name":"evo chip","kind":"evolve","power":50},"reaction":"PULSE is already mega — it hands the evo chip back.","effects":{"hunger":0,"happiness":0,"energy":0,"stage":null},"pet":{...}}}
```

A request without `item.name` gets an `error` reply with code
`bad_item`. Like `pet.feed`, gifts change state, so they must not be
retried automatically.

### Avatar (`pet.avatar`, optional)

The pet's picture, for the deck's pet panel. The request payload is
`{}`, or it may name a `frame` (0 or 1, the idle animation frame) and/or
a `mood` word to draw.

```json
{"type":"pet.avatar","id":"…","payload":{"frame":0}}
{"type":"pet.avatar.ok","id":"…","payload":{"form":"chirplet","mood":"playful","frame":0,"width":16,"height":16,
  "palette":[null,"#121018","#ffd440","#ffffff"],
  "rows":[[0,0,0,1,1,1,0,…16 numbers…],…16 rows…]}}
```

- `palette[0]` is `null`, meaning transparent. Every other entry is
  `"#rrggbb"`.
- `rows` is 16 lists of 16 palette indexes, top to bottom.
- The deck draws the picture with half-block characters in the nearest
  terminal colours, 8 text rows tall. It does this only when the panel has
  room, the terminal has colour, and Unicode is on. Otherwise it shows
  the ASCII face.
- A companion without this message replies `error` / `unknown_type`.
  The deck then stops asking.

### Events (`pet.event`, optional push)

Sent by the companion **only** after a `hello` with `"events": true`
that it echoed. It has a fresh `id`, and the deck never replies. The deck
shows each one on the ticker line, writes it to the pet log, and uses
`pet` to refresh the pet panel.

```json
{"type":"pet.event","id":"<new id>","payload":{"event":"hungry","detail":{},"pet":{...pet object...}}}
```

| `event`        | `detail` the deck reads (all optional) | shown as |
|----------------|----------------------------------------|----------|
| `hungry`       | —                                      | alert: "PULSE is hungry!" |
| `tired`        | —                                      | warning |
| `sad`          | —                                      | warning |
| `evolved`      | `to` or `form` (new species), `stage`, `from` | "PULSE evolved into Wavling!" |
| `level_up`     | `level`                                | "PULSE reached level 4!" |
| `learned_move` | `move` (or `name`)                     | "PULSE learned Static Snap!" |
| `signal_found` | `ssid` (or `name`, `signal`), optional `rssi` | "PULSE sniffed out a signal: …" |

Any other `event` name is shown as plain info, with `detail.text` if
present. Send a need event (`hungry`, `tired`, `sad`) once when the
need crosses its line, not on every tick. The deck's own threshold
alerts (`config/pet.ini`) stay quiet for a need the companion has
just reported.

### Errors

```json
{"type":"error","id":"<same as the request>","payload":{"code":"unknown_type","message":"unsupported message type: foo"}}
```

Suggested codes: `unknown_type`, `bad_payload`, `bad_item`, `timeout`,
`proto_mismatch`, `unavailable`.

## Timeouts and retries

What the main board does (`cyberdeck/link.py`):

- Waits up to `timeout` (1.5 s) for a reply with the matching `id`.
  A reply that arrives later is counted as "stray" and dropped.
- If a line cannot be parsed, discard it and keep listening.
- Do not retry `pet.feed`, `pet.play`, or `pet.gift` automatically (they change
  state). `ping` and `pet.status` may be retried once.

## Example exchange

Main → companion:

```text
{"type":"pet.feed","id":"aa11...","payload":{"item":"ration"}}
```

Companion → main:

```text
{"type":"pet.feed.ok","id":"aa11...","payload":{"item":"ration","hunger_delta":-28,"pet":{"name":"PULSE","species":"Chirplet","stage":"baby","hunger":10,"happiness":80,"energy":80,"mood":"playful","age_hours":11.5}}}
```
