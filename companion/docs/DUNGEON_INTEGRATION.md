# Dungeon, rest, sleep, and evolution protocol

The main cyberdeck's dungeon (`cyberdeck/features/dungeon.py`) is a simple
turn-based crawl: the player moves, then monsters take a turn, and loot
can be gifted to PULSE. This companion is the *authoritative* pet. The
deck should treat itself as stateless: ask for combat stats, ask the pet
to act or take a hit, and apply the numbers that come back. Do not keep a
second copy of HP or energy on the deck.

This document is the pet-side contract. It does not change the main repo.

## Suggested deck-side flow

1. When a dungeon run starts (or when the party menu opens), send
   `pet.combat.stats` and show the pet next to the player.
2. Each round, after the player acts, send `pet.combat.action` with the
   move the player picked for the pet (or omit `move` and let the pet
   pick) plus a snapshot of the current enemy: `{name, level, hp, attack,
   defense, speed}`. Apply the returned `damage` to that enemy if `hit`
   is true.
3. When an enemy targets the pet, send `pet.combat.hit {damage: N}`.
   Player items that heal the pet (a repair kit, a potion) can use
   `{heal: N}` or a negative `damage` on the same message.
4. A fainted pet cannot fight until it rests or is healed above 0 HP.
   Offer `pet.rest` (or `pet.sleep {on: true}`) so a fainted pet is never
   stuck.
5. When the run ends, send `pet.combat.end {result: "win"|"lose"|"flee",
   xp, enemies: [...]}`. The pet grants XP, may level up or learn a move,
   adjusts mood, and records a care-mistake if it fainted on a loss.
6. Loot after a win can still go through `pet.gift`. An evo chip makes
   the pet *ready to evolve* but does not change its form (see below).

## Rest and sleep

### `pet.rest` → `pet.rest.ok`

Instant short rest (smaller than the demo caretaker rest): about +20
energy, a little mood, and +6 HP.

```json
{"type":"pet.rest","id":"...","payload":{}}
{"type":"pet.rest.ok","id":"...","payload":{"rested":true,"energy_delta":20,"pet":{...}}}
```

If energy is already full *and* the pet is not fainted and not missing
HP, the reply is `rested: false` with a `reason` (energy is already
full). A fainted pet, or one with missing HP, may still rest so it is
not stuck.

### `pet.sleep {on}` → `pet.sleep.ok`

```json
{"type":"pet.sleep","id":"...","payload":{"on":true}}
{"type":"pet.sleep.ok","id":"...","payload":{"asleep":true,"changed":true,"pet":{...}}}
```

While asleep, each tick:

- energy refills steadily (`ENERGY_SLEEP_PER_HOUR`, default 25 per
  simulated hour)
- hunger drains more slowly (40% of the usual rate)
- HP regenerates slowly (`HP_REGEN_PER_HOUR`, default 8)

The pet will not play, train, or fight. It wakes automatically at full
energy and emits a `pet.event` of kind `woke`. The pet object includes
`asleep: true|false`. Sending `{on: false}` wakes it early. Going to
sleep when already fully rested (and not missing HP) is refused with a
reason and `changed: false`.

## Combat

### Formulas

Integers throughout.

Outgoing hit chance:

    accuracy' = clamp(move.accuracy + (pet.speed - enemy.speed) // 2, 5, 100)
    hit       = rng.randint(1, 100) <= accuracy'

Outgoing damage (only if the move hits):

- If the move's effect is `"guard"`: damage is 0, and the pet raises a
  guard flag that halves the *next* incoming hit.
- Otherwise: `damage = max(1, move.power + pet.attack - enemy.defense)`

