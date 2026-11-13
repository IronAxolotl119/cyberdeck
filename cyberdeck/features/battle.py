"""battle.py — Pokemon-style turn-based battles for the dungeon.

In plain English: when you walk into an enemy in the dungeon (or an
enemy walks into you), the map gives way to a battle screen. PULSE, the
companion pet, does the fighting. Each turn you pick one of four things:

  FIGHT   pick one of PULSE's (up to 4) moves       -> pet.combat.action
  BAG     use an item from PULSE's battle bag       -> pet.combat.item
  PULSE   look at PULSE's mood and status (free)
  RUN     try to get away (the deck rolls the odds) -> pet.combat.end flee

Then the enemy takes its turn: the deck picks its attack and base damage
and the companion works out what PULSE actually takes (pet.combat.hit).
Win and PULSE gets XP, maybe loot for its bag, and bond (pet.combat.end).
If PULSE faints, you are sent back to the dungeon and PULSE is sad.

Who owns what (companion/docs/DUNGEON_INTEGRATION.md "Battle protocol"):
  * the deck: the enemy (HP, its AI, base damage), the flee roll, the screen
  * the companion: PULSE (HP, EN, moves, types, the bag, mood, every line it says)

The screen is plain ASCII and exactly 48 columns wide (SCREEN_W):

  +----------------------------------------------+
  |                  BUG  Lv2  DATA              |   enemy, top right
  |                  HP [##########]  22/22      |
  |                  (o_o)                       |
  |  [(^_^)]                                     |   PULSE, bottom left
  |  PULSE  Lv4  CHIRPLET  NORM                  |
  |  HP [##########]  20/20                      |
  |  EN [########..]  80/100                     |
  +----------------------------------------------+
  | A wild BUG appeared!                         |   battle log
  +----------------------------------------------+
  | PULSE: Hi bug. Bye bug. Soon.                |   what PULSE says (own row)
  +----------------------------------------------+
  | > FIGHT        BAG                           |   2x2 menu
  |   PULSE        RUN                           |
  +----------------------------------------------+
  | arrows move  enter pick  f b p r             |
  +----------------------------------------------+

Older companions: any field they leave out falls back to a sensible
default (moves are NORM, everything is x1, no bag, deck-written log
lines), so a battle still works.

Numbers (enemy stats, faces, flee odds, XP, loot chance) live in
cyberdeck/battledata.py and config/battle.ini. Docs: docs/BATTLE.md.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from cyberdeck.activity import Activity, Row
from cyberdeck.battledata import (
    CHART,
    MAX_LOADOUT,
    ODDS,
    ROSTER,
    SAY_MAX,
    SCREEN_W,
    EnemyKind,
    chart_from_payload,
    clamp_say,
    effectiveness,
    effectiveness_label,
    flee_chance,
    load_battle_config,
    norm_type,
    roster_entry,
    win_xp,
)
from cyberdeck.protocol import (
    TYPE_ERROR,
    TYPE_PET_COMBAT_ACTION,
    TYPE_PET_COMBAT_END,
    TYPE_PET_COMBAT_HIT,
    TYPE_PET_COMBAT_ITEM,
    TYPE_PET_COMBAT_ROSTER,
    TYPE_PET_COMBAT_RUN,
    TYPE_PET_COMBAT_STATS,
)

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry

MENU = ("FIGHT", "BAG", "PULSE", "RUN")       # 2x2: FIGHT BAG / PULSE RUN
INNER = SCREEN_W - 4                           # 44 columns between "| " and " |"
LOG_ROWS = 4                                   # battle-log rows (fewer on a short screen)
PREFIX_MAX = SAY_MAX - len("PULSE: ")          # 37: longest say that still fits a "PULSE: " prefix


# ---------------------------------------------------------------------------
# The enemy in a battle (the deck owns its HP and AI)
# ---------------------------------------------------------------------------
@dataclass
class Enemy:
    kind: EnemyKind
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    calm: int = 0          # turns left before it may start another battle

    @classmethod
    def spawn(cls, kind: EnemyKind, level: int) -> "Enemy":
        s = kind.stats(level)
        return cls(kind, max(1, level), s["hp"], s["hp"], s["attack"], s["defense"], s["speed"])

    @property
    def name(self) -> str:
        return self.kind.name

    @property
    def type(self) -> str:
        return self.kind.type

    def describe(self) -> dict[str, Any]:
        """The enemy object sent in pet.combat.stats / action / hit / run."""
        return {"id": self.kind.id, "name": self.kind.name, "type": self.kind.type, "level": self.level,
                "hp": max(0, self.hp), "max_hp": self.max_hp, "attack": self.attack, "defense": self.defense,
                "speed": self.speed, "face": self.kind.face, "boss": self.kind.boss}


def enemy_for(name: str, level: int = 1, roster: tuple[EnemyKind, ...] = ROSTER) -> Enemy | None:
    kind = roster_entry(name, roster)
    return Enemy.spawn(kind, level) if kind else None


# ---------------------------------------------------------------------------
# One battle: the rules and the turn flow (no drawing in here)
# ---------------------------------------------------------------------------
@dataclass
class Battle:
    """PULSE vs one enemy. `ally` is the dungeon's PulseAlly (it holds the link)."""

    ally: Any
    enemy: Enemy
    depth: int = 1
    rng: random.Random = field(default_factory=random.Random)
    odds: dict[str, float] = field(default_factory=lambda: dict(ODDS))
    on_say: Callable[[str, str], None] | None = None      # (say line, enemy name) -> LCD
    roll_loot: Callable[[], dict[str, Any] | None] | None = None

    def __post_init__(self) -> None:
        self.phase = "log"            # log | menu | fight | bag | pulse | end
        self.after = "menu"           # where the log pages lead
        self.pages: list[list[str]] = []
        self.shown: list[str] = []    # the log lines on screen right now
        self.history: list[str] = []  # every log line this battle (headless prints these)
        self.say = ""
        self.cursor = {"menu": 0, "fight": 0, "bag": 0}
        self.turn = 0
        self.result: str | None = None        # win | lose | flee | abort
        self.end_body: dict[str, Any] = {}
        self.loot: list[dict[str, Any]] = []
        self.xp = 0
        self.pet: dict[str, Any] = {}
        self.moves: list[dict[str, Any]] = []
        self.bag: list[dict[str, Any]] | None = None     # None = this companion has no bag
        self.flee_hint: float | None = None
        self.run_lines: dict[str, str] | None = None
        self.log_rows = LOG_ROWS
        self.note = ""

    # --- talking to the companion ----------------------------------------------------
    def _soft(self, typ: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(reply body, error payload). A dead link drops PULSE (ally.note says why)."""
        try:
            reply = self.ally.request(typ, payload)
        except Exception as exc:   # CompanionError, timeouts…
            self.ally._drop(f"lost contact ({exc})")
            return None, {"code": "link", "message": str(exc)}
        body = reply.payload or {}
        if reply.type == TYPE_ERROR:
            return None, body
        return body, None

    @property
    def chart(self) -> dict[str, dict[str, float]]:
        """The companion's chart (pet.combat.roster, asked once per run), else ours."""
        if getattr(self.ally, "chart", None) is None:
            body, _err = self._soft(TYPE_PET_COMBAT_ROSTER, {}) if self.ally.online else (None, None)
            self.ally.chart = chart_from_payload(body) or {}
        return self.ally.chart or CHART

    # --- start -------------------------------------------------------------------------
    def start(self) -> bool:
        """pet.combat.stats {enemy}. False = PULSE can't battle (fight solo instead)."""
        body, err = self._soft(TYPE_PET_COMBAT_STATS, {"enemy": self.enemy.describe()})
        if body is None:
            if err and err.get("code") == "unknown_type":
                self.ally._drop("this companion can't fight yet")
            elif err and err.get("code") != "link":
                self.ally._drop(f"companion said: {err.get('message') or err.get('code')}")
            return False
        self._read_sheet(body)
        if body.get("asleep") is True or self.pet.get("status") == "asleep":
            self.note = "PULSE is asleep"
            return False
        if int(self.pet.get("hp") or 0) <= 0:
            self.note = "PULSE has fainted"
            return False
        if isinstance(body.get("flee_chance"), (int, float)):
            self.flee_hint = float(body["flee_chance"])
        foe = self.enemy.name.upper()
        lines = _lines(body.get("log")) or [f"A wild {foe} appeared!", f"Go, {self.pet_name}!"]
        self._say(body.get("say"))
        self._queue(lines, "menu")
        return True

    def _read_sheet(self, body: dict[str, Any]) -> None:
        pet = body.get("pet") if isinstance(body.get("pet"), dict) else {}
        card = {"name": self.ally.name, "species": "", "type": "NORM", "level": 1, "hp": 0, "max_hp": 1,
                "energy": None, "max_energy": 100, "mood": "", "status": "ok", "face": "(^_^)"}
        for key in card:
            if key in body and not isinstance(body.get(key), (dict, list)):
                card[key] = body[key]
        for key in card:
            if pet.get(key) is not None:
                card[key] = pet[key]
        card["type"] = norm_type(body.get("type") or card.get("type"))
        self.pet = card
        self.ally.hp, self.ally.max_hp = int(card["hp"] or 0), max(1, int(card["max_hp"] or 1))
        if isinstance(card.get("energy"), (int, float)):
            self.ally.energy = int(card["energy"])
        moves = []
        for raw in (body.get("moves") or [])[:MAX_LOADOUT]:
            if isinstance(raw, str):
                raw = {"name": raw}
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                continue
            acc = raw.get("accuracy", 100)
            acc = int(acc * 100) if isinstance(acc, float) and acc <= 1 else int(acc or 100)
            moves.append({"name": str(raw["name"]).strip(), "type": norm_type(raw.get("type")),
                          "power": int(raw.get("power") or 0), "energy_cost": int(raw.get("energy_cost") or 0),
                          "accuracy": acc, "rank": int(raw.get("rank") or 0),
                          "desc": clamp_say(raw.get("desc") or f"{raw['name']}.")})
        self.moves = moves
        if isinstance(body.get("bag"), list):
            self.bag = [b for b in body["bag"] if isinstance(b, dict) and b.get("qty", 1)]

    def _update_pet(self, body: dict[str, Any]) -> None:
        """Copy PULSE's latest HP / EN / status / mood from any reply."""
        pet = body.get("pet") if isinstance(body.get("pet"), dict) else {}
        for key in ("hp", "pet_hp"):
            if isinstance(body.get(key), int) and not isinstance(body.get(key), bool):
                self.pet["hp"] = max(0, body[key])
        if "hp" not in body and "pet_hp" not in body and isinstance(pet.get("hp"), int):
            self.pet["hp"] = max(0, pet["hp"])
        if isinstance(pet.get("max_hp"), int) and pet["max_hp"] > 0:
            self.pet["max_hp"] = pet["max_hp"]
        energy = body.get("energy", pet.get("energy"))
        if isinstance(energy, (int, float)) and not isinstance(energy, bool):
            self.pet["energy"] = int(round(energy))
        elif self.pet.get("energy") is not None and isinstance(body.get("energy_spent"), int):
            self.pet["energy"] = max(0, int(self.pet["energy"]) - body["energy_spent"])
        for key in ("status", "mood", "level"):
            value = body.get(key) if key in ("status",) else pet.get(key)
            if isinstance(value, (str, int)) and value != "":
                self.pet[key] = value
        if isinstance(body.get("bag"), list):
            self.bag = [b for b in body["bag"] if isinstance(b, dict) and b.get("qty", 1)]
        self.ally.hp, self.ally.max_hp = int(self.pet["hp"]), int(self.pet["max_hp"])
        if self.pet.get("energy") is not None:
            self.ally.energy = int(self.pet["energy"])

    @property
    def pet_name(self) -> str:
        return str(self.pet.get("name") or self.ally.name).upper()

    def _say(self, text: Any) -> None:
        line = clamp_say(text)
        if line:
            self.say = line
            if self.on_say:
                try:
                    self.on_say(line, self.enemy.name)
                except Exception:  # noqa: BLE001 — the LCD must never break a battle
                    pass

    def _queue(self, lines: list[str], after: str) -> None:
        """Show these log lines a page at a time, then go to `after`."""
        lines = [clamp_say(x) for x in lines if clamp_say(x)]
        self.history += lines
        size = max(1, self.log_rows)
        self.pages = [lines[i:i + size] for i in range(0, len(lines), size)] or [[]]
        self.shown = self.pages.pop(0)
        self.phase, self.after = "log", after

    def advance(self) -> None:
        """Any key on a log page: the next page, or on to the next screen."""
        if self.pages:
            self.shown = self.pages.pop(0)
            return
        self.phase = self.after

    def drain(self) -> None:
        """Skip through every waiting log page (headless and scripts)."""
        while self.phase == "log":
            self.advance()

    @property
    def over(self) -> bool:
        return self.result is not None

    # --- FIGHT ---------------------------------------------------------------------------
    def affordable(self, move: dict[str, Any]) -> bool:
        energy = self.pet.get("energy")
        return energy is None or int(move.get("energy_cost") or 0) <= int(energy)

    def fight(self, index: int | None) -> None:
        """Use move `index` (0-based) from the FIGHT menu; None = let PULSE pick."""
        if self.over:
            return
        self.turn += 1
        payload: dict[str, Any] = {"enemy": self.enemy.describe(), "turn": self.turn}
        move = self.moves[index] if index is not None and 0 <= index < len(self.moves) else None
        if move is not None:
            payload["move"] = move["name"]
        body, err = self._soft(TYPE_PET_COMBAT_ACTION, payload)
        if body is None:
            return self._abort(err)
        self._update_pet(body)
        used = str(body.get("move_used") or body.get("move") or (move or {}).get("name") or "a move")
        lines = _lines(body.get("log"))
        if not lines:   # an older companion: write the log ourselves
            if body.get("acted") is False:
                lines = [f"{self.pet_name} won't fight! ({body.get('reason', 'no reason')})"]
            elif body.get("hit") and int(body.get("damage") or 0) > 0:
                lines = [f"{self.pet_name} used {used.upper()}!",
                         f"{self.enemy.name.upper()} took {int(body['damage'])} damage."]
            elif body.get("effect") == "guard":
                lines = [f"{self.pet_name} used {used.upper()}!", f"{self.pet_name} is guarding!"]
            else:
                lines = [f"{self.pet_name} used {used.upper()}!", f"{self.pet_name}'s attack missed!"]
            if body.get("fallback") and move is not None:
                lines.insert(0, f"(not enough EN for {move['name'].upper()})")
        if isinstance(body.get("cheer"), str) and body["cheer"].strip():
            lines.append(body["cheer"])
        self._say(body.get("say") or (body.get("reaction") if not body.get("log") else ""))
        damage = body.get("damage")
        if body.get("acted") is not False and body.get("hit", True) and isinstance(damage, int) and damage > 0:
            self.enemy.hp = max(0, self.enemy.hp - damage)
        if self.enemy.hp <= 0:
            lines.append(f"{self.enemy.name.upper()} was deleted!")
            return self._win(lines)
        self._enemy_turn(lines)

    # --- the enemy's turn (deck-side AI) ----------------------------------------------
    def pick_attack(self) -> tuple[str, str, float]:
        """The enemy AI: usually the attack that hurts PULSE's type most, else a random one."""
        attacks = self.enemy.kind.attacks or ((f"{self.enemy.name} attack", self.enemy.type, 1.0),)
        if self.rng.random() < self.odds["smart"]:
            ptype = self.pet.get("type", "NORM")
            return max(attacks, key=lambda a: effectiveness(a[1], ptype, self.chart) * a[2])
        return self.rng.choice(attacks)

    def base_damage(self, power: float) -> int:
        atk = max(1, self.enemy.attack)
        low = max(1, int(atk * self.odds["dmg_min"]))
        roll = self.rng.randint(low, max(low, atk))
        extra = self.depth // max(1, int(self.odds["dmg_depth"]))
        return max(1, int(round(roll * power)) + extra)

    def _enemy_turn(self, lines: list[str]) -> None:
        name, atype, power = self.pick_attack()
        damage = self.base_damage(power)
        foe = self.enemy.name.upper()
        body, err = self._soft(TYPE_PET_COMBAT_HIT, {"damage": damage, "enemy": self.enemy.describe(),
                                                     "attack_type": atype})
        if body is None:
            lines.append(f"{foe} used {name.upper()}!")
            self._queue(lines, "menu")
            return self._abort(err, keep=True)
        self._update_pet(body)
        if body.get("fainted") is True:
            self.pet["hp"] = self.ally.hp = 0
        hit_lines = _lines(body.get("log"))
        if hit_lines and hit_lines[0].upper() == f"{foe} ATTACKS!":
            hit_lines[0] = f"{foe} used {name.upper()}!"          # the deck names the attack
        elif hit_lines:
            hit_lines.insert(0, f"{foe} used {name.upper()}!")
        else:
            taken = body.get("damage_taken", damage)
            hit_lines = [f"{foe} used {name.upper()}!", f"{self.pet_name} took {taken} damage."]
            if body.get("fainted"):
                hit_lines.append(f"{self.pet_name} fainted!")
        lines += hit_lines
        self._say(body.get("say") or (body.get("reaction") if not body.get("log") else ""))
        if body.get("fainted") is True or int(self.pet.get("hp") or 0) <= 0:
            return self._lose(lines)
        self._queue(lines, "menu")

    # --- BAG ------------------------------------------------------------------------------
    def use_item(self, index: int) -> None:
        if self.over:
            return
        if not self.bag:
            self._say("The bag is empty." if self.bag is not None else "No battle bag on this companion.")
            self.phase = "menu"
            return
        item = self.bag[max(0, min(index, len(self.bag) - 1))]
        label = str(item.get("name") or item.get("id") or "item")
        if item.get("usable_in_battle") is False:
            self._say(f"Can't use the {label} in battle.")
            return
        body, err = self._soft(TYPE_PET_COMBAT_ITEM, {"item": str(item.get("id") or label)})
        if body is None:
            if err and err.get("code") == "unknown_type":
                self.bag = None
                self._say("No battle bag on this companion.")
                self.phase = "menu"
                return
            if err and err.get("code") in {"none_left", "not_usable_in_battle", "bad_payload"}:
                self._say(err.get("say") or err.get("message") or "Can't use that.")
                if err.get("code") == "none_left":
                    self.bag = [b for b in self.bag if b is not item]
                return
            return self._abort(err)
        self._update_pet(body)
        self._say(body.get("say"))
        if body.get("consumed") is False or body.get("ok") is False:
            return          # nothing happened, so no turn is used
        self.turn += 1
        self._enemy_turn(_lines(body.get("log")) or [f"You used {label.upper()}!"])

    # --- RUN ------------------------------------------------------------------------------
    def chance_to_flee(self) -> float:
        e = self.enemy
        return flee_chance(int(getattr(self.ally.stats, "speed", 0) or self.pet.get("speed") or 0),
                           int(self.pet.get("level") or 1), e.speed, e.level, e.kind.boss, self.odds)

    def run(self) -> None:
        if self.over:
            return
        if self.run_lines is None:     # optional: the companion's lines for both outcomes
            body, _err = self._soft(TYPE_PET_COMBAT_RUN, {"enemy": self.enemy.describe()})
            self.run_lines = {k: clamp_say(body.get(k)) for k in ("say_ok", "say_fail")} if body else {}
        self.turn += 1
        if self.rng.random() < self.chance_to_flee():
            self._say(self.run_lines.get("say_ok"))
            return self._finish("flee", ["Got away safely!"])
        self._say(self.run_lines.get("say_fail") or "")
        self._enemy_turn(["Couldn't get away!"])

    # --- the end ---------------------------------------------------------------------------
    def _win(self, lines: list[str]) -> None:
        boss = self.enemy.kind.boss
        self.xp = win_xp(self.depth, boss, self.odds)
        chance = self.odds["boss_loot"] if boss else self.odds["loot"]
        if self.roll_loot is not None and self.rng.random() < chance:
            item = self.roll_loot()
            if item:
                self.loot = [{k: item[k] for k in ("name", "kind", "power") if k in item}]
        self._finish("win", lines, self.xp)

    def _lose(self, lines: list[str]) -> None:
        self.enemy.calm = int(self.odds["calm_turns"])
        self._finish("lose", lines)

    def _finish(self, result: str, lines: list[str], xp: int = 0) -> None:
        self.result = result
        if result == "flee":
            self.enemy.calm = int(self.odds["calm_turns"])
        payload: dict[str, Any] = {"result": result, "xp": int(xp), "enemies": [self.enemy.describe()]}
        if self.loot:
            payload["loot"] = self.loot
        body, _err = self._soft(TYPE_PET_COMBAT_END, payload)
        self.end_body = dict(body or {})
        if body:
            self._update_pet(body)
            if isinstance(body.get("mood"), str):
                self.pet["mood"] = body["mood"]
            end_lines = _lines(body.get("log"))
            if not end_lines:
                end_lines = {"win": [f"{self.pet_name} won the battle!", f"{self.pet_name} gained "
                                     f"{body.get('xp_granted', xp)} XP!"],
                             "lose": [f"{self.pet_name} lost the battle..."],
                             "flee": []}.get(result, [])
            lines += [x for x in end_lines if x not in lines]      # e.g. both sides say "Got away safely!"
            self._say(body.get("say") or (body.get("reaction") if not body.get("log") else ""))
        else:
            if result == "win":
                lines.append(f"{self.pet_name} won the battle!")
        self._queue(lines, "end")

    def _abort(self, err: dict[str, Any] | None, keep: bool = False) -> None:
        """The link died mid-battle: back to the dungeon, solo."""
        self.result = "abort"
        why = (err or {}).get("message") or (err or {}).get("code") or "no answer"
        self.note = f"lost contact with {self.ally.name} ({why})"
        if keep:
            self.pages.append([clamp_say("The link dropped! Back to the dungeon.")])
            self.after = "end"
        else:
            self._queue(["The link dropped! Back to the dungeon."], "end")

    @property
    def xp_granted(self) -> int:
        value = self.end_body.get("xp_granted", self.end_body.get("xp", self.xp))
        return int(value) if isinstance(value, (int, float)) else self.xp

    def summary(self) -> str:
        """One line for the dungeon's message log after the battle."""
        foe = self.enemy.name
        if self.result == "win":
            loot = ", ".join(str(i.get("name")) for i in self.loot)
            return f"{self.pet_name} beat the {foe}! +{self.xp_granted} xp" + (f", {loot} to bag" if loot else "")
        if self.result == "lose":
            return f"{self.pet_name} fainted vs the {foe}. mood: {self.pet.get('mood') or 'sad'}"
        if self.result == "flee":
            return f"you got away from the {foe}."
        return self.note or "battle over."

    # --- keys -----------------------------------------------------------------------------
    def key(self, key: str) -> bool:
        """One key press. Returns True when the battle screen should close."""
        k = key.lower() if len(key) == 1 else key
        if self.phase == "log":
            self.advance()
            return False
        if self.phase == "end":
            return True
        if self.phase == "menu":
            c = self.cursor["menu"]
            moves = {"left": c - 1 if c % 2 else c, "a": c - 1 if c % 2 else c, "h": c - 1 if c % 2 else c,
                     "right": c + 1 if c % 2 == 0 else c, "d": c + 1 if c % 2 == 0 else c,
                     "l": c + 1 if c % 2 == 0 else c,
                     "up": c - 2 if c >= 2 else c, "w": c - 2 if c >= 2 else c, "k": c - 2 if c >= 2 else c,
                     "down": c + 2 if c < 2 else c, "s": c + 2 if c < 2 else c, "j": c + 2 if c < 2 else c}
            if k in moves:
                self.cursor["menu"] = moves[k]
            elif k in ("enter", " ", "space"):
                self.pick(MENU[c])
            elif k in ("f", "b", "p", "r"):
                self.pick({"f": "FIGHT", "b": "BAG", "p": "PULSE", "r": "RUN"}[k])
            elif k.isdigit() and 1 <= int(k) <= len(self.moves):
                self.fight(int(k) - 1)
            elif k in ("q", "esc"):
                self._say(clamp_say("No quitting mid-battle! Pick RUN to flee."))
            return False
        if k in ("esc", "backspace", "q", "x") or (self.phase == "pulse" and k in ("enter", " ", "space")):
            self.phase = "menu"
            return False
        if self.phase == "fight":
            n = len(self.moves)
            if k in ("up", "w", "k") and n:
                self.cursor["fight"] = (self.cursor["fight"] - 1) % n
            elif k in ("down", "s", "j") and n:
                self.cursor["fight"] = (self.cursor["fight"] + 1) % n
            elif k.isdigit() and 1 <= int(k) <= n:
                self.fight(int(k) - 1)
            elif k == "a":
                self.fight(None)
            elif k in ("enter", " ", "space"):
                self.fight(self.cursor["fight"] if n else None)
        elif self.phase == "bag":
            n = len(self.bag or [])
            if k in ("up", "w", "k") and n:
                self.cursor["bag"] = (self.cursor["bag"] - 1) % n
            elif k in ("down", "s", "j") and n:
                self.cursor["bag"] = (self.cursor["bag"] + 1) % n
            elif k in ("enter", " ", "space"):
                self.use_item(self.cursor["bag"])
        return False

    def pick(self, choice: str) -> None:
        """FIGHT / BAG / PULSE / RUN from the main menu."""
        if choice == "RUN":
            self.run()
        elif choice == "FIGHT":
            if self.moves:
                self.phase = "fight"
            else:
                self.fight(None)          # an old companion with no move list: let PULSE pick
        elif choice == "BAG":
            if self.bag:
                self.cursor["bag"] = min(self.cursor["bag"], len(self.bag) - 1)
                self.phase = "bag"
            else:
                self._say("The bag is empty." if self.bag is not None else "No battle bag on this companion.")
        elif choice == "PULSE":
            self.phase = "pulse"


def _lines(raw: Any) -> list[str]:
    return [clamp_say(x) for x in raw if isinstance(x, str) and x.strip()] if isinstance(raw, list) else []


# ---------------------------------------------------------------------------
# Drawing: rows of (text, color role), every box row exactly 48 columns
# ---------------------------------------------------------------------------
def bar(value: Any, top: Any, width: int = 10) -> str:
    try:
        v, t = max(0, int(value)), max(1, int(top))
    except (TypeError, ValueError):
        return "[" + "?" * width + "]"
    filled = min(width, (v * width + t - 1) // t) if v else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def hp_role(value: Any, top: Any) -> str:
    try:
        frac = int(value) / max(1, int(top))
    except (TypeError, ValueError):
        return "text"
    return "ok" if frac > 0.5 else "warn" if frac > 0.25 else "alert"


def say_row(name: str, text: str) -> str:
    """The say line on its own row: 'PULSE: …' only when it fits (<= 37 chars)."""
    text = clamp_say(text)
    prefix = f"{name.upper()}: "
    return (prefix + text) if text and len(prefix + text) <= INNER else text


class _Box:
    """Builds the 48-column box one row at a time."""

    def __init__(self) -> None:
        self.rows: list[Row] = []

    def rule(self) -> None:
        self.rows.append([("+" + "-" * (SCREEN_W - 2) + "+", "dim")])

    def line(self, *pieces: tuple[str, str]) -> None:
        out: Row = [("| ", "dim")]
        used = 0
        for text, role in pieces:
            text = text[: INNER - used]
            if text:
                out.append((text, role))
                used += len(text)
        out.append((" " * (INNER - used) + " |", "dim"))
        self.rows.append(out)


def _at(col: int, *pieces: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return ((" " * col, "text"),) + pieces


def render_battle(b: Battle, width: int = SCREEN_W, height: int = 30) -> list[Row]:
    """The battle screen. Rows are 48 columns, centred in `width`."""
    e, p = b.enemy, b.pet
    menu_rows = {"fight": 7, "bag": 6, "pulse": 4, "end": 4}.get(b.phase, 2)
    b.log_rows = max(1, min(LOG_ROWS, height - 15 - menu_rows))   # 15 = borders, PULSE/enemy, say, keys
    box = _Box()
    box.rule()
    # Enemy: top right.
    col = 18
    tag = f"{e.name.upper()}  Lv{e.level}  {e.type}" + ("  BOSS" if e.kind.boss else "")
    if len(tag) > INNER - col:          # long names (KERNEL PANIC): single spaces, then shift left
        tag = tag.replace("  ", " ")
    box.line(*_at(min(col, INNER - len(tag)), (tag, "alert")))
    box.line(*_at(col, ("HP ", "dim"), (bar(e.hp, e.max_hp), hp_role(e.hp, e.max_hp)),
                   (f" {max(0, e.hp):>3}/{e.max_hp}", "text")))
    face = "(x_x)" if e.hp <= 0 else e.kind.face
    box.line(*_at(col + 2, (clamp_say(face, 20), "alert")))
    # PULSE: bottom left.
    pet_face = clamp_say(p.get("face") or "(^_^)", 16)
    if int(p.get("hp") or 0) <= 0:
        pet_face = "(x_x)"
    box.line((" [" + pet_face + "]", "title"))
    species = str(p.get("species") or "").upper()
    head = f" {b.pet_name}  Lv{p.get('level', '?')}" + (f"  {species}" if species else "") + f"  {p.get('type', 'NORM')}"
    box.line((head, "title"))
    box.line((" HP ", "dim"), (bar(p.get("hp"), p.get("max_hp")), hp_role(p.get("hp"), p.get("max_hp"))),
             (f" {int(p.get('hp') or 0):>3}/{p.get('max_hp', '?')}", "text"))
    energy = p.get("energy")
    if energy is None:
        box.line((" EN ", "dim"), ("(not reported)", "dim"))
    else:
        top = p.get("max_energy") or 100
        box.line((" EN ", "dim"), (bar(energy, top), hp_role(energy, top) if int(energy) < 25 else "prompt"),
                 (f" {int(energy):>3}/{top}", "text"))
    box.rule()
    # Battle log.
    # While a log page is up, that page; otherwise the newest lines, dimmed.
    shown = b.shown[: b.log_rows] if b.phase == "log" else b.history[-b.log_rows:]
    for i in range(b.log_rows):
        text = shown[i] if i < len(shown) else ""
        box.line((text, "text" if b.phase == "log" else "dim"))
    box.rule()
    # What PULSE says, on its own row.
    box.line((say_row(b.pet_name, b.say), "prompt"))
    box.rule()
    # The menu area.
    if b.phase == "fight":
        _fight_rows(b, box)
    elif b.phase == "bag":
        _bag_rows(b, box)
    elif b.phase == "pulse":
        _pulse_rows(b, box)
    elif b.phase == "end":
        _end_rows(b, box)
    else:
        c = b.cursor["menu"] if b.phase == "menu" else -1
        role = "text" if b.phase == "menu" else "dim"
        for r in (0, 2):
            box.line(*((("> " if c == i else "  ") + f"{MENU[i]:<13}", "prompt" if c == i else role)
                       for i in (r, r + 1)))
    box.rule()
    hints = {"log": "[any key] continue",
             "menu": "arrows move  enter pick  f b p r  1-4 move",
             "fight": "up/down  enter use  1-4  a auto  esc back",
             "bag": "up/down  enter use  esc back",
             "pulse": "[esc] back",
             "end": "[any key] back to the dungeon"}
    box.line((hints.get(b.phase, ""), "dim"))
    box.rule()
    pad = " " * max(0, (width - SCREEN_W) // 2)
    rows = [[(pad, "text")] + row if pad else row for row in box.rows]
    return rows[:height] if height < len(rows) else rows


def _fight_rows(b: Battle, box: _Box) -> None:
    sel = b.cursor["fight"]
    for i in range(MAX_LOADOUT):
        if i >= len(b.moves):
            box.line(("  -", "dim"))
            continue
        m = b.moves[i]
        cur = "> " if i == sel else "  "
        text = (f"{cur}{m['name'].upper()[:14]:<14} [{m['type']:<4}] PWR {m['power']:>2} "
                f"EN {m['energy_cost']:>2} R{m['rank']}")
        role = "prompt" if i == sel else "text"
        if not b.affordable(m):
            role = "warn"
        box.line((text, role))
    box.rule()
    if b.moves:
        m = b.moves[min(sel, len(b.moves) - 1)]
        mult = effectiveness(m["type"], b.enemy.type, b.chart)
        label = effectiveness_label(mult)
        box.line((m["desc"], "text"))
        box.line((f"Rank {m['rank']}  Acc {m['accuracy']}  vs {b.enemy.type}: {label}"
                  + ("" if b.affordable(m) else "  LOW EN"), "warn" if label == "super" else "dim"))
    else:
        box.line(("PULSE picks its own move.", "dim"))
        box.line(("", "dim"))


def _bag_rows(b: Battle, box: _Box) -> None:
    items = b.bag or []
    sel = min(b.cursor["bag"], max(0, len(items) - 1))
    top = max(0, sel - 3)
    for i in range(top, top + 4):
        if i >= len(items):
            box.line(("", "dim"))
            continue
        it = items[i]
        usable = it.get("usable_in_battle", True) is not False
        text = (("> " if i == sel else "  ") + f"{str(it.get('name', it.get('id', '?'))).upper()[:18]:<18} "
                f"x{int(it.get('qty', 1)):<3}" + ("" if usable else " (not in battle)"))
        box.line((text, ("prompt" if i == sel else "text") if usable else "dim"))
    box.rule()
    desc = items[sel].get("desc") if items else ""
    box.line((clamp_say(desc or ""), "text"))


def _pulse_rows(b: Battle, box: _Box) -> None:
    p = b.pet
    stats = getattr(b.ally, "stats", None)
    box.line((f"{b.pet_name}  {str(p.get('species') or '').upper()}  {p.get('type', 'NORM')}  Lv{p.get('level', '?')}",
              "title"))
    box.line((f"HP {p.get('hp', '?')}/{p.get('max_hp', '?')}   EN {p.get('energy', '?')}/{p.get('max_energy', 100)}",
              "text"))
    box.line((f"Mood: {p.get('mood') or '?'}   Status: {p.get('status') or 'ok'}", "prompt"))
    if stats is not None:
        box.line((f"ATK {stats.attack}  DEF {stats.defense}  SPD {stats.speed}   Run {int(b.chance_to_flee() * 100)}%",
                  "dim"))
    else:
        box.line((f"Run chance {int(b.chance_to_flee() * 100)}%", "dim"))


def _end_rows(b: Battle, box: _Box) -> None:
    body = b.end_body
    if b.result == "win":
        level = body.get("level")
        box.line(("VICTORY!", "ok"), (f"   +{b.xp_granted} XP" + (f"   Lv {level}" if level else "")
                                      + ("  LEVEL UP!" if body.get("level_up") else ""), "title"))
        bond = body.get("bond")
        delta = body.get("bond_delta")
        box.line((f"BOND {bond}" + (f" ({float(delta):+.0f})" if isinstance(delta, (int, float)) else "")
                  if bond else "BOND -", "prompt"))
        loot = ", ".join(str(i.get("name")) for i in (body.get("loot_added") or b.loot)) or "none"
        box.line((clamp_say(f"LOOT {loot}"), "text"))
        learned = body.get("learned_move")
        box.line((clamp_say(f"NEW MOVE {learned}") if learned else "", "ok"))
    elif b.result == "lose":
        box.line((f"{b.pet_name} FAINTED...", "alert"))
        box.line((f"Mood: {b.pet.get('mood') or 'sad'}", "warn"))
        box.line((f"HP {b.pet.get('hp', 0)}/{b.pet.get('max_hp', '?')}  `pet rest` heals it", "dim"))
        box.line(("The enemy is still out there.", "dim"))
    elif b.result == "flee":
        box.line(("GOT AWAY SAFELY", "ok"))
        box.line((f"Mood: {b.pet.get('mood') or '?'}", "text"))
        box.line(("", "dim"))
        box.line(("", "dim"))
    else:
        box.line(("LINK LOST", "alert"))
        box.line((clamp_say(b.note), "dim"))
        box.line(("You carry on solo.", "dim"))
        box.line(("", "dim"))


def battle_text(b: Battle) -> list[str]:
    """The screen as plain text lines (headless, tests, docs)."""
    return ["".join(t for t, _r in row) for row in render_battle(b, SCREEN_W, 40)]


# ---------------------------------------------------------------------------
# A battle on its own (the `battle` command): no map, just the fight
# ---------------------------------------------------------------------------
class BattleActivity(Activity):
    title = "BATTLE"
    tick = 0.5
    subtitle = " RUN to flee · esc back · ^C close "

    def __init__(self, ctx: "Context", battle: Battle) -> None:
        super().__init__()
        self.ctx = ctx
        self.battle = battle

    def on_key(self, key: str) -> None:
        if key == "ctrl+c":
            self.done = True
        elif self.battle.key(key):
            self.done = True
        if self.done:
            if not self.battle.over:
                self.battle._finish("flee", [])
            self.result_lines = [self.battle.summary()]
            self.ctx.services.pop("battle", None)
            self.ctx.services.pop("pet_cache", None)

    def render(self, width: int, height: int) -> list[Row]:
        return render_battle(self.battle, width, height)

    def headless(self) -> list[str]:
        self.battle.drain()
        return battle_text(self.battle) + ["(headless: `battle fight <n>`, `battle bag <n>`, `battle run`, "
                                           "`battle auto`, `battle show`)"]


def lcd_hook(ctx: "Context") -> Callable[[str, str], None]:
    """Send PULSE's battle lines to the LCD (rate-limited like other notices)."""
    def hook(text: str, foe: str) -> None:
        lcd = ctx.services.get("lcd")
        if lcd is not None and hasattr(lcd, "battle_say"):
            lcd.battle_say(text, foe)
    return hook


def new_battle(ctx: "Context", ally: Any, enemy: Enemy, depth: int = 1, rng: random.Random | None = None,
               roll_loot: Callable[[], dict[str, Any] | None] | None = None) -> Battle:
    _roster, odds = load_battle_config(ctx.config_dir)
    return Battle(ally, enemy, depth=depth, rng=rng or random.Random(), odds=odds, on_say=lcd_hook(ctx),
                  roll_loot=roll_loot)


def register(reg: "Registry") -> None:
    def battle_cmd(ctx: "Context", args: list[str]) -> list[str]:
        """battle <enemy> [level] | fight <n|name> | auto | bag <n|name> | pulse | run | show | end"""
        from cyberdeck.features.dungeon import PulseAlly, roll_battle_loot

        action = args[0].lower() if args else ""
        roster, _odds = load_battle_config(ctx.config_dir)
        kind = roster_entry(" ".join(args[:2]), roster) if len(args) > 1 and not args[1].isdigit() else None
        kind = kind or (roster_entry(action, roster) if action else None)
        if not action:
            return ["usage: battle <enemy> [level]   enemies: " + ", ".join(k.id for k in roster)]
        if kind is not None:                     # start a new battle
            level_arg = args[-1] if len(args) > 1 and args[-1].isdigit() else "1"
            enemy = Enemy.spawn(kind, max(1, int(level_arg)))
            ally = PulseAlly(ctx.companion.request)
            if not ctx.companion.is_linked():
                return [f"{ally.name} can't battle: link down"]
            if not ally.join():
                return [f"{ally.name} can't battle: {ally.note}"]
            battle = new_battle(ctx, ally, enemy, depth=enemy.level, roll_loot=roll_battle_loot)
            if not battle.start():
                return [f"{ally.name} can't battle: {battle.note or ally.note}"]
            ctx.services["battle"] = battle
            ctx.activity = BattleActivity(ctx, battle)
            return []
        b = ctx.services.get("battle")
        if b is None:
            if action not in {"fight", "auto", "bag", "pulse", "run", "show", "end"}:
                return [f"no enemy '{action}' — enemies: " + ", ".join(k.id for k in roster)]
            return ["no battle going on — start one: battle <enemy> [level]"]
        b.drain()
        before = len(b.history)
        rest = " ".join(args[1:]).strip()
        if action == "fight":
            if rest.isdigit():
                b.fight(int(rest) - 1)
            else:
                index = next((i for i, m in enumerate(b.moves) if m["name"].lower() == rest.lower()), None)
                if rest and index is None:
                    return [f"no move '{rest}' — moves: " + ", ".join(m["name"] for m in b.moves)]
                b.fight(index)
        elif action == "auto":
            b.fight(None)
        elif action == "bag":
            items = b.bag or []
            if not rest:
                return [f"{i}. {it.get('name')} x{it.get('qty', 1)}" + ("" if it.get("usable_in_battle", True)
                                                                      else " (not in battle)")
                        for i, it in enumerate(items, 1)] or ["the bag is empty"]
            index = int(rest) - 1 if rest.isdigit() else next(
                (i for i, it in enumerate(items) if rest.lower() in (str(it.get("id")), str(it.get("name")))), -1)
            if not 0 <= index < len(items):
                return [f"no item '{rest}' in the bag"]
            b.use_item(index)
        elif action == "pulse":
            b.phase = "pulse"
            out = battle_text(b)
            b.phase = "menu"
            return out
        elif action == "run":
            b.run()
        elif action == "end":
            if not b.over:
                b._finish("flee", [])
        elif action != "show":
            return ["usage: battle fight <n|name> | auto | bag [n|name] | pulse | run | show | end"]
        b.drain()
        out = list(b.history[before:])
        if b.say:
            out.append(say_row(b.pet_name, b.say))
        out.append(f"{b.enemy.name.upper()} HP {max(0, b.enemy.hp)}/{b.enemy.max_hp}  |  {b.pet_name} HP "
                   f"{b.pet.get('hp')}/{b.pet.get('max_hp')} EN {b.pet.get('energy', '?')}")
        if action == "show":
            out = battle_text(b)
        if b.over:
            out.append(b.summary())
            ctx.services.pop("battle", None)
            ctx.services.pop("pet_cache", None)
        return out

    def complete(ctx: "Context", args: list[str]) -> list[str]:
        if len(args) <= 1:
            return [k.id for k in ROSTER] + ["fight", "auto", "bag", "pulse", "run", "show", "end"]
        return []

    reg.command("battle", "Turn-based battle: battle <enemy> [level], then fight/bag/pulse/run.",
                usage="battle <enemy> [level] | fight <n> | auto | bag <n> | pulse | run | show | end",
                subcommands=("fight", "auto", "bag", "pulse", "run", "show", "end"),
                completer=complete)(battle_cmd)
