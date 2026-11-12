# Dashboard discovery protocol

The Rich dashboard on the main deck reports each new Wi-Fi, Bluetooth,
LAN, or RFID find to the companion with `pet.discovery`. The companion
is authoritative: it awards PULSE XP, may teach or strengthen a
kind-themed move, dedupes repeats, and tracks a short environment mood
from how busy the air has been.

The dashboard also sends `hello` with `"events": true` and
`"client": "dash"`. The extra `client` field is accepted and ignored;
the companion, the main deck, and the dashboard can all share one
TCP server at the same time.

## `pet.discovery` → `pet.discovery.ok`

Request payload:

```json
{"kind":"wifi","id":"aa:bb:cc:dd:ee:ff","interest":2,"event":"wifi_found","label":"cafe-guest"}
```

| field      | required | notes |
|------------|----------|-------|
| `kind`     | yes      | one of `wifi`, `bt`, `lan`, `rfid`. Missing or unknown → `bad_payload`. |
| `id`       | yes      | stable id of the device or tag (MAC, host, UID, …). Missing or blank → `bad_payload`. |
| `interest` | no       | clamped to 1–3 (default 1). Values outside that range are not an error. |
| `event`    | no       | dashboard event name; stored nowhere, only flavour. |
| `label`    | no       | human name used in the reaction text. |

Reply payload:

```json
{"xp":7,"learned_move":null,"strengthened_move":null,
 "reaction":"PULSE logged cafe-guest (+7 XP).",
 "level":1,"level_up":false,"pet":{...}}
```

`learned_move` and `strengthened_move` are the move's display name, or
`null`. `pet` is the usual status object (including `environment`).

A **repeat** of the same `kind`+`id` is not an error. The reply is
`pet.discovery.ok` with `xp: 0`, no move change, and a gentle reaction
such as "PULSE has already logged this wifi signal."

## XP

First sightings only. Base XP depends on kind, plus 3 for each interest
step above 1:

| kind | interest 1 | interest 2 | interest 3 |
|------|------------|------------|------------|
| wifi | 4          | 7          | 10         |
| bt   | 3          | 6          | 9          |
| lan  | 3          | 6          | 9          |
| rfid | 8          | 11         | 14         |

If the pet is **asleep**, the find is still recorded (and will not award
XP again later), but XP is halved (rounded) and the pet will not learn
or strengthen a move. The reaction is a sleepy one.

A first sighting that grants enough XP to level up sets `level_up: true`
and emits the usual `level_up` (and any level-taught `learned_move`)
events.

## Learning and strengthening moves

On a first sighting while awake, there is a `0.12 × interest` chance
(capped at 1) to interact with that kind's move pool:

| kind | themed moves |
|------|----------------|
| wifi | Signal Sniff, Beacon Burst, Channel Hop |
| bt   | Blue Pulse, Tooth Echo |
| lan  | Ping Echo, ARP Whisper |
| rfid | Tag Bite, Near Field |

- If the pet does not yet know every move in the pool, it **learns** one
  of the remaining ones (`learned_move` is set; a `learned_move` event
  is pushed).
- If it already knows the whole pool, it **strengthens** one of those
  known moves (`strengthened_move` is set; a `move_strengthened` event
  is pushed with `{move, rank, bonus}`).

Each strengthen adds one **rank** to that move (saved on the pet,
capped at 5). Effective power is `base + rank × 2`. Combat uses the
effective power, and `pet.combat.stats` reports `power`, `rank`, and
`bonus` on each move.

These themed moves are discovery-only (`learn_level` is unset); they
are not unlocked by leveling.

`move_strengthened` is a keep-event: event pacing will not drop or
merge it.

## Environment mood

`pet.status` (and every other `pet` object) includes:

```json
"environment": {"busy": 0.0, "buff": "calm"}
```

`busy` is 0–1: the number of first-finds in a rolling 300-second
wall-clock window, divided by 8 (and capped at 1). Only first sightings
count; repeats do not.

- A busy window (`busy` near 1, or several high-interest finds) gives a
  short **"busy airwaves"** buff: temporary +4 mood and +1 speed. These
  are applied when reading status and combat stats; they are not written
  into the saved meters.
- A quieter stretch of finds is **"curious: sniffing around"** (+2 mood).
- A long quiet stretch (a full window with no new first-finds after the
  pet has seen at least one) makes the pet **"bored"** (−2 mood). The
  status mood word may also become `bored` when no stronger need is in
  play.
- A brand-new pet with no finds yet is **calm**, not bored.

The rolling window itself is not persisted across save/load; after a
reload the meter starts calm until new finds arrive. Sighting keys and
move ranks *are* saved.

## Hello `client` field

```json
{"type":"hello","id":"...","payload":{"role":"main","proto":1,"events":true,"client":"dash"}}
```

`client` is ignored. `events` still opts that TCP session into
`pet.event` pushes. The same companion process can serve the deck and
the dashboard together; replies stay on the socket that sent the
request, and opted-in sessions each receive events.
