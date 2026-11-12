"""Dungeon combat helpers. The pet is authoritative; the deck is stateless.

The main deck's dungeon (cyberdeck/features/dungeon.py) is a simple
turn-based crawl: the player moves, then monsters take a turn, and
loot can be gifted to PULSE. These helpers let that dungeon ask the
companion "what can the pet do?" and "please take a swing / take a hit"
without keeping pet HP on the deck.

Damage formulas (integers throughout, documented so both sides agree):

  Outgoing hit chance
      accuracy' = clamp(move.accuracy + (pet.speed - enemy.speed) // 2, 5, 100)
      hit       = rng.randint(1, 100) <= accuracy'

  Outgoing damage (only if the move hits)
      If the move's effect is "guard": damage = 0, and the pet raises a
      guard flag that halves the *next* incoming hit.
      Otherwise: damage = max(1, move.power + pet.attack - enemy.defense)

  Incoming hit (pet.combat.hit, field "damage")
      taken = max(0, damage - pet.defense // 4)
      if the pet is guarding: taken = taken // 2, then the flag clears
      HP is reduced by `taken` and clamped at 0 (fainted).

  Incoming heal (pet.combat.hit field "heal", or a negative "damage")
      HP increases by that amount, up to max_hp. Defense is not applied.
      A fainted pet that is healed above 0 can fight again.

Fallback when the requested move is too expensive: use Chirp if the pet
knows it and can afford it, otherwise a built-in Guard (0 energy, 0
damage, effect "guard"). Asleep or fainted pets do not act at all.
"""

from __future__ import annotations

import random
from typing import Any

from pet.creature import Creature
from pet.moves import GUARD_MOVE, MOVE_LIBRARY, Move, find_move


def clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def move_payload(move: Move) -> dict[str, Any]:
    """One move as advertised by pet.combat.stats."""
    return {
        "name": move.name,
        "power": move.power,
        "accuracy": move.accuracy,
        "energy_cost": move.energy_cost,
        "kind": move.kind,
        "effect": move.effect,
    }


def stats_payload(creature: Creature) -> dict[str, Any]:
    """pet.combat.stats.ok body: the pet's live combat sheet."""
    stats = creature.stats
    moves = []
    for move_id in creature.known_move_ids:
        move = MOVE_LIBRARY.get(move_id)
        if move is not None:
            moves.append(move_payload(move))
    return {
        "hp": stats.hp,
        "max_hp": stats.max_hp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed,
        "level": stats.level,
        "moves": moves,
        "fainted": creature.fainted,
        "asleep": creature.asleep,
        "guarding": creature.guarding,
    }


def parse_enemy(raw: Any) -> dict[str, Any]:
    """Pull combat numbers out of a deck-supplied enemy object."""
    data = raw if isinstance(raw, dict) else {}
    name = str(data.get("name") or "foe")
    def _num(key: str, default: int) -> int:
        try:
            return int(data.get(key, default))
        except (TypeError, ValueError):
            return default
    return {
        "name": name,
        "level": _num("level", 1),
        "hp": _num("hp", 1),
        "attack": _num("attack", 1),
        "defense": _num("defense", 0),
        "speed": _num("speed", 1),
    }


def pick_move(creature: Creature, requested: str | None) -> tuple[Move, bool, str | None]:
    """Choose a move. Returns (move, used_fallback, reason_if_cannot_act).

    reason is set (and move is unused) when the pet is asleep or fainted.
    used_fallback is True when Chirp or Guard was substituted because the
    requested move was missing or too expensive.
    """
    if creature.asleep:
        return GUARD_MOVE, False, "asleep"
    if creature.fainted:
        return GUARD_MOVE, False, "fainted"

    move: Move | None = None
    if requested:
        move = find_move(requested)
        if move is not None and move.id != "guard" and move.id not in creature.known_move_ids:
            move = None

    energy = creature.stats.energy
    if move is not None and energy >= move.energy_cost:
        return move, False, None

    # Too tired (or unknown move): Chirp if known and affordable, else Guard.
    chirp = MOVE_LIBRARY.get("chirp")
    if (
        chirp is not None
        and "chirp" in creature.known_move_ids
        and energy >= chirp.energy_cost
    ):
        return chirp, True, None
    return GUARD_MOVE, True, None


