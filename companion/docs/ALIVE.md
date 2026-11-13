# Aliveness: what makes the pet feel alive

The life engine (`pet/life.py`) runs every tick on top of the stats. Its job is
to make the pet do things on its own, have opinions, remember you and its
surroundings, and keep talking without repeating itself. Everything here is
**backward compatible with proto 1**. Old decks keep working: new fields are
extra keys, new messages are opt-in, and life events reach a deck only after it
sends `hello {events: true}`.

- Text: `pet/lines/*.lines` (about 2,000 lines in 218 pools). Run `python3 scripts/line_stats.py` for the counts.
- Engine: `pet/life.py`. Voices: `pet/speech.py`. Generated stories: `pet/stories.py`.
- Dates: `pet/calendar.py`. Sensors: `pet/world.py`. Rival fights: `pet/rival.py`.
- Protocol: `link/alive.py` (new messages) and `link/handler.py` (status fields, events).
- Try it fast: `python3 scripts/alive_demo.py --hours 48 --seed 11`. This runs a fake clock, fake signals and weather, and no network.

## Features

| Feature | What happens |
|---|---|
| Idle life | After 60 s without attention the pet does something every 1–3 min. It might look around, yawn, doze, chase a bug, stretch, hum, dance, count packets, talk to the router, or show off a trinket. After a long quiet spell it stares at the screen. Each pet has a favorite idle. |
| Pet asks | While a deck is linked, it asks for food, rest, play, a scan, attention, or a heal, based on its stats. Answering in time gives bond and a thank-you (`ask_answered`). Ignored asks lower mood (`ask_ignored`), and 3 in a row cause a tantrum. |
| Wants and dislikes | Each hatchling gets a secret favorite and hated food flavor (sweet, spicy, salty, sour, crunchy, electric, cheesy, fruity, minty, or umami) and a favorite and hated signal kind. Feeding reveals them (`preference_learned`). A liked food gives extra mood; a hated one gives a frown. |
| Lingering moods | Wins, losses, praise, scolds, rain, and gifts add modifiers that fade over minutes to hours. They count toward mood everywhere, including the face, combat, and `mood_word`. |
| Bond | Bond is a hidden 0–100 value shown as a tier: stranger, friendly, close, bonded, or soulmate. Care, answered asks, and play raise it. Undeserved scolds and neglect lower it. Bond unlocks gifts, crits, cheers, and the love face. |
| Absence greetings | The greeting depends on how long you were away: first, quick, short, medium, long, very_long, or sulk. There are also morning, night, on_time, late, and a reunion after a link drop. |
| Network memory | It counts how often it sees each network and guesses what each one is (home, work, the café, school). It greets familiar networks (`recognize`), treats very familiar ones as turf, notices strangers on its turf (`suspicious`), and gets nervous in a crowd of new names (`new_place`). |
| Diary | At midnight it writes a diary entry about the day: meals, games, wins, treasure, adventures, weather, visits, and its mood. It keeps 30 entries. Read them with `pet.diary`. |
| Real-world awareness | It knows the local clock (time of day, late night), the CPU temperature (from `/sys/class/thermal`, or a simulation), how many signals are around (busy or empty), and optionally the weather. |
| Deck activity and jealousy | The deck reports app launches, dungeon runs, and scans with `pet.activity`, and the pet comments on them. If you keep using other apps without paying it attention, it gets jealous. |
| Sickness and cure | Overfeeding (3 extra meals in 20 min) causes a `glitch`. Neglect can cause `corrupted`. A sick pet won't play and loses mood until healed with `pet.heal` or a `medicine` gift. A glitch also clears by itself after 2 h. |
| Quirks | Hiccups and sneezes (4 sneeze styles), plus a personal dance, catchphrase, and verbal tic. They happen more often when stats are low. |
| Mood-driven combat | An angry pet hits 25% harder but less accurately. A tired pet misses more. An angry and tired pet may refuse to move. Low obedience can make it disobey. A high bond adds accuracy, a crit chance, and cheers. Default pets are neutral, so the old formulas are unchanged. |
| Dreams | While asleep it dreams about real memories: discoveries, fights, food, adventures, and you. |
| Talks back | Each stage speaks differently. Babies babble (*me hungwy! pii!*). In-training pets talk in short, simple sentences. Rookies sound like kids, champions like teens, and ultimate and mega pets sound wise. Mood openers, time of day (hyper in the afternoon, rambling late at night), and its tic and catchphrase change the delivery. |
| Boredom | Feeding the same food or playing the same game repeatedly (within 30 min) gives 100%, 100%, 75%, 50%, then 25%, and the 6th time it refuses. |
| Time-of-day habits | It's sleepy after 23:00 and hyper in the afternoon. At night it can doze off on its own. |
| Tantrums | Three scolds in 10 min or three ignored asks start a 60 s tantrum. During a tantrum, feed, play, rest, sleep, rename, praise, and combat are refused with `reason: "tantrum"`. A toy gift ends a tantrum early. |
| Nicknames | `pet.rename`. With a close bond it's happy about it; with a low bond it's confused. Renaming again within 10 min gets a teasing reply. |
| Learns your schedule | It records which hours you connect. Once a habit forms (4 or more sessions), it greets you with `on_time` or `late` and waits by the screen near that hour. |
| Praise and scold | These shape obedience: wild, willful, good, or loyal. Praise right after good behavior counts more, and spamming it does nothing. A scold is "deserved" only right after misbehavior; otherwise it hurts bond. |
| Gifts back | At bond `bonded` or higher, it sometimes brings you a trinket, a fun fact about your networks, or (rarely) an evo chip (`gift_back`). |
| Separation and reunion | When the last deck disconnects it notices and gets lonely after a minute. The next hello gives a `reunion` with a mood boost. |
| Wandering adventures | When left alone with enough energy, it may wander off for 3–8 min to a generated place. Its stats decide the outcome (great, good, meh, or bad). It comes back with a story, XP, and sometimes loot. Any care action calls it home early. |
| Treasure hunting | Rare signals (hidden or blank names, `??` names, very strong signals, or interesting RFID tags) become `treasure`. Each one gives +6 XP and an "echo" trinket for the shelf. |
| Territory | Networks seen 30 or more times become turf. See network memory above. |
| Collecting shelf | Up to 30 trinkets from gifts, treasure, adventures, and rivals. It has a favorite and shows it off with `pet.shelf {show_off:true}`. |
| Birthdays and hatchdays | On its yearly birthday it gets a party, a costume, +5 max HP, and +1 to attack, defense, and speed. Each monthly hatchday gets a smaller party. |
| Holidays and costumes | 15 holidays, including New Year, Valentine's, Pi Day, St Patrick's, April Fools, Earth Day, May the 4th, the solstice, July 4th, Programmers' Day, Halloween, Thanksgiving, Christmas, Leap Day, and Friday the 13th. Each has a costume shown on the face and in `pet.status.costume`. |
| Weather (optional) | `--weather LAT,LON` fetches Open-Meteo every 30 min (sunny, cloudy, fog, rain, snow, or storm), with a matching mood nudge and comment. With no network it does nothing. |
| Rival | Each pet has a named rival that scales with the pet's level and shows up every few hours. You can fight it, ignore it, or `challenge` it. The companion resolves the fight using your real moves, and your pet yields at 1 HP instead of fainting. The rival keeps a win/loss record. |
| Egg legacy | A mega form can lay an egg that passes on one move and one trait (catchphrase, dance, sneeze, or favorite food). Hatching it (with confirm) starts generation N+1. The new pet keeps its networks, schedule, diary, rival, one heirloom trinket, and lineage, plus a small head start on bond. |

