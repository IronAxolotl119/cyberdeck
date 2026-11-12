"""The CPU rival: one recurring nemesis whose strength tracks the pet's level.

The fight reuses pet.combat: the pet swings with perform_action (so ranks,
moods, bond luck, and obedience all apply) and the rival's blows go through
incoming_taken. A rival "spar" never knocks the pet below 1 HP.
"""

from __future__ import annotations

import random
from typing import Any

from pet.combat import incoming_taken, perform_action
from pet.moves import MOVE_LIBRARY
from pet.speech import fill

MAX_ROUNDS = 10


def make_rival(name: str, pet_level: int, rng: random.Random, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scale the rival to the pet's level (it also grows a little with its wins)."""
    wins = int((record or {}).get("wins", 0))
    level = max(1, int(pet_level) + rng.choice((-1, 0, 0, 1, 1, 2)) + min(2, wins // 3))
    return {
        "name": name,
        "level": level,
        "hp": 14 + level * 6,
        "attack": 3 + level * 2,
        "defense": 2 + int(level * 1.5),
        "speed": 3 + int(level * 1.5),
    }


def _best_move(creature: Any) -> str | None:
    best = None
    best_power = -1
    for move_id in creature.known_move_ids:
        move = MOVE_LIBRARY.get(move_id)
        if move is None or move.effect == "guard" or move.energy_cost > creature.stats.energy:
            continue
        power = creature.effective_power(move)
        if power > best_power:
            best, best_power = move.name, power
    return best


def fight_rival(creature: Any, foe: dict[str, Any], rng: random.Random, picker=None, slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a full rival battle. Returns {result, rounds, log, damage_dealt}."""
    rival_hp = int(foe["hp"])
    log: list[str] = []
    dealt = 0
    result = "lose"
    pet_start = creature.stats.hp
    for round_no in range(1, MAX_ROUNDS + 1):
        action = perform_action(creature, _best_move(creature), foe, rng)
        if action.get("acted"):
            dealt += int(action["damage"])
            rival_hp -= int(action["damage"])
        log.append(f"R{round_no}: {action['reaction']}")
        if rival_hp <= 0:
            result = "win"
            break
        raw = int(foe["attack"]) + rng.randint(0, 3)
        taken = incoming_taken(raw, creature.stats.defense, creature.guarding)
        creature.guarding = False
        if creature.stats.hp - taken <= 1:
            creature.stats.hp = 1
            log.append(f"R{round_no}: {foe['name']} hits for {taken}. PULSE yields!")
            result = "lose"
            break
        creature.stats.hp -= taken
        if picker is not None and rng.random() < 0.25:
            log.append(f"R{round_no}: {foe['name']}: " + fill(picker.pick("rival.taunt", rng), slots))
        log.append(f"R{round_no}: {foe['name']} hits for {taken}.")
    else:
        # Out of rounds: whoever lost the smaller share of HP wins.
        pet_share = creature.stats.hp / max(1, pet_start)
        rival_share = rival_hp / max(1, int(foe["hp"]))
        result = "win" if pet_share >= rival_share else "lose"
    creature.stats.clamp()
    return {"result": result, "rounds": len({line.split(':')[0] for line in log}), "log": log, "damage_dealt": dealt, "rival_hp": max(0, rival_hp)}
