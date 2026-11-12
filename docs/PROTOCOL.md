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
| `pet.status`  | `pet.status.ok` | `{}`                    | `pet` object (see below), optional `evolution` |
| `pet.feed`    | `pet.feed.ok`   | `item` (string)         | `item`, `hunger_delta`, `pet` |
| `pet.play`    | `pet.play.ok`   | `game` (string)         | `game`, `played` (bool), optional `reason`, `pet` |
| `pet.gift`    | `pet.gift.ok`   | `item` (object, below)  | `accepted`, `item`, `reaction`, `effects`, `pet` |
| `pet.avatar`  | `pet.avatar.ok` | optional `frame`, `mood`| text face, see below (optional message) |
| `pet.rest`    | `pet.rest.ok`   | `{}`                    | `rested`, `energy_delta`, optional `reason`, `pet` |
| `pet.sleep`   | `pet.sleep.ok`  | `on` (bool)             | `asleep`, `changed`, `reason` (or null), `pet` |
| `pet.evolve`  | `pet.evolve.ok` | `species`               | `accepted`, `from`, `to`, `reaction`, optional `reason` |
| `pet.combat.stats`  | `pet.combat.stats.ok`  | `{}`               | combat sheet, see "Dungeon combat" |
| `pet.combat.action` | `pet.combat.action.ok` | optional `move`, `enemy` | outcome, see "Dungeon combat" |
| `pet.combat.hit`    | `pet.combat.hit.ok`    | `damage` or `heal` | `damage_taken`, `healed`, `pet_hp`, `fainted`, `pet` |
| `pet.combat.end`    | `pet.combat.end.ok`    | `result`, `xp`, `enemies` | `result`, `xp_granted`, `reaction`, `pet` |
| *(push)*      | —               |                         | `pet.event`, see below (optional) |
|               | `error`         |                         | `code`, `message` |

The rest / sleep / evolve / combat messages and the evolution events
are specified by the companion in
[companion/docs/DUNGEON_INTEGRATION.md](../companion/docs/DUNGEON_INTEGRATION.md).
That document wins if anything here disagrees; the sections below are
the deck's summary of it.

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
  "asleep": false,
  "avatar": {"style": "face", "mood": "playful", "frame": 0, "text": "(^_^)"}
}
```

The deck shows whatever `species` (or `form`) the companion sends. Do
not expect any fixed name.

**Stages**, lowest to highest: `egg`, `baby`, `in-training`, `rookie`,
`champion`, `ultimate`, `mega`. A companion may use only some of them.

**Optional fields** (the deck shows them only when present): `level`,
`xp`, `hp`, `max_hp`, `attack`, `defense`, `speed`, `moves` (a list
of names), `care_mistakes`, `asleep` (zZ in the pet pane and status
bar), and `avatar` `{style: "face", mood, frame, text}` — the face to
show right now. The deck fetches the full animation with `pet.avatar`
again only when the mood (or asleep) changes, not on every frame.
Older companions sent `avatar` `{form, mood, frame}` for a sprite; that
still works. Unknown extra fields are ignored.

**Evolution progress** comes beside the pet in `pet.status.ok`:

```json
{"pet": {...}, "evolution": {"next_stage": "champion", "progress": 0.62, "ready": false,
  "choices": [{"species": "Voltshard", "stage": "champion", "hint": "needs level 5+", "available": false}, …]}}
```

`progress` is 0–1 (the deck draws a bar in the pet pane and in
`pet status`), `ready` turns true when the time gates are met (or an evo
chip skipped them), and `choices` lists every branch for the next
stage. A choice with `available: false` is shown greyed out with its
`hint` and cannot be picked. `next_stage: null` means the final form.
The pet **never** evolves on its own: the deck sends `pet.evolve
{species}`. A refusal is `accepted: false` with a `reason` (not ready /
unavailable); an unknown species is `error` / `bad_payload`.

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
| `evolve`  | makes the pet *ready to evolve* now (skipping the time gates) and pushes `evolve_soon` + `evolve_choice`; it never evolves by itself. Refused at `mega`. |
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

The pet's animated text face, for the pet pane and `pet avatar`. The
request payload is `{}`, or it may name a `frame` and/or a `mood`.

```json
{"type":"pet.avatar","id":"…","payload":{}}
{"type":"pet.avatar.ok","id":"…","payload":{"style":"face","mood":"playful","frame":0,
  "frames":["(^_^)","(^_~)"],"interval_ms":600}}
