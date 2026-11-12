"""The Creature class: one pet, with stats, moves, and a clock.

tick(dt) is the heartbeat. It drains hunger and energy, shifts mood, ages
the pet, and then checks for care mistakes, level-ups, new level-moves, and
evolution readiness. feed / train / rest are the caretaker actions. to_dict and
from_dict make the whole object JSON-friendly so save.py can be tiny.
"""

from __future__ import annotations

import random
import time

from pet.evolution import (
    EVOLVE_SOON_PROGRESS,
    STARTER_SPECIES_ID,
    Species,
    choose_next_form,
    evolution_status as _evolution_status,
    get_species,
    resolve_species,
    rules_from,
    time_gates_met,
)
from pet.life import Life
from pet.moves import MOVE_LIBRARY, get_move, moves_for_level
from pet.stats import Stats


# How fast the meters drain, per simulated hour.
HUNGER_DRAIN_PER_HOUR = 4.0
ENERGY_DRAIN_PER_HOUR = 5.0
MOOD_DRAIN_PER_HOUR = 1.0

# If a meter is at or below this after a tick, that tick counts as a mistake.
CARE_MISTAKE_THRESHOLD = 5.0

# How much each action swings the meters / stats.
FEED_HUNGER = 35.0
FEED_MOOD = 6.0
REST_ENERGY = 40.0
REST_MOOD = 4.0
TRAIN_ENERGY_COST = 18.0
TRAIN_HUNGER_COST = 6.0
TRAIN_STAT_GAIN = 1
TRAIN_MOOD = 3.0

# Instant rest used by the protocol (smaller than the demo caretaker rest).
SHORT_REST_ENERGY = 20.0
SHORT_REST_MOOD = 2.0
SHORT_REST_HP = 6
REST_HP = 8

# While asleep: energy refills, hunger slows, HP trickles back.
ENERGY_SLEEP_PER_HOUR = 25.0
HUNGER_SLEEP_FACTOR = 0.4
HP_REGEN_PER_HOUR = 8.0

# XP awards from caretaker actions. Discovery XP lives in discovery/effects.py.
FEED_XP = 1
TRAIN_XP = 5

# Level 1->2 costs 10 XP, 2->3 costs 20, 3->4 costs 30, ...
XP_PER_LEVEL_STEP = 10


def xp_needed_for_level(level: int) -> int:
    """Total XP required to *reach* this level from level 1.

    Level 1 is 0, level 2 is 10, level 3 is 30, level 4 is 60, and so on.
    This is the triangular series 10 * (1 + 2 + ... + (level-1)).
    """
    if level <= 1:
        return 0
    steps = level - 1
    return XP_PER_LEVEL_STEP * steps * (steps + 1) // 2


def level_for_xp(xp: int) -> int:
    """The level a creature with this much total XP should be."""
    level = 1
    while xp >= xp_needed_for_level(level + 1):
        level += 1
    return level


