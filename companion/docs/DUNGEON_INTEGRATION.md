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

Since the turn-based battle update, enemy contact opens a Pokemon-style
battle: FIGHT / BAG / PULSE / RUN, fully ASCII on a 48-column screen. The
deck owns the battle flow, enemy HP, enemy AI, and the flee roll. The
companion owns PULSE: HP, EN, moves, types, bag, mood, and every line it
says. The full message contract is in **Battle protocol** below. All new
fields are additions, so the old crawl (stats / action / hit / end) keeps
working.

### Formulas

Integers throughout.

Outgoing hit chance:

    accuracy' = clamp(move.accuracy + (pet.speed - enemy.speed) // 2
                      + mood/bond accuracy mods, 5, 100)
    hit       = rng.randint(1, 100) <= accuracy'

Outgoing damage (only if the move hits):

- If the move's effect is `"guard"`, damage is 0 and the pet raises a
  guard flag that halves the *next* incoming hit.
- Otherwise:

      base   = max(1, move.power(+rank bonus) + pet.attack - enemy.defense)
      base   = round(base * 1.25) if PULSE is angry
      crit   = random() < 1/16 + bond bonus ((bond-40)/300 at bond >= 45)
      base   = int(base * 1.5) on a crit
      mult   = CHART[move.type][enemy.type]        (2, 1, 0.5 or 0)
      damage = 0 if mult == 0 else max(1, int(base * mult))

