"""dungeon.py — a tiny ASCII roguelike. Loot you find can be gifted to PULSE.

  dungeon            start a run (also `rogue`)
  loot               your loot bag (saved between runs)
  loot gift <n>      send item n to PULSE over the companion link
  loot drop <n>      throw item n away
  loot add <item>    put a loot-table item in the bag without crawling
                     (for scripts, tests and trying gifts: `loot add evo chip`)
  pet gift <n>       same as `loot gift`

In the dungeon:
  arrows / wasd / hjkl   move (walk into a monster to start a battle)
  >                      go down the stairs (deeper = tougher, better loot)
  g                      gift the newest loot to PULSE right now
  i                      show / hide your loot bag
  .                      wait a turn
  q / Esc                leave (your loot is kept)

Battles (config/fx.ini [dungeon] battle = yes, the default): walking
into an enemy, or an enemy walking into you, opens a Pokemon-style
turn-based battle on a 48-column ASCII screen: FIGHT / BAG / PULSE / RUN.
PULSE does the fighting with its real moves; the deck plays the enemy.
The rules, the screen and the keys are in features/battle.py and
docs/BATTLE.md; enemies, faces and odds in cyberdeck/battledata.py and
config/battle.ini. Without PULSE (no link, asleep, fainted, an old
companion) you fight with your own fists, as before.

With battle = no, PULSE fights at your side the old way
(config/fx.ini [dungeon] pulse = yes):
  * at the start the deck asks the companion for PULSE's real combat
    stats (pet.combat.stats): hp, energy, attack, moves with power,
    accuracy and energy cost. The HUD shows PULSE's HP and energy.
  * each round, after you act, PULSE gets a turn if a monster is within
    two steps of you. You pick its move (1-9, space = auto, . = let it
    rest) or, with pulse_moves = auto, the pet picks its own move. The deck sends pet.combat.action and the pet decides
    whether it hits and for how much; the deck applies the damage. A move
    PULSE can't afford becomes the pet's fallback (Chirp or Guard).
  * monsters next to you sometimes go for PULSE instead (pet.combat.hit).
    A fainted PULSE sits out until a repair kit revives it — kits go to
    whichever of you is more hurt (pet.combat.hit {heal}); `pet rest`
    after the run also gets it back on its feet.
  * when you leave or are derezzed, pet.combat.end reports the result
    (win / lose / flee), xp and defeated enemies; PULSE may level up or
    learn a move (those arrive as the usual pet.event pushes).
  * no link, an older companion, or a sleeping PULSE: you play solo, and
    the HUD says why.

Map symbols:  @ you   # wall   . floor   > stairs   * loot   ! repair kit
              b bug  g glitch  s spam  v virus  l lag  w worm  F firewall
              D daemon  K kernel panic (boss of every 5th floor)

Loot kinds match the pet.gift protocol message (docs/PROTOCOL.md):
food lowers hunger, toys cheer PULSE up, batteries restore energy,
trinkets are keepsakes, and the legendary evo chip makes PULSE evolve.

Game is the pure rules (map generation, moving, fighting) and takes a
seeded random generator, so tests can replay exact games. DungeonActivity
just draws it. Map size and starting health: config/fx.ini [dungeon].
"""

from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from cyberdeck.activity import Activity, Row
from cyberdeck.battledata import ODDS, ROSTER, EnemyKind, load_battle_config, roster_entry
from cyberdeck.commands import PET_ACTIONS, CompanionError, run_pet_hooks
from cyberdeck.config import Settings
from cyberdeck.fx import banner
from cyberdeck.petdata import CombatStats, Move, combat_stats
from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_PET_STATUS,
    TYPE_PET_COMBAT_ACTION,
    TYPE_PET_COMBAT_END,
    TYPE_PET_COMBAT_HIT,
    TYPE_PET_COMBAT_STATS,
    TYPE_PET_GIFT,
)

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

# name, kind, power, rarity, weight (how often it drops)
LOOT_TABLE = [
    ("glitch berry", "food", 20, "common", 10),
    ("data cookie", "food", 30, "common", 8),
    ("byte burger", "food", 45, "rare", 3),
    ("bit ball", "toy", 20, "common", 8),
    ("laser pointer", "toy", 35, "rare", 3),
    ("power cell", "battery", 25, "common", 7),
    ("overclock chip", "battery", 50, "rare", 2),
    ("shiny capacitor", "trinket", 10, "common", 6),
    ("evo chip", "evolve", 0, "legendary", 1),
]

# name, glyph, hp, attack, first depth it appears on — the small "solo"
# numbers for fighting with your fists. The full enemy list (types, faces,
# battle stats) is ROSTER in cyberdeck/battledata.py.
MONSTERS = [(k.name, k.glyph, k.solo_hp, k.solo_attack, k.depth) for k in ROSTER if not k.boss]

WALL, FLOOR, STAIRS = "#", ".", ">"
MOVES = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
         "w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0),
         "k": (0, -1), "j": (0, 1), "h": (-1, 0), "l": (1, 0),
         "y": (-1, -1), "u": (1, -1), "b": (-1, 1), "n": (1, 1)}
SIGHT = 7