Incoming hit (`pet.combat.hit` field `"damage"`):

    taken = max(0, damage - pet.defense // 4)
    if the pet is guarding: taken = taken // 2, then the flag clears
    HP is reduced by `taken` and clamped at 0 (fainted).

Incoming heal (`"heal"` or a negative `"damage"`): HP increases by that
amount, up to `max_hp`. Defense is not applied. A fainted pet healed
above 0 can fight again.

### Fallback when a move cannot be used

This is a deliberate design choice:

- If the pet is **asleep** or **fainted**, the action is refused
  (`acted: false`, with a reason). No energy is spent.
- If the requested move is missing or too expensive, the pet auto-picks
  **Chirp** when it knows Chirp and can afford it, otherwise a built-in
  **Guard** (0 energy, 0 damage, effect `"guard"`). The reply sets
  `fallback: true`.

### Messages

`pet.combat.stats` → `pet.combat.stats.ok`:

```json
{"hp":32,"max_hp":32,"attack":6,"defense":5,"speed":7,"level":1,
 "moves":[{"name":"Chirp","power":8,"accuracy":95,"energy_cost":4,"kind":"electric","effect":null,"rank":0,"bonus":0}],
 "fainted":false,"asleep":false,"guarding":false}
```

Each move includes `rank` (0–5) and `bonus` (rank × 2). `power` is the
effective value (`base + bonus`). Strengthen ranks come from dashboard
finds (`docs/DISCOVERY.md`) and are used when the pet attacks.
`speed` may also include a temporary +1 environment buff when the air
has been busy.

`pet.combat.action {move, enemy}` → `pet.combat.action.ok`:

```json
{"move":"Chirp","hit":true,"damage":12,"effect":null,"energy_spent":4,
 "pet_hp":32,"reaction":"PULSE uses Chirp on the slime for 12.",
 "acted":true,"pet":{...}}
```

`pet.combat.hit {damage}` or `{heal}` → `pet.combat.hit.ok`:

```json
{"damage_taken":4,"healed":0,"pet_hp":28,"fainted":false,"pet":{...}}
```

`pet.combat.end {result, xp, enemies}` → `pet.combat.end.ok`:

`result` must be `"win"`, `"lose"`, or `"flee"`. XP is granted (and may
level-up / teach a move, emitting the usual events). A win lifts mood; a
loss drops it and, if the pet is fainted, adds a care-mistake.

## Evolution (player-chosen, never automatic)

The pet never changes form from `tick`, rest, sleep, or a gift. Time
gates (age, level, XP) live in one config, `DEFAULT_EVOLUTION_GATES` in
`pet/evolution.py`, and may be overlaid by an optional
`evolution.json` in the project root. `--evolution-speed` (on `main.py`
and `companion.py`) is a multiplier: `1.0` is the default, `2.0` halves
every gate (twice as fast; level is at least 2), `0.5` doubles them.

Default gates, keyed by the stage the pet is evolving *from*:

| Stage        | min age (hours) | min level | min XP |
|--------------|-----------------|-----------|--------|
| baby         | 36              | 4         | 40     |
| in-training  | 100             | 8         | 140    |
| rookie       | 220             | 13        | 320    |
| champion     | 400             | 19        | 600    |
| ultimate     | 620             | 26        | 960    |

Branch rules (care, mood, discoveries) are unchanged: a well-cared-for
Wavling may become Aethling; a neglected or gloomy one becomes Staticore;
3+ unique discoveries open Hexwave / Jammeron.

When the time gates are met (or an evo chip has skipped them),
`pet.status.evolution` looks like:

```json
{
  "next_stage": "rookie",
  "progress": 1.0,
  "ready": true,
  "choices": [
    {"species":"Aethling","stage":"rookie",
     "hint":"needs good care (mood 50+, at most 2 care mistakes)",
     "available":true},
    {"species":"Staticore","stage":"rookie",
     "hint":"the harder path (low mood or many care mistakes)",
     "available":true}
  ]
}
```

`available` follows the care / stat / discovery rules only (not the time
gates). `hint` explains the requirement, for example
`"needs 3+ unique discoveries"`.

Events, in order:

1. `evolve_soon` — once, when progress reaches about 90% of the time
   gates. Detail: `{from, to_candidates, next_stage, eta_hours, progress}`.
2. `evolve_choice` — once, when `ready` becomes true. Detail:
   `{from, next_stage, choices, progress}`. If an evo chip jumps straight
   to ready, `evolve_soon` still fires first, then `evolve_choice`.
3. The pet waits.
4. `pet.evolve {species}` → `pet.evolve.ok {accepted, from, to, reaction}`.
   On success this is followed by `evolving` then `evolved`.
5. A known species that is not a next form, not ready, or not
   `available` is refused with `accepted: false` and a `reason`. An
   unknown species is a `bad_payload` error.

An evolve-chip gift (`pet.gift` with `kind: "evolve"`) sets
`ready: true` immediately, emits `evolve_choice` (and `evolve_soon` if it
had not fired yet), and does **not** evolve on its own. At mega the gift
is refused.

The headless `python3 main.py` sim auto-picks the first available choice
so a long run still progresses. `companion.py --avatar` prints a boxed
list of choices in the terminal; it does not block on stdin (the server
thread has to keep listening). Send `pet.evolve` from the deck to
confirm.

These growth events (`evolve_soon`, `evolve_choice`, `evolving`,
`evolved`, plus `woke`) are never dropped by event pacing.

## Dashboard discoveries

New devices and tags found by the Rich dashboard are reported with
`pet.discovery`. First sightings grant PULSE XP (scaled by kind and
interest) and may teach or strengthen a kind-themed move. Repeats of the
same `kind`+`id` return `xp: 0` and a gentle reaction, not an error.
`pet.status` includes `environment: {busy, buff}`: a busy window gives a
small temporary mood/speed buff, and a long quiet stretch makes the pet
bored. Hello may include a `client` field (ignored) so the dashboard and
the main deck can share one companion connection.

Full request/reply shape, XP table, move pools, and rank rules:
[`docs/DISCOVERY.md`](DISCOVERY.md).


## Mood, bond, and the dungeon (life engine additions)

See [ALIVE.md](ALIVE.md) for the whole life engine. These are the parts that touch the dungeon. They are all extra keys, so a deck that ignores them keeps working.

- **`pet.combat.action.ok`** can also carry:
  - `crit` (bool): a bond crit, ×2 damage.
  - `disobeyed` (bool): low obedience made the pet use a different move than asked.
  - `mood_effect`: `angry` (×1.25 damage, −5 accuracy), `tired` (−10 accuracy), or null.
  - `cheer` (string): a line to show when bond is close or better.
  - `reason`: if the action did not happen, this is `"tantrum"` (pet is sulking for about 60 s) or `"refused"` (angry and exhausted). The reply's `say` gives the pet's line. Treat these like a wasted turn, not an error.
- **Default pets are neutral.** At normal mood, energy, obedience, and bond the formulas above are unchanged, and no extra RNG is drawn.
- **`pet.combat.end.ok`** includes `say`, a short win, lose, or flee line. A win adds a "won_fight" mood boost that fades over about 15 min. A loss adds a "lost_fight" mood dip that fades over about 25 min.
- **`pet.activity`** reports dungeon runs so the pet can react to them:
  - Send `{kind:"dungeon_start", name}` when a run begins. The reply includes `cheer` when the bond is high enough.
  - Send `{kind:"dungeon", name, result:"win"|"lose"|"flee"}` when the run ends.
  - Send `{kind:"app", name}` on other app launches. Lots of app time without pet care makes it jealous.
- **`medicine`** is a new `pet.gift` kind. It cures `glitch` and `corrupted` sickness. A sick pet still fights, but its mood keeps falling until it is healed.
- **Rival fights** (`pet.rival`) are resolved on the companion with the same `perform_action` and incoming-damage math. The pet yields at 1 HP, so it never faints to a rival.