```

- The deck shows `frames` in turn, one every `interval_ms`, so the face
  animates on the deck without more messages. Frames are short one-line
  strings; the deck drops control characters and trims each to 24
  characters (and strips stray outer quotes like `'(^_^)'`).
- While the pet is `asleep`, a face without `zZ` gets ` zZ` added.
- Headless `pet avatar` prints the frames as text.
- **Older companions** answered with a 16x16 sprite instead:
  `{form, mood, frame, width, height, palette: [null, "#rrggbb", …],
  rows: [[…16 palette indexes…], …16 rows]}`. The deck still accepts
  that as a fallback: half-block colour rows in the pet pane (colour +
  Unicode terminals only) and a character grid in headless
  `pet avatar`.
- A companion without this message replies `error` / `unknown_type`.
  The deck then stops asking and uses a face for the mood word.

### Rest and sleep (`pet.rest`, `pet.sleep`)

`pet rest` is an instant short rest (the companion: about +20 energy,
+6 HP). A fully rested pet answers `rested: false` with a `reason`; a
fainted or hurt pet may always rest, so it is never stuck.

`pet sleep on|off` sends `{"on": true|false}`. While asleep the pet's
energy refills each tick (HP slowly too), it will not play or fight,
and it wakes **by itself** at full energy with a `woke` event. Going to
sleep when fully rested is refused: `changed: false` and a `reason`.

### Dungeon combat (`pet.combat.*`)

PULSE fights next to the player in the deck's dungeon. The companion is
the authority: the deck only applies the numbers it gets back.

```json
{"type":"pet.combat.stats","id":"…","payload":{}}
{"type":"pet.combat.stats.ok","id":"…","payload":{"hp":32,"max_hp":32,"attack":6,"defense":5,"speed":7,"level":1,
  "moves":[{"name":"Chirp","power":8,"accuracy":95,"energy_cost":4,"kind":"electric","effect":null}],
  "fainted":false,"asleep":false,"guarding":false}}

{"type":"pet.combat.action","id":"…","payload":{"move":"Chirp","enemy":{"name":"worm","level":3,"hp":12,"attack":4,"defense":0,"speed":0}}}
{"type":"pet.combat.action.ok","id":"…","payload":{"move":"Chirp","hit":true,"damage":12,"effect":null,"energy_spent":4,
  "pet_hp":32,"reaction":"PULSE uses Chirp on the worm for 12.","acted":true,"pet":{...}}}

{"type":"pet.combat.hit","id":"…","payload":{"damage":5,"enemy":{...}}}
{"type":"pet.combat.hit.ok","id":"…","payload":{"damage_taken":4,"healed":0,"pet_hp":28,"fainted":false,"pet":{...}}}

{"type":"pet.combat.end","id":"…","payload":{"result":"win","xp":11,"enemies":["bug","glitch"]}}
{"type":"pet.combat.end.ok","id":"…","payload":{"result":"win","xp_granted":11,"reaction":"…","pet":{...}}}
```

- `accuracy` is 0–100. Energy is not on the combat sheet; the deck reads
  it from the `pet` object.
- `move` may be left out: the pet picks. A missing or unaffordable move
  makes the pet fall back to Chirp (if it knows and can afford it) or a
  built-in Guard (0 energy, 0 damage, `effect: "guard"`, halves the next
  hit), with `fallback: true`.
- An asleep or fainted pet answers `acted: false` with a `reason`.
- Heals (a repair kit) use `pet.combat.hit` with `heal: N` (or a
  negative `damage`). A fainted pet healed above 0 fights again.
- `result` is `win`, `lose` or `flee`. Level-ups and new moves arrive as
  the usual `level_up` / `learned_move` events.
- The deck plays solo if `pet.combat.stats` fails, is unknown, or says
  the pet is asleep.

### Discoveries (`pet.discovery`)

The interface's awareness tab reports each first sighting of a Wi-Fi
network, Bluetooth device, LAN host or RFID tag as `pet.discovery`
(`kind`, `id`, `interest` 1-3, `label`). The reply `pet.discovery.ok`
carries `xp`, `level`, `level_up`, `learned_move`,
`strengthened_move`, `reaction` and the pet object (with
`environment`). Full rules: the "Protocol: `pet.discovery`" section of
[DASHBOARD.md](DASHBOARD.md). The deck's built-in sim pet
(`cyberdeck/companion.py`) answers it too, so `--link sim` behaves like
the real companion.

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
| `evolve_soon`  | `from`, `next_stage`, `to_candidates`, `eta_hours`, `progress` | pop-up: "close to evolving" |
| `evolve_choice`| `from`, `next_stage`, `choices`, `progress` | alert + pop-up listing the forms; `pet evolve` picks |
| `evolving`     | `from`, `to`                            | pop-up with a shimmer animation |
| `evolved`      | `to` or `form` (new species), `stage`, `from` | pop-up: "PULSE evolved into Wavling!" |
| `woke`         | optional `reason`                       | "PULSE woke up" (zZ goes away) |
| `level_up`     | `level`                                | "PULSE reached level 4!" |
| `learned_move` | `move` (or `name`)                     | "PULSE learned Static Snap!" |
| `signal_found` | `ssid` (or `name`, `signal`), optional `rssi` | "PULSE sniffed out a signal: …" |

Evolution events come in this order: `evolve_soon` (once, near the end
of the time gates), `evolve_choice` (once, when ready), then — after the
deck's `pet.evolve` — `evolving` and `evolved`. The companion never drops
these (or `woke`) when it paces events. In the full-screen deck the four
evolution events open a boxed pop-up (config/pet.ini `[popups]`).

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