## Text pools and the no-repeat picker

`pet/lines/*.lines` is plain text with sections:

```
[feed.like]
yesss {flavor} and crunchy!
oh, {food}. you remembered.
```

- Keys fall back from specific to general: `chatter.happy` → `chatter`.
- Slots like `{name}`, `{food}`, `{network}`, `{place}`, `{hours}`, and `{rival}` are filled from the pet's real state. Any slot that's missing gets a safe default, so text never crashes.
- `LinePicker` remembers which lines each pool used recently. It won't reuse a line until 80% of that pool has been used. That history is saved in the save file, so it survives restarts.
- Stage voice, mood openers, tier closers, time-of-day flavor, the tic, and the catchphrase are applied on top. The same line therefore reads differently at different times.
- Diary entries and adventure stories are built from fragment pools. That gives about 7×10⁸ possible diaries and about 4×10⁸ adventures, plus 900 trinket names, 300 rival names, and 432 catchphrases.

To add lines, put new lines under any `[section]`, or add a new section. No code changes are needed.

## Protocol additions (all `{type,id,payload}`, proto 1)

### New `pet.status.pet` fields

| Field | Type | Meaning |
|---|---|---|
| `nickname` | string or null | Set by `pet.rename` |
| `display_name` | string | The nickname, or else the species name |
| `bond` | string | stranger, friendly, close, bonded, or soulmate |
| `sick` | string or null | `glitch` or `corrupted` |
| `tantrum` | bool | |
| `away` | bool | Out on an adventure |
| `costume` | string or null | Holiday key, or `party` |
| `speech` | string or null | The current speech bubble, if any |
| `ask` | string or null | The current request: food, rest, play, scan, investigate, attention, or heal |
| `life` | object | The same as `pet.life` (below) |