def outgoing_accuracy(move: Move, pet_speed: int, enemy_speed: int) -> int:
    return clamp_int(int(move.accuracy) + (int(pet_speed) - int(enemy_speed)) // 2, 5, 100)


def outgoing_damage(move: Move, pet_attack: int, enemy_defense: int) -> int:
    if move.effect == "guard":
        return 0
    return max(1, int(move.power) + int(pet_attack) - int(enemy_defense))


def incoming_taken(raw_damage: int, defense: int, guarding: bool) -> int:
    taken = max(0, int(raw_damage) - int(defense) // 4)
    if guarding:
        taken = taken // 2
    return taken


def perform_action(
    creature: Creature,
    requested: str | None,
    enemy: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """Resolve one pet.combat.action. Mutates the creature (energy, guard, etc.)."""
    move, fallback, reason = pick_move(creature, requested)
    if reason:
        return {
            "acted": False,
            "reason": reason,
            "move": "",
            "hit": False,
            "damage": 0,
            "effect": None,
            "energy_spent": 0,
            "pet_hp": creature.stats.hp,
            "fallback": False,
            "reaction": (
                f"PULSE is {reason} and cannot fight."
                if reason == "asleep"
                else "PULSE is fainted and cannot fight until it rests."
            ),
        }

    foe = parse_enemy(enemy)
    chance = outgoing_accuracy(move, creature.stats.speed, foe["speed"])
    hit = rng.randint(1, 100) <= chance
    damage = outgoing_damage(move, creature.stats.attack, foe["defense"]) if hit else 0
    if move.effect == "guard":
        creature.guarding = True
        hit = True
        damage = 0
    spent = int(move.energy_cost)
    creature.stats.energy = max(0.0, creature.stats.energy - spent)
    creature.stats.clamp()

    if move.effect == "guard":
        reaction = f"PULSE braces behind a noise veil. the {foe['name']} will have a harder time hitting."
    elif not hit:
        reaction = f"PULSE uses {move.name} — it misses the {foe['name']}."
    else:
        reaction = f"PULSE uses {move.name} on the {foe['name']} for {damage}."
    if fallback:
        asked = (requested or "that move").strip() or "that move"
        reaction = f"too tired for {asked}; {reaction}"

    return {
        "acted": True,
        "reason": None,
        "move": move.name,
        "hit": hit,
        "damage": damage,
        "effect": move.effect,
        "energy_spent": spent,
        "pet_hp": creature.stats.hp,
        "fallback": fallback,
        "reaction": reaction,
    }


def apply_hit(creature: Creature, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply pet.combat.hit: a positive damage, or a heal."""
    heal = payload.get("heal")
    damage_raw = payload.get("damage")
    healing = 0
    if heal is not None:
        try:
            healing = max(0, int(heal))
        except (TypeError, ValueError):
            healing = 0
    elif damage_raw is not None:
        try:
            value = int(damage_raw)
        except (TypeError, ValueError):
            value = 0
        if value < 0:
            healing = -value
        else:
            taken = incoming_taken(value, creature.stats.defense, creature.guarding)
            creature.guarding = False
            creature.stats.hp = max(0, creature.stats.hp - taken)
            creature.stats.clamp()
            return {
                "damage_taken": taken,
                "healed": 0,
                "pet_hp": creature.stats.hp,
                "fainted": creature.fainted,
            }

    if healing:
        creature.stats.hp = min(creature.stats.max_hp, creature.stats.hp + healing)
        creature.stats.clamp()
        return {
            "damage_taken": 0,
            "healed": healing,
            "pet_hp": creature.stats.hp,
            "fainted": creature.fainted,
        }

    # Empty payload: no change.
    creature.guarding = False
    return {
        "damage_taken": 0,
        "healed": 0,
        "pet_hp": creature.stats.hp,
        "fainted": creature.fainted,
    }



def apply_combat_end(creature: Creature, payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve pet.combat.end: XP, mood, and a care-mistake if the pet fainted."""
    result = str(payload.get("result") or "").strip().lower()
    if result not in {"win", "lose", "flee"}:
        return {
            "ok": False,
            "reason": "result must be 'win', 'lose', or 'flee'",
            "result": result,
            "xp_granted": 0,
            "events": [],
            "reaction": "",
        }
    try:
        xp = max(0, int(payload.get("xp") or 0))
    except (TypeError, ValueError):
        xp = 0
    events: list[str] = []
    if xp > 0:
        events.extend(creature.add_xp(xp))
    if result == "win":
        creature.stats.mood += 8.0
        reaction = "PULSE looks proud after the win."
    elif result == "lose":
        creature.stats.mood -= 10.0
        if creature.fainted:
            creature.stats.care_mistakes += 1
            events.append("Care mistake (fainted in the dungeon)!")
        reaction = "PULSE slumps after the loss."
    else:
        creature.stats.mood -= 2.0
        reaction = "PULSE scurries out of the dungeon."
    creature.guarding = False
    creature.stats.clamp()
    return {
        "ok": True,
        "reason": None,
        "result": result,
        "xp_granted": xp,
        "events": events,
        "reaction": reaction,
    }