class Creature:
    """A single virtual pet."""

    def __init__(
        self,
        name: str = "Pulse",
        species_id: str = STARTER_SPECIES_ID,
        stats: Stats | None = None,
        known_move_ids: list[str] | None = None,
        discovered_ids: set[str] | None = None,
        inventory: list[str] | None = None,
        asleep: bool = False,
        evolution_ready_override: bool = False,
        evolve_soon_announced: bool = False,
        evolve_choice_announced: bool = False,
        sighting_keys: set[str] | None = None,
        move_ranks: dict[str, int] | None = None,
        life: Life | None = None,
    ) -> None:
        self.name = name
        self.species_id = species_id
        self.stats = stats if stats is not None else Stats()
        self.stats.clamp()
        # discovered_ids remembers signal ids already processed, so a network
        # the pet has already "seen" does not keep awarding XP every tick.
        self.discovered_ids: set[str] = set(discovered_ids or [])
        # Names of gifts received over the link (newest last). Kept short.
        self.inventory: list[str] = list(inventory or [])
        self.asleep = bool(asleep)
        # Fractional HP regen bank so small dt still adds up while sleeping.
        self._hp_regen_bank = 0.0
        # Set by a guard move; cleared after the next incoming hit.
        self.guarding = False
        # An evolve-chip skips the age/level/XP gates until the player picks.
        self.evolution_ready_override = bool(evolution_ready_override)
        # "evolve_soon" / "evolve_choice" already announced for this form.
        self.evolve_soon_announced = bool(evolve_soon_announced)
        self.evolve_choice_announced = bool(evolve_choice_announced)
        # Protocol notices drained by the link handler (kind, detail).
        self._evo_notices: list[tuple[str, dict]] = []
        # Dashboard first-sightings (kind:id) and per-move strengthen ranks.
        self.sighting_keys: set[str] = set(sighting_keys or [])
        self.move_ranks: dict[str, int] = {
            str(k): int(v) for k, v in (move_ranks or {}).items()
        }
        # Rolling window of first-finds for the environment busy meter
        # (monotonic timestamps; not persisted).
        self._recent_sightings: list[tuple[float, int]] = []
        self._last_find_at: float | None = None
        if known_move_ids is None:
            # Brand-new pets start with every move their current level unlocks
            # (for a level-1 Chirplet that is just Chirp).
            self.known_move_ids: list[str] = [
                move.id for move in moves_for_level(self.stats.level)
            ]
        else:
            self.known_move_ids = list(known_move_ids)
        # Bond, memory, moods, quirks, diary... (see pet/life.py).
        self.life: Life = life if life is not None else Life.new(random.Random(), time.time())

    @property
    def species(self) -> Species:
        """The Species record for the current form."""
        return get_species(self.species_id)

    @property
    def fainted(self) -> bool:
        """True when HP has reached 0. The pet cannot fight until it rests."""
        return self.stats.hp <= 0

    def tick(self, dt: float) -> list[str]:
        """Advance simulated time by dt hours and return a list of event lines.

        dt is in *simulated* hours, not wall-clock seconds. The headless demo
        treats one loop iteration as one hour (dt=1.0).
        """
        if dt <= 0:
            return []

        events: list[str] = []
        stats = self.stats

        stats.age += dt
        if self.asleep:
            stats.hunger -= HUNGER_DRAIN_PER_HOUR * HUNGER_SLEEP_FACTOR * dt
            stats.energy += ENERGY_SLEEP_PER_HOUR * dt
            self._regen_hp(HP_REGEN_PER_HOUR * dt)
        else:
            stats.hunger -= HUNGER_DRAIN_PER_HOUR * dt
            stats.energy -= ENERGY_DRAIN_PER_HOUR * dt

        # Mood slowly fades, and dips faster when the pet is hungry or tired.
        mood_delta = -MOOD_DRAIN_PER_HOUR * dt
        if stats.hunger < 30:
            mood_delta -= 2.0 * dt
        if not self.asleep and stats.energy < 30:
            mood_delta -= 2.0 * dt
        if stats.hunger > 70 and stats.energy > 70:
            mood_delta += 1.0 * dt  # a well-kept pet cheers up a little
        stats.mood += mood_delta

        # Starvation chips away at HP so neglect is visible on the sheet.
        if stats.hunger <= 0 and not self.asleep:
            lost = max(1, int(dt))
            stats.hp -= lost
            events.append(
                f"{self.name} is starving and loses {lost} HP (now {max(0, stats.hp)})."
            )

        stats.clamp()

        if self.asleep and stats.energy >= 100.0:
            self.asleep = False
            events.append(f"{self.name} wakes up, fully rested.")

        if (
            stats.hunger <= CARE_MISTAKE_THRESHOLD
            or (not self.asleep and stats.energy <= CARE_MISTAKE_THRESHOLD)
        ):
            stats.care_mistakes += 1
            reason = "hunger" if stats.hunger <= CARE_MISTAKE_THRESHOLD else "exhaustion"
            events.append(
                f"Care mistake ({reason})! Total mistakes: {stats.care_mistakes}."
            )

        events.extend(self._check_level_up())
        # The pet never auto-evolves; we only announce readiness.
        events.extend(self._announce_evolution())
        return events

    def feed(self) -> list[str]:
        """Give the pet a meal: fill hunger, lift mood, grant a little XP."""
        self.stats.hunger += FEED_HUNGER
        self.stats.mood += FEED_MOOD
        self.stats.clamp()
        events = [f"{self.name} eats. Hunger is now {self.stats.hunger:.0f}."]
        events.extend(self.add_xp(FEED_XP))
        return events

    def train(self, stat: str = "attack") -> list[str]:
        """Spend energy to raise one combat stat and earn XP.

        stat must be one of 'attack', 'defense', or 'speed'. Training while
        exhausted or asleep does nothing so the caretaker has to rest first.
        """
        if stat not in {"attack", "defense", "speed"}:
            raise ValueError(f"Cannot train {stat!r}; use attack, defense, or speed.")
        if self.asleep:
            return [f"{self.name} is asleep and will not train."]
        if self.stats.energy < TRAIN_ENERGY_COST:
            return [f"{self.name} is too tired to train."]

        self.stats.energy -= TRAIN_ENERGY_COST
        self.stats.hunger -= TRAIN_HUNGER_COST
        self.stats.mood += TRAIN_MOOD
        current = int(getattr(self.stats, stat))
        setattr(self.stats, stat, current + TRAIN_STAT_GAIN)
        self.stats.clamp()
        events = [
            f"{self.name} trains {stat}! {stat} is now {getattr(self.stats, stat)}, "
            f"energy {self.stats.energy:.0f}."
        ]
        events.extend(self.add_xp(TRAIN_XP))
        return events

    def rest(self) -> list[str]:
        """Recover energy, a little mood, and some HP. No XP — rest is not training."""
        self.stats.energy += REST_ENERGY
        self.stats.mood += REST_MOOD
        self.stats.hp = min(self.stats.max_hp, self.stats.hp + REST_HP)
        self.stats.clamp()
        return [f"{self.name} rests. Energy is now {self.stats.energy:.0f}."]

    def short_rest(self) -> list[str]:
        """A smaller instant rest used by the pet.rest protocol message."""
        self.stats.energy += SHORT_REST_ENERGY
        self.stats.mood += SHORT_REST_MOOD
        self.stats.hp = min(self.stats.max_hp, self.stats.hp + SHORT_REST_HP)
        self.stats.clamp()
        return [f"{self.name} takes a short rest. Energy is now {self.stats.energy:.0f}."]

    def energy_is_full(self) -> bool:
        return self.stats.energy >= 100.0

    def needs_hp(self) -> bool:
        return self.stats.hp < self.stats.max_hp

    def set_asleep(self, on: bool) -> list[str]:
        """Start or stop a longer sleep. Returns log lines (may be empty)."""
        if on:
            if self.asleep:
                return [f"{self.name} is already asleep."]
            if self.energy_is_full() and not self.needs_hp() and not self.fainted:
                return [f"{self.name} is already fully rested."]
            self.asleep = True
            return [f"{self.name} curls up and goes to sleep."]
        if not self.asleep:
            return [f"{self.name} is already awake."]
        self.asleep = False
        return [f"{self.name} is woken up."]

    def _regen_hp(self, amount: float) -> int:
        """Add fractional HP; returns the whole points actually applied."""
        if amount <= 0 or self.stats.hp >= self.stats.max_hp:
            return 0
        self._hp_regen_bank += amount
        gained = int(self._hp_regen_bank)
        if gained <= 0:
            return 0
        self._hp_regen_bank -= gained
        before = self.stats.hp
        self.stats.hp = min(self.stats.max_hp, self.stats.hp + gained)
        return self.stats.hp - before

    def add_xp(self, amount: int) -> list[str]:
        """Grant XP and immediately apply any level-ups it causes."""
        if amount <= 0:
            return []
        self.stats.xp += int(amount)
        events = [f"{self.name} gains {amount} XP (total {self.stats.xp})."]
        events.extend(self._check_level_up())
        return events

    def learn_move(self, move_id: str) -> list[str]:
        """Teach a library move if the pet does not already know it.

        Returns a log line on success, or an empty list if the move was
        already known. Raises KeyError for an id that is not in the library.
        """
        move = get_move(move_id)  # validates the id
        if move_id in self.known_move_ids:
            return []
        self.known_move_ids.append(move_id)
        return [f"{self.name} learned {move.name}!"]

    def unique_discovery_count(self) -> int:
        """Scanner signals plus unique dashboard kind+id sightings."""
        return len(self.discovered_ids) + len(self.sighting_keys)

    def note_sighting(self, stamp: float, interest: int) -> None:
        """Record a first dashboard find in the rolling environment window."""
        from discovery.sighting import ACTIVITY_WINDOW

        self._recent_sightings.append((float(stamp), int(interest)))
        self._last_find_at = float(stamp)
        keep = ACTIVITY_WINDOW * 2
        self._recent_sightings = [
            (ts, value) for ts, value in self._recent_sightings if stamp - ts <= keep
        ]

    def move_rank(self, move_id: str) -> int:
        """Saved strengthen rank for a move (0 if never boosted)."""
        try:
            return max(0, int(self.move_ranks.get(move_id, 0)))
        except (TypeError, ValueError):
            return 0

    def strengthen_move(self, move_id: str) -> int:
        """Bump a move's power rank by one, capped. Returns the new rank."""
        from discovery.sighting import MAX_RANK

        new_rank = min(MAX_RANK, self.move_rank(move_id) + 1)
        self.move_ranks[move_id] = new_rank
        return new_rank

    def effective_power(self, move) -> int:
        """Base power plus the saved rank bonus."""
        from discovery.sighting import effective_power as _effective

        return _effective(move.power, self.move_rank(move.id))

    def environment(self, now: float | None = None) -> dict:
        """pet.status.environment: busy 0-1 and a short buff label."""
        from discovery.sighting import environment_state

        state = environment_state(
            self._recent_sightings,
            now=now,
            last_find_at=self._last_find_at,
        )
        return {"busy": state["busy"], "buff": state["buff"]}

    def environment_bonuses(self, now: float | None = None) -> tuple[int, int]:
        """Temporary (mood_bonus, speed_bonus) from the environment window."""
        from discovery.sighting import environment_state

        state = environment_state(
            self._recent_sightings,
            now=now,
            last_find_at=self._last_find_at,
        )
        return int(state["mood_bonus"]), int(state["speed_bonus"])

    def known_moves(self):
        """Move objects for every id in known_move_ids, skipping unknowns."""
        result = []
        for move_id in self.known_move_ids:
            move = MOVE_LIBRARY.get(move_id)
            if move is not None:
                result.append(move)
        return result

    def _check_level_up(self) -> list[str]:
        """Raise level to match current XP and unlock any new level-moves."""
        events: list[str] = []
        target = level_for_xp(self.stats.xp)
        while self.stats.level < target:
            self.stats.level += 1
            events.append(f"{self.name} reached level {self.stats.level}!")
            for move in moves_for_level(self.stats.level):
                events.extend(self.learn_move(move.id))
        return events

    def evolution_status(self) -> dict:
        """Hints for pet.status.evolution (progress, ready, choices)."""
        return _evolution_status(
            self.species_id,
            self.stats,
            discovered=self.unique_discovery_count(),
            ready_override=self.evolution_ready_override,
        )

    def take_evolution_notices(self) -> list[tuple[str, dict]]:
        """Drain evolve_soon / evolve_choice notices for the link handler."""
        notices = self._evo_notices
        self._evo_notices = []
        return notices

    def _announce_evolution(self) -> list[str]:
        """Push evolve_soon then evolve_choice once when the gates are reached.

        An evolve-chip that jumps straight to ready still fires evolve_soon
        first, then evolve_choice. The pet does not change form here.
        """
        status = self.evolution_status()
        if not status.get("next_stage"):
            return []
        events: list[str] = []
        close = bool(status.get("ready")) or float(status.get("progress") or 0) >= EVOLVE_SOON_PROGRESS
        if close and not self.evolve_soon_announced:
            self.evolve_soon_announced = True
            candidates = list(status.get("to_candidates") or [])
            detail = {
                "from": status.get("from"),
                "to_candidates": candidates,
                "next_stage": status.get("next_stage"),
                "eta_hours": status.get("eta_hours"),
                "progress": status.get("progress"),
            }
            self._evo_notices.append(("evolve_soon", detail))
            names = ", ".join(candidates) or str(status.get("next_stage"))
            events.append(
                f"{self.name} is close to evolving ({status.get('from')} -> {names})."
            )
        if status.get("ready") and not self.evolve_choice_announced:
            # If a chip skipped the 90% window, fire soon first (above), then choice.
            self.evolve_choice_announced = True
            detail = {
                "from": status.get("from"),
                "next_stage": status.get("next_stage"),
                "choices": list(status.get("choices") or []),
                "progress": status.get("progress"),
            }
            self._evo_notices.append(("evolve_choice", detail))
            events.append(f"{self.name} is ready to evolve. Choose a form.")
        return events

    def catalyze_evolution(self) -> list[str]:
        """An evolve-chip: skip the time gates and become ready, but do not evolve."""
        if not rules_from(self.species_id):
            return []
        self.evolution_ready_override = True
        events = [
            f"An evo chip hums. {self.name} is ready to choose a new form."
        ]
        events.extend(self._announce_evolution())
        return events

    def next_form_id(self) -> str | None:
        """Preferred next species id (care/stat/discovery), or None at mega."""
        chosen = choose_next_form(
            self.species_id, self.stats, discovered=self.unique_discovery_count()
        )
        return chosen.id if chosen is not None else None

    def first_available_choice(self) -> str | None:
        """Species id of the first available branch, or None if none are ready."""
        status = self.evolution_status()
        if not status.get("ready"):
            return None
        for choice in status.get("choices") or []:
            if choice.get("available"):
                return str(choice.get("species_id") or choice.get("species") or "")
        return None

    def evolve_to(self, species: str) -> dict:
        """Player-chosen evolution. Never called automatically from tick.

        Returns a result dict:
            accepted, from, to, reaction, reason, unknown, events
        unknown=True means the name was not in the species list.
        """
        previous = self.species
        target = resolve_species(species) if species else None
        if target is None:
            return {
                "accepted": False,
                "unknown": True,
                "from": previous.name,
                "to": None,
                "reason": f"unknown species: {species!r}",
                "reaction": f"{self.name} tilts its head. it does not know that form.",
                "events": [],
            }
        rules = [rule for rule in rules_from(self.species_id) if rule.to_species == target.id]
        status = self.evolution_status()
        if not rules:
            return {
                "accepted": False,
                "unknown": False,
                "from": previous.name,
                "to": target.name,
                "reason": f"{target.name} is not a next form for {previous.name}",
                "reaction": f"{self.name} cannot become {target.name} from here.",
                "events": [],
            }
        if not status.get("ready"):
            return {
                "accepted": False,
                "unknown": False,
                "from": previous.name,
                "to": target.name,
                "reason": "not ready to evolve yet",
                "reaction": f"{self.name} is not ready to evolve yet.",
                "events": [],
            }
        rule = rules[0]
        choice = next(
            (c for c in status.get("choices") or [] if c.get("species_id") == target.id),
            None,
        )
        if choice is None or not choice.get("available"):
            hint = (choice or {}).get("hint") or rule.hint or "requirements not met"
            return {
                "accepted": False,
                "unknown": False,
                "from": previous.name,
                "to": target.name,
                "reason": f"{target.name} is not available ({hint})",
                "reaction": f"{self.name} cannot take that path yet ({hint}).",
                "events": [],
            }
        events = self._evolve_into(target)
        return {
            "accepted": True,
            "unknown": False,
            "from": previous.name,
            "from_stage": previous.stage,
            "to": target.name,
            "to_stage": target.stage,
            "reason": None,
            "reaction": f"{self.name} begins to glow... it is becoming {target.name}!",
            "events": events,
        }

    def advance_evolution(self) -> list[str]:
        """Auto-pick the first available choice (headless sim only).

        Returns an empty list if the pet is not ready or no branch is available.
        """
        target_id = self.first_available_choice()
        if not target_id:
            return []
        result = self.evolve_to(target_id)
        return list(result.get("events") or [])

    def receive_gift(self, name: str, keep: int = 20) -> None:
        """Remember a received gift name (newest last, capped)."""
        self.inventory = (self.inventory + [name])[-keep:]

    def _evolve_into(self, nxt: Species) -> list[str]:
        """Apply one form change and its stat bonuses."""
        previous = self.species
        self.species_id = nxt.id
        self.evolution_ready_override = False
        self.evolve_soon_announced = False
        self.evolve_choice_announced = False
        self._evo_notices = []
        self.stats.max_hp += nxt.bonus_hp
        self.stats.hp += nxt.bonus_hp
        self.stats.attack += nxt.bonus_attack
        self.stats.defense += nxt.bonus_defense
        self.stats.speed += nxt.bonus_speed
        self.stats.clamp()
        return [
            f"{self.name} evolved! {previous.name} ({previous.stage}) -> "
            f"{nxt.name} ({nxt.stage})."
        ]

    def summary(self) -> str:
        """One compact line of status, handy for logs and tests."""
        s = self.stats
        moves = ", ".join(move.name for move in self.known_moves()) or "none"
        return (
            f"{self.life.nickname or self.name} the {self.species.name} [{self.species.stage}] "
            f"Lv{s.level} XP={s.xp} age={s.age:.1f}h  "
            f"HP={s.hp}/{s.max_hp} ATK={s.attack} DEF={s.defense} SPD={s.speed}  "
            f"hunger={s.hunger:.0f} energy={s.energy:.0f} mood={s.mood:.0f}  "
            f"mistakes={s.care_mistakes}  moves=[{moves}]"
        )

    def to_dict(self) -> dict:
        """Full snapshot as a plain dict (JSON-ready)."""
        return {
            "name": self.name,
            "species_id": self.species_id,
            "stats": self.stats.to_dict(),
            "known_move_ids": list(self.known_move_ids),
            "discovered_ids": sorted(self.discovered_ids),
            "inventory": list(self.inventory),
            "asleep": self.asleep,
            "evolution_ready_override": self.evolution_ready_override,
            "evolve_soon_announced": self.evolve_soon_announced,
            "evolve_choice_announced": self.evolve_choice_announced,
            "sighting_keys": sorted(self.sighting_keys),
            "move_ranks": {k: int(v) for k, v in sorted(self.move_ranks.items())},
            "life": self.life.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Creature:
        """Rebuild a Creature from a to_dict() snapshot."""
        stats_data = data.get("stats") or {}
        return cls(
            name=data.get("name", "Pulse"),
            species_id=data.get("species_id", STARTER_SPECIES_ID),
            stats=Stats.from_dict(stats_data),
            known_move_ids=list(data.get("known_move_ids") or []),
            discovered_ids=set(data.get("discovered_ids") or []),
            inventory=list(data.get("inventory") or []),
            asleep=bool(data.get("asleep", False)),
            evolution_ready_override=bool(data.get("evolution_ready_override", False)),
            evolve_soon_announced=bool(data.get("evolve_soon_announced", False)),
            evolve_choice_announced=bool(data.get("evolve_choice_announced", False)),
            sighting_keys=set(data.get("sighting_keys") or []),
            move_ranks=dict(data.get("move_ranks") or {}),
            life=Life.from_dict(data.get("life"), now=time.time()),
        )

    # ------------------------------------------------------------------
    # Egg legacy and full reset
    # ------------------------------------------------------------------

    def hatch_from_egg(self, now: float, rng: random.Random) -> dict:
        """Replace this pet with the hatchling from its egg (in place).

        The new pet starts as a fresh Chirplet that already knows the
        inherited move and carries the inherited trait. Household memory
        (networks, schedule, diary, rival, one heirloom trinket) carries over.
        """
        old_life = self.life
        egg = dict(old_life.egg or {})
        if not egg:
            raise ValueError("no egg to hatch")
        parent_species = self.species.name
        legacy = {
            "move": egg.get("move"),
            "trait": egg.get("trait"),
            "trait_value": egg.get("trait_value"),
            "generation": int(egg.get("generation", old_life.generation + 1)),
            "parent": egg.get("parent"),
        }
        fresh_life = Life.new(rng, now, legacy=legacy)
        fresh_life.inherit_from(old_life, egg, parent_species)
        fresh = Creature(name=self.name, life=fresh_life)
        move_id = egg.get("move")
        if move_id and move_id in MOVE_LIBRARY and move_id not in fresh.known_move_ids:
            fresh.known_move_ids.append(move_id)
        self.__dict__.update(fresh.__dict__)
        return legacy

    def reset(self, now: float | None = None, rng: random.Random | None = None) -> None:
        """Start over with a brand-new hatchling (nothing is inherited)."""
        fresh = Creature(name=self.name, life=Life.new(rng or random.Random(), time.time() if now is None else now))
        self.__dict__.update(fresh.__dict__)