`pet.avatar.ok` also carries `anim`, `costume`, and `bubble`.

### Changed replies (extra keys only)

- `pet.feed.ok`: `reaction` (normal, like, hate, bored, refuse, stuffed), `flavor`, and `say`. When the pet refuses because of boredom or a tantrum, `fed: false` and `reason` is `bored` or `tantrum`.
- `pet.play.ok`: `say`. When it refuses, `reason` is `bored`, `sick`, or `tantrum`.
- `pet.rest.ok` and `pet.sleep.ok`: `say`. During a tantrum they are refused with `reason: "tantrum"`.
- `pet.gift`: the new kind `medicine` heals. A toy also ends a tantrum. Replies include `say`.
- `pet.combat.action.ok`: `crit`, `disobeyed`, `mood_effect`, an optional `cheer`, and `reason` can be `tantrum` or `refused`.
- `pet.discovery`: rare signals and interesting RFID tags also count as treasure, which sends the `treasure` event and adds a shelf trinket. A discovery also answers a pending `scan` or `investigate` ask.

### New messages

| Request | Payload | Reply highlights |
|---|---|---|
| `pet.rename` | `{name}` (≤16 printable characters) | `accepted, nickname, old, reaction, say` |
| `pet.praise` | `{}` | `accepted, effect (deserved/normal/spam/low_bond), obedience, say` |
| `pet.scold` | `{}` | `deserved, tantrum, obedience, say` |
| `pet.heal` | `{}` | `healed, was, say` |
| `pet.diary` | `{limit?=7 (1–30), today?:bool}` | `entries[{date,text}], count, today?` (today is a draft for the current day) |
| `pet.shelf` | `{show_off?:bool}` | `items[], count, favorite, say?` |
| `pet.activity` | `{kind, name?, result?}`, where kind is `dungeon_start`, `dungeon`, `app`, `scan`, `typing`, … and result is `win`, `lose`, or `flee` | `say`; also `cheer` on a dungeon start when bond ≥ close |
| `pet.say` | `{topic}`: chatter, funfact, greeting, dream, ramble, cheer, or ask | `say` |
| `pet.rival` | `{action}`: status, fight, ignore, or challenge | `result, retort, log[], xp, trinket, record{wins,losses}, say` |
| `pet.adventure` | `{action}`: status, go, or recall | `place, return_in`; on recall, `story, outcome, xp, trinket` |
| `pet.egg` | `{action: status/lay/hatch, move?, trait?, confirm?}` | Status: `egg, can_lay, generation, lineage`. Hatch needs `confirm:true`. |
| `pet.life` | `{}` | `life` (bond, obedience, modifiers, sick, tantrum, ask, likes, quirks, time_of_day, temp, weather, costume, place, turf, shelf_count, adventure, rival, usual_time, egg, link) |
| `pet.reset` | `{confirm:true}` | `reset, pet`. Without confirm it returns `bad_payload`. |
| `pet.stats` | `{}` (or `pet.status {full:true}`, which adds the sheet as `full`) | Every stat: raw fields for bars, plus `pages[{title,lines}]` in the order Overview, Combat, Care, Traits & Evolution, Moves & Loadout, Bag, Battle Record, Discoveries. Lines are ASCII and 44 characters or fewer, and undiscovered favorites stay hidden. See DUNGEON_INTEGRATION.md, "Full stats". |
| `pet.dungeons` | `{}` | `dungeons[]` (id, name, kind, type, rare, difficulty, in_range, home, depth_record, checkpoint, last_seen, unlocked_at, ...), ordered home first, then in range, then by depth. See DUNGEON_INTEGRATION.md, "Network dungeons". |
| `pet.dungeon.enter` | `{id, floor?}` | `seed`, `floor`, `layout`, `theme`, `enemies`, `boss`, `checkpoint`, `xp_mult`, `loot_mult`, `say`. `floor` defaults to the last checkpoint. |
| `pet.dungeon.floor` | `{id, floor, result: cleared/stairs/fainted/fled, xp?, enemies?, loot?, boss_beaten?}` | `xp`, `loot`, `bond`, `depth_record`, `new_record`, `checkpoint_reached`, `next` (`null` when the run ends), `say`, `log`. |
| `pet.dungeon.leave` | `{id, floor?}` | `depth_record`, `checkpoint`, `say`. |