@dataclass
class Monster:
    name: str
    glyph: str
    x: int
    y: int
    hp: int
    attack: int

    level: int = 1
    kind: EnemyKind | None = None       # its roster entry (type, face, battle stats)
    enemy: Any = None                   # its battle self (battle.Enemy), made on the first battle
    calm: int = 0                       # turns before it may start another battle

    def describe(self) -> dict[str, Any]:
        """The enemy snapshot sent in pet.combat.action / hit (the old crawl)."""
        kind = self.kind or roster_entry(self.name)
        out = {"name": self.name, "level": self.level, "hp": self.hp, "attack": self.attack,
               "defense": 0, "speed": 0}
        if kind is not None:
            out.update(id=kind.id, type=kind.type)
        return out

    def battle_enemy(self) -> Any:
        """This monster's battle stats (kept between battles, so damage sticks)."""
        from cyberdeck.features.battle import Enemy

        if self.enemy is None:
            kind = self.kind or roster_entry(self.name) or ROSTER[0]
            self.enemy = Enemy.spawn(kind, self.level)
        return self.enemy


# --- PULSE as a party member ---------------------------------------------------------

class PulseAlly:
    """PULSE in the dungeon. Every outcome comes from the companion.

    The companion is the authority (companion/docs/DUNGEON_INTEGRATION.md):
    the deck keeps only the last HP / energy it was told, for the HUD.
    `request` is ctx.companion.request (or a fake in tests). Any link
    trouble or "unknown message" reply turns PULSE off for the rest of
    the run (solo mode) instead of breaking the game; `note` says why.
    """

    def __init__(self, request: Callable[..., Any], name: str = "PULSE") -> None:
        self.request = request
        self.name = name
        self.online = False
        self.note = ""
        self.hp = self.max_hp = 0
        self.energy: int | None = None
        self.moves: tuple[Move, ...] = ()
        self.stats: CombatStats | None = None
        self.defeated: list[str] = []      # enemies beaten while PULSE was in the party
        self.ended = False
        self.chart: dict[str, Any] | None = None   # the companion's type chart (asked once)

    # One request; None (and offline) on any trouble.
    def _ask(self, typ: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            reply = self.request(typ, payload)
        except Exception as exc:   # CompanionError, timeouts…
            self._drop(f"lost contact ({exc})")
            return None
        body = reply.payload or {}
        if reply.type == TYPE_ERROR:
            if body.get("code") == "unknown_type":
                self._drop("this companion can't fight yet")
            else:
                self._drop(f"companion said: {body.get('message') or body.get('code')}")
            return None
        return body

    def _drop(self, why: str) -> None:
        self.online = False
        self.note = why

    def join(self) -> bool:
        """Ask for PULSE's combat stats. False = solo run (see note)."""
        self.online = True
        body = self._ask(TYPE_PET_COMBAT_STATS, {})
        if body is None:
            return False
        stats = combat_stats(body)
        if stats is None:
            self._drop("companion sent no combat stats")
            return False
        if stats.asleep:
            self._drop("asleep — `pet sleep off` to bring it next time")
            return False
        self.stats, self.hp, self.max_hp, self.energy = stats, stats.hp, stats.max_hp, stats.energy
        self.moves = stats.moves
        if self.energy is None:
            # Energy lives in the pet object, not the combat sheet.
            status = self._ask(TYPE_PET_STATUS, {})
            if status is None:
                return False
            self._update(status)
        self.note = ""
        return True

    @property
    def fainted(self) -> bool:
        return self.hp <= 0

    @property
    def can_act(self) -> bool:
        return self.online and not self.fainted

    def affordable(self, move: Move) -> bool:
        return self.energy is None or move.energy_cost <= self.energy

    def act(self, move: Move | None, enemy: Monster) -> dict[str, Any] | None:
        """pet.combat.action. move None = let the pet pick. A move it cannot
        afford comes back as a fallback (Chirp or Guard) chosen by the pet."""
        payload: dict[str, Any] = {"enemy": enemy.describe()}
        if move is not None:
            payload["move"] = move.name
        body = self._ask(TYPE_PET_COMBAT_ACTION, payload)
        if body is None:
            return None
        self._update(body)
        return body

    def take_hit(self, damage: int, enemy: Monster) -> dict[str, Any] | None:
        body = self._ask(TYPE_PET_COMBAT_HIT, {"damage": int(damage), "enemy": enemy.describe()})
        if body is None:
            return None
        if body.get("fainted") is True:
            self.hp = 0
        self._update(body)
        return body

    def heal(self, amount: int) -> dict[str, Any] | None:
        """A repair kit for PULSE: pet.combat.hit {heal: N} (revives it too)."""
        body = self._ask(TYPE_PET_COMBAT_HIT, {"heal": int(amount)})
        if body is None:
            return None
        self._update(body)
        return body

    def _update(self, body: dict[str, Any]) -> None:
        """Copy what the pet reported (pet_hp, and the pet object's hp / energy)."""
        pet = body.get("pet") if isinstance(body.get("pet"), dict) else {}
        for value in (pet.get("hp"), body.get("pet_hp")):       # pet_hp wins when both are sent
            if isinstance(value, int) and not isinstance(value, bool):
                self.hp = max(0, value)
        for value in (pet.get("max_hp"),):
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                self.max_hp = value
        energy = pet.get("energy", body.get("energy"))
        if isinstance(energy, (int, float)) and not isinstance(energy, bool):
            self.energy = int(round(energy))
        elif self.energy is not None and isinstance(body.get("energy_spent"), int):
            self.energy = max(0, self.energy - body["energy_spent"])

    def end(self, result: str, xp: int) -> dict[str, Any] | None:
        """pet.combat.end, once. Sent even if PULSE fainted (it still learns)."""
        if self.ended or self.stats is None or not self.online:
            return None
        self.ended = True
        return self._ask(TYPE_PET_COMBAT_END, {"result": result, "xp": int(xp), "enemies": list(self.defeated)})


@dataclass
class Game:
    """One dungeon run: the map, you, monsters, items, and the rules."""

    width: int = 56
    height: int = 17
    max_hp: int = 20
    rng: random.Random = field(default_factory=random.Random)
    on_loot: Callable[[dict[str, Any]], None] | None = None  # called when you pick loot up
    ally: PulseAlly | None = None          # PULSE, when it joined the run
    pulse_moves: str = "ask"               # ask | auto
    pulse_target_chance: float = 0.35
    battles: bool = False                  # walking into an enemy starts a turn-based battle
    roster: tuple[EnemyKind, ...] = ROSTER
    odds: dict[str, float] = field(default_factory=lambda: dict(ODDS))
    on_say: Callable[[str, str], None] | None = None   # battle say lines -> the LCD
    on_battle_end: Callable[[Any], None] | None = None
    ctx: Any = None          # the deck context (battle settings, the STATS page)

    def __post_init__(self) -> None:
        self.depth = 0
        self.awaiting: Monster | None = None   # PULSE's turn: waiting for you to pick a move
        self.last_target: Monster | None = None
        self.hp = self.max_hp
        self.score = 0
        self.over = False
        self.found: list[dict[str, Any]] = []
        self.messages: deque[str] = deque(maxlen=4)
        self.battle: Any = None                # the open battle (features/battle.Battle)
        self.battles_won = 0
        self.battle_xp = 0
        self.new_level()

    # --- map generation ------------------------------------------------------

    def new_level(self) -> None:
        """Carve rooms and corridors for the next depth."""
        self.depth += 1
        for _ in range(20):  # retry until we get at least three rooms
            self.grid = [[WALL] * self.width for _ in range(self.height)]
            rooms: list[tuple[int, int, int, int]] = []
            for _ in range(80):
                w, h = self.rng.randint(4, 10), self.rng.randint(3, 5)
                x, y = self.rng.randint(1, self.width - w - 2), self.rng.randint(1, self.height - h - 2)
                if any(x - 1 < rx + rw and x + w + 1 > rx and y - 1 < ry + rh and y + h + 1 > ry
                       for rx, ry, rw, rh in rooms):
                    continue
                rooms.append((x, y, w, h))
                if len(rooms) >= 8:
                    break
            if len(rooms) >= 3:
                break
        for x, y, w, h in rooms:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.grid[yy][xx] = FLOOR
        centres = [(x + w // 2, y + h // 2) for x, y, w, h in rooms]
        for (x1, y1), (x2, y2) in zip(centres, centres[1:]):
            self._corridor(x1, y1, x2, y2)
        self.px, self.py = centres[0]
        self.stairs = centres[-1]
        self.grid[self.stairs[1]][self.stairs[0]] = STAIRS
        self.seen: set[tuple[int, int]] = set()
        self.monsters: list[Monster] = []
        self.items: dict[tuple[int, int], dict[str, Any]] = {}
        free = [(x, y) for y in range(self.height) for x in range(self.width)
                if self.grid[y][x] == FLOOR and (x, y) != (self.px, self.py)
                and abs(x - self.px) + abs(y - self.py) > 4]
        self.rng.shuffle(free)
        kinds = [k for k in self.roster if not k.boss and k.depth <= self.depth] or [self.roster[0]]
        for _ in range(min(10, 2 + self.depth)):
            if free:
                kind = self.rng.choice(kinds)
                x, y = free.pop()
                bonus = self.depth // 3
                self.monsters.append(Monster(kind.name, kind.glyph, x, y, kind.solo_hp + bonus * 2,
                                             kind.solo_attack + bonus, self.depth, kind))
        # A boss guards the stairs on every Nth floor (kernel panic: every 5th).
        for kind in (k for k in self.roster if k.boss and k.every and self.depth % k.every == 0):
            spots = sorted((p for p in free if p != self.stairs),
                           key=lambda p: abs(p[0] - self.stairs[0]) + abs(p[1] - self.stairs[1]))
            if spots:
                x, y = spots[0]
                free.remove((x, y))
                self.monsters.append(Monster(kind.name, kind.glyph, x, y, kind.solo_hp, kind.solo_attack,
                                             self.depth + 1, kind))
        for _ in range(self.rng.randint(3, 5)):
            if free:
                self.items[free.pop()] = self.roll_loot()
        for _ in range(self.rng.randint(1, 2)):
            if free:
                self.items[free.pop()] = {"name": "repair kit", "kind": "heal", "power": 8}
        self._reveal()
        self.say(f"depth {self.depth}. find the stairs >")

    def _corridor(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """An L-shaped tunnel between two points."""
        if self.rng.random() < 0.5:
            self._hline(x1, x2, y1)
            self._vline(y1, y2, x2)
        else:
            self._vline(y1, y2, x1)
            self._hline(x1, x2, y2)

    def _hline(self, x1: int, x2: int, y: int) -> None:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            self.grid[y][x] = FLOOR

    def _vline(self, y1: int, y2: int, x: int) -> None:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            self.grid[y][x] = FLOOR

    def roll_loot(self) -> dict[str, Any]:
        """Pick a loot item; deeper levels make rare things likelier."""
        weights = [w * (1 + self.depth * 0.5 if r != "common" else 1) for *_x, r, w in LOOT_TABLE]
        name, kind, power, rarity, _w = self.rng.choices(LOOT_TABLE, weights=weights)[0]
        return {"name": name, "kind": kind, "power": power, "rarity": rarity, "depth": self.depth}

    # --- turns -----------------------------------------------------------------

    def say(self, text: str) -> None:
        self.messages.append(text)

    def monster_at(self, x: int, y: int) -> Monster | None:
        return next((m for m in self.monsters if m.x == x and m.y == y), None)

    def walkable(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and self.grid[y][x] != WALL

    def move(self, dx: int, dy: int) -> None:
        """Your turn: walk, fight, or pick things up; then the monsters move."""
        if self.over:
            return
        if self.battle is not None:
            return
        if self.awaiting is not None:
            self.say(f"{self.ally_name}'s turn first: 1-9 pick a move, space = auto, . = rest")
            return
        nx, ny = self.px + dx, self.py + dy
        target = self.monster_at(nx, ny)
        if target is not None and self.battles and self.start_battle(target):
            return                       # the battle is this turn
        if target is not None:
            hit = self.rng.randint(2, 4) + self.depth // 3
            self.last_target = target
            self.damage(target, hit, "you")
        elif not self.walkable(nx, ny):
            self.say("bonk. that's a wall.")
            return  # bumping a wall does not cost a turn
        else:
            self.px, self.py = nx, ny
            self._pick_up()
        self._reveal()
        self.end_round()

    def wait(self) -> None:
        if not self.over and self.awaiting is None and self.battle is None:
            self.end_round()

    # --- turn-based battles (features/battle.py) -----------------------------------

    @property
    def can_battle(self) -> bool:
        return self.battles and self.ally is not None and self.ally.can_act

    def start_battle(self, m: Monster, ambush: bool = False) -> bool:
        """Open a battle with monster m. False = PULSE can't (you fight solo)."""
        if not self.can_battle:
            return False
        from cyberdeck.features.battle import Battle

        battle = Battle(self.ally, m.battle_enemy(), depth=self.depth, rng=self.rng, odds=self.odds,
                        on_say=self.on_say, roll_loot=self.roll_loot)
        battle.ctx = self.ctx
        if not battle.start():
            self.say(f"{self.ally_name} can't battle: {battle.note or self.ally.note}. fists it is!")
            return False
        battle.monster = m
        self.battle = battle
        self.last_target = m
        self.say(f"the {m.name} jumps you!" if ambush else f"battle! {self.ally_name} vs the {m.name}")
        return True

    def close_battle(self) -> None:
        """The battle screen closed: apply the result to the map."""
        b, self.battle = self.battle, None
        if b is None:
            return
        m = getattr(b, "monster", None)
        if not b.over:
            b._finish("flee", [])
        if b.result == "win" and m in self.monsters:
            self.monsters.remove(m)
            self.score += 10 * self.depth * (3 if b.enemy.kind.boss else 1)
            self.battles_won += 1
            self.battle_xp += b.xp_granted
            if self.ally is not None:
                self.ally.defeated.append(m.name)
        elif m is not None:
            m.calm = b.enemy.calm
        self.say(b.summary())
        if self.on_battle_end is not None:
            self.on_battle_end(b)

    def damage(self, target: Monster, amount: int, who: str) -> None:
        """Apply a hit to a monster; remove it (and score) when it drops."""
        target.hp -= amount
        if target.hp > 0:
            if who == "you":
                self.say(f"you hit the {target.name} for {amount}.")
            return
        self.monsters.remove(target)
        self.score += 10 * self.depth
        if self.ally is not None and self.ally.online:
            self.ally.defeated.append(target.name)
        self.say(f"{who} deleted the {target.name}. (+{10 * self.depth})" if who != "you"
                 else f"you delete the {target.name}. (+{10 * self.depth})")

    # --- PULSE's turn ------------------------------------------------------------

    @property
    def ally_name(self) -> str:
        return self.ally.name if self.ally else "PULSE"

    def pet_target(self) -> Monster | None:
        """What PULSE goes for: your last target if close, else the nearest within 2."""
        def near(m: Monster) -> int:
            return max(abs(m.x - self.px), abs(m.y - self.py))
        if self.last_target in self.monsters and near(self.last_target) <= 2:
            return self.last_target
        close = sorted((m for m in self.monsters if near(m) <= 2), key=near)
        return close[0] if close else None

    def end_round(self) -> None:
        """After you act: PULSE's turn (maybe waiting for your pick), then the monsters."""
        if self.over:
            return
        if self.battles:                 # PULSE fights in battles, not alongside you
            self.monsters_turn()
            return
        target = self.pet_target() if self.ally is not None and self.ally.can_act else None
        if target is not None:
            if self.pulse_moves == "ask":
                self.awaiting = target
                return
            self.pet_act(None, target)       # the pet picks its own move
        self.monsters_turn()

    def pet_choose(self, index: int | None) -> None:
        """Your pick for PULSE: a move number (0-based), None = auto, -1 = rest."""
        target, self.awaiting = self.awaiting, None
        if target is None or self.ally is None:
            return
        if index == -1:
            self.say(f"{self.ally_name} holds back this round.")
        else:
            moves = self.ally.moves
            move = moves[index] if index is not None and 0 <= index < len(moves) else None
            if target in self.monsters:
                self.pet_act(move, target)
        self.monsters_turn()

    def pet_act(self, move: Move | None, target: Monster) -> None:
        result = self.ally.act(move, target) if self.ally else None
        if result is None:
            self.say(f"{self.ally_name} drops out: {self.ally.note if self.ally else 'gone'} — you're solo.")
            return
        used = result.get("move") or (move.name if move else "a move")
        reaction = result.get("reaction") or (f"{self.ally_name} uses {used}"
                                              + (f" for {result.get('damage')}" if result.get("hit") else " — miss"))
        if result.get("fallback") and move is not None:
            reaction = f"(can't use {move.name}) {reaction}"
        self.say(str(reaction))
        if result.get("acted") is False:
            return
        damage = result.get("damage")
        if result.get("hit") and isinstance(damage, int) and damage > 0 and target in self.monsters:
            self.damage(target, damage, self.ally_name)

    def descend(self) -> bool:
        if self.over or (self.px, self.py) != self.stairs:
            self.say("no stairs here.")
            return False
        self.score += 25 * self.depth
        self.hp = min(self.max_hp, self.hp + 3)
        self.new_level()
        return True

    def _pick_up(self) -> None:
        item = self.items.pop((self.px, self.py), None)
        if item is None:
            return
        if item["kind"] == "heal":
            ally = self.ally
            # The kit goes to whichever of you is more hurt (it also revives PULSE).
            if (ally is not None and ally.online and ally.max_hp and ally.hp < ally.max_hp
                    and ally.hp / ally.max_hp < self.hp / self.max_hp):
                before = ally.hp
                if ally.heal(item["power"]) is not None:
                    self.say(f"repair kit → {ally.name}: +{ally.hp - before} hp"
                             + (" (back on its feet!)" if before <= 0 < ally.hp else ""))
                    return
            self.hp = min(self.max_hp, self.hp + item["power"])
            self.say(f"repair kit: +{item['power']} hp")
            return
        self.found.append(item)
        self.score += 5 * self.depth
        self.say(f"found {item['name']} ({item['rarity']} {item['kind']})! g = gift to PULSE")
        if self.on_loot:
            self.on_loot(item)

    def monsters_turn(self) -> None:
        for m in list(self.monsters):
            dist = max(abs(m.x - self.px), abs(m.y - self.py))
            if m.calm > 0:               # just fled from (or beat PULSE): it keeps its distance
                m.calm -= 1
                continue
            if dist == 1 and self.can_battle:
                if self.start_battle(m, ambush=True):
                    return
            if dist == 1:
                dmg = self.rng.randint(1, m.attack)
                ally = self.ally
                if ally is not None and ally.can_act and self.rng.random() < self.pulse_target_chance:
                    result = ally.take_hit(dmg, m)
                    if result is not None:
                        self.say(str(result.get("reaction") or f"the {m.name} hits {ally.name}."))
                        if ally.fainted:
                            self.say(f"{ally.name} fainted! a repair kit will bring it back.")
                        continue
                self.hp -= dmg
                self.say(f"the {m.name} hits you for {dmg}.")
                if self.hp <= 0:
                    self.hp = 0
                    self.over = True
                    self.say(f"you were derezzed by a {m.name} on depth {self.depth}.")
                    return
            elif dist <= SIGHT:
                # Step toward you along the bigger gap first.
                sx = (self.px > m.x) - (self.px < m.x)
                sy = (self.py > m.y) - (self.py < m.y)
                for dx, dy in ((sx, 0), (0, sy)) if abs(self.px - m.x) >= abs(self.py - m.y) else ((0, sy), (sx, 0)):
                    nx, ny = m.x + dx, m.y + dy
                    if (dx or dy) and self.walkable(nx, ny) and not self.monster_at(nx, ny) and (nx, ny) != (self.px, self.py):
                        m.x, m.y = nx, ny
                        break

    def _reveal(self) -> None:
        for y in range(self.py - SIGHT, self.py + SIGHT + 1):
            for x in range(self.px - SIGHT, self.px + SIGHT + 1):
                if 0 <= x < self.width and 0 <= y < self.height and (x - self.px) ** 2 + (y - self.py) ** 2 <= SIGHT ** 2:
                    self.seen.add((x, y))

    def finish(self) -> list[str]:
        """End of run: tell the companion (pet.combat.end) and summarise."""
        ally = self.ally
        if self.battle is not None:      # closed mid-battle (ctrl+c): count it as running away
            self.close_battle()
        if self.battles:
            if ally is None or ally.stats is None:
                return []
            if not ally.online and not self.battles_won:
                return [f"{ally.name}: {ally.note or 'could not battle'}"]
            # Every battle already sent its own pet.combat.end.
            return [f"{ally.name}: {self.battles_won} battle{'s' if self.battles_won != 1 else ''} won, "
                    f"+{self.battle_xp} xp"]
        if ally is None or ally.stats is None:
            return []
        result = "lose" if self.over else ("win" if ally.defeated else "flee")
        xp = min(500, sum(3 * self.depth for _ in ally.defeated) + 2 * self.depth)
        body = ally.end(result, xp)
        if body is None:
            return [f"{ally.name}: {ally.note or 'could not report the run'}"]
        granted = body.get("xp_granted", body.get("xp_gained", xp))
        self.end_payload = dict(body, xp_granted=granted)
        # Level-ups and new moves arrive as pet.event pushes, like any other growth.
        return [f"{ally.name}: +{granted} xp" + (f" — {body['reaction']}" if body.get("reaction") else "")]

    def visible(self, x: int, y: int) -> bool:
        return (x - self.px) ** 2 + (y - self.py) ** 2 <= SIGHT ** 2

    # --- drawing ------------------------------------------------------------------

    def tile(self, x: int, y: int, reveal_all: bool = False) -> tuple[str, str]:
        """(character, color role) for one map cell."""
        if (x, y) == (self.px, self.py):
            return "@", "title"
        if not reveal_all and (x, y) not in self.seen:
            return " ", "dim"
        lit = reveal_all or self.visible(x, y)
        if lit:
            m = self.monster_at(x, y)
            if m:
                return m.glyph, "alert"
            item = self.items.get((x, y))
            if item:
                return ("!", "ok") if item["kind"] == "heal" else ("*", "prompt")
        ch = self.grid[y][x]
        if ch == STAIRS:
            return ">", "warn"
        return ch, ("text" if lit else "dim")

    def map_text(self, reveal_all: bool = True) -> list[str]:
        return ["".join(self.tile(x, y, reveal_all)[0] for x in range(self.width)) for y in range(self.height)]


# --- loot bag (saved between runs) -------------------------------------------------

class LootBag:
    """data/loot.json: the items you have found and not gifted yet."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def items(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [d for d in data if isinstance(d, dict) and d.get("name")]
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=1), encoding="utf-8")

    def add(self, item: dict[str, Any]) -> None:
        self._save(self.items() + [item])

    def take(self, ref: str) -> dict[str, Any]:
        """Remove an item by number (1-based) or by name."""
        items = self.items()
        if ref.isdigit() and 1 <= int(ref) <= len(items):
            index = int(ref) - 1
        else:
            index = next((i for i, it in enumerate(items) if it["name"].lower() == ref.lower()), -1)
            if index < 0:
                raise KeyError(f"no loot '{ref}' (type `loot` to list it)")
        item = items.pop(index)
        self._save(items)
        return item


def loot_bag(ctx: "Context") -> LootBag:
    return LootBag(ctx.data_path("loot.json"))


def gift(ctx: "Context", item: dict[str, Any]) -> tuple[bool, list[str]]:
    """Send one item to PULSE with pet.gift. Returns (accepted, lines)."""
    payload_item = {k: item[k] for k in ("name", "kind", "power", "rarity") if k in item}
    try:
        reply = ctx.companion.request(TYPE_PET_GIFT, {"item": payload_item})
    except CompanionError as exc:
        return False, [f"could not reach PULSE: {exc}"]
    payload = reply.payload or {}
    if reply.type == TYPE_ERROR:
        return False, [f"PULSE refused: {payload.get('message', 'error')}"]
    run_pet_hooks(ctx, "gift", payload)
    ctx.services.pop("pet_cache", None)
    effects = payload.get("effects") or {}
    changes = "  ".join(f"{k} {v:+d}" for k, v in effects.items() if isinstance(v, int) and v)
    if effects.get("stage"):
        changes += f"  stage → {effects['stage']}"
    accepted = payload.get("accepted", True) is not False
    lines = [f"[gift] {payload.get('reaction', 'gift delivered' if accepted else 'gift refused')}"]
    if not accepted:
        # e.g. an evolve chip when the pet is already at its last form.
        lines.append("       (not accepted — it stays in your loot bag)")
    elif changes.strip():
        lines.append(f"       {changes.strip()}")
    return accepted, lines


def loot_named(name: str) -> dict[str, Any] | None:
    """A loot-table item by name (case-insensitive; a unique prefix works too)."""
    wanted = name.strip().lower()
    exact = [row for row in LOOT_TABLE if row[0] == wanted]
    rows = exact or [row for row in LOOT_TABLE if row[0].startswith(wanted)]
    if len(rows) != 1:
        return None
    item_name, kind, power, rarity, _w = rows[0]
    return {"name": item_name, "kind": kind, "power": power, "rarity": rarity, "depth": 0}


EMPTY_BAG = ("loot bag empty — go `dungeon` crawling, or `loot add <item>` "
             f"({', '.join(row[0] for row in LOOT_TABLE)})")


def format_item(i: int, item: dict[str, Any]) -> str:
    return f"{i:>3}. {item['name']:<16} {item.get('kind', '?'):<8} power {item.get('power', 0):>3}  {item.get('rarity', '')}"


# --- the full-screen game -----------------------------------------------------------

class DungeonActivity(Activity):
    title = "DUNGEON"
    tick = 0.5

    def __init__(self, ctx: "Context", game: Game, unicode: bool = True) -> None:
        super().__init__()
        self.ctx = ctx
        self.game = game
        self.unicode = unicode
        self.show_bag = False
        self.finished = False
        game.on_loot = loot_bag(ctx).add

    @property
    def subtitle(self) -> str:
        """The hint on the frame (the unified UI shows it bottom right)."""
        return " battle: RUN to flee · esc back " if self.game.battle is not None else " q / esc leave "

    def on_key(self, key: str) -> None:
        g = self.game
        if g.battle is not None:
            # The battle screen has the keys (FIGHT / BAG / PULSE / RUN).
            if g.battle.key(key):
                g.close_battle()
            return
        if key in {"q", "esc"}:
            self.done = True
        elif g.over:
            self.done = True
        elif g.awaiting is not None:
            # PULSE's turn: 1-9 = that move, space/enter = auto, . = hold back.
            if key.isdigit() and key != "0":
                g.pet_choose(int(key) - 1)
            elif key in {" ", "space", "enter"}:
                g.pet_choose(None)
            elif key == ".":
                g.pet_choose(-1)
            else:
                g.say(f"{g.ally_name}'s turn: 1-9 pick a move, space = auto, . = rest")
        elif key in MOVES:
            g.move(*MOVES[key])
        elif key == ">":
            g.descend()
        elif key == ".":
            g.wait()
        elif key == "i":
            self.show_bag = not self.show_bag
        elif key == "g":
            bag = loot_bag(self.ctx)
            items = bag.items()
            if not items:
                g.say("your loot bag is empty.")
                return
            item = bag.take(str(len(items)))
            ok, lines = gift(self.ctx, item)
            if not ok:
                bag.add(item)  # keep it if delivery failed
            g.say(lines[0])
        if self.done and not self.finished:
            self.finished = True
            self.result_lines = [f"dungeon: depth {g.depth}, score {g.score}, "
                                 f"{len(g.found)} loot found this run — `loot` to see your bag"]
            self.result_lines += g.finish()
            payload = getattr(g, "end_payload", None)
            if payload:
                run_pet_hooks(self.ctx, "combat", payload)
                self.ctx.services.pop("pet_cache", None)

    def render(self, width: int, height: int) -> list[Row]:
        g = self.game
        if g.battle is not None:
            from cyberdeck.features.battle import render_battle

            return render_battle(g.battle, width, height)
        if g.over:
            return self._game_over(width, height)
        rows: list[Row] = []
        hp_role = "ok" if g.hp > g.max_hp * 0.5 else "warn" if g.hp > g.max_hp * 0.25 else "alert"
        rows.append([(" HP ", "dim"), (f"{g.hp}/{g.max_hp}", hp_role), (f"   depth {g.depth}   score {g.score}"
                     f"   loot {len(loot_bag(self.ctx).items())}", "text")] + pulse_hud(g))
        view_h = max(3, height - 7)
        view_w = max(10, width - 2)
        # Scroll the map so you stay on screen when it is bigger than the terminal.
        ox = max(0, min(g.px - view_w // 2, g.width - view_w))
        oy = max(0, min(g.py - view_h // 2, g.height - view_h))
        for y in range(oy, min(g.height, oy + view_h)):
            row: Row = [(" ", "dim")]
            for x in range(ox, min(g.width, ox + view_w)):
                ch, role = g.tile(x, y)
                if row and row[-1][1] == role:
                    row[-1] = (row[-1][0] + ch, role)
                else:
                    row.append((ch, role))
            rows.append(row)
        rows.append([])
        for msg in list(g.messages)[-3:]:
            rows.append([(" " + msg, "text")])
        if g.awaiting is not None and g.ally is not None:
            picks = "  ".join(f"{i}:{m.name}({m.energy_cost})" + ("" if g.ally.affordable(m) else "×")
                              for i, m in enumerate(g.ally.moves[:9], 1))
            rows.append([(f" {g.ally_name} vs {g.awaiting.name}: ", "title"), (picks, "prompt"),
                         ("  space auto · . rest", "dim")])
        else:
            rows.append([(" arrows/wasd move · > stairs · g gift newest loot · i bag · q quit", "dim")])
        if self.show_bag:
            bag = loot_bag(self.ctx).items()
            overlay = [[(" LOOT BAG ", "title")]] + [[(" " + format_item(i, it), "prompt")] for i, it in enumerate(bag, 1)]
            if not bag:
                overlay.append([(" (empty)", "dim")])
            rows[1:1 + len(overlay)] = overlay
        return rows[:height]

    def _game_over(self, width: int, height: int) -> list[Row]:
        g = self.game
        rows: list[Row] = [[]] * max(0, height // 2 - 6)
        for line in banner("DEREZZED", self.unicode):
            rows.append([(line.center(width)[:width], "alert")])
        rows.append([])
        rows.append([(f"depth {g.depth}  ·  score {g.score}  ·  {len(g.found)} loot found".center(width)[:width], "title")])
        rows.append([(g.messages[-1].center(width)[:width] if g.messages else "", "text")])
        rows.append([("your loot is safe in the bag. any key to return".center(width)[:width], "dim")])
        return rows[:height]

    def headless(self) -> list[str]:
        return (["dungeon needs the full-screen UI. a taste of depth 1:"] + self.game.map_text()
                + ["battles work here too: `battle bug`, then `battle fight 1`, `battle bag 1`, `battle run`"])


def pulse_hud(g: Game) -> Row:
    """'│ PULSE HP 20/28 EN 64' (or why PULSE is not with you) for the HUD."""
    ally = g.ally
    if ally is None:
        return []
    if not ally.online:
        return [("   │ ", "dim"), (f"{ally.name}: solo — {ally.note}", "warn")]
    if ally.fainted:
        return [("   │ ", "dim"), (f"{ally.name} fainted", "alert")]
    role = "ok" if ally.hp > ally.max_hp * 0.5 else "warn" if ally.hp > ally.max_hp * 0.25 else "alert"
    energy = f" EN {ally.energy}" if ally.energy is not None else ""
    return [("   │ ", "dim"), (f"{ally.name} ", "title"), (f"HP {ally.hp}/{ally.max_hp}", role), (energy, "text")]


def roll_battle_loot(depth: int = 1, rng: random.Random | None = None) -> dict[str, Any]:
    """A loot-table item for PULSE's battle bag (the `battle` command's drops)."""
    rng = rng or random.Random()
    weights = [w * (1 + depth * 0.5 if r != "common" else 1) for *_x, r, w in LOOT_TABLE]
    name, kind, power, rarity, _w = rng.choices(LOOT_TABLE, weights=weights)[0]
    return {"name": name, "kind": kind, "power": power, "rarity": rarity, "depth": depth}


def new_game(ctx: "Context", seed: int | None = None) -> Game:
    s = Settings.load("fx.ini", ctx.config_dir)
    roster, odds = load_battle_config(ctx.config_dir)
    game = Game(width=max(30, s.get_int("dungeon", "width", 56)), height=max(12, s.get_int("dungeon", "height", 17)),
                max_hp=max(5, s.get_int("dungeon", "hp", 20)), rng=random.Random(seed),
                pulse_moves="auto" if s.get_str("dungeon", "pulse_moves", "ask").lower() == "auto" else "ask",
                pulse_target_chance=max(0.0, min(1.0, s.get_float("dungeon", "pulse_target_chance", 0.35))),
                battles=s.get_bool("dungeon", "battle", True), roster=roster or ROSTER, odds=odds)
    game.ctx = ctx
    if game.battles:
        from cyberdeck.features.battle import lcd_hook

        game.on_say = lcd_hook(ctx)

        def battle_over(b: Any) -> None:
            if b.end_body:
                run_pet_hooks(ctx, "battle", dict(b.end_body, enemy=b.enemy.name))
                ctx.services.pop("pet_cache", None)
        game.on_battle_end = battle_over
    if s.get_bool("dungeon", "pulse", True):
        ally = PulseAlly(ctx.companion.request)
        if not ctx.companion.is_linked():
            ally.note = "link down"
        else:
            ally.join()
        game.ally = ally
        game.say(f"{ally.name} joins you! HP {ally.hp}/{ally.max_hp}" if ally.online
                 else f"{ally.name} can't come: {ally.note}. solo run.")
    return game


def register(reg: "Registry") -> None:
    @reg.command("dungeon", "ASCII dungeon crawl; loot can be gifted to PULSE.", aliases=("rogue",))
    def cmd_dungeon(ctx: "Context", args: list[str]) -> list[str]:
        del args
        ctx.activity = DungeonActivity(ctx, new_game(ctx), unicode=ctx.services.get("unicode", True))
        return []

    def loot_cmd(ctx: "Context", args: list[str]) -> list[str]:
        bag = loot_bag(ctx)
        action = args[0].lower() if args else "list"
        if action == "list":
            items = bag.items()
            return [format_item(i, it) for i, it in enumerate(items, 1)] or [EMPTY_BAG]
        if action == "add":
            if len(args) < 2:
                return ["usage: loot add <item>   items: " + ", ".join(row[0] for row in LOOT_TABLE)]
            item = loot_named(" ".join(args[1:]))
            if item is None:
                return [f"no loot called '{' '.join(args[1:])}' — try: " + ", ".join(row[0] for row in LOOT_TABLE)]
            bag.add(item)
            return [f"added {item['name']} ({item['rarity']} {item['kind']}) as #{len(bag.items())} — `pet gift {len(bag.items())}`"]
        if action in {"gift", "drop"}:
            if len(args) < 2:
                return [f"usage: loot {action} <number|name>"]
            if not bag.items():
                return [EMPTY_BAG]
            try:
                item = bag.take(" ".join(args[1:]))
            except KeyError as exc:
                return [str(exc.args[0])]
            if action == "drop":
                return [f"dropped {item['name']}."]
            ok, lines = gift(ctx, item)
            if not ok:
                bag.add(item)
            return lines
        return ["usage: loot [list|gift <n>|drop <n>|add <item>]"]

    def loot_complete(ctx: "Context", args: list[str]) -> list[str]:
        if len(args) == 1 and args[0] in {"gift", "drop"}:
            return [str(i) for i in range(1, len(loot_bag(ctx).items()) + 1)]
        if len(args) == 1 and args[0] == "add":
            return [row[0].split()[0] for row in LOOT_TABLE]
        return []

    reg.command("loot", "Your dungeon loot: list, gift <n> to PULSE, drop <n>, add <item>.",
                usage="loot [list|gift <n>|drop <n>|add <item>]", subcommands=("list", "gift", "drop", "add"),
                completer=loot_complete)(loot_cmd)

    # `pet gift 2` works too.
    PET_ACTIONS["gift"] = lambda ctx, args: loot_cmd(ctx, ["gift", *args[1:]]) if len(args) > 1 else loot_cmd(ctx, ["list"])
