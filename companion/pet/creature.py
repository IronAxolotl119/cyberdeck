"""The Creature class: one pet, with stats, moves, and a clock.

tick(dt) is the heartbeat. It drains hunger and energy, shifts mood, ages
the pet, and then checks for care mistakes, level-ups, new level-moves, and
evolutions. feed / train / rest are the caretaker actions. to_dict and
from_dict make the whole object JSON-friendly so save.py can be tiny.
"""

from __future__ import annotations

from pet.evolution import (
    STARTER_SPECIES_ID,
    Species,
    choose_evolution,
    get_species,
)
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
        if known_move_ids is None:
            # Brand-new pets start with every move their current level unlocks
            # (for a level-1 Chirplet that is just Chirp).
            self.known_move_ids: list[str] = [
                move.id for move in moves_for_level(self.stats.level)
            ]
        else:
            self.known_move_ids = list(known_move_ids)

    @property
    def species(self) -> Species:
        """The Species record for the current form."""
        return get_species(self.species_id)

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
        stats.hunger -= HUNGER_DRAIN_PER_HOUR * dt
        stats.energy -= ENERGY_DRAIN_PER_HOUR * dt

        # Mood slowly fades, and dips faster when the pet is hungry or tired.
        mood_delta = -MOOD_DRAIN_PER_HOUR * dt
        if stats.hunger < 30:
            mood_delta -= 2.0 * dt
        if stats.energy < 30:
            mood_delta -= 2.0 * dt
        if stats.hunger > 70 and stats.energy > 70:
            mood_delta += 1.0 * dt  # a well-kept pet cheers up a little
        stats.mood += mood_delta

        # Starvation chips away at HP so neglect is visible on the sheet.
        if stats.hunger <= 0:
            lost = max(1, int(dt))
            stats.hp -= lost
            events.append(
                f"{self.name} is starving and loses {lost} HP (now {max(0, stats.hp)})."
            )

        stats.clamp()

        if (
            stats.hunger <= CARE_MISTAKE_THRESHOLD
            or stats.energy <= CARE_MISTAKE_THRESHOLD
        ):
            stats.care_mistakes += 1
            reason = "hunger" if stats.hunger <= CARE_MISTAKE_THRESHOLD else "exhaustion"
            events.append(
                f"Care mistake ({reason})! Total mistakes: {stats.care_mistakes}."
            )

        events.extend(self._check_level_up())
        events.extend(self._check_evolution())
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
        exhausted does nothing so the caretaker has to rest first.
        """
        if stat not in {"attack", "defense", "speed"}:
            raise ValueError(f"Cannot train {stat!r}; use attack, defense, or speed.")
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
        """Recover energy and a little mood. No XP — rest is not training."""
        self.stats.energy += REST_ENERGY
        self.stats.mood += REST_MOOD
        self.stats.clamp()
        return [f"{self.name} rests. Energy is now {self.stats.energy:.0f}."]

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

    def _check_evolution(self) -> list[str]:
        """Evolve if a rule matches, applying the new form's stat bonuses once."""
        nxt = choose_evolution(self.species_id, self.stats)
        if nxt is None:
            return []
        return self._evolve_into(nxt)

    def next_form_id(self) -> str | None:
        """The species id this pet would grow into next, or None at a final form.

        If age/level/XP already satisfy a table rule, that rule wins. Otherwise
        the branch still follows the same care/mood gates used by evolution.py
        (well-cared-for Wavling -> Aethling, otherwise Staticore). Used by
        the link-layer evolve gift, which may catalyze a form change before
        the usual time gates have been met.
        """
        if self.species.stage == "rookie":
            return None
        chosen = choose_evolution(self.species_id, self.stats)
        if chosen is not None:
            return chosen.id
        if self.species_id == "chirplet":
            return "wavling"
        if self.species_id == "wavling":
            if self.stats.care_mistakes <= 2 and self.stats.mood >= 50:
                return "aethling"
            return "staticore"
        return None

    def advance_evolution(self) -> list[str]:
        """Grow into the next form now, choosing the branch via our rules.

        Returns an empty list if the pet is already at a final rookie form.
        """
        target_id = self.next_form_id()
        if target_id is None or target_id == self.species_id:
            return []
        return self._evolve_into(get_species(target_id))

    def receive_gift(self, name: str, keep: int = 20) -> None:
        """Remember a received gift name (newest last, capped)."""
        self.inventory = (self.inventory + [name])[-keep:]

    def _evolve_into(self, nxt: Species) -> list[str]:
        """Apply one form change and its stat bonuses."""
        previous = self.species
        self.species_id = nxt.id
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
            f"{self.name} the {self.species.name} [{self.species.stage}] "
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
        )
