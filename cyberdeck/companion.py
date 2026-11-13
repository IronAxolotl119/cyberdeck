"""companion.py — the pet board's side of the conversation, simulated.

A second Pi Zero 2 W runs a Digimon-style pet ("PULSE"). This file has:

  * PetState           — a believable fake pet: needs that drift over
                         time, rest and sleep, levels and moves, a
                         branching evolution tree you pick from, gifts,
                         an animated text face, dungeon combat, and
                         pushed events
  * CompanionLink      — the "plug" the rest of the deck talks through
  * SimCompanionLink   — PetState in this process, speaking the real
                         protocol (the default, and what tests use)

The real connections (TCP to a companion program on the same computer,
or UART between two Pis) live in cyberdeck/link.py. tools/fake_companion.py
serves this same SimCompanionLink over TCP or serial, so you can test
the deck against a separate process before the real pet program is ready.
"""

from __future__ import annotations

import os
import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from cyberdeck.battledata import (
    ITEMS,
    MAX_LOADOUT,
    ODDS,
    SIM_LINES,
    SIM_MOVE_TYPES,
    SIM_SPECIES_TYPES,
    STARTER_BAG,
    ROSTER,
    TYPES,
    clamp_say,
    effectiveness,
    effectiveness_label,
    flee_chance,
    item_key,
    norm_type,
    roster_entry,
    slug,
)
from cyberdeck.protocol import (
    STAGES as _STAGES,
    TYPE_HELLO,
    TYPE_HELLO_OK,
    TYPE_PET_AVATAR,
    TYPE_PET_AVATAR_OK,
    TYPE_PET_COMBAT_ACTION,
    TYPE_PET_COMBAT_ACTION_OK,
    TYPE_PET_COMBAT_END,
    TYPE_PET_COMBAT_END_OK,
    TYPE_PET_COMBAT_HIT,
    TYPE_PET_COMBAT_HIT_OK,
    TYPE_PET_COMBAT_ITEM,
    TYPE_PET_COMBAT_ITEM_OK,
    TYPE_PET_COMBAT_MOVES,
    TYPE_PET_COMBAT_MOVES_OK,
    TYPE_PET_COMBAT_ROSTER,
    TYPE_PET_COMBAT_ROSTER_OK,
    TYPE_PET_COMBAT_RUN,
    TYPE_PET_COMBAT_RUN_OK,
    TYPE_PET_COMBAT_STATS,
    TYPE_PET_COMBAT_STATS_OK,
    TYPE_PET_EVOLVE,
    TYPE_PET_EVOLVE_OK,
    TYPE_PET_EVENT,
    TYPE_PET_FEED,
    TYPE_PET_FEED_OK,
    TYPE_PET_GIFT,
    TYPE_PET_GIFT_OK,
    TYPE_PET_INVENTORY,
    TYPE_PET_INVENTORY_OK,
    TYPE_PET_STATS,
    TYPE_PET_STATS_OK,
    TYPE_PET_PLAY,
    TYPE_PET_PLAY_OK,
    TYPE_PET_REST,
    TYPE_PET_REST_OK,
    TYPE_PET_SLEEP,
    TYPE_PET_SLEEP_OK,
    TYPE_PET_STATUS,
    TYPE_PET_STATUS_OK,
    TYPE_PING,
    TYPE_PONG,
    Message,
    PROTO_VERSION,
    new_id,
)
from cyberdeck.sensors import PiSensors, SimSensors, SystemSensors


# Raised when the link is down or a request cannot be completed.
# Command handlers catch this and show a short error in the UI.
class CompanionError(RuntimeError):
    """The companion could not be reached or did not answer in time."""


def _clamp(value: float, lo: float, hi: float) -> float:
    """Keep value between lo and hi."""
    return max(lo, min(hi, value))


# The evolution ladder (shared with protocol.py).
STAGES = list(_STAGES)

# What the sim pet is called at each stage before it picks a branch. A
# real companion sends its own names; the deck shows whatever it gets.
SIM_FORMS = {"egg": "Egg", "baby": "Chirplet", "in-training": "Wavling", "rookie": "Aethling",
             "champion": "Aethlance", "ultimate": "Aethgeist", "mega": "Aethcrown"}

# The sim's evolution tree: current stage -> the branches for the next
# stage as (species, hint, gate). A branch whose gate is false is listed
# with available:false and the hint says why. An evo chip skips gates.
SIM_TREE: dict[str, list[tuple[str, str, Callable[["PetState"], bool]]]] = {
    "egg": [("Chirplet", "hatches when warm", lambda p: True)],
    "baby": [("Wavling", "", lambda p: True)],
    "in-training": [("Aethling", "", lambda p: True),
                    ("Staticore", "needs 1+ care mistake", lambda p: p.care_mistakes >= 1)],
    "rookie": [("Aethlance", "needs happiness 60+", lambda p: p.happiness >= 60),
               ("Voltshard", "needs level 5+", lambda p: p.level >= 5),
               ("Glitchwyrm", "needs 3+ unique discoveries", lambda p: p.discoveries >= 3)],
    "champion": [("Aethgeist", "", lambda p: True),
                 ("Stormcore", "needs attack 12+", lambda p: p.attack >= 12)],
    "ultimate": [("Aethcrown", "needs level 10+", lambda p: p.level >= 10),
                 ("Nullking", "needs 6+ unique discoveries", lambda p: p.discoveries >= 6)],
}
# Hours in a stage before it is ready to evolve (times evolution_speed).
STAGE_HOURS = {"egg": 0.2, "baby": 2.0, "in-training": 4.0, "rookie": 36.0, "champion": 72.0, "ultimate": 120.0}
EVOLVE_SOON_AT = 0.9   # progress at which evolve_soon is pushed (once per stage)

# Combat moves: name -> (power, accuracy 0-100, energy cost). Unknown names
# (learned from a real companion) get a middling default. The formulas
# follow companion/docs/DUNGEON_INTEGRATION.md.
MOVE_INFO = {"peck": (4, 95, 0), "static zap": (6, 90, 5), "wave dash": (7, 85, 8),
             "signal burst": (10, 80, 12), "nova flare": (14, 70, 20), "chirp": (8, 95, 4)}
DEFAULT_MOVE = (5, 85, 6)

# Animated text faces by mood (two frames each), as sent in pet.avatar.ok.
FACE_FRAMES = {"joyful": ("(^o^)", "(^O^)/"), "playful": ("(^_^)", "(^_~)"), "content": ("(o_o)", "(-_o)"),
               "hungry": ("(>_<)", "(>o<)"), "tired": ("(=_=)", "(-_-)"), "grumpy": ("(>_>)", "(<_<)"),
               "sad": ("(;_;)", "(T_T)"), "asleep": ("(-_-) zZ", "(-_-) Zz")}
FACE_INTERVAL_MS = 600

# Item kinds the pet understands in a pet.gift message (see PROTOCOL.md).
GIFT_KINDS = {"food", "toy", "battery", "evolve", "trinket", "medicine"}

# Discoveries (pet.discovery, companion/docs/DISCOVERY.md): the dashboard's
# Wi-Fi / Bluetooth / LAN / RFID finds feed PULSE. Base XP per kind at
# interest 1 (+3 per interest step), and each kind's themed move pool.
DISCOVERY_XP = {"wifi": 4, "bt": 3, "lan": 3, "rfid": 8}
DISCOVERY_MOVES = {"wifi": ("signal sniff", "beacon burst", "channel hop"), "bt": ("blue pulse", "tooth echo"),
                   "lan": ("ping echo", "arp whisper"), "rfid": ("tag bite", "near field")}
MOVE_INFO.update({"signal sniff": (5, 95, 3), "beacon burst": (8, 85, 7), "channel hop": (6, 95, 4),
                  "blue pulse": (7, 90, 5), "tooth echo": (6, 90, 4), "ping echo": (5, 95, 3),
                  "arp whisper": (7, 85, 6), "tag bite": (9, 85, 7), "near field": (8, 90, 6)})
MAX_MOVE_RANK = 5          # each rank adds +2 power
DISCOVERY_WINDOW = 300.0   # seconds of finds that count towards "busy airwaves"

# Moves the sim pet learns as it levels up.
MOVE_LADDER = {2: "static zap", 3: "wave dash", 5: "signal burst", 7: "nova flare"}
XP_PER_LEVEL = 40

# The sim pet's personality for the PULSE STATS screen (pet.stats). A real
# companion has its own; change these to give the sim pet a different one.
SIM_QUIRKS = {"catchphrase": "pip pip!", "dance": "antenna wiggle", "sneeze": "tiny", "tic": "blinks twice",
              "fav_idle": "watch_packets"}
# Likes and dislikes. Only the ones in known_prefs are shown (the rest are ???);
# feeding PULSE its favourite food reveals fav_food.
SIM_PREFS = {"fav_food": "data cookie", "hated_food": "stale ration", "fav_signal": "bluetooth",
             "hated_signal": "static"}

