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
    TYPE_PET_COMBAT_STATS,
    TYPE_PET_COMBAT_STATS_OK,
    TYPE_PET_EVOLVE,
    TYPE_PET_EVOLVE_OK,
    TYPE_PET_EVENT,
    TYPE_PET_FEED,
    TYPE_PET_FEED_OK,
    TYPE_PET_GIFT,
    TYPE_PET_GIFT_OK,
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
               "asleep": ("(-_-) zZ", "(-_-) Zz")}
FACE_INTERVAL_MS = 600

# Item kinds the pet understands in a pet.gift message (see PROTOCOL.md).
GIFT_KINDS = {"food", "toy", "battery", "evolve", "trinket"}

# Moves the sim pet learns as it levels up.
MOVE_LADDER = {2: "static zap", 3: "wave dash", 5: "signal burst", 7: "nova flare"}
XP_PER_LEVEL = 40

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

    # --- combat ------------------------------------------------------------------------

    def _move_info(self, name: str) -> tuple[int, int, int, str | None]:
        power, accuracy, cost = MOVE_INFO.get(name.lower(), DEFAULT_MOVE)
        return power, accuracy, cost, None

    def combat_stats(self) -> dict[str, Any]:
        """pet.combat.stats.ok: the live combat sheet (energy lives in the pet object)."""
        moves = []
        for name in self.moves:
            power, accuracy, cost, effect = self._move_info(name)
            moves.append({"name": name, "power": power, "accuracy": accuracy, "energy_cost": cost,
                          "kind": "electric", "effect": effect})
        return {"hp": self.hp, "max_hp": self.max_hp, "attack": self.attack, "defense": self.defense,
                "speed": self.speed, "level": self.level, "moves": moves, "fainted": self.hp <= 0,
                "asleep": self.asleep, "guarding": self.guarding}

    def _pick_move(self, requested: str | None) -> tuple[str, bool]:
        """(move name, fallback?) — the pet's own pick when none (or a bad one) is asked for."""
        def affordable(name: str) -> bool:
            return self._move_info(name)[2] <= self.energy
        if requested:
            known = next((m for m in self.moves if m.lower() == requested.lower()), None)
            if known and affordable(known):
                return known, False
            chirp = next((m for m in self.moves if m.lower() == "chirp"), None)
            return (chirp, True) if chirp and affordable(chirp) else ("Guard", True)
        options = [m for m in self.moves if affordable(m)]
        if not options:
            return "Guard", False
        best = max(options, key=lambda m: self._move_info(m)[0] * self._move_info(m)[1])
        return best, False

    def combat_action(self, move: str | None, enemy: dict[str, Any]) -> dict[str, Any]:
        """PULSE acts on an enemy (the formulas in DUNGEON_INTEGRATION.md)."""
        foe = str(enemy.get("name") or "foe")
        out: dict[str, Any] = {"move": move, "hit": False, "damage": 0, "effect": None, "energy_spent": 0}
        if self.asleep or self.hp <= 0:
            why = "asleep" if self.asleep else "fainted"
            out.update(acted=False, reason=why, reaction=f"{self.name} is {why} and cannot fight.",
                       pet_hp=self.hp, pet=self.as_payload())
            return out
        name, fallback = self._pick_move(move)
        out["move"] = name
        if name == "Guard":
            self.guarding = True
            out.update(hit=True, effect="guard", reaction=f"{self.name} braces itself (Guard).")
        else:
            power, accuracy, cost, _effect = self._move_info(name)
            self.energy = _clamp(self.energy - cost, 0.0, 100.0)
            out["energy_spent"] = cost
            speed = enemy.get("speed") if isinstance(enemy.get("speed"), int) else 0
            chance = max(5, min(100, accuracy + (self.speed - speed) // 2))
            if self.rng.randint(1, 100) <= chance:
                defense = enemy.get("defense") if isinstance(enemy.get("defense"), int) else 0
                damage = max(1, power + self.attack - defense)
                out.update(hit=True, damage=damage, reaction=f"{self.name} uses {name} on the {foe} for {damage}.")
            else:
                out["reaction"] = f"{self.name}'s {name} misses the {foe}."
        out.update(acted=True, pet_hp=self.hp)
        if fallback:
            out["fallback"] = True
        out["pet"] = self.as_payload()
        return out

    def combat_hit(self, damage: int = 0, heal: int = 0, enemy: str = "foe") -> dict[str, Any]:
        """An enemy hits PULSE (defense and Guard soak some), or a heal (negative damage too)."""
        if damage < 0:
            heal, damage = heal - damage, 0
        healed = 0
        if heal > 0:
            before = self.hp
            self.hp = min(self.max_hp, self.hp + heal)
            healed = self.hp - before
        taken = 0
        if damage > 0:
            taken = max(0, damage - self.defense // 4)
            if self.guarding:
                taken //= 2
                self.guarding = False
            self.hp = max(0, self.hp - taken)
        return {"damage_taken": taken, "healed": healed, "pet_hp": self.hp, "fainted": self.hp <= 0,
                "pet": self.as_payload()}

    def combat_end(self, result: str, xp: int, enemies: list[Any]) -> dict[str, Any] | None:
        """The run is over: grant xp (events report level-ups and new moves). None = bad result."""
        if result not in {"win", "lose", "flee"}:
            return None
        xp = max(0, int(xp))
        self.gain_xp(xp)
        if result == "win":
            self.happiness = _clamp(self.happiness + 5.0, 0.0, 100.0)
            reaction = f"{self.name} cheers! (+{xp} xp)"
        elif result == "lose":
            self.happiness = _clamp(self.happiness - 8.0, 0.0, 100.0)
            if self.hp <= 0:
                self.care_mistakes += 1
            reaction = f"{self.name} droops after the loss. (+{xp} xp)"
        else:
            reaction = f"{self.name} is glad to be out of there. (+{xp} xp)"
        self._check_needs()
        self._check_evolution()
        return {"result": result, "xp_granted": xp, "reaction": reaction, "pet": self.as_payload()}


    def mood(self) -> str:
        """Pick a short mood word from the current needs."""
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
            "care_mistakes": self.care_mistakes,
            "asleep": self.asleep,
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

    def gift(self, item: dict[str, Any]) -> dict[str, Any]:
        """Receive an item from the main deck (dungeon loot). See PROTOCOL.md."""
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "trinket").lower()
        if kind not in GIFT_KINDS:
            kind = "trinket"
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
        elif kind == "evolve":
            # Ready right now with every branch unlocked; the deck still picks.
            self.chip_ready = True
            self._check_evolution()
            reaction = f"the {name} glows... {self.name} is ready to evolve! pick a form with `pet evolve`"
        else:
            self.happiness = _clamp(self.happiness + 10.0, 0.0, 100.0)
            reaction = f"{self.name} adds the {name} to its hoard."
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
            return message.reply(TYPE_PET_STATUS_OK, {"pet": self.pet.as_payload(),
                                                      "evolution": self.pet.evolution()})
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
            return message.reply(TYPE_PET_GIFT_OK, self.pet.gift(item))
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
            return message.reply(TYPE_PET_COMBAT_STATS_OK, self.pet.combat_stats())
        if typ == TYPE_PET_COMBAT_ACTION:
            move = payload.get("move")
            if move is not None and not isinstance(move, str):
                return message.error("bad_payload", "pet.combat.action move must be a string")
            enemy = payload.get("enemy")
            if enemy is not None and not isinstance(enemy, dict):
                return message.error("bad_payload", "pet.combat.action enemy must be an object")
            return message.reply(TYPE_PET_COMBAT_ACTION_OK, self.pet.combat_action(move, enemy or {}))
        if typ == TYPE_PET_COMBAT_HIT:
            if "damage" not in payload and "heal" not in payload:
                return message.error("bad_payload", "pet.combat.hit needs payload.damage or payload.heal")
            def number(key: str) -> int:
                value = payload.get(key, 0)
                return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
            enemy = payload.get("enemy")
            name = enemy.get("name") if isinstance(enemy, dict) else enemy
            return message.reply(TYPE_PET_COMBAT_HIT_OK,
                                 self.pet.combat_hit(number("damage"), number("heal"), str(name or "foe")))
        if typ == TYPE_PET_COMBAT_END:
            xp = payload.get("xp") if isinstance(payload.get("xp"), int) else 0
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            result = self.pet.combat_end(str(payload.get("result") or "").strip().lower(), xp, enemies)
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
