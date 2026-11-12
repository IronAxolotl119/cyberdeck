"""dungeon.py — a tiny ASCII roguelike. Loot you find can be gifted to PULSE.

  dungeon            start a run (also `rogue`)
  loot               your loot bag (saved between runs)
  loot gift <n>      send item n to PULSE over the companion link
  loot drop <n>      throw item n away
  pet gift <n>       same as `loot gift`

In the dungeon:
  arrows / wasd / hjkl   move (walk into a monster to fight it)
  >                      go down the stairs (deeper = tougher, better loot)
  g                      gift the newest loot to PULSE right now
  i                      show / hide your loot bag
  .                      wait a turn
  q / Esc                leave (your loot is kept)

Map symbols:  @ you   # wall   . floor   > stairs   * loot   ! repair kit
              b bug  g glitch  v virus  w worm  D daemon

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
from cyberdeck.commands import PET_ACTIONS, CompanionError, run_pet_hooks
from cyberdeck.config import Settings
from cyberdeck.fx import banner
from cyberdeck.protocol import TYPE_ERROR, TYPE_PET_GIFT

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

# name, glyph, hp, attack, first depth it appears on
MONSTERS = [
    ("bug", "b", 3, 1, 1),
    ("glitch", "g", 5, 2, 1),
    ("virus", "v", 8, 3, 2),
    ("worm", "w", 12, 4, 3),
    ("daemon", "D", 18, 5, 4),
]

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


@dataclass
class Game:
    """One dungeon run: the map, you, monsters, items, and the rules."""

    width: int = 56
    height: int = 17
    max_hp: int = 20
    rng: random.Random = field(default_factory=random.Random)
    on_loot: Callable[[dict[str, Any]], None] | None = None  # called when you pick loot up

    def __post_init__(self) -> None:
        self.depth = 0
        self.hp = self.max_hp
        self.score = 0
        self.over = False
        self.found: list[dict[str, Any]] = []
        self.messages: deque[str] = deque(maxlen=4)
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
        kinds = [m for m in MONSTERS if m[4] <= self.depth]
        for _ in range(min(10, 2 + self.depth)):
            if free:
                name, glyph, hp, atk, _d = self.rng.choice(kinds)
                x, y = free.pop()
                bonus = self.depth // 3
                self.monsters.append(Monster(name, glyph, x, y, hp + bonus * 2, atk + bonus))
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
        nx, ny = self.px + dx, self.py + dy
        target = self.monster_at(nx, ny)
        if target is not None:
            hit = self.rng.randint(2, 4) + self.depth // 3
            target.hp -= hit
            if target.hp <= 0:
                self.monsters.remove(target)
                self.score += 10 * self.depth
                self.say(f"you delete the {target.name}. (+{10 * self.depth})")
            else:
                self.say(f"you hit the {target.name} for {hit}.")
        elif not self.walkable(nx, ny):
            self.say("bonk. that's a wall.")
            return  # bumping a wall does not cost a turn
        else:
            self.px, self.py = nx, ny
            self._pick_up()
        self._reveal()
        self.monsters_turn()

    def wait(self) -> None:
        if not self.over:
            self.monsters_turn()

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
            if dist == 1:
                dmg = self.rng.randint(1, m.attack)
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
        game.on_loot = loot_bag(ctx).add

    def on_key(self, key: str) -> None:
        g = self.game
        if key in {"q", "esc"}:
            self.done = True
        elif g.over:
            self.done = True
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
        if self.done:
            self.result_lines = [f"dungeon: depth {g.depth}, score {g.score}, "
                                 f"{len(g.found)} loot found this run — `loot` to see your bag"]

    def render(self, width: int, height: int) -> list[Row]:
        g = self.game
        if g.over:
            return self._game_over(width, height)
        rows: list[Row] = []
        hp_role = "ok" if g.hp > g.max_hp * 0.5 else "warn" if g.hp > g.max_hp * 0.25 else "alert"
        rows.append([(" HP ", "dim"), (f"{g.hp}/{g.max_hp}", hp_role), (f"   depth {g.depth}   score {g.score}"
                     f"   loot {len(loot_bag(self.ctx).items())}", "text")])
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
        return ["dungeon needs the full-screen UI. a taste of depth 1:"] + self.game.map_text()


def new_game(ctx: "Context", seed: int | None = None) -> Game:
    s = Settings.load("fx.ini", ctx.config_dir)
    return Game(width=max(30, s.get_int("dungeon", "width", 56)), height=max(12, s.get_int("dungeon", "height", 17)),
                max_hp=max(5, s.get_int("dungeon", "hp", 20)), rng=random.Random(seed))


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
            return [format_item(i, it) for i, it in enumerate(items, 1)] or ["loot bag empty — go `dungeon` crawling"]
        if action in {"gift", "drop"}:
            if len(args) < 2:
                return [f"usage: loot {action} <number|name>"]
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
        return ["usage: loot [list|gift <n>|drop <n>]"]

    def loot_complete(ctx: "Context", args: list[str]) -> list[str]:
        if len(args) == 1 and args[0] in {"gift", "drop"}:
            return [str(i) for i in range(1, len(loot_bag(ctx).items()) + 1)]
        return []

    reg.command("loot", "Your dungeon loot: list, gift <n> to PULSE, drop <n>.",
                usage="loot [list|gift <n>|drop <n>]", subcommands=("list", "gift", "drop"),
                completer=loot_complete)(loot_cmd)

    # `pet gift 2` works too.
    PET_ACTIONS["gift"] = lambda ctx, args: loot_cmd(ctx, ["gift", *args[1:]]) if len(args) > 1 else loot_cmd(ctx, ["list"])
