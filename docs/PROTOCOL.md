# Dual-board message protocol

This is the language the **main cyberdeck** and the **companion pet board**
speak to each other. Both sides should implement this document the same
way. The Python reference is `cyberdeck/protocol.py`.

The first scaffold only talks to a simulated companion in the same
process, but the messages are already in the final shape so a real
UART link can be dropped in later.

## Transport (later: UART)

- **Framing:** one UTF-8 JSON object per line, ended by `\n`.
  A trailing `\r` is ignored. Empty lines are ignored.
- **Planned serial settings:** 115200 baud, 8 data bits, no parity,
  1 stop bit (8N1), no hardware flow control.
- **Planned device:** `/dev/ttyAMA0` on a Pi Zero 2 W, overridable
  with `--serial` or the `CYBERDECK_SERIAL` environment variable.
- **Direction:** the main board sends *requests*. The companion
  answers with a *reply* that uses the same `id`. The companion
  does not send unsolicited messages in this version.

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
{"type":"hello","id":"...","payload":{"role":"main","proto":1}}
{"type":"hello.ok","id":"...","payload":{"role":"companion","proto":1,"source":"sim","pet":"PULSE"}}
```

## Message types

| Request       | Reply           | Payload (request)       | Payload (reply) |
|---------------|-----------------|-------------------------|-----------------|
| `hello`       | `hello.ok`      | `role`, `proto`         | `role`, `proto`, optional `source`, `pet` |
| `ping`        | `pong`          | `{}`                    | `ok` (true), optional `source` |
| `pet.status`  | `pet.status.ok` | `{}`                    | `pet` object (see below) |
| `pet.feed`    | `pet.feed.ok`   | `item` (string)         | `item`, `hunger_delta`, `pet` |
| `pet.play`    | `pet.play.ok`   | `game` (string)         | `game`, `played` (bool), optional `reason`, `pet` |
| `pet.gift`    | `pet.gift.ok`   | `item` (object, below)  | `accepted`, `item`, `reaction`, `effects`, `pet` |
|               | `error`         |                         | `code`, `message` |

### Pet object

```json
{
  "name": "PULSE",
  "species": "Bitling",
  "stage": "rookie",
  "hunger": 38,
  "happiness": 72,
  "energy": 80,
  "mood": "playful",
  "age_hours": 11.5,
  "gifts": 3
}
```

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
| `evolve`  | moves one stage up: egg → baby → rookie → champion → ultimate → mega |
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

A request without `item.name` gets an `error` reply with code
`bad_item`. Like `pet.feed`, gifts change state, so they must not be
retried automatically.

### Errors

```json
{"type":"error","id":"<same as the request>","payload":{"code":"unknown_type","message":"unsupported message type: foo"}}
```

Suggested codes: `unknown_type`, `bad_payload`, `bad_item`, `timeout`,
`proto_mismatch`, `unavailable`.

## Timeouts and retries

Recommended on the main board when a real link exists:

- Wait up to 1 second for a reply with the matching `id`.
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
{"type":"pet.feed.ok","id":"aa11...","payload":{"item":"ration","hunger_delta":-28,"pet":{"name":"PULSE","species":"Bitling","stage":"rookie","hunger":10,"happiness":80,"energy":80,"mood":"playful","age_hours":11.5}}}
```
