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
      Otherwise: damage = max(1, effective_power + pet.attack - enemy.defense)
      where effective_power is the move's base power plus its saved rank
      bonus (2 points per strengthen rank, capped at rank 5). Then: x1.25
      if angry, x1.5 on a crit (1/16 + bond bonus), and x the type chart
      (pet/battle.py; 0 = immune). See docs/DUNGEON_INTEGRATION.md.

  Incoming hit (pet.combat.hit, field "damage")
      taken = max(0, damage - pet.defense // 4), then x the type chart
      (attack_type vs PULSE's species type)
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


def move_payload(move: Move, creature: Creature | None = None) -> dict[str, Any]:
    """One move as advertised by pet.combat.stats.

    When a creature is given, power is the effective (ranked) value and
    rank / bonus are included so the dungeon can show the boost.
    """
    rank = 0
    power = int(move.power)
    if creature is not None:
        rank = creature.move_rank(move.id)
        power = creature.effective_power(move)
    return {
        "name": move.name,
        "power": power,
        "accuracy": move.accuracy,
        "energy_cost": move.energy_cost,
        "kind": move.kind,
        "effect": move.effect,
        "rank": rank,
        "bonus": power - int(move.power),
    }


def stats_payload(creature: Creature) -> dict[str, Any]:
    """pet.combat.stats.ok body: the pet's live combat sheet."""
    stats = creature.stats
    moves = []
    for move_id in creature.known_move_ids:
        move = MOVE_LIBRARY.get(move_id)
        if move is not None:
            moves.append(move_payload(move, creature))
    _mood, speed_bonus = creature.environment_bonuses()
    return {
        "hp": stats.hp,
        "max_hp": stats.max_hp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed + speed_bonus,
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


def outgoing_damage(
    move: Move,
    pet_attack: int,
    enemy_defense: int,
    power: int | None = None,
) -> int:
    if move.effect == "guard":
        return 0
    used = int(move.power) if power is None else int(power)
    return max(1, used + int(pet_attack) - int(enemy_defense))


def incoming_taken(raw_damage: int, defense: int, guarding: bool) -> int:
    taken = max(0, int(raw_damage) - int(defense) // 4)
    if guarding:
        taken = taken // 2
    return taken


def combat_mods(creature: Creature) -> dict[str, Any]:
    """Mood / bond / obedience modifiers from creature.life (all neutral by default).

    angry  (mood incl. lingering modifiers <= 28): damage x1.25, accuracy -5
    tired  (energy < 25): accuracy -10; tired *and* angry: 30% chance to refuse
    obedience < 40: up to 40% chance to ignore the requested move
    bond > 40: +1 accuracy per 15 bond; bond >= 45: crit chance (bond-40)/300
    """
    life = getattr(creature, "life", None)
    mods = {"angry": False, "tired": False, "accuracy": 0, "damage_mult": 1.0,
            "refuse_chance": 0.0, "disobey_chance": 0.0, "crit_chance": 0.0,
            "tantrum": False, "cheer": False}
    if life is None:
        return mods
    mood = creature.stats.mood + life.mood_offset()
    mods["angry"] = mood <= 28
    mods["tired"] = creature.stats.energy < 25
    if mods["angry"]:
        mods["damage_mult"] = 1.25
        mods["accuracy"] -= 5
    if mods["tired"]:
        mods["accuracy"] -= 10
    if mods["angry"] and mods["tired"]:
        mods["refuse_chance"] = 0.3
    if life.obedience < 40:
        mods["disobey_chance"] = min(0.4, (40 - life.obedience) / 100)
    if life.bond > 40:
        mods["accuracy"] += int((life.bond - 40) // 15)
    if life.bond >= 45:
        mods["crit_chance"] = (life.bond - 40) / 300
        mods["cheer"] = True
    mods["tantrum"] = life.in_tantrum()
    return mods


def _flavor(creature: Creature, key: str, slots: dict[str, Any], rng: random.Random) -> str:
    life = getattr(creature, "life", None)
    if life is None:
        return ""
    from pet.speech import fill

    return fill(life.picker.pick(key, rng), dict(life.slots(creature, life.last_now), **slots))


def pet_status(creature: Creature) -> str:
    """ok | glitch | fainted (battle status shown under the HP bar)."""
    if creature.fainted:
        return "fainted"
    life = getattr(creature, "life", None)
    if life is not None and life.sick:
        return "glitch"
    return "ok"


def _say(creature: Creature, key: str, rng: random.Random, slots: dict[str, Any]) -> str:
    from pet.battle import battle_say

    return battle_say(creature, key, rng, slots)


def perform_action(
    creature: Creature,
    requested: str | None,
    enemy: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """Resolve one pet.combat.action. Mutates the creature (energy, guard, etc.).

    Type effectiveness: damage = int(base * chart[move.type][enemy.type]),
    at least 1 unless immune (0). Crits: 1/16 base chance plus the bond
    bonus, x1.5. Angry pets hit x1.25. See pet/battle.py for the chart.
    """
    from pet.battle import (
        CRIT_BASE, CRIT_MULT, best_move, clamp_say, display_name, effectiveness,
        effectiveness_label, move_type, normalize_enemy,
    )

    foe = normalize_enemy(enemy)
    name = display_name(creature)
    foe_up = clamp_say(foe["name"].upper(), 16)
    auto = not (requested or "").strip()
    if auto and enemy and not creature.asleep and not creature.fainted:
        smart = best_move(creature, foe)
        requested = smart.name if smart is not None else None
    move, fallback, reason = pick_move(creature, requested)
    mods = combat_mods(creature)
    if not reason and mods["tantrum"]:
        reason = "tantrum"
    if not reason and mods["refuse_chance"] and rng.random() < mods["refuse_chance"]:
        reason = "refused"
    slots = {"foe": foe["name"], "move": move.name, "type": move_type(move)}
    if reason:
        life = getattr(creature, "life", None)
        if reason == "asleep":
            reaction = "PULSE is asleep and cannot fight."
            log = [f"{name} is fast asleep."]
            say = ""
        elif reason == "fainted":
            reaction = "PULSE is fainted and cannot fight until it rests."
            log = [f"{name} has fainted and cannot fight."]
            say = ""
        else:
            key = "combat.tantrum" if reason == "tantrum" else "combat.refuse"
            reaction = _flavor(creature, key, {"foe": foe["name"]}, rng) or f"PULSE refuses to fight ({reason})."
            log = [f"{name} is throwing a tantrum!" if reason == "tantrum" else f"{name} won't listen!"]
            say = _say(creature, "battle.tantrum" if reason == "tantrum" else "battle.refuse", rng, slots)
            if life is not None:
                life.note_misbehave(life.last_now)
        return {
            "acted": False,
            "reason": reason,
            "move": "",
            "move_used": None,
            "type": None,
            "hit": False,
            "missed": False,
            "damage": 0,
            "effect": None,
            "effectiveness": 1.0,
            "effectiveness_label": "normal",
            "crit": False,
            "obeyed": False,
            "energy_spent": 0,
            "energy_cost": 0,
            "pet_hp": creature.stats.hp,
            "fallback": False,
            "auto": auto,
            "reaction": reaction,
            "mood_effect": "angry" if mods["angry"] else "tired" if mods["tired"] else None,
            "log": [clamp_say(line) for line in log],
            "say": say,
            "enemy": foe,
        }

    disobeyed = False
    if mods["disobey_chance"] and not fallback and rng.random() < mods["disobey_chance"]:
        options = [
            MOVE_LIBRARY[m] for m in creature.known_move_ids
            if m in MOVE_LIBRARY and m != move.id and MOVE_LIBRARY[m].energy_cost <= creature.stats.energy
        ]
        if options:
            move = rng.choice(options)
            disobeyed = True
            life = getattr(creature, "life", None)
            if life is not None:
                life.note_misbehave(life.last_now)
    slots.update(move=move.name, type=move_type(move))

    _mood, speed_bonus = creature.environment_bonuses()
    chance = outgoing_accuracy(move, creature.stats.speed + speed_bonus, foe["speed"])
    chance = clamp_int(chance + int(mods["accuracy"]), 5, 100)
    hit = rng.randint(1, 100) <= chance
    power = creature.effective_power(move)
    mult = 1.0 if move.effect == "guard" else effectiveness(move_type(move), foe["type"])
    damage = outgoing_damage(move, creature.stats.attack, foe["defense"], power=power) if hit else 0
    crit = False
    if hit and damage and mods["damage_mult"] != 1.0:
        damage = max(1, int(round(damage * mods["damage_mult"])))
    if hit and damage and mult > 0:
        if rng.random() < CRIT_BASE + mods["crit_chance"]:
            damage = int(damage * CRIT_MULT)
            crit = True
    if hit and damage:
        damage = 0 if mult == 0 else max(1, int(damage * mult))
    if move.effect == "guard":
        creature.guarding = True
        hit = True
        damage = 0
        mult = 1.0
    label = effectiveness_label(mult)
    spent = int(move.energy_cost)
    creature.stats.energy = max(0.0, creature.stats.energy - spent)
    creature.stats.clamp()

    # Legacy one-line reaction (kept for old decks).
    if move.effect == "guard":
        reaction = f"PULSE braces behind a noise veil. the {foe['name']} will have a harder time hitting."
    elif not hit:
        reaction = f"PULSE uses {move.name} - it misses the {foe['name']}."
    else:
        reaction = f"PULSE uses {move.name} on the {foe['name']} for {damage}."
    if fallback and not auto:
        asked = (requested or "that move").strip() or "that move"
        reaction = f"too tired for {asked}; {reaction}"
    extras: list[str] = []
    if disobeyed:
        extras.append(_flavor(creature, "combat.disobey", slots, rng))
    if crit:
        extras.append(_flavor(creature, "combat.crit", slots, rng))
    elif hit and damage and mods["angry"]:
        extras.append(_flavor(creature, "combat.angry", slots, rng))
    elif not hit and mods["tired"] and move.effect != "guard":
        extras.append(_flavor(creature, "combat.tired_miss", slots, rng))
    extras = [e for e in extras if e]
    if extras:
        reaction = f"{reaction} {' '.join(extras)}"

    # Battle log: short, ready-to-print lines for the 48-column screen.
    log: list[str] = []
    if fallback and not auto and requested:
        log.append(f"{name} is too tired for that!")
    if disobeyed:
        log.append(f"{name} ignored your order!")
    log.append(f"{name} used {move.name.upper()}!")
    if move.effect == "guard":
        log.append(f"{name} is guarding!")
    elif not hit:
        log.append(f"{name}'s attack missed!")
    elif mult == 0:
        log.append(f"It doesn't affect {foe_up}...")
    else:
        if crit:
            log.append("A critical hit!")
        if label == "super":
            log.append("It's super effective!")
        elif label == "weak":
            log.append("It's not very effective...")
        log.append(f"{foe_up} took {damage} damage.")

    if disobeyed:
        key = "battle.disobey"
    elif move.effect == "guard":
        key = "battle.guard"
    elif not hit:
        key = "battle.miss"
    elif mult == 0:
        key = "battle.immune"
    elif crit:
        key = "battle.crit"
    elif label == "super":
        key = "battle.super"
    elif label == "weak":
        key = "battle.weak"
    else:
        key = f"battle.move.{move_type(move).lower()}" if rng.random() < 0.4 else "battle.move"
    say = _say(creature, key, rng, slots)

    result = {
        "acted": True,
        "reason": None,
        "move": move.name,
        "move_used": move.name,
        "move_id": move.id,
        "type": move_type(move),
        "hit": hit,
        "missed": not hit,
        "damage": damage,
        "effect": move.effect,
        "effectiveness": mult,
        "effectiveness_label": label,
        "crit": crit,
        "obeyed": not disobeyed,
        "energy_spent": spent,
        "energy_cost": spent,
        "pet_hp": creature.stats.hp,
        "fallback": fallback and not auto,
        "auto": auto,
        "reaction": reaction,
        "disobeyed": disobeyed,
        "mood_effect": "angry" if mods["angry"] else "tired" if mods["tired"] else None,
        "log": [clamp_say(line) for line in log],
        "say": say,
        "enemy": foe,
    }
    if mods["cheer"]:
        life = getattr(creature, "life", None)
        if life is not None:
            result["cheer"] = clamp_say(life.say(creature, "cheer", rng, life.last_now, decorate=False))
    return result


def apply_hit(
    creature: Creature,
    payload: dict[str, Any],
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Apply pet.combat.hit: a positive damage (after PULSE's type), or a heal.

    taken = max(0, damage - defense // 4); then x chart[attack_type][pet type]
    (at least 1 if it was > 0, unless immune); guarding halves it.
    attack_type comes from payload.attack_type, else the enemy's type.
    """
    from pet.battle import (
        clamp_say, display_name, effectiveness, effectiveness_label, normalize_enemy, norm_type, pet_type,
    )

    rng = rng or random.Random()
    heal = payload.get("heal")
    damage_raw = payload.get("damage")
    enemy_raw = payload.get("enemy")
    foe = normalize_enemy(enemy_raw) if isinstance(enemy_raw, dict) else None
    name = display_name(creature)
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
            attack_type = norm_type(payload.get("attack_type"), foe["type"] if foe else "NORM")
            mult = effectiveness(attack_type, pet_type(creature))
            taken = max(0, value - int(creature.stats.defense) // 4)
            if taken:
                taken = 0 if mult == 0 else max(1, int(taken * mult))
            if creature.guarding:
                taken //= 2
            creature.guarding = False
            creature.stats.hp = max(0, creature.stats.hp - taken)
            creature.stats.clamp()
            label = effectiveness_label(mult)
            foe_up = clamp_say((foe["name"] if foe else "the foe").upper(), 16)
            # The deck writes "FOE used ATTACK!" itself; we report the result.
            log: list[str] = []
            if mult == 0:
                log.append(f"It doesn't affect {name}...")
            else:
                if label == "super":
                    log.append("It's super effective!")
                elif label == "weak":
                    log.append("It's not very effective...")
                log.append(f"{name} took {taken} damage.")
            slots = {"foe": foe["name"] if foe else "foe", "type": attack_type}
            if creature.fainted:
                log.append(f"{name} fainted!")
                key = "battle.faint"
            elif mult == 0:
                key = "battle.immune_hit"
            elif label == "weak":
                key = "battle.resist"
            elif creature.stats.hp * 4 <= creature.stats.max_hp:
                key = "battle.lowhp"
            else:
                key = "battle.hurt"
            return {
                "damage": taken,
                "damage_taken": taken,
                "healed": 0,
                "attack_type": attack_type,
                "effectiveness": mult,
                "effectiveness_label": label,
                "pet_hp": creature.stats.hp,
                "fainted": creature.fainted,
                "low_hp": 0 < creature.stats.hp and creature.stats.hp * 4 <= creature.stats.max_hp,
                "log": [clamp_say(line) for line in log],
                "say": _say(creature, key, rng, slots),
            }

    if healing:
        creature.stats.hp = min(creature.stats.max_hp, creature.stats.hp + healing)
        creature.stats.clamp()
        return {
            "damage": 0,
            "damage_taken": 0,
            "healed": healing,
            "pet_hp": creature.stats.hp,
            "fainted": creature.fainted,
            "log": [clamp_say(f"{name} recovered {healing} HP.")],
            "say": _say(creature, "battle.item.heal", rng, {"amount": healing, "item": "repair kit"}),
        }

    # Empty payload: no change.
    creature.guarding = False
    return {
        "damage": 0,
        "damage_taken": 0,
        "healed": 0,
        "pet_hp": creature.stats.hp,
        "fainted": creature.fainted,
        "log": [],
        "say": "",
    }


def use_item(creature: Creature, ref: Any, rng: random.Random) -> dict[str, Any]:
    """pet.combat.item: use one bag item in battle.

    Returns {ok, error?, consumed, item, hp, energy, status_cleared, say, log}.
    error is "none_left" (not in the bag) or "not_usable_in_battle".
    Food heals HP (and revives a fainted pet), batteries restore EN,
    medicine cures glitch and heals a little. An item that would do
    nothing (full HP, full EN, not sick) is not used up: ok false,
    error None, reason "no_effect".
    """
    from pet.battle import MAX_ENERGY, Bag, clamp_say, display_name

    bag = Bag(creature)
    name = display_name(creature)
    key = bag.find(ref)
    base = {"consumed": False, "status_cleared": False, "hp": creature.stats.hp,
            "energy": int(round(creature.stats.energy)), "log": []}
    if key is None or bag.qty(key) <= 0:
        info_name = str(ref)
        return dict(base, ok=False, error="none_left", item={"id": key, "name": info_name, "qty": 0},
                    say=_say(creature, "battle.item.none_left", rng, {"item": info_name}))
    info = bag.info(key)
    item_row = {"id": key, "name": info["name"], "kind": info["kind"], "qty": bag.qty(key)}
    slots = {"item": info["name"]}
    if not info["usable_in_battle"]:
        return dict(base, ok=False, error="not_usable_in_battle", item=item_row,
                    say=_say(creature, "battle.item.not_usable", rng, slots))
    stats = creature.stats
    life = getattr(creature, "life", None)
    kind, amount = info["kind"], int(info["amount"])
    status_cleared = False
    log = [f"You used {info['name'].upper()}!"]
    if kind == "food":
        if stats.hp >= stats.max_hp:
            return dict(base, ok=False, error=None, reason="no_effect", item=item_row,
                        say=_say(creature, "battle.item.no_effect", rng, slots))
        before = stats.hp
        stats.hp = min(stats.max_hp, stats.hp + amount)
        gained = stats.hp - before
        log.append(f"{name} recovered {gained} HP.")
        key_say, slots["amount"] = "battle.item.heal", gained
    elif kind == "battery":
        if stats.energy >= MAX_ENERGY:
            return dict(base, ok=False, error=None, reason="no_effect", item=item_row,
                        say=_say(creature, "battle.item.no_effect", rng, slots))
        before_en = stats.energy
        stats.energy = min(float(MAX_ENERGY), stats.energy + amount)
        gained = int(round(stats.energy - before_en))
        log.append(f"{name} recovered {gained} EN.")
        key_say, slots["amount"] = "battle.item.energy", gained
    else:  # medicine
        sick = life is not None and life.sick
        if not sick and stats.hp >= stats.max_hp:
            return dict(base, ok=False, error=None, reason="no_effect", item=item_row,
                        say=_say(creature, "battle.item.no_effect", rng, slots))
        if sick:
            life.heal(creature, rng, life.last_now)
            status_cleared = True
            log.append(f"{name} is no longer glitched!")
        before = stats.hp
        stats.hp = min(stats.max_hp, stats.hp + amount)
        if stats.hp > before:
            log.append(f"{name} recovered {stats.hp - before} HP.")
        key_say, slots["amount"] = ("battle.item.cure" if status_cleared else "battle.item.heal"), stats.hp - before
    stats.clamp()
    bag.take(key)
    item_row["qty"] = bag.qty(key)
    return {
        "ok": True,
        "error": None,
        "consumed": True,
        "item": item_row,
        "hp": stats.hp,
        "energy": int(round(stats.energy)),
        "status_cleared": status_cleared,
        "log": [clamp_say(line) for line in log],
        "say": _say(creature, key_say, rng, slots),
    }


def flee_chance(creature: Creature, enemy: dict[str, Any]) -> float:
    """Suggested flee odds (the deck rolls): 0.5 + speed and level edge, 10%..95%.

    chance = 0.5 + (pet_speed - enemy_speed) * 0.05 + (pet_level - enemy_level) * 0.05
    Bosses halve it.
    """
    from pet.battle import normalize_enemy

    foe = normalize_enemy(enemy)
    _mood, speed_bonus = creature.environment_bonuses()
    speed = creature.stats.speed + speed_bonus
    chance = 0.5 + (speed - foe["speed"]) * 0.05 + (creature.stats.level - foe["level"]) * 0.05
    if foe["boss"]:
        chance *= 0.5
    return round(max(0.1, min(0.95, chance)), 2)


def default_xp(enemies: Any) -> int:
    """XP for a win when the deck sends none: 5 + 3 x level per enemy (boss x3)."""
    from pet.battle import normalize_enemy

    total = 0
    for raw in enemies if isinstance(enemies, list) else []:
        foe = normalize_enemy(raw if isinstance(raw, dict) else {"name": raw})
        total += (5 + 3 * foe["level"]) * (3 if foe["boss"] else 1)
    return total


def apply_combat_end(creature: Creature, payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve pet.combat.end: XP, mood, loot into the bag, and fainting.

    lose: mood -10, a care mistake if PULSE fainted, then HP is restored
    to 1 so it can limp home (it still needs rest to heal).
    loot (list of {name, kind, power} or names) goes into the bag on any result.
    """
    from pet.battle import Bag

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
    enemies = payload.get("enemies")
    if "xp" in payload:
        try:
            xp = max(0, int(payload.get("xp") or 0))
        except (TypeError, ValueError):
            xp = 0
    else:
        xp = default_xp(enemies) if result == "win" else 0
    events: list[str] = []
    if xp > 0:
        events.extend(creature.add_xp(xp))
    fainted = False
    if result == "win":
        creature.stats.mood += 8.0
        reaction = "PULSE looks proud after the win."
    elif result == "lose":
        creature.stats.mood -= 10.0
        if creature.fainted:
            fainted = True
            creature.stats.care_mistakes += 1
            events.append("Care mistake (fainted in the dungeon)!")
            creature.stats.hp = 1
        reaction = "PULSE slumps after the loss."
    else:
        creature.stats.mood -= 2.0
        reaction = "PULSE scurries out of the dungeon."
    loot_added: list[dict[str, Any]] = []
    loot = payload.get("loot")
    if isinstance(loot, list):
        bag = Bag(creature)
        for item in loot[:20]:
            try:
                info = bag.add(item)
            except (ValueError, TypeError):
                continue
            loot_added.append({"id": info["id"], "name": info["name"], "qty": bag.qty(info["id"])})
    boss = False
    if isinstance(enemies, list):
        from pet.battle import normalize_enemy

        boss = any(normalize_enemy(e if isinstance(e, dict) else {"name": e})["boss"] for e in enemies)
    creature.guarding = False
    creature.stats.clamp()
    return {
        "ok": True,
        "reason": None,
        "result": result,
        "xp_granted": xp,
        "events": events,
        "reaction": reaction,
        "fainted": fainted,
        "boss": boss,
        "loot_added": loot_added,
    }