Incoming hit (`pet.combat.hit` field `"damage"`, with an optional
`attack_type`, defaulting to the enemy's type):

    taken = max(0, damage - pet.defense // 4)
    taken = 0 if mult == 0 else max(1, int(taken * mult))  (if taken > 0)
       where mult = CHART[attack_type][PULSE's type]
    if the pet is guarding: taken = taken // 2, then the flag clears
    HP is reduced by `taken` and clamped at 0 (fainted).

Incoming heal (`"heal"` or a negative `"damage"`): HP rises by that
amount, up to `max_hp`. Defense and types are not applied. A fainted pet
that is healed above 0 can fight again.

### Fallback when a move cannot be used

- If the pet is **asleep** or **fainted**, the action is refused
  (`acted: false` with a reason). No energy is spent.
- If `move` is **omitted**, the pet picks its own move: the loadout move
  with the best expected damage against the enemy's type that it can
  afford. The reply sets `auto: true`.
- If the requested move is unknown or too expensive, the pet uses
  **Chirp** when it knows Chirp and can afford it. Otherwise it uses a
  built-in **Guard** (0 EN, 0 damage, effect `"guard"`). The reply sets
  `fallback: true`.
- Tantrums, refusals (angry and exhausted), and disobedience (low
  obedience) come from the life engine. They show up as `obeyed: false`.

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
  - `crit` (bool): a critical hit (1/16 base, more with bond), ×1.5 damage.
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


## Battle protocol (turn-based, 48 columns)

Exact contract for Cyberdeck Main. Every message is `{type,id,payload}`
on proto 1. Replies are `<type>.ok`, and errors are
`error {code, message}`. **Every `say` line, every `log` line, and every
`desc` is plain ASCII and at most 44 characters.** That is the inner
width of a 48-column box with a 2-character border on each side. The
companion enforces this at runtime: lines are cut at a word boundary with
`...`, and a test covers every battle pool. Show `say` bare on its own
row. A `PULSE: ` prefix only fits when the line is 37 characters or
shorter.

### Types

`NORM, VOLT, DATA, NET, FIRE, ICE, VOID`. The chart below shows the
attacker in rows and the defender in columns. Blank means 1x.

```
atk\def    NORM VOLT DATA NET  FIRE ICE  VOID
NORM        .    .    .    .   0.5   .   0.5
VOLT        .   0.5   2    .    .    2   0.5
DATA        .    .   0.5   .   0.5   .    2
NET         .    2    .   0.5  0.5   .    0
FIRE        .    .    2    2    0   0.5   .
ICE         .   0.5   .    2    2   0.5   .
VOID        2    .   0.5   .    .    .   0.5
```

There are two immunities: NET -> VOID (packets vanish) and FIRE -> FIRE
(a firewall cannot burn). `effectiveness_label` is `super` (2), `weak`
(0.5), `normal` (1), or `immune` (0). `pet.combat.roster` returns the
chart as JSON.

PULSE's moves:

| Type | Moves |
|---|---|
| NORM | Chirp |
| VOLT | Static Snap, Tag Bite, Near Field |
| DATA | Packet Burst, Signal Sniff, Ping Echo |
| NET | Echo Pulse, Stray Handshake, Channel Hop |
| FIRE | Bright Arc, Beacon Burst |
| ICE | Blue Pulse, Tooth Echo |
| VOID | Noise Veil (guard), ARP Whisper |

PULSE's own (defending) type:

| Type | Species |
|---|---|
| NORM | Chirplet, Wavling |
| FIRE | Aethling, Luminode, Aurorion, Aethernova |
| NET | Hexwave, Resonarch, Signumega |
| VOLT | Staticore, Voltshard, Stormbit, Nullstorm |
| VOID | Jammeron, Blackout, Voidstatic |

### Enemy roster

The data file is `pet/data/roster.json`, and `pet.combat.roster` returns
it. Stats are the companion's level-1 suggestions; each level adds
`per_level`. The deck owns enemy HP and AI, so it may use its own
numbers. `type` is what matters to the companion. Entries marked `extra`
are optional additions beyond Main's list.

| id | glyph | type | weak | depth | face | hp/atk/def/spd |
|---|---|---|---|---|---|---|
| bug | b | DATA | VOLT | 1 | `(o_o)` | 18/4/1/4 |
| glitch | g | VOID | DATA | 1 | `(#_#)` | 20/5/1/6 |
| spam | s | NET | FIRE | 1 | `(^o^)` | 16/4/0/7 |
| popup (extra) | p | NORM | - | 1 | `[o.o]!` | 14/3/1/5 |
| virus | v | DATA | FIRE | 2 | `(>_<)` | 26/6/2/5 |
| lag | l | ICE | VOLT | 2 | `(-_-)` | 30/5/3/2 |
| worm | w | NET | ICE | 3 | `(~o~)` | 34/7/2/6 |
| firewall | F | FIRE | ICE | 3 | `[=_=]` | 40/6/5/3 |
| daemon | D | VOID | DATA | 4 | `` (`_') `` | 44/9/3/6 |
| cryptominer (extra) | c | FIRE | ICE | 4 | `($_$)` | 38/8/3/4 |
| botnet (extra) | n | NET | FIRE | 5 | `(0_0)` | 48/9/3/7 |
| kernel_panic (boss, every 5th floor) | K | VOID | DATA | 5 | `(X_X)` | 90/12/5/6 |

`weak` is Main's listed weakness. The roster message also gives
`weak_to`, `resists`, and `immune_to` from the chart. For example, bug
is weak to VOLT and FIRE.

### Enemy object

The deck sends this in `pet.combat.stats`, `pet.combat.action`,
`pet.combat.hit`, and `pet.combat.run`:

```json
{"id":"bug","name":"bug","type":"DATA","level":2,"hp":22,"max_hp":22,"attack":5}
```

Missing fields are filled in from the roster entry that matches `id`, or
else `name`: type, defense, speed, and face. An unknown enemy is NORM.
The companion echoes the normalized enemy as `enemy` in replies.

### 1. Battle start: `pet.combat.stats {enemy?}`

This is the same message as before, so `enemy` is optional. With an
enemy, the reply includes an intro taunt keyed to the enemy's type, or a
boss line.

```json
{"say":"Hi bug. Bye bug. Soon.",
 "log":["A wild BUG appeared!","Go, PULSE!"],
 "moves":[{"id":"static_snap","name":"Static Snap","type":"VOLT","power":14,
           "energy_cost":8,"accuracy":90,"rank":0,"bonus":0,"kind":"strike",
           "effect":null,"desc":"A sharp VOLT crackle. Great vs DATA."}],
 "known_moves":[...every known move, legacy shape...],
 "bag":[{"id":"cache_cookie","name":"cache cookie","kind":"food","qty":3,
         "usable_in_battle":true,"desc":"Restores 20 HP. Crunchy."}],
 "pet":{"name":"PULSE","species":"Chirplet","type":"NORM","level":4,"hp":20,
        "max_hp":20,"energy":80,"max_energy":100,"mood":"playful",
        "status":"ok","face":"(^_^)"},
 "enemy":{...normalized...},"flee_chance":0.65,"type":"NORM",
 "energy":80,"max_energy":100,
 "hp":20,"max_hp":20,"attack":8,"defense":6,"speed":6,"level":4,
 "fainted":false,"asleep":false,"guarding":false}
```

- `moves` is the battle loadout, with **at most 4** moves. Use
  `pet.combat.moves` to change it.
- `status` is `ok`, `glitch` (sick), or `fainted`.
- `face` is a one-line ASCII face.
- Without an enemy, `say` is `""`.

### 2. FIGHT: `pet.combat.action {move?, enemy, turn?}`

```json
{"move_used":"Static Snap","type":"VOLT","damage":40,"effectiveness":2.0,
 "effectiveness_label":"super","crit":false,"missed":false,"obeyed":true,
 "energy_cost":8,"hp":20,"energy":72,"status":"ok","turn":3,
 "say":"Weakness found!",
 "log":["PULSE used STATIC SNAP!","It's super effective!","BUG took 40 damage."],
 "acted":true,"enemy":{...},
 "move":"Static Snap","hit":true,"effect":null,"energy_spent":8,"pet_hp":20,
 "reaction":"...legacy line...","pet":{...}}
```

- The deck subtracts `damage` from its own enemy HP.
- `obeyed:false` means a tantrum, a refusal, or disobedience.
  - On a tantrum or refusal, `acted:false` and `reason` is `tantrum` or
    `refused`. Nothing was used, so treat it as a wasted turn.
  - On disobedience, `acted:true` and `move_used` is a different move.
    The `log` includes `PULSE ignored your order!`.
- Optional extras are `fallback`, `auto`, `disobeyed`, `mood_effect`, and
  `cheer`. `cheer` is a short line shown when the bond is high.
- `log` order is: the optional too-tired line, the optional "ignored your
  order" line, then `X used MOVE!`, then one of: `X is guarding!`,
  `X's attack missed!`, `It doesn't affect FOE...`, or
  [`A critical hit!`], [`It's super effective!` / `It's not very
  effective...`], `FOE took N damage.`
- `say` comes from the pools in this order: disobey, guard, miss, immune,
  crit, super, weak, then a move reaction (generic or by type).

### 3. Enemy turn: `pet.combat.hit {damage, enemy?, attack_type?}`

The deck rolls its enemy AI and base damage. The companion applies
defense, the type chart against PULSE's type, and guard.

```json
{"damage":8,"damage_taken":8,"attack_type":"DATA","effectiveness":1.0,
 "effectiveness_label":"normal","hp":12,"pet_hp":12,"energy":72,
 "status":"ok","fainted":false,"low_hp":false,
 "say":"Ouch!","log":["PULSE took 8 damage."],"healed":0,"pet":{...}}
```

- **The deck writes the enemy's attack line itself**, as `FOE used
  ATTACK!`. The companion's `log` only reports the result: an
  effectiveness line if any, then `PULSE took N damage.` or `It doesn't
  affect PULSE...`, then `PULSE fainted!` if PULSE fainted.
- The `say` key is one of: `faint` when PULSE faints (the log ends with
  `PULSE fainted!`), `immune_hit`, `resist`, `lowhp` (HP at 25% or
  less), or `hurt`.
- `{heal:N}` still works (for example, a deck-side repair kit).

### 4. BAG: `pet.combat.item {item}`

`item` is a bag id or a name (`cookie` works as an alias for
`cache_cookie`).

```json
{"ok":true,"consumed":true,"item":{"id":"cache_cookie","name":"cache cookie",
 "kind":"food","qty":2},"hp":20,"energy":72,"status":"ok",
 "status_cleared":false,"say":"Crunch! +16 HP!",
 "log":["You used CACHE COOKIE!","PULSE recovered 16 HP."],"bag":[...],"pet":{...}}
```

| Item | Effect |
|---|---|
| food (cache cookie 20, glitch berry 15, data cookie 25, byte burger 40, or a gift's `power`) | Restores HP. It also revives a fainted PULSE. |
| battery (battery 30, power cell 25, overclock chip 50) | Restores EN, up to 100. |
| medicine (medicine 5, patch kit 10) | Cures glitch (`status_cleared:true`) and heals a little. |
| evo chip, toys, trinkets | Not usable in battle. |

Outcomes that use up no item:

- An item that would do nothing (full HP, full EN, not sick) gives
  `ok:false`, `consumed:false`, `reason:"no_effect"`, and a `say` line.
  It is not an error.
- Errors carry `say` and `item` in the error payload as well:
  - `error {code:"none_left"}`: the bag has none of that item.
  - `error {code:"not_usable_in_battle"}`: for example, an evo chip.
  - `error {code:"bad_payload"}`: no item was given.

The bag:

- New pets start with 3 cache cookies, 1 battery, and 1 medicine.
- Things that add to the bag:
  - `pet.gift {item, store:true}`
  - A medicine gift for a pet that is not sick
  - `pet.combat.end {loot:[...]}`
  - `pet.inventory {action:"add"}`
- `pet.inventory {action:"list"|"add"|"drop", item?, qty?}` (optional)
  returns
  `{bag:[{id,name,kind,qty,usable_in_battle,desc}], changed?}`.

### 5. RUN

The deck rolls the flee and then sends `pet.combat.end {result:"flee"}`.
The optional `pet.combat.run {enemy}` returns
`{chance, say_ok, say_fail}`, with the suggested odds and both lines:

    chance = clamp(0.5 + (pet.speed - enemy.speed) * 0.05
                       + (pet.level - enemy.level) * 0.05, 0.10, 0.95)
    halved against a boss

`pet.combat.stats` with an enemy also returns `flee_chance`.

### 6. Battle end: `pet.combat.end {result, xp?, enemies?, loot?}`

```json
{"result":"win","xp":40,"xp_granted":40,"level_up":true,"level":5,
 "learned_move":null,"bond_delta":2.0,"bond":"friendly","mood":"joyful",
 "fainted":false,"loot_added":[{"id":"data_cookie","name":"data cookie","qty":1}],
 "say":"Level up! Stronger already!",
 "log":["PULSE won the battle!","PULSE gained 40 XP!","PULSE grew to Lv 5!",
        "Put DATA COOKIE in the bag."],
 "hp":16,"energy":64,"status":"ok","reaction":"...legacy...","pet":{...}}
```

- **win**:
  - Grants XP. If `xp` is missing, it is 5 + 3 × level for each enemy,
    and ×3 for a boss.
  - Mood +8 and a 15-minute "won_fight" glow.
  - Bond +2, or +5 for a boss.
  - `say` is the first that applies: a learned-move line, a level-up
    line, a boss victory line, or a victory line.
- **lose**:
  - Mood −10, a 25-minute "lost_fight" dip, and a 1-hour "fainted" dip
    (−10).
  - If PULSE fainted, it counts as a care mistake, then HP is restored
    to **1** so it can limp home. Resting still heals it fully.
  - `say` is a defeat line, and `mood` is always `"sad"` in a loss reply.
- **flee**: mood −2.
  - **The deck writes its own `Got away safely!` log line.** The
    companion's flee reply has no generic escape line in `log`. Its `log`
    is empty unless loot was added.
  - `say` is PULSE's own reaction (a "got away" personality line). Show it
    after the deck's line.
- `loot` is a list of `{name, kind, power}` objects or names. They go
  into the bag on any result.
- `enemies` is a list of names or enemy objects. Bosses count for the
  bond and XP bonus.

### Optional for now

`pet.combat.moves`, `pet.inventory`, `pet.combat.roster`, and
`pet.combat.run` are **optional**. A deck can run the whole battle with
only stats, action, hit, item, and end:

- `pet.combat.stats` already returns the loadout and the bag.
- `pet.combat.item` already returns the updated bag.
- The deck can keep its own copy of the roster.

### Move menu: `pet.combat.moves {set?, auto?}` (optional)

- Returns `moves` (the loadout of at most 4 moves, the same shape as in
  stats), `known` (every move PULSE knows), and `auto` (a bool).
- `set: ["Blue Pulse", "Chirp"]` sets the loadout, with 1 to 4 known
  moves. `auto: true` goes back to automatic.
- The automatic loadout is the strongest move of each type first, then
  the strongest of the rest. Chirp is kept as the cheap fallback.

### Screens (48 columns, ASCII)

These are suggestions. Main owns the UI. Every row is exactly 48
characters. The layout is classic: the **enemy is top-right** and **PULSE
is bottom-left**.

**Encounter.** Shown after `pet.combat.stats {enemy}`. The log rows are
the reply's `log` and then `say`.

```
+----------------------------------------------+
|                               BUG  Lv2  DATA |
|                        HP [##########] 22/22 |
|                                    (o_o)     |
|     [(^_^)]                                  |
| PULSE  Lv4  NORM                             |
| HP [##########] 20/20                        |
| EN [########..] 80/100                       |
+----------------------------------------------+
| A wild BUG appeared!                         |
| Go, PULSE!                                   |
| Hi bug. Bye bug. Soon.                       |
+----------------------------------------------+
| > FIGHT        BAG                           |
|   PULSE        RUN                           |
+----------------------------------------------+
```

**Move select (FIGHT).** Shows `moves` (at most 4). The bottom row is the
highlighted move's `desc` and its effectiveness against the enemy's type,
which the deck computes from the chart in `pet.combat.roster`.

```
+----------------------------------------------+
|        BUG  Lv2  DATA  HP [##########] 22/22 |
| PULSE  Lv4  HP [##########] 20/20  EN 80     |
+----------------------------------------------+
| > STATIC SNAP   VOLT  EN 8   PWR 14          |
|   ECHO PULSE    NET   EN 10  PWR 18          |
|   BLUE PULSE    ICE   EN 6   PWR 13          |
|   CHIRP         NORM  EN 4   PWR 8           |
+----------------------------------------------+
| A sharp VOLT crackle. Great vs DATA.         |
| Rank 0  Acc 90  vs DATA: super               |
+----------------------------------------------+
| [enter] use   [esc] back                     |
+----------------------------------------------+
```

**Bag.** Shows `bag`. Rows where `usable_in_battle` is false are greyed
out. The bottom row is `desc`.

```
+----------------------------------------------+
|        BUG  Lv2  DATA  HP [##########] 22/22 |
| PULSE  Lv4  HP [##........]  4/20  EN 80     |
+----------------------------------------------+
| > CACHE COOKIE      x3                       |
|   BATTERY           x1                       |
|   MEDICINE          x1                       |
|   EVO CHIP          x1  (not in battle)      |
+----------------------------------------------+
| Restores 20 HP. Crunchy.                     |
+----------------------------------------------+
| [enter] use   [esc] back                     |
+----------------------------------------------+
```

**Log / victory.** For each turn, print the lines in this order:

1. The deck's own enemy line, `FOE used ATTACK!`.
2. The `pet.combat.hit` `log`.
3. The `pet.combat.action` `log`, then its `say`.

At the end, print `pet.combat.end`'s `log`, then its `say`.

```
+----------------------------------------------+
|        BUG  Lv2  DATA  HP [..........]  0/22 |
|                                    (x_x)     |
|     [(^o^)]                                  |
| PULSE  Lv5  HP [#######...] 16/22  EN 64     |
+----------------------------------------------+
| BUG used BITE!                               |
| PULSE took 6 damage.                         |
| PULSE used STATIC SNAP!                      |
| It's super effective!                        |
| BUG took 40 damage.                          |
| PULSE won the battle!                        |
| PULSE gained 40 XP!                          |
| PULSE grew to Lv 5!                          |
| Level up! Stronger already!                  |
+----------------------------------------------+
| [any key] continue                           |
+----------------------------------------------+
```

**PULSE** (the 4th menu item) is up to Main. One suggestion is a status
card: `pet.status` mood, bond, and nickname, with a "cheer" action that
sends `pet.praise` and shows its `say`, or `pet.say {topic:"cheer"}` for
a free line. A praise that lands after a good turn counts as "deserved"
and builds obedience.


## Full stats: `pet.stats` (and `pet.status {full:true}`)

Everything about PULSE on one sheet, for a stats screen.

- `pet.stats {}` returns `pet.stats.ok` with the sheet.
- `pet.status {full:true}` returns the usual `pet` plus the same sheet as
  `full`. Without `full:true`, `pet.status` is unchanged.

**Raw fields**, for drawing XP, HP, EN, and evolution bars:

| Group | Fields |
|---|---|
| Identity | `name`, `nickname`, `species`, `species_id`, `stage`, `type`, `generation` |
| Level and XP | `level`, `xp`, `xp_level_start`, `xp_next_level`, `xp_to_next`, `xp_pct` |
| Age | `age_hours` (simulated), `age_days` (real), `birthday`, `next_birthday` |
| Combat | `hp`, `max_hp`, `energy`, `max_energy` (100), `attack`, `defense`, `speed`, `speed_bonus`, `crit_chance`, `accuracy_mod`, `damage_mult`, `disobey_chance`, `guarding`, `status` |
| Care | `hunger` (protocol need: 0 full, 100 starving), `fullness`, `happiness` (mood including lingering modifiers), `obedience` (0-100), `discipline` (wild / willful / good / loyal), `care_mistakes` |
| Mood | `mood`, `mood_offset`, `modifiers[]`, `bond` (tier), `bond_hearts` (0-5) |
| Status | `sick`, `tantrum` |
| Personality | `quirks{catchphrase, dance, sneeze, tic, fav_idle}` |
| Favorites | `favorites` (only the ones discovered so far), `favorites_hidden` (count) |
| Evolution | `evolution{next_stage, ready, progress_pct, eta_hours, gates{age, level, xp: {have, need, pct}}, choices[]}` |
| Moves | `moves[]` (name, type, power, energy_cost, accuracy, rank, desc), `loadout[]` (names), `loadout_auto` |
| Bag | `bag[]`, `bag_items`, `bag_total` |
| Battle record | `record{wins, losses, flees, bosses, faints, fights}` (lifetime, saved), `rival{name, wins, losses}` |
| Discoveries | `discoveries`, `networks_known`, `treasures`, `trinkets`, `favorite_trinket`, `adventures`, `diary_entries` |

**Pre-formatted rows**

- `pages: [{title, lines}]` comes in this fixed order: **Overview, Combat,
  Care, Traits & Evolution, Moves & Loadout, Bag, Battle Record,
  Discoveries**.
- Titles are 20 characters or fewer. Every line is plain ASCII and 44
  characters or fewer, with spacing kept for alignment.
- `lines` is the same content as one flat list, with `== Title ==` rows as
  page breaks.
- Undiscovered favorites show as `???`.
- In Moves & Loadout, a `*` marks a move that is in the battle loadout.
- Tests check the titles, the page order, and the line limits.

**Stats screen (48 columns).** A suggestion: page through `pages` with the
arrow keys. These two pages were generated from a real sheet.

```
+----------------------------------------------+
| BITSY STATS                    Overview  1/8 |
+----------------------------------------------+
| BITSY  (Aethling)                            |
| Aethling  rookie  FIRE                       |
| Lv 9  XP 400/450                             |
| XP [####......] 50 to go                     |
| HP [########..] 31/38                        |
| EN [######....] 64/100                       |
| Mood playful  Bond bonded                    |
| Status ok                                    |
| Age 12.0d  Born 2026-10-31                   |
+----------------------------------------------+
| [<-/->] page   [esc] back                    |
+----------------------------------------------+
```

```
+----------------------------------------------+
| BITSY STATS          Traits & Evolution  4/8 |
+----------------------------------------------+
| Catchphrase  krrk-tastic!                    |
| Dance        antenna twirl                   |
| Sneeze       tiny                            |
| Fav idle     chase bug                       |
| Fav food     fruity                          |
| Hates food   ???                             |
| Fav signal   ???                             |
| Hates signal ???                             |
| Next: champion  64%                          |
| AGE   [######....] 64%                       |
| LEVEL [#######...] 69%                       |
| XP    [##########] 100%                      |
| Can become: Luminode, Hexwave(locked)        |
+----------------------------------------------+
| [<-/->] page   [esc] back                    |
+----------------------------------------------+
```