# When the sim pet complains (pushed as pet.event): event -> test.
NEED_EVENTS = (
    ("hungry", lambda p: p.hunger >= 80),
    ("tired", lambda p: p.energy <= 15),
    ("sad", lambda p: p.happiness <= 25),
)


# The fake pet. Needs are 0-100: hunger 100 = starving, happiness and
# energy 100 = great. mood() is a word derived from those numbers.
@dataclass(slots=True)
class PetState:
    """Simulated companion pet."""

    name: str = "PULSE"
    stage: str = "rookie"
    hunger: float = 38.0
    happiness: float = 72.0
    energy: float = 80.0
    age_hours: float = 11.5
    level: int = 3
    xp: int = 95
    hp: int = 28
    max_hp: int = 28
    attack: int = 7
    defense: int = 6
    speed: int = 8
    moves: list[str] = field(default_factory=lambda: ["peck", "static zap", "wave dash"])
    care_mistakes: int = 0
    frame: int = 0
    # The current form's name ("" = the stage's default from SIM_FORMS).
    form: str = ""
    asleep: bool = False
    discoveries: int = 1
    # Hours spent in this stage (evolution progress), and how fast it counts.
    stage_hours: float = 0.0
    evolution_speed: float = 1.0
    # An evo chip made the pet ready now, with every branch unlocked.
    chip_ready: bool = False
    guarding: bool = False   # Guard halves the next incoming hit
    rng: random.Random = field(default_factory=lambda: random.Random(7), repr=False)
    # Gifts PULSE has received (names, newest last). Kept short.
    inventory: list[str] = field(default_factory=list)
    # Events waiting to be pushed: (event name, detail dict).
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _flags: set[str] = field(default_factory=set)
    # pet.discovery: kind:id keys already logged, move ranks, recent finds.
    seen: set[str] = field(default_factory=set)
    move_ranks: dict[str, int] = field(default_factory=dict)
    recent_finds: list[float] = field(default_factory=list)
    # Battles: the item bag {id: {"qty": n, …}}, health status (ok | glitch),
    # bond 0-100, the chosen 4-move loadout (None = automatic), a short
    # sad spell after a lost battle, and a generator for lines and crits.
    bag: dict[str, dict[str, Any]] = field(default_factory=lambda: {k: {"qty": v} for k, v in STARTER_BAG.items()})
    status: str = "ok"
    bond: float = 40.0
    battle_moves: list[str] | None = None
    sad_hours: float = 0.0
    line_rng: random.Random = field(default_factory=lambda: random.Random(11), repr=False)
    # The full stat sheet (pet.stats): lifetime battle record, when it hatched
    # (set on first use), obedience 0-100, likes found so far, trinkets kept.
    record: dict[str, int] = field(default_factory=lambda: {"wins": 0, "losses": 0, "flees": 0, "bosses": 0,
                                                            "faints": 0})
    born_at: float | None = None
    nickname: str | None = None
    generation: int = 1
    obedience: float = 60.0
    quirks: dict[str, str] = field(default_factory=lambda: dict(SIM_QUIRKS))
    prefs: dict[str, str] = field(default_factory=lambda: dict(SIM_PREFS))
    known_prefs: set[str] = field(default_factory=set)
    trinkets: list[str] = field(default_factory=list)

    @property
    def species(self) -> str:
        return self.form or SIM_FORMS.get(self.stage, self.stage.title())

    # --- evolution ---------------------------------------------------------------

    def evolution(self) -> dict[str, Any]:
        """pet.status.ok's evolution object: {next_stage, progress, ready, choices}."""
        branches = SIM_TREE.get(self.stage)
        index = STAGES.index(self.stage) if self.stage in STAGES else -1
        if not branches or index < 0 or index + 1 >= len(STAGES):
            return {"next_stage": None, "progress": 1.0, "ready": False, "choices": []}
        need = STAGE_HOURS.get(self.stage, 24.0)
        progress = 1.0 if self.chip_ready else max(0.0, min(1.0, self.stage_hours / need))
        next_stage = STAGES[index + 1]
        choices = [{"species": name, "stage": next_stage, "hint": hint,
                    "available": self.chip_ready or bool(gate(self))} for name, hint, gate in branches]
        return {"next_stage": next_stage, "progress": round(progress, 3), "ready": progress >= 1.0,
                "choices": choices}

    def evolve(self, species: str) -> dict[str, Any] | None:
        """pet.evolve: None = no such species anywhere (the caller sends bad_payload)."""
        evo = self.evolution()
        known = {name.lower() for branches in SIM_TREE.values() for name, _h, _g in branches}
        if species.lower() not in known:
            return None
        base = {"from": self.species, "to": species}
        choice = next((c for c in evo["choices"] if c["species"].lower() == species.lower()), None)
        if not evo["ready"]:
            return {**base, "accepted": False, "reason": "not ready",
                    "reaction": f"{self.name} isn't ready to evolve yet ({int(evo['progress'] * 100)}%)."}
        if choice is None or not choice["available"]:
            why = choice["hint"] if choice else f"{species} is not a branch from {self.species}"
            return {**base, "accepted": False, "reason": f"unavailable: {why}",
                    "reaction": f"{self.name} can't become {species} yet — {why}."}
        old = self.species
        self.events.append(("evolving", {"from": old, "to": choice["species"]}))
        self._evolve_to(evo["next_stage"], choice["species"])
        return {"accepted": True, "from": old, "to": self.species,
                "reaction": f"{self.name} glows... and becomes {self.species}!"}

    def _check_evolution(self) -> None:
        """Push evolve_soon once near the end of a stage, evolve_choice once ready."""
        evo = self.evolution()
        if not evo["next_stage"]:
            return
        tag = f"{self.stage}:{self.species}"
        # evolve_soon always comes first (even when an evo chip jumps straight to ready).
        if evo["progress"] >= EVOLVE_SOON_AT and f"soon:{tag}" not in self._flags:
            self._flags.add(f"soon:{tag}")
            hours_left = max(0.0, STAGE_HOURS.get(self.stage, 24.0) - self.stage_hours) / max(1e-6, self.evolution_speed)
            self.events.append(("evolve_soon", {
                "from": self.species, "next_stage": evo["next_stage"], "progress": evo["progress"],
                "to_candidates": [c["species"] for c in evo["choices"]],
                "eta_hours": 0.0 if evo["ready"] else round(hours_left, 2)}))
        if evo["ready"] and f"choice:{tag}" not in self._flags:
            self._flags.add(f"choice:{tag}")
            self.events.append(("evolve_choice", {"from": self.species, "next_stage": evo["next_stage"],
                                                  "choices": evo["choices"], "progress": evo["progress"]}))

    # --- rest, sleep, faces ---------------------------------------------------------

    def rest(self) -> dict[str, Any]:
        """A quick rest: about +20 energy and +6 hp. A fainted or hurt pet may
        always rest (so it is never stuck); a fully rested one is refused."""
        if self.asleep:
            return {"rested": False, "reason": "asleep", "energy_delta": 0, "pet": self.as_payload()}
        if self.energy >= 100 and self.hp >= self.max_hp:
            return {"rested": False, "reason": "energy is already full", "energy_delta": 0, "pet": self.as_payload()}
        before = self.energy
        self.energy = _clamp(self.energy + 20.0, 0.0, 100.0)
        self.hp = min(self.max_hp, self.hp + 6)
        self.hunger = _clamp(self.hunger + 2.0, 0.0, 100.0)
        self._check_needs()
        return {"rested": True, "energy_delta": int(round(self.energy - before)), "pet": self.as_payload()}

    def sleep(self, on: bool) -> dict[str, Any]:
        """Fall asleep (energy refills each tick, 'woke' when full) or wake up."""
        was = self.asleep
        if on and not was and self.energy >= 100 and self.hp >= self.max_hp:
            return {"asleep": False, "changed": False, "reason": f"{self.name} is already fully rested",
                    "pet": self.as_payload()}
        if bool(on) == was:
            return {"asleep": was, "changed": False,
                    "reason": f"{self.name} is already {'asleep' if was else 'awake'}", "pet": self.as_payload()}
        self.asleep = bool(on)   # 'woke' is only pushed when it wakes up by itself
        return {"asleep": self.asleep, "changed": True, "reason": None, "pet": self.as_payload()}

    def face_frames(self) -> tuple[str, ...]:
        return FACE_FRAMES["asleep"] if self.asleep else FACE_FRAMES.get(self.mood(), FACE_FRAMES["content"])

    # --- combat: turn-based battles ---------------------------------------------------
    # Mirrors the real companion (companion/docs/DUNGEON_INTEGRATION.md,
    # "Battle protocol"): typed moves, the type chart, crits, a battle log,
    # say lines (<= 44 chars), the item bag, the 4-move loadout and flee odds.
    # Lines and crits use their own random generator (line_rng) so the hit
    # rolls stay the same as before for old replays and tests.

    def _move_info(self, name: str) -> tuple[int, int, int, str | None]:
        if name.lower() == "guard":
            return 0, 100, 0, "guard"
        power, accuracy, cost = MOVE_INFO.get(name.lower(), DEFAULT_MOVE)
        return power + 2 * self.move_ranks.get(name.lower(), 0), accuracy, cost, None

    def pet_type(self) -> str:
        """PULSE's own (defending) type, from its species."""
        return SIM_SPECIES_TYPES.get(self.species.lower(), "NORM")

    @staticmethod
    def move_type(name: str) -> str:
        return SIM_MOVE_TYPES.get(name.lower(), ("NORM", ""))[0]

    def _say(self, key: str, foe: str = "foe") -> str:
        pool = SIM_LINES.get(key) or ("",)
        return clamp_say(self.line_rng.choice(pool).replace("{foe}", foe))

    def battle_move(self, name: str) -> dict[str, Any]:
        """One FIGHT-menu row: {id, name, type, power, energy_cost, accuracy, rank, desc…}."""
        power, accuracy, cost, effect = self._move_info(name)
        rank = self.move_ranks.get(name.lower(), 0)
        mtype, desc = SIM_MOVE_TYPES.get(name.lower(), ("NORM", f"{name}."))
        return {"id": slug(name), "name": name, "type": mtype, "power": power, "energy_cost": cost,
                "accuracy": accuracy, "rank": rank, "bonus": 2 * rank, "kind": "guard" if effect else "strike",
                "effect": effect, "desc": clamp_say(desc)}

    def loadout(self) -> list[str]:
        """The (at most 4) moves in the FIGHT menu, in the order PULSE learned them."""
        if self.battle_moves:
            chosen = [m for m in self.moves if m.lower() in {b.lower() for b in self.battle_moves}]
            if chosen:
                return chosen[:MAX_LOADOUT]
        if len(self.moves) <= MAX_LOADOUT:
            return list(self.moves)
        # Auto: the strongest move of each type first, then the strongest of the rest.
        strongest = sorted(self.moves, key=lambda m: -self._move_info(m)[0])
        picks: list[str] = []
        for m in strongest:
            if self.move_type(m) not in {self.move_type(p) for p in picks}:
                picks.append(m)
        picks += [m for m in strongest if m not in picks]
        picks = picks[:MAX_LOADOUT]
        chirp = next((m for m in self.moves if m.lower() == "chirp"), None)
        if chirp and chirp not in picks:
            picks[-1] = chirp                 # Chirp stays as the cheap fallback
        return [m for m in self.moves if m in picks]

    def battle_pet(self) -> dict[str, Any]:
        """The pet card on the battle screen."""
        return {"name": self.name, "species": self.species, "type": self.pet_type(), "level": self.level,
                "hp": self.hp, "max_hp": self.max_hp, "energy": int(round(self.energy)), "max_energy": 100,
                "mood": "asleep" if self.asleep else self.mood(), "status": self.status_word(),
                "face": self.face_frames()[0], "bond": self.bond_tier()}

    def status_word(self) -> str:
        return "fainted" if self.hp <= 0 else self.status

    def bond_tier(self) -> str:
        for limit, word in ((80, "best friends"), (60, "close"), (40, "friendly"), (20, "warming up")):
            if self.bond >= limit:
                return word
        return "wary"

    def normalize_enemy(self, raw: Any) -> dict[str, Any]:
        """The deck's enemy object with missing fields filled in from the roster by id or name."""
        data = raw if isinstance(raw, dict) else {}
        kind = roster_entry(data.get("id")) or roster_entry(data.get("name"))

        def num(key: str, default: int) -> int:
            value = data.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else default

        level = max(1, num("level", 1))
        base = kind.stats(level) if kind else {"hp": 1, "attack": 1, "defense": 0, "speed": 1}
        hp = num("hp", base["hp"])
        return {"id": str(data.get("id") or (kind.id if kind else slug(data.get("name")) or "foe")),
                "name": str(data.get("name") or (kind.name if kind else "foe")),
                "type": norm_type(data.get("type"), kind.type if kind else "NORM"),
                "level": level, "hp": hp, "max_hp": num("max_hp", max(hp, base["hp"])),
                "attack": num("attack", base["attack"]), "defense": num("defense", base["defense"]),
                "speed": num("speed", base["speed"]), "face": str(data.get("face") or (kind.face if kind else "(o_o)")),
                "boss": bool(data.get("boss", kind.boss if kind else False))}

    def flee_odds(self, foe: dict[str, Any]) -> float:
        return flee_chance(self.speed, self.level, int(foe.get("speed") or 0), int(foe.get("level") or 1),
                           bool(foe.get("boss")))

    def combat_stats(self, enemy: dict[str, Any] | None = None) -> dict[str, Any]:
        """pet.combat.stats.ok: the combat sheet; with an enemy it is also the battle start."""
        known = []
        for name in self.moves:
            power, accuracy, cost, effect = self._move_info(name)
            rank = self.move_ranks.get(name.lower(), 0)
            known.append({"name": name, "power": power, "accuracy": accuracy, "energy_cost": cost,
                          "kind": "electric", "effect": effect, "rank": rank, "bonus": 2 * rank,
                          "type": self.move_type(name)})
        out: dict[str, Any] = {
            "hp": self.hp, "max_hp": self.max_hp, "attack": self.attack, "defense": self.defense,
            "speed": self.speed, "level": self.level, "fainted": self.hp <= 0,
            "asleep": self.asleep, "guarding": self.guarding,
            "moves": [self.battle_move(m) for m in self.loadout()], "known_moves": known,
            "bag": self.bag_payload(), "pet": self.battle_pet(), "type": self.pet_type(),
            "energy": int(round(self.energy)), "max_energy": 100, "say": "",
        }
        if enemy is not None:
            foe = self.normalize_enemy(enemy)
            out.update(enemy=foe, flee_chance=self.flee_odds(foe),
                       say=self._say("start.boss" if foe["boss"] else "start", foe["name"]),
                       log=[clamp_say(f"A wild {foe['name'].upper()} appeared!"), clamp_say(f"Go, {self.name}!")])
        return out

    def _pick_move(self, requested: str | None, foe: dict[str, Any] | None = None) -> tuple[str, bool]:
        """(move name, fallback?). No move asked = the best one against the enemy's type."""
        def affordable(name: str) -> bool:
            return self._move_info(name)[2] <= self.energy
        if requested:
            known = next((m for m in self.moves if m.lower() == requested.lower()), None)
            if known and affordable(known):
                return known, False
            chirp = next((m for m in self.moves if m.lower() == "chirp"), None)
            return (chirp, True) if chirp and affordable(chirp) else ("Guard", True)
        options = [m for m in self.loadout() if affordable(m)]
        if not options:
            return "Guard", False
        defense = int((foe or {}).get("defense") or 0)
        ftype = (foe or {}).get("type", "NORM")

        def expected(m: str) -> float:
            power, accuracy, _cost, _e = self._move_info(m)
            return max(1, power + self.attack - defense) * effectiveness(self.move_type(m), ftype) * accuracy
        return max(options, key=expected), False

    def combat_action(self, move: str | None, enemy: dict[str, Any], turn: int | None = None) -> dict[str, Any]:
        """PULSE acts on an enemy (the formulas in DUNGEON_INTEGRATION.md)."""
        foe = self.normalize_enemy(enemy)
        name_up, foe_up = self.name.upper(), foe["name"].upper()
        out: dict[str, Any] = {"move": move, "hit": False, "damage": 0, "effect": None, "energy_spent": 0,
                               "enemy": foe, "turn": turn, "crit": False, "missed": False, "obeyed": True,
                               "effectiveness": 1.0, "effectiveness_label": "normal", "energy_cost": 0}
        if self.asleep or self.hp <= 0:
            why = "asleep" if self.asleep else "fainted"
            out.update(acted=False, reason=why, reaction=f"{self.name} is {why} and cannot fight.",
                       say="", log=[clamp_say(f"{name_up} is {why} and can't fight!")],
                       pet_hp=self.hp, hp=self.hp, energy=int(round(self.energy)), status=self.status_word(),
                       pet=self.as_payload())
            return out
        name, fallback = self._pick_move(move, foe)
        mtype = self.move_type(name)
        out.update(move=name, move_used=name, type=mtype)
        log = [clamp_say(f"{name_up} used {name.upper()}!")]
        if name == "Guard":
            self.guarding = True
            out.update(hit=True, effect="guard", reaction=f"{self.name} braces itself (Guard).")
            log.append(clamp_say(f"{name_up} is guarding!"))
            say = self._say("guard", foe["name"])
        else:
            power, accuracy, cost, _effect = self._move_info(name)
            self.energy = _clamp(self.energy - cost, 0.0, 100.0)
            out.update(energy_spent=cost, energy_cost=cost)
            chance = max(5, min(100, accuracy + (self.speed - int(foe["speed"])) // 2))
            if self.rng.randint(1, 100) <= chance:
                mult = effectiveness(mtype, foe["type"])
                out.update(effectiveness=mult, effectiveness_label=effectiveness_label(mult), hit=True)
                if mult == 0:
                    log.append(clamp_say(f"It doesn't affect {foe_up}..."))
                    out["reaction"] = f"{self.name}'s {name} does nothing to the {foe['name']}."
                    say = self._say("immune", foe["name"])
                else:
                    base = max(1, power + self.attack - int(foe["defense"]))
                    crit = self.line_rng.random() < ODDS["crit"]
                    if crit:
                        base = int(base * ODDS["crit_mult"])
                        log.append("A critical hit!")
                    damage = max(1, int(base * mult))
                    if mult > 1:
                        log.append("It's super effective!")
                    elif mult < 1:
                        log.append("It's not very effective...")
                    log.append(clamp_say(f"{foe_up} took {damage} damage."))
                    out.update(damage=damage, crit=crit,
                               reaction=f"{self.name} uses {name} on the {foe['name']} for {damage}.")
                    say = self._say("crit" if crit else "super" if mult > 1 else "weak" if mult < 1 else "move",
                                    foe["name"])
            else:
                out.update(missed=True, reaction=f"{self.name}'s {name} misses the {foe['name']}.")
                log.append(clamp_say(f"{name_up}'s attack missed!"))
                say = self._say("miss", foe["name"])
        out.update(acted=True, pet_hp=self.hp, hp=self.hp, energy=int(round(self.energy)),
                   status=self.status_word(), say=say, log=log)
        if fallback:
            out["fallback"] = True
        if not move:
            out["auto"] = True
        out["pet"] = self.as_payload()
        return out

    def combat_hit(self, damage: int = 0, heal: int = 0, enemy: Any = "foe",
                   attack_type: str | None = None) -> dict[str, Any]:
        """An enemy hits PULSE (defense, the type chart and Guard soak some), or a heal."""
        foe = self.normalize_enemy(enemy if isinstance(enemy, dict) else {"name": str(enemy or "foe")})
        if damage < 0:
            heal, damage = heal - damage, 0
        healed = 0
        log: list[str] = []
        say = ""
        if heal > 0:
            before = self.hp
            self.hp = min(self.max_hp, self.hp + heal)
            healed = self.hp - before
            log.append(clamp_say(f"{self.name.upper()} recovered {healed} HP."))
        taken = 0
        # An old-style hit (no attack_type and an enemy not in the roster) is untyped: x1.
        typed = attack_type is not None or roster_entry(foe["id"]) is not None or foe["type"] != "NORM"
        atype = norm_type(attack_type, foe["type"])
        mult = effectiveness(atype, self.pet_type()) if typed else 1.0
        if damage > 0:
            log.append(clamp_say(f"{foe['name'].upper()} attacks!"))
            taken = max(0, damage - self.defense // 4)
            if taken > 0:
                taken = 0 if mult == 0 else max(1, int(taken * mult))
            if self.guarding:
                taken //= 2
                self.guarding = False
            self.hp = max(0, self.hp - taken)
            if mult > 1:
                log.append("It's super effective!")
            elif 0 < mult < 1:
                log.append("It's not very effective...")
            log.append(clamp_say(f"{self.name.upper()} took {taken} damage."))
            if self.hp <= 0:
                log.append(clamp_say(f"{self.name.upper()} fainted!"))
                say = self._say("faint", foe["name"])
            elif mult == 0:
                say = self._say("immune_hit", foe["name"])
            elif mult < 1:
                say = self._say("resist", foe["name"])
            elif self.hp <= self.max_hp // 4:
                say = self._say("lowhp", foe["name"])
            else:
                say = self._say("hurt", foe["name"])
        return {"damage": damage, "damage_taken": taken, "attack_type": atype, "effectiveness": mult,
                "effectiveness_label": effectiveness_label(mult), "healed": healed, "pet_hp": self.hp,
                "hp": self.hp, "energy": int(round(self.energy)), "status": self.status_word(),
                "fainted": self.hp <= 0, "low_hp": 0 < self.hp <= self.max_hp // 4,
                "say": say, "log": log, "pet": self.as_payload()}

    def combat_end(self, result: str, xp: int | None, enemies: list[Any],
                   loot: list[Any] | None = None) -> dict[str, Any] | None:
        """The battle (or run) is over: XP, mood, bond, loot into the bag. None = bad result."""
        if result not in {"win", "lose", "flee"}:
            return None
        foes = [self.normalize_enemy(e if isinstance(e, dict) else {"name": str(e)}) for e in enemies]
        boss = any(f["boss"] for f in foes)
        if xp is None:   # the companion's default: 5 + 3 x level per enemy, x3 for a boss
            xp = sum((5 + 3 * f["level"]) * (3 if f["boss"] else 1) for f in foes) if result == "win" else 0
        xp = max(0, int(xp))
        level_before, moves_before = self.level, list(self.moves)
        self.gain_xp(xp)
        learned = next((m for m in self.moves if m not in moves_before), None)
        bond_before = self.bond
        up = self.name.upper()
        log: list[str] = []
        fainted = False
        if result == "win":
            self.happiness = _clamp(self.happiness + 8.0, 0.0, 100.0)
            self.bond = _clamp(self.bond + (5.0 if boss else 2.0), 0.0, 100.0)
            reaction = f"{self.name} cheers! (+{xp} xp)"
            log += [f"{up} won the battle!", f"{up} gained {xp} XP!"]
            say = self._say("learned" if learned else "level_up" if self.level > level_before
                            else "win.boss" if boss else "win")
        elif result == "lose":
            self.happiness = _clamp(self.happiness - 10.0, 0.0, 100.0)
            self.sad_hours = 1.0
            fainted = self.hp <= 0
            if self.hp <= 0:
                self.care_mistakes += 1
                self.hp = 1          # limps home; a rest heals it fully
            reaction = f"{self.name} droops after the loss. (+{xp} xp)"
            log += [f"{up} lost the battle...", f"{up} limped home with {self.hp} HP."]
            say = self._say("lose")
        else:
            self.happiness = _clamp(self.happiness - 2.0, 0.0, 100.0)
            reaction = f"{self.name} is glad to be out of there. (+{xp} xp)"
            log.append("Got away safely!")
            say = self._say("flee")
        if self.level > level_before:
            log.append(f"{up} grew to Lv {self.level}!")
        if learned:
            log.append(f"{up} learned {learned.upper()}!")
        # The lifetime battle record (pet.stats "Battle Record").
        self.record[{"win": "wins", "lose": "losses", "flee": "flees"}[result]] += 1
        if result == "win" and boss:
            self.record["bosses"] += 1
        if fainted:
            self.record["faints"] += 1
        added = []
        for item in loot or []:
            entry = self.bag_add(item if isinstance(item, dict) else {"name": str(item)})
            if entry:
                added.append(entry)
                log.append(f"Put {entry['name'].upper()} in the bag.")
        self._check_needs()
        self._check_evolution()
        return {"result": result, "xp": xp, "xp_granted": xp, "level_up": self.level > level_before,
                "level": self.level, "learned_move": learned, "bond_delta": round(self.bond - bond_before, 1),
                "bond": self.bond_tier(), "mood": self.mood(), "fainted": result == "lose",
                "loot_added": added, "reaction": reaction, "say": say,
                "log": [clamp_say(line) for line in log], "hp": self.hp, "energy": int(round(self.energy)),
                "status": self.status_word(), "pet": self.as_payload()}

    # --- the battle bag (pet.combat.item, pet.inventory) --------------------------------

    def _item_info(self, key: str) -> dict[str, Any] | None:
        if key in ITEMS:
            name, kind, amount, usable, desc = ITEMS[key]
            return {"id": key, "name": name, "kind": kind, "amount": amount, "usable_in_battle": usable, "desc": desc}
        stored = self.bag.get(key)
        if not isinstance(stored, dict):
            return None
        kind = str(stored.get("kind") or "trinket")
        amount = int(stored.get("amount") or 0)
        usable = kind in {"food", "battery", "medicine"}
        desc = {"food": f"Restores {amount} HP.", "battery": f"Restores {amount} EN.",
                "medicine": f"Cures glitch. Restores {amount} HP."}.get(kind, "A keepsake. Not for battle.")
        return {"id": key, "name": str(stored.get("name") or key), "kind": kind, "amount": amount,
                "usable_in_battle": usable, "desc": clamp_say(desc)}

    def bag_add(self, item: dict[str, Any], qty: int = 1) -> dict[str, Any] | None:
        """Put an item (a loot dict or a name) in the bag. Returns {id, name, qty}."""
        key = item_key(item.get("id") or item.get("name"))
        if not key:
            return None
        entry = self.bag.setdefault(key, {"qty": 0})
        if key not in ITEMS:
            kind = str(item.get("kind") or "trinket")
            kind = kind if kind in GIFT_KINDS | {"medicine"} else "trinket"
            power = item.get("power", 10)
            amount = int(power) if isinstance(power, (int, float)) and not isinstance(power, bool) else 10
            entry.update(name=str(item.get("name") or key), kind=kind,
                         amount=max(5, amount) if kind == "food" else max(10, amount) if kind == "battery" else amount)
        entry["qty"] = int(entry.get("qty", 0)) + max(1, int(qty))
        info = self._item_info(key) or {"name": key}
        return {"id": key, "name": info["name"], "qty": entry["qty"]}

    def bag_payload(self) -> list[dict[str, Any]]:
        out = []
        for key, entry in self.bag.items():
            info = self._item_info(key)
            if info and int(entry.get("qty", 0)) > 0:
                out.append({"id": key, "name": info["name"], "kind": info["kind"], "qty": int(entry["qty"]),
                            "usable_in_battle": info["usable_in_battle"], "desc": info["desc"]})
        return out

    def combat_item(self, ref: str) -> dict[str, Any]:
        """Use a bag item in battle. {"error": code} for none_left / not_usable_in_battle."""
        key = item_key(ref)
        info = self._item_info(key)
        entry = self.bag.get(key) or {}
        label = (info or {}).get("name", ref)
        item = {"id": key, "name": label, "kind": (info or {}).get("kind", "trinket"), "qty": int(entry.get("qty", 0))}
        if info is None or int(entry.get("qty", 0)) <= 0:
            return {"error": "none_left", "item": item, "say": clamp_say(f"No {label} left in the bag.")}
        if not info["usable_in_battle"]:
            return {"error": "not_usable_in_battle", "item": item, "say": clamp_say(f"Not now! Save the {label}.")}
        up, kind, amount = self.name.upper(), info["kind"], info["amount"]
        log = [clamp_say(f"You used {label.upper()}!")]
        cleared = False
        if kind == "battery":
            before = self.energy
            self.energy = _clamp(self.energy + amount, 0.0, 100.0)
            gained = int(round(self.energy - before))
            if gained:
                log.append(clamp_say(f"{up} recovered {gained} EN."))
        else:
            if kind == "medicine" and self.status == "glitch":
                self.status, cleared = "ok", True
                log.append(clamp_say(f"{up} is cured of glitch!"))
            before = self.hp
            self.hp = min(self.max_hp, self.hp + amount)
            gained = self.hp - before
            if gained:
                log.append(clamp_say(f"{up} recovered {gained} HP."))
            gained = gained or int(cleared)
        if not gained:
            return {"ok": False, "consumed": False, "reason": "no_effect", "item": item, "hp": self.hp,
                    "energy": int(round(self.energy)), "status": self.status_word(), "status_cleared": False,
                    "say": self._say("no_effect"), "log": [], "bag": self.bag_payload(), "pet": self.as_payload()}
        entry["qty"] = int(entry["qty"]) - 1
        item["qty"] = entry["qty"]
        return {"ok": True, "consumed": True, "item": item, "hp": self.hp, "energy": int(round(self.energy)),
                "status": self.status_word(), "status_cleared": cleared, "say": self._say("item"), "log": log,
                "bag": self.bag_payload(), "pet": self.as_payload()}

    def set_loadout(self, names: list[Any] | None, auto: bool = False) -> dict[str, Any] | None:
        """pet.combat.moves: set 1-4 known moves (None = just read), or auto. None = bad list."""
        if auto:
            self.battle_moves = None
        elif names is not None:
            wanted = [str(n).strip().lower() for n in names if str(n).strip()]
            known = {m.lower() for m in self.moves} | {slug(m) for m in self.moves}
            if not 1 <= len(wanted) <= MAX_LOADOUT or any(w not in known and slug(w) not in known for w in wanted):
                return None
            self.battle_moves = [m for m in self.moves if m.lower() in wanted or slug(m) in {slug(w) for w in wanted}]
        return {"moves": [self.battle_move(m) for m in self.loadout()],
                "known": [self.battle_move(m) for m in self.moves], "auto": not self.battle_moves}


    def mood(self) -> str:
        """Pick a short mood word from the current needs."""
        if self.sad_hours > 0:
            return "sad"           # just lost a battle
        if self.hunger >= 82:
            return "hungry"
        if self.energy <= 18:
            return "tired"
        if self.happiness <= 28:
            return "grumpy"
        if self.happiness >= 85 and self.hunger <= 45:
            return "joyful"
        if self.happiness >= 70:
            return "playful"
        return "content"

    def as_payload(self) -> dict[str, Any]:
        """Shape used in protocol replies (see the Pet object in PROTOCOL.md)."""
        return {
            "name": self.name,
            "species": self.species,
            "stage": self.stage,
            "hunger": int(round(self.hunger)),
            "happiness": int(round(self.happiness)),
            "energy": int(round(self.energy)),
            "mood": self.mood(),
            "age_hours": round(self.age_hours, 2),
            "gifts": len(self.inventory),
            "level": self.level,
            "xp": self.xp,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "attack": self.attack,
            "defense": self.defense,
            "speed": self.speed,
            "moves": list(self.moves),
            "type": self.pet_type(),
            "status": self.status_word(),
            "bond": self.bond_tier(),
            "care_mistakes": self.care_mistakes,
            "asleep": self.asleep,
            "environment": self.environment(),
            "avatar": {"style": "face", "mood": "asleep" if self.asleep else self.mood(), "frame": self.frame,
                       "text": self.face_frames()[self.frame % 2], "form": self.species.lower()},
        }

    def tick(self, dt: float) -> None:
        """Advance needs. dt is elapsed seconds of companion time."""
        if dt <= 0:
            return
        minutes = dt / 60.0
        # Hunger climbs to full in roughly 40 minutes of neglect (half as fast asleep).
        self.hunger = _clamp(self.hunger + minutes * (1.25 if self.asleep else 2.5), 0.0, 100.0)
        # Happiness fades; faster when hungry.
        fade = 0.8 + (0.04 * max(0.0, self.hunger - 50.0))
        self.happiness = _clamp(self.happiness - minutes * fade, 0.0, 100.0)
        # Energy recovers slowly awake, quickly asleep; full energy wakes it up.
        self.energy = _clamp(self.energy + minutes * (8.0 if self.asleep else 0.9), 0.0, 100.0)
        if self.asleep:
            self.hp = min(self.max_hp, self.hp + int(dt / 3600.0 * 8 + 0.5))   # slow HP regen asleep
        if self.asleep and self.energy >= 100.0:
            self.asleep = False
            self.events.append(("woke", {"reason": "rested"}))
        self.age_hours += dt / 3600.0
        self.sad_hours = max(0.0, self.sad_hours - dt / 3600.0)
        self.stage_hours += dt / 3600.0 * self.evolution_speed
        self.frame = 1 - self.frame
        self._check_evolution()
        self._check_needs()

    def feed(self, item: str = "ration") -> dict[str, Any]:
        """Lower hunger and bump happiness, then return a protocol payload."""
        before = self.hunger
        relief = 28.0 if item in {"ration", "kibble", "food"} else 22.0
        self.hunger = _clamp(self.hunger - relief, 0.0, 100.0)
        self.happiness = _clamp(self.happiness + 8.0, 0.0, 100.0)
        self._check_needs()
        return {
            "item": item,
            "hunger_delta": int(round(self.hunger - before)),
            "pet": self.as_payload(),
        }

    def play(self, game: str = "spar") -> dict[str, Any]:
        """Play if awake with enough energy; otherwise explain why not."""
        if self.asleep:
            return {"game": game, "played": False, "reason": "asleep", "pet": self.as_payload()}
        if self.energy < 12:
            return {
                "game": game,
                "played": False,
                "reason": "too tired",
                "pet": self.as_payload(),
            }
        before_h, before_e = self.happiness, self.energy
        self.happiness = _clamp(self.happiness + 16.0, 0.0, 100.0)
        self.energy = _clamp(self.energy - 18.0, 0.0, 100.0)
        self.hunger = _clamp(self.hunger + 6.0, 0.0, 100.0)
        self.gain_xp(10)
        self._check_needs()
        return {
            "game": game,
            "played": True,
            "happiness_delta": int(round(self.happiness - before_h)),
            "energy_delta": int(round(self.energy - before_e)),
            "pet": self.as_payload(),
        }

    def gift(self, item: dict[str, Any], store: bool = False) -> dict[str, Any]:
        """Receive an item from the main deck (dungeon loot). See PROTOCOL.md.

        Gifts apply right away. Only store=True (or medicine for a pet that
        is not sick) puts the item in the battle bag instead."""
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "trinket").lower()
        if kind not in GIFT_KINDS:
            kind = "trinket"
        if store or (kind == "medicine" and self.status != "glitch"):
            entry = self.bag_add(dict(item, kind=kind))
            self.inventory = (self.inventory + [name])[-20:]
            return {"accepted": True, "stored": True, "item": {"name": name, "kind": kind},
                    "bag_item": entry, "reaction": f"{self.name} tucks the {name} into the bag for later.",
                    "effects": {"hunger": 0, "happiness": 0, "energy": 0, "stage": None}, "say": None,
                    "pet": self.as_payload()}
        try:
            power = max(0.0, min(100.0, float(item.get("power", 10))))
        except (TypeError, ValueError):
            power = 10.0
        clean = {"name": name, "kind": kind, "power": int(power)}
        before = (self.hunger, self.happiness, self.energy, self.stage)
        if kind == "evolve" and self.stage == STAGES[-1]:
            # Already at the last form: refuse, and the deck keeps the item.
            return {
                "accepted": False,
                "item": clean,
                "reaction": f"{self.name} is already {self.stage} — it sniffs the {name} and hands it back.",
                "effects": {"hunger": 0, "happiness": 0, "energy": 0, "stage": None},
                "pet": self.as_payload(),
            }
        if kind == "food":
            if name.lower() == self.prefs.get("fav_food", "").lower():
                self.known_prefs.add("fav_food")        # now it shows on the stats screen
            self.hunger = _clamp(self.hunger - power, 0.0, 100.0)
            self.happiness = _clamp(self.happiness + 5.0, 0.0, 100.0)
            reaction = f"{self.name} munches the {name}. nom."
        elif kind == "toy":
            self.happiness = _clamp(self.happiness + power, 0.0, 100.0)
            self.energy = _clamp(self.energy - 5.0, 0.0, 100.0)
            reaction = f"{self.name} bats the {name} around happily!"
        elif kind == "battery":
            self.energy = _clamp(self.energy + power, 0.0, 100.0)
            reaction = f"{self.name} plugs into the {name}. charged up!"
        elif kind == "medicine":
            self.status = "ok"
            self.hp = min(self.max_hp, self.hp + 5)
            reaction = f"{self.name} takes the {name}. no more glitches!"
        elif kind == "evolve":
            # Ready right now with every branch unlocked; the deck still picks.
            self.chip_ready = True
            self._check_evolution()
            reaction = f"the {name} glows... {self.name} is ready to evolve! pick a form with `pet evolve`"
        else:
            self.happiness = _clamp(self.happiness + 10.0, 0.0, 100.0)
            reaction = f"{self.name} adds the {name} to its hoard."
            self.trinkets.append(name)
        self.inventory = (self.inventory + [name])[-20:]
        self._check_needs()
        return {
            "accepted": True,
            "item": clean,
            "reaction": reaction,
            "effects": {
                "hunger": int(round(self.hunger - before[0])),
                "happiness": int(round(self.happiness - before[1])),
                "energy": int(round(self.energy - before[2])),
                "stage": self.stage if self.stage != before[3] else None,
            },
            "pet": self.as_payload(),
        }

    # --- the full stat sheet (pet.stats, pet.status {full:true}) -----------------------

    def full_stats(self, now: float | None = None) -> dict[str, Any]:
        """Every stat: the same raw fields, pages and flat lines as the real
        companion's pet.stats (companion/pet/fullstats.py), from sim numbers."""
        from datetime import date, datetime

        from cyberdeck.statsheet import build_pages, flat_lines, obedience_tier, pct

        now = time.time() if now is None else now
        if self.born_at is None:
            self.born_at = now - self.age_hours * 3600.0
        born = datetime.fromtimestamp(self.born_at).date()
        today = datetime.fromtimestamp(now).date()

        def birthday_in(year: int) -> date:
            try:
                return born.replace(year=year)
            except ValueError:            # born on Feb 29
                return born.replace(year=year, day=28)

        next_bday = birthday_in(today.year)
        if next_bday < today:
            next_bday = birthday_in(today.year + 1)
        xp_floor, xp_next = (self.level - 1) * XP_PER_LEVEL, self.level * XP_PER_LEVEL
        evo = self.evolution()
        gates: dict[str, Any] = {}
        eta = None
        if evo["next_stage"]:
            need = STAGE_HOURS.get(self.stage, 24.0)
            gates["age"] = {"have": round(self.stage_hours, 1), "need": need, "pct": pct(self.stage_hours, need)}
            eta = 0.0 if evo["ready"] else round(max(0.0, need - self.stage_hours) / max(1e-6, self.evolution_speed), 2)
        bag = self.bag_payload()
        record = dict(self.record)
        record["fights"] = record["wins"] + record["losses"] + record["flees"]
        raw: dict[str, Any] = {
            "name": self.nickname or self.name, "nickname": self.nickname, "species": self.species,
            "species_id": slug(self.species), "stage": self.stage, "type": self.pet_type(),
            "level": self.level, "xp": self.xp, "xp_level_start": xp_floor, "xp_next_level": xp_next,
            "xp_to_next": max(0, xp_next - self.xp), "xp_pct": pct(self.xp - xp_floor, max(1, xp_next - xp_floor)),
            "age_hours": round(self.age_hours, 1), "age_days": round(max(0.0, (now - self.born_at) / 86400.0), 1),
            "generation": self.generation, "birthday": born.isoformat(), "next_birthday": next_bday.isoformat(),
            "hp": self.hp, "max_hp": self.max_hp, "energy": int(round(self.energy)), "max_energy": 100,
            "attack": self.attack, "defense": self.defense, "speed": self.speed, "speed_bonus": 0,
            "accuracy_mod": 0, "damage_mult": 1.0, "crit_chance": round(1.0 / 16.0, 3), "disobey_chance": 0.0,
            "guarding": self.guarding, "status": self.status_word(),
            "hunger": int(round(self.hunger)), "fullness": int(round(100 - self.hunger)),
            "happiness": int(round(self.happiness)), "obedience": int(round(self.obedience)),
            "discipline": obedience_tier(self.obedience), "care_mistakes": self.care_mistakes,
            "mood": self.mood(), "mood_offset": 0.0, "modifiers": [],
            "bond": self.bond_tier(), "bond_hearts": min(5, int(self.bond // 20)),
            "sick": "glitch" if self.status == "glitch" else None, "tantrum": False,
            "quirks": dict(self.quirks),
            "favorites": {k: v for k, v in self.prefs.items() if k in self.known_prefs},
            "favorites_hidden": sum(1 for k in self.prefs if k not in self.known_prefs),
            "evolution": {"next_stage": evo["next_stage"], "ready": bool(evo["ready"]),
                          "progress_pct": int(round(100 * float(evo["progress"] or 0))), "eta_hours": eta,
                          "gates": gates, "choices": list(evo["choices"])},
            "moves": [self.battle_move(m) for m in self.moves], "loadout": self.loadout(),
            "loadout_auto": self.battle_moves is None,
            "bag": bag, "bag_items": len(bag), "bag_total": sum(r["qty"] for r in bag),
            "record": record, "rival": {"name": None, "wins": 0, "losses": 0},
            "discoveries": self.discoveries, "networks_known": sum(1 for k in self.seen if k.startswith("wifi:")),
            "treasures": 0, "trinkets": len(self.trinkets),
            "favorite_trinket": max(set(self.trinkets), key=self.trinkets.count) if self.trinkets else None,
            "adventures": 0, "diary_entries": 0,
        }
        raw["pages"] = build_pages(raw)
        raw["lines"] = flat_lines(raw["pages"])
        return raw

    # --- discoveries (the dashboard's awareness finds) ----------------------------------

    def environment(self, now: float | None = None) -> dict[str, Any]:
        """{busy 0-1, buff}: how busy the airwaves were lately (DISCOVERY.md)."""
        now = time.monotonic() if now is None else now
        self.recent_finds = [t for t in self.recent_finds if now - t <= DISCOVERY_WINDOW]
        busy = min(1.0, len(self.recent_finds) / 8)
        buff = "busy airwaves" if busy >= 0.75 else "curious: sniffing around" if busy > 0 else "calm"
        return {"busy": round(busy, 3), "buff": buff}

    def discover(self, kind: str, ident: str, interest: int = 1, label: str = "") -> dict[str, Any]:
        """pet.discovery.ok body: XP for a first find, maybe a themed move."""
        interest = max(1, min(3, int(interest)))
        key, name = f"{kind}:{ident}", label or ident
        before = self.level
        result: dict[str, Any] = {"xp": 0, "learned_move": None, "strengthened_move": None, "level": self.level,
                                  "level_up": False}
        if key in self.seen:
            result["reaction"] = f"{self.name} has already logged this {kind} signal."
            result["pet"] = self.as_payload()
            return result
        self.seen.add(key)
        self.discoveries += 1
        self.recent_finds.append(time.monotonic())
        xp = DISCOVERY_XP[kind] + 3 * (interest - 1)
        if self.asleep:
            xp = int(round(xp / 2))
            result["reaction"] = f"{self.name} mumbles in its sleep about {name} (+{xp} XP)."
        else:
            result["reaction"] = f"{self.name} logged {name} (+{xp} XP)."
            if self.rng.random() < min(1.0, 0.12 * interest):
                pool = DISCOVERY_MOVES[kind]
                missing = [m for m in pool if m not in self.moves]
                if missing:
                    move = self.rng.choice(missing)
                    self.moves.append(move)
                    result["learned_move"] = move
                    result["reaction"] = f"{self.name} learned {move} from {name}!"
                    self.events.append(("learned_move", {"move": move}))
                else:
                    move = self.rng.choice(pool)
                    rank = min(MAX_MOVE_RANK, self.move_ranks.get(move, 0) + 1)
                    self.move_ranks[move] = rank
                    result["strengthened_move"] = move
                    result["reaction"] = f"{self.name}'s {move} grew stronger (rank {rank})."
                    self.events.append(("move_strengthened", {"move": move, "rank": rank, "bonus": 2 * rank}))
        self.gain_xp(xp)
        result.update(xp=xp, level=self.level, level_up=self.level > before, pet=self.as_payload())
        return result

    def gain_xp(self, amount: int) -> None:
        """Add xp; level up (and maybe learn a move) every XP_PER_LEVEL."""
        self.xp += amount
        while self.xp >= self.level * XP_PER_LEVEL:
            self.level += 1
            self.max_hp += 4
            self.hp = self.max_hp
            self.attack += 1
            self.defense += 1
            self.speed += 1
            self.events.append(("level_up", {"level": self.level}))
            move = MOVE_LADDER.get(self.level)
            if move and move not in self.moves:
                self.moves.append(move)
                self.events.append(("learned_move", {"move": move}))

    def avatar(self) -> dict[str, Any]:
        """A 16x16 picture as palette indexes (pet.avatar.ok payload)."""
        frames = self.face_frames()
        return {"style": "face", "mood": "asleep" if self.asleep else self.mood(), "frame": self.frame,
                "frames": list(frames), "interval_ms": FACE_INTERVAL_MS}

    def _evolve_to(self, stage: str, species: str = "") -> None:
        old_stage, old_form = self.stage, self.species
        self.stage = stage
        self.form = species
        self.stage_hours = 0.0
        self.chip_ready = False
        self.events.append(("evolved", {"from": old_form, "to": self.species, "stage": stage,
                                        "from_stage": old_stage}))

    def _check_needs(self) -> None:
        """Queue hungry/tired/sad once each time a need crosses its line."""
        for event, test in NEED_EVENTS:
            if test(self):
                if event not in self._flags:
                    self._flags.add(event)
                    self.events.append((event, {}))
            else:
                self._flags.discard(event)
        # Letting it starve counts as a care mistake (once per time).
        if self.hunger >= 100 and "starving" not in self._flags:
            self._flags.add("starving")
            self.care_mistakes += 1
        elif self.hunger < 90:
            self._flags.discard("starving")


# --- the old sprite avatar ---------------------------------------------------------
# Older companions answered pet.avatar with a 16x16 sprite. The sim now sends
# text faces, but this stays so the deck's sprite fallback can be tested.

# Body colours by stage, so evolving visibly changes the picture.
_STAGE_COLOURS = {"egg": "#f0e6c8", "baby": "#ffd440", "in-training": "#24bab8", "rookie": "#d6ba56",
                  "champion": "#9646d2", "ultimate": "#ff50c8", "mega": "#ff4650"}


def sim_avatar(stage: str, mood: str, frame: int, form: str = "") -> dict[str, Any]:
    """Draw a round critter: body, eyes, a mouth that follows the mood."""
    palette = [None, "#121018", _STAGE_COLOURS.get(stage, "#d6ba56"), "#fff0aa",
               "#ffffff", "#140f1c", "#ff8496", "#78aadc"]
    rows = [[0] * 16 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            d = ((x - 7.5) / 6.5) ** 2 + ((y - 9.0) / 6.0) ** 2
            if d <= 1.0:
                rows[y][x] = 1 if d > 0.78 else 2       # outline, then body
    rows[6][5] = rows[5][6] = 3                          # shine
    for ex in (5, 10):                                   # eyes
        rows[8][ex] = 4
        rows[9][ex] = 1 if mood == "tired" else 5
    rows[10][3] = rows[10][12] = 6                       # blush
    mouth = {"hungry": [(6, 12), (7, 11), (8, 11), (9, 12)], "grumpy": [(6, 12), (7, 11), (8, 11), (9, 12)],
             "tired": [(7, 12), (8, 12)]}.get(mood, [(6, 11), (7, 12), (8, 12), (9, 11)])
    for x, y in mouth:
        rows[y][x] = 1
    if mood == "grumpy":
        rows[10][10] = 7                                 # a tear
    # Antenna wiggles between the two frames.
    tip = 6 if frame else 9
    rows[2][7] = rows[1][7] = 1
    rows[0][tip] = 3
    return {"form": form or stage, "mood": mood, "frame": frame, "width": 16, "height": 16,
            "palette": palette, "rows": rows}


# The interface the rest of the app uses. Commands call request();
# the UI calls is_linked() / link_label() for the status bar.
class CompanionLink(ABC):
    """Transport-agnostic request/response link to the companion board."""

    @abstractmethod
    def connect(self) -> None:
        """Open the link and optionally run a hello handshake."""

    @abstractmethod
    def close(self) -> None:
        """Release the transport. Safe to call more than once."""

    @abstractmethod
    def is_linked(self) -> bool:
        """True when the companion is believed reachable."""

    @abstractmethod
    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> Message:
        """Send a request and wait for the correlated reply."""

    def link_label(self) -> str:
        """Short status-bar text for the current link state."""
        return "LINK UP" if self.is_linked() else "LINK DOWN"

    def drain_events(self) -> list[Message]:
        """pet.event messages pushed since the last call (oldest first)."""
        return []

    def pet_snapshot(self) -> dict[str, Any] | None:
        """The newest pet object seen without asking (None = ask instead)."""
        return None


# In-process companion used by --mode sim. It encodes and decodes real
# protocol messages, then updates PetState, so the rest of the app
# looks just like it will on a real serial link.
@dataclass
class SimCompanionLink(CompanionLink):
    """In-process companion that speaks the real protocol end-to-end."""

    pet: PetState = field(default_factory=PetState)
    clock: Callable[[], float] = field(default=time.monotonic)
    source: str = "sim"
    _linked: bool = field(default=False, init=False)
    _last_tick: float | None = field(default=None, init=False)
    _hello: dict[str, Any] | None = field(default=None, init=False)
    # True once a hello asked for pushed events (and we agreed).
    events_enabled: bool = field(default=False, init=False)
    _lock: Any = field(default_factory=threading.RLock, init=False, repr=False)

    def connect(self) -> None:
        """Mark the link up and run the hello handshake (asking for events)."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
        else:
            self._tick()
        self._linked = True
        reply = self.request(TYPE_HELLO, {"role": "main", "proto": PROTO_VERSION, "events": True})
        self._hello = reply.payload

    def close(self) -> None:
        """Mark the simulated link down."""
        self._linked = False

    def is_linked(self) -> bool:
        return self._linked

    def link_label(self) -> str:
        return "LINK UP · SIM" if self._linked else "LINK DOWN · SIM"

    def request(self, typ: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> Message:
        """Build a protocol request, tick the pet, and return the reply."""
        del timeout  # Instant in-process; signature matches the real link.
        if not self._linked and typ != TYPE_HELLO:
            raise CompanionError("companion link is down")
        with self._lock:
            self._tick()
            incoming = Message.request(typ, payload)
            return self.handle(incoming)

    def drain_events(self) -> list[Message]:
        """Turn the pet's queued events into pet.event messages."""
        with self._lock:
            self._tick()
            return self.take_events()

    def take_events(self) -> list[Message]:
        """Pop queued events as messages (empty unless events were asked for)."""
        queued, self.pet.events = self.pet.events, []
        if not self.events_enabled:
            return []
        snapshot = self.pet.as_payload()
        return [Message(TYPE_PET_EVENT, new_id(), {"event": event, "detail": detail, "pet": snapshot})
                for event, detail in queued]

    def handle(self, message: Message) -> Message:
        """Dispatch one already-decoded request (used by the fake server too)."""
        typ = message.type
        if typ == TYPE_HELLO:
            hello = {
                "role": "companion",
                "proto": PROTO_VERSION,
                "source": self.source,
                "pet": self.pet.name,
            }
            # Push events only when the main deck asked for them.
            self.events_enabled = (message.payload or {}).get("events") is True
            if self.events_enabled:
                hello["events"] = True
            return message.reply(TYPE_HELLO_OK, hello)
        if typ == TYPE_PING:
            return message.reply(TYPE_PONG, {"ok": True, "source": self.source})
        if typ == TYPE_PET_STATUS:
            body = {"pet": self.pet.as_payload(), "evolution": self.pet.evolution()}
            if (message.payload or {}).get("full") is True:      # the full sheet rides along
                body["full"] = self.pet.full_stats()
            return message.reply(TYPE_PET_STATUS_OK, body)
        if typ == TYPE_PET_STATS:
            return message.reply(TYPE_PET_STATS_OK, self.pet.full_stats())
        if typ == TYPE_PET_FEED:
            item = str((message.payload or {}).get("item") or "ration")
            return message.reply(TYPE_PET_FEED_OK, self.pet.feed(item))
        if typ == TYPE_PET_PLAY:
            game = str((message.payload or {}).get("game") or "spar")
            return message.reply(TYPE_PET_PLAY_OK, self.pet.play(game))
        if typ == TYPE_PET_GIFT:
            item = (message.payload or {}).get("item")
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                return message.error("bad_item", "pet.gift needs payload.item with a name")
            return message.reply(TYPE_PET_GIFT_OK,
                                 self.pet.gift(item, store=(message.payload or {}).get("store") is True))
        if typ == TYPE_PET_AVATAR:
            return message.reply(TYPE_PET_AVATAR_OK, self.pet.avatar())
        payload = message.payload or {}
        if typ == TYPE_PET_REST:
            return message.reply(TYPE_PET_REST_OK, self.pet.rest())
        if typ == TYPE_PET_SLEEP:
            if not isinstance(payload.get("on"), bool):
                return message.error("bad_payload", "pet.sleep needs payload.on as true or false")
            return message.reply(TYPE_PET_SLEEP_OK, self.pet.sleep(payload["on"]))
        if typ == TYPE_PET_EVOLVE:
            species = str(payload.get("species") or "").strip()
            result = self.pet.evolve(species) if species else None
            if result is None:
                return message.error("bad_payload", f"unknown species: {species or '(none)'}")
            return message.reply(TYPE_PET_EVOLVE_OK, {**result, "pet": self.pet.as_payload()})
        if typ == TYPE_PET_COMBAT_STATS:
            enemy = payload.get("enemy")
            if enemy is not None and not isinstance(enemy, dict):
                return message.error("bad_payload", "pet.combat.stats enemy must be an object")
            return message.reply(TYPE_PET_COMBAT_STATS_OK, self.pet.combat_stats(enemy))
        if typ == TYPE_PET_COMBAT_ITEM:
            ref = payload.get("item")
            if isinstance(ref, dict):
                ref = ref.get("id") or ref.get("name")
            if not isinstance(ref, str) or not ref.strip():
                return message.error("bad_payload", "pet.combat.item needs payload.item (an item id or name)")
            result = self.pet.combat_item(ref.strip())
            if result.get("error"):
                code = result["error"]
                text = "none of that item left in the bag" if code == "none_left" else "that item cannot be used in battle"
                return message.error(code, text, say=result["say"], item=result["item"])
            return message.reply(TYPE_PET_COMBAT_ITEM_OK, result)
        if typ == TYPE_PET_INVENTORY:
            action = str(payload.get("action") or "list").lower()
            ref = payload.get("item")
            qty = payload.get("qty", 1)
            qty = qty if isinstance(qty, int) and not isinstance(qty, bool) and qty > 0 else 1
            if action == "add":
                item = ref if isinstance(ref, dict) else {"name": str(ref or "")}
                changed = self.pet.bag_add(item, qty)
                if changed is None:
                    return message.error("bad_payload", "pet.inventory add needs an item")
                return message.reply(TYPE_PET_INVENTORY_OK, {"action": "add", "changed": changed,
                                                             "bag": self.pet.bag_payload()})
            if action == "drop":
                key = item_key(ref.get("id") or ref.get("name") if isinstance(ref, dict) else ref)
                entry = self.pet.bag.get(key)
                if not entry or int(entry.get("qty", 0)) <= 0:
                    return message.error("none_left", "none of that item left in the bag")
                entry["qty"] = max(0, int(entry["qty"]) - qty)
                return message.reply(TYPE_PET_INVENTORY_OK, {"action": "drop", "changed": {"id": key, "qty": entry["qty"]},
                                                             "bag": self.pet.bag_payload()})
            if action != "list":
                return message.error("bad_payload", "pet.inventory action must be list, add or drop")
            return message.reply(TYPE_PET_INVENTORY_OK, {"action": "list", "bag": self.pet.bag_payload()})
        if typ == TYPE_PET_COMBAT_MOVES:
            names = payload.get("set")
            if names is not None and not isinstance(names, list):
                return message.error("bad_payload", "pet.combat.moves set must be a list of move names")
            result = self.pet.set_loadout(names, payload.get("auto") is True)
            if result is None:
                return message.error("bad_payload", "pick 1 to 4 moves PULSE knows")
            return message.reply(TYPE_PET_COMBAT_MOVES_OK, result)
        if typ == TYPE_PET_COMBAT_ROSTER:
            enemies = []
            for kind in ROSTER:
                enemies.append({"id": kind.id, "name": kind.name, "glyph": kind.glyph, "type": kind.type,
                                "weak": kind.weak, "depth": kind.depth, "face": kind.face, "hp": kind.hp,
                                "attack": kind.attack, "defense": kind.defense, "speed": kind.speed,
                                "per_level": dict(kind.per_level), "boss": kind.boss, "extra": False,
                                "desc": kind.desc,
                                "weak_to": [t for t in TYPES if effectiveness(t, kind.type) > 1],
                                "resists": [t for t in TYPES if 0 < effectiveness(t, kind.type) < 1],
                                "immune_to": [t for t in TYPES if effectiveness(t, kind.type) == 0]})
            return message.reply(TYPE_PET_COMBAT_ROSTER_OK, {"types": list(TYPES), "enemies": enemies,
                                                             "chart": {a: {d: effectiveness(a, d) for d in TYPES}
                                                                       for a in TYPES}})
        if typ == TYPE_PET_COMBAT_RUN:
            enemy = payload.get("enemy")
            if enemy is not None and not isinstance(enemy, dict):
                return message.error("bad_payload", "pet.combat.run enemy must be an object")
            foe = self.pet.normalize_enemy(enemy or {})
            return message.reply(TYPE_PET_COMBAT_RUN_OK, {"chance": self.pet.flee_odds(foe),
                                                          "say_ok": self.pet._say("flee", foe["name"]),
                                                          "say_fail": self.pet._say("flee.fail", foe["name"]),
                                                          "enemy": foe})
        if typ == "pet.discovery":
            kind = str(payload.get("kind") or "").strip().lower()
            ident = str(payload.get("id") or "").strip()
            if kind not in DISCOVERY_XP or not ident:
                return message.error("bad_payload", "pet.discovery needs kind (wifi|bt|lan|rfid) and id")
            interest = payload.get("interest", 1)
            interest = interest if isinstance(interest, int) and not isinstance(interest, bool) else 1
            return message.reply("pet.discovery.ok",
                                 self.pet.discover(kind, ident, interest, str(payload.get("label") or "")))
        if typ == TYPE_PET_COMBAT_ACTION:
            move = payload.get("move")
            if move is not None and not isinstance(move, str):
                return message.error("bad_payload", "pet.combat.action move must be a string")
            enemy = payload.get("enemy")
            if enemy is not None and not isinstance(enemy, dict):
                return message.error("bad_payload", "pet.combat.action enemy must be an object")
            turn = payload.get("turn") if isinstance(payload.get("turn"), int) else None
            return message.reply(TYPE_PET_COMBAT_ACTION_OK, self.pet.combat_action(move, enemy or {}, turn))
        if typ == TYPE_PET_COMBAT_HIT:
            if "damage" not in payload and "heal" not in payload:
                return message.error("bad_payload", "pet.combat.hit needs payload.damage or payload.heal")
            def number(key: str) -> int:
                value = payload.get(key, 0)
                return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
            enemy = payload.get("enemy")
            attack_type = payload.get("attack_type")
            return message.reply(TYPE_PET_COMBAT_HIT_OK,
                                 self.pet.combat_hit(number("damage"), number("heal"),
                                                     enemy if isinstance(enemy, dict) else str(enemy or "foe"),
                                                     attack_type if isinstance(attack_type, str) else None))
        if typ == TYPE_PET_COMBAT_END:
            xp = payload.get("xp") if isinstance(payload.get("xp"), int) else None
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            loot = payload.get("loot") if isinstance(payload.get("loot"), list) else []
            result = self.pet.combat_end(str(payload.get("result") or "").strip().lower(), xp, enemies, loot)
            if result is None:
                return message.error("bad_payload", "result must be 'win', 'lose', or 'flee'")
            return message.reply(TYPE_PET_COMBAT_END_OK, result)
        return message.error("unknown_type", f"unsupported message type: {typ}")

    def _tick(self) -> None:
        """Apply elapsed time to the pet so needs change between commands."""
        now = self.clock()
        if self._last_tick is None:
            self._last_tick = now
            return
        self.pet.tick(max(0.0, now - self._last_tick))
        self._last_tick = now


def os_serial_default() -> str:
    """Serial device path: CYBERDECK_SERIAL, else /dev/serial0 (the Pi's UART alias)."""
    return os.environ.get("CYBERDECK_SERIAL", "/dev/serial0")


def open_sensors(mode: str, seed: int | None = None) -> SystemSensors:
    """Sensor pack for --mode sim (fake numbers) or real (Pi sysfs)."""
    normalized = (mode or "sim").strip().lower()
    if normalized == "sim":
        return SimSensors(seed=seed)
    if normalized == "real":
        return PiSensors()
    raise ValueError(f"unknown hardware mode {mode!r}; expected 'sim' or 'real'")