Each reply's type is the request type plus `.ok`. A bad payload gives `error {code:"bad_payload"}`.

### New `pet.event` kinds

These are sent only after `hello {events:true}`. Every one carries `say`, a ready-to-show line, so a deck can simply display it.

| Group | Event kinds |
|---|---|
| Talking and greeting | `speech`, `greeting {kind}`, `reunion`, `lonely` |
| Idle life and quirks | `idle {action}`, `dream {about}`, `sneeze`, `hiccup` |
| Asks | `ask {kind, expires_in}`, `ask_answered`, `ask_ignored` |
| Sickness and behavior | `sick {kind}`, `healed`, `tantrum {reason, seconds}`, `tantrum_over` |
| Learning and gifts | `preference_learned`, `gift_back {kind, item?, fact?}` |
| Adventures and treasure | `adventure_start {place, return_in}`, `adventure_return {place, outcome, story, xp, trinket}`, `treasure {name, trinket, xp}` |
| Places and signals | `recognize {network, place, visits}`, `suspicious`, `new_place`, `busy_place`, `empty_place` |
| Environment and time of day | `hot`, `cold {temp_c}`, `sleepy`, `hyper`, `weather {condition}`, `jealous {app}` |
| Rival | `rival_appears {rival, level, taunt}`, `rival_result`, `rival_leaves` |
| Calendar | `birthday {years, boost}`, `hatchday {months}`, `holiday {holiday, label, costume}`, `diary {date, text}` |
| Eggs | `egg_laid`, `egg_hatched` |

Life events are sent on `tick` only, never inside a request's reply. That keeps replies and event order the same as before.

## Suggested deck-side changes (Cyberdeck Main)

1. Show any `say` (from replies or events) as a speech bubble next to the face, and add events with `say` to the popup log.
2. Send `pet.activity` when a dungeon starts and ends (`dungeon_start`, then `dungeon` with result win, lose, or flee), on app launches (`app`, name), and on scans. Show `cheer` when it's present.
3. Add buttons or commands: rename, praise, scold, heal, diary, shelf, adventure go/recall, rival fight/ignore/challenge, egg, and reset (with confirm).
4. Show `pet.status` `costume`, `sick`, `tantrum`, `away`, `bond`, and `ask`. For example, show an ask icon and make the matching action answer it.
5. Add `medicine` as a loot kind so `pet gift` can cure sickness.
6. When the reply reason is `tantrum`, `bored`, or `refused`, show the `say` line instead of an error.

## Tunables (top of `pet/life.py`)

| Constant | Default | Meaning |
|---|---|---|
| `IDLE_AFTER` / `IDLE_EVERY` | 60 s / 60–180 s | When idle actions start, and how often they repeat |
| `CHATTER_EVERY` | 150–360 s | Unprompted chatter |
| `DREAM_EVERY` | 120–300 s | Dreams while asleep |
| `ASK_COOLDOWN` / `ASK_TIMEOUT` | 900 s / 240 s | Asks (only while linked) |
| `IGNORED_FOR_TANTRUM` / `SCOLDS_FOR_TANTRUM` / `TANTRUM_SECONDS` | 3 / 3 / 60 | Tantrums |
| `ADVENTURE_IDLE` / `ADVENTURE_LEN` | 300 s / 180–480 s | Adventures (1800 s cooldown afterwards) |
| `RIVAL_FIRST` / `RIVAL_EVERY` / `RIVAL_WAIT` | 1–2 h / 3–6 h / 300 s | Rival visits |
| `BORED_WINDOW` | 1800 s | Boredom memory |
| `OVERFEED_WINDOW` / `OVERFEED_FOR_SICK` | 1200 s / 3 | Overfeeding glitch |
| `KNOWN_AT` / `TURF_AT` | 3 / 30 sightings | Network recognition |
| `HOT_C` / `COLD_C` (world.py) | 70 / 30 °C | CPU temperature reactions |

`--life-pace 4` makes idle life, chatter, adventures, and rivals 4× more frequent for demos.

## Saves

The save format is now version 2: `creature.life` holds the life state and the picker history. Version 1 saves still load, and their missing life fields get fresh, seeded defaults. `--reset` backs up the `--save` file to `.bak` and starts a new pet.
