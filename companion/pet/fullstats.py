"""Everything about PULSE in one deck-friendly block (pet.stats, pet.status full).

Raw numbers for bars plus pre-formatted pages: `pages: [{title, lines}]`
with titles <= 20 chars and lines that are plain ASCII and <= 44 chars,
in a fixed order (PAGE_TITLES). Undiscovered favorites stay hidden.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pet import calendar as cal
from pet.battle import (
    MAX_ENERGY, SAY_MAX, Bag, battle_move_payload, clamp_say, display_name, fit_row, loadout, pet_type,
)
from pet.creature import level_for_xp, xp_needed_for_level
from pet.evolution import current_gates_for_species
from pet.moves import MOVE_LIBRARY

PAGE_TITLES: tuple[str, ...] = (
    "Overview",
    "Combat",
    "Care",
    "Traits & Evolution",
    "Moves & Loadout",
    "Bag",
    "Battle Record",
    "Discoveries",
)
TITLE_MAX = 20

PREF_LABELS = {
    "fav_food": "Fav food", "hated_food": "Hates food",
    "fav_signal": "Fav signal", "hated_signal": "Hates signal",
}


def _pct(have: float, need: float) -> int:
    if need <= 0:
        return 100
    return int(max(0, min(100, round(100 * have / need))))


def bar(value: float, maximum: float, width: int = 10) -> str:
    filled = int(round(width * max(0.0, min(1.0, value / maximum)))) if maximum > 0 else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _row(label: str, value: Any) -> str:
    return fit_row(f"{label:<13}{value}")


def full_stats(creature: Any, mood_word: str, now: float) -> dict[str, Any]:
    """The whole stat sheet: raw fields + `pages` + flat `lines`."""
    from pet.combat import combat_mods, pet_status

    stats = creature.stats
    life = creature.life
    species = creature.species
    name = display_name(creature)

    # ---- identity / XP
    level = stats.level
    xp_floor = xp_needed_for_level(level)
    xp_next = xp_needed_for_level(level + 1)
    xp_into = max(0, stats.xp - xp_floor)
    xp_span = max(1, xp_next - xp_floor)
    hatched = cal.local_dt(life.hatched_at)
    today = cal.local_dt(now).date()
    birthday = hatched.date()
    try:
        next_bday = birthday.replace(year=today.year)
    except ValueError:  # Feb 29
        next_bday = birthday.replace(year=today.year, day=28)
    if next_bday < today:
        try:
            next_bday = birthday.replace(year=today.year + 1)
        except ValueError:
            next_bday = birthday.replace(year=today.year + 1, day=28)
    real_days = max(0.0, (now - life.hatched_at) / 86400.0)

    # ---- combat
    mods = combat_mods(creature)
    _mood_bonus, speed_bonus = creature.environment_bonuses()
    happiness = max(0.0, min(100.0, stats.mood + life.mood_offset(now)))

    # ---- evolution
    evo = creature.evolution_status()
    gates_out: dict[str, Any] = {}
    if evo.get("next_stage"):
        gates = current_gates_for_species(creature.species_id)
        gates_out = {
            "age": {"have": round(stats.age, 1), "need": gates["min_age"], "pct": _pct(stats.age, gates["min_age"])},
            "level": {"have": level, "need": int(gates["min_level"]), "pct": _pct(level, gates["min_level"])},
            "xp": {"have": stats.xp, "need": int(gates["min_xp"]), "pct": _pct(stats.xp, gates["min_xp"])},
        }
    choices = [c for c in evo.get("choices") or [] if isinstance(c, dict)]

    # ---- moves, bag, record, discoveries
    known = [battle_move_payload(creature, MOVE_LIBRARY[m]) for m in creature.known_move_ids if m in MOVE_LIBRARY]
    load = [m.name for m in loadout(creature)]
    bag_rows = Bag(creature).payload()
    record = {k: int(life.record.get(k, 0)) for k in ("wins", "losses", "flees", "bosses", "faints")}
    fights = record["wins"] + record["losses"] + record["flees"]
    rival = life.rival or {}
    favorites = life.known_preferences()
    hidden = sum(1 for k in life.prefs if k not in favorites)
    quirks = {k: v.strip() for k, v in life.quirks.items() if k in {"catchphrase", "dance", "sneeze", "tic", "fav_idle"}}
    modifiers = life.active_modifiers(now)

    raw: dict[str, Any] = {
        "name": name,
        "nickname": life.nickname,
        "species": species.name,
        "species_id": creature.species_id,
        "stage": species.stage,
        "type": pet_type(creature),
        "level": level,
        "xp": stats.xp,
        "xp_level_start": xp_floor,
        "xp_next_level": xp_next,
        "xp_to_next": max(0, xp_next - stats.xp),
        "xp_pct": _pct(xp_into, xp_span),
        "age_hours": round(stats.age, 1),
        "age_days": round(real_days, 1),
        "generation": life.generation,
        "birthday": birthday.isoformat(),
        "next_birthday": next_bday.isoformat(),
        "hp": stats.hp,
        "max_hp": stats.max_hp,
        "energy": int(round(stats.energy)),
        "max_energy": MAX_ENERGY,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed + speed_bonus,
        "speed_bonus": speed_bonus,
        "accuracy_mod": int(mods["accuracy"]),
        "damage_mult": mods["damage_mult"],
        "crit_chance": round(1.0 / 16.0 + mods["crit_chance"], 3),
        "disobey_chance": round(mods["disobey_chance"], 2),
        "guarding": bool(creature.guarding),
        "status": pet_status(creature),
        "hunger": int(round(100 - stats.hunger)),  # protocol need: 0 full, 100 starving
        "fullness": int(round(stats.hunger)),
        "happiness": int(round(happiness)),
        "obedience": int(round(life.obedience)),
        "discipline": _obedience_tier(life.obedience),
        "care_mistakes": stats.care_mistakes,
        "mood": mood_word,
        "mood_offset": round(life.mood_offset(now), 1),
        "modifiers": modifiers,
        "bond": life.bond_tier(),
        "bond_hearts": min(5, int(life.bond // 20)),
        "sick": life.sick.get("kind") if life.sick else None,
        "tantrum": life.in_tantrum(now),
        "quirks": quirks,
        "favorites": favorites,
        "favorites_hidden": hidden,
        "evolution": {
            "next_stage": evo.get("next_stage"),
            "ready": bool(evo.get("ready")),
            "progress_pct": int(round(100 * float(evo.get("progress") or 0))),
            "eta_hours": evo.get("eta_hours"),
            "gates": gates_out,
            "choices": choices,
        },
        "moves": known,
        "loadout": load,
        "loadout_auto": not creature.battle_moves,
        "bag": bag_rows,
        "bag_items": len(bag_rows),
        "bag_total": sum(r["qty"] for r in bag_rows),
        "record": dict(record, fights=fights),
        "rival": {"name": rival.get("name"), "wins": int(rival.get("wins", 0)), "losses": int(rival.get("losses", 0))},
        "discoveries": creature.unique_discovery_count(),
        "networks_known": len(life.networks),
        "treasures": len(life.treasures),
        "trinkets": len(life.shelf),
        "favorite_trinket": life.favorite_trinket(),
        "adventures": len(life.adventures) if isinstance(life.adventures, list) else int(life.adventures or 0),
        "diary_entries": len(life.diary),
    }

    pages = _pages(raw)
    flat: list[str] = []
    for page in pages:
        flat.append(fit_row(f"== {page['title']} =="))
        flat.extend(page["lines"])
    raw["pages"] = pages
    raw["lines"] = flat
    return raw


def _obedience_tier(value: float) -> str:
    if value < 25:
        return "wild"
    if value < 50:
        return "willful"
    if value < 75:
        return "good"
    return "loyal"


def _pages(r: dict[str, Any]) -> list[dict[str, Any]]:
    gen = f"  Gen {r['generation']}" if r["generation"] > 1 else ""
    overview = [
        f"{r['name']}" + (f"  ({r['species']})" if r["nickname"] else ""),
        f"{r['species']}  {r['stage']}  {r['type']}{gen}",
        f"Lv {r['level']}  XP {r['xp']}/{r['xp_next_level']}",
        f"XP {bar(r['xp_pct'], 100)} {r['xp_to_next']} to go",
        f"HP {bar(r['hp'], r['max_hp'])} {r['hp']}/{r['max_hp']}",
        f"EN {bar(r['energy'], r['max_energy'])} {r['energy']}/{r['max_energy']}",
        f"Mood {r['mood']}  Bond {r['bond']}",
        f"Status {r['status']}" + ("  TANTRUM" if r["tantrum"] else ""),
        f"Age {r['age_days']}d  Born {r['birthday']}",
    ]
    combat = [
        _row("HP", f"{r['hp']}/{r['max_hp']}"),
        _row("EN", f"{r['energy']}/{r['max_energy']}"),
        _row("Attack", r["attack"]),
        _row("Defense", r["defense"]),
        _row("Speed", f"{r['speed']}" + (f" (+{r['speed_bonus']} buff)" if r["speed_bonus"] else "")),
        _row("Type", r["type"]),
        _row("Crit", f"{round(r['crit_chance'] * 100, 1)}%"),
        _row("Accuracy", f"{r['accuracy_mod']:+d}"),
        _row("Damage", f"x{r['damage_mult']:g}"),
        _row("Disobey", f"{int(r['disobey_chance'] * 100)}%"),
    ]
    care = [
        _row("Hunger", f"{bar(r['hunger'], 100)} {r['hunger']}"),
        _row("Happiness", f"{bar(r['happiness'], 100)} {r['happiness']}"),
        _row("Energy", f"{bar(r['energy'], 100)} {r['energy']}"),
        _row("Obedience", f"{r['discipline']} ({r['obedience']})"),
        _row("Mistakes", r["care_mistakes"]),
        _row("Mood", f"{r['mood']} ({r['mood_offset']:+g})"),
        _row("Bond", f"{r['bond']} " + "<3" * r["bond_hearts"]),
        _row("Sick", r["sick"] or "no"),
    ]
    for mod in r["modifiers"][:4]:
        care.append(fit_row(f"  {mod.get('kind', '?')} {float(mod.get('value', 0)):+.0f}"))
    q = r["quirks"]
    traits = [
        _row("Catchphrase", q.get("catchphrase", "-")),
        _row("Dance", q.get("dance", "-")),
        _row("Sneeze", q.get("sneeze", "-")),
        _row("Fav idle", str(q.get("fav_idle", "-")).replace("_", " ")),
    ]
    for key, label in PREF_LABELS.items():
        traits.append(_row(label, r["favorites"].get(key, "???")))
    evo = r["evolution"]
    if evo["next_stage"]:
        traits.append(fit_row(f"Next: {evo['next_stage']}  {evo['progress_pct']}%" + ("  READY" if evo["ready"] else "")))
        for gate in ("age", "level", "xp"):
            g = evo["gates"].get(gate)
            if g:
                traits.append(fit_row(f"{gate.upper():<6}{bar(g['pct'], 100)} {g['pct']}%"))
        names = [c.get("species", "?") + ("" if c.get("available", True) else "(locked)") for c in evo["choices"]]
        if names:
            traits.append(fit_row("Can become: " + ", ".join(names)))
    else:
        traits.append("Final form reached.")
    moves = []
    for m in r["moves"]:
        star = "*" if m["name"] in r["loadout"] else " "
        moves.append(fit_row(f"{star}{m['name'][:15]:<15} {m['type']:<4} P{m['power']:<3} EN{m['energy_cost']:<3} R{m['rank']}"))
    moves.append(fit_row("* = loadout" + (" (auto)" if r["loadout_auto"] else "")))
    bag = [fit_row(f"{row['name'][:22]:<22} x{row['qty']:<3}" + ("" if row["usable_in_battle"] else " (no battle)")) for row in r["bag"]]
    bag = bag or ["(empty)"]
    bag.append(fit_row(f"{r['bag_items']} kinds, {r['bag_total']} items"))
    rec, riv = r["record"], r["rival"]
    record = [
        _row("Fights", rec["fights"]),
        _row("Wins", rec["wins"]),
        _row("Losses", rec["losses"]),
        _row("Fled", rec["flees"]),
        _row("Bosses", rec["bosses"]),
        _row("Fainted", rec["faints"]),
        _row("Rival", riv["name"] or "-"),
        _row("vs Rival", f"{riv['losses']}W {riv['wins']}L"),
    ]
    found = [
        _row("Discoveries", r["discoveries"]),
        _row("Networks", r["networks_known"]),
        _row("Treasures", r["treasures"]),
        _row("Trinkets", r["trinkets"]),
        _row("Favorite", r["favorite_trinket"] or "-"),
        _row("Adventures", r["adventures"]),
        _row("Diary", f"{r['diary_entries']} entries"),
    ]
    bodies = [overview, combat, care, traits, moves, bag, record, found]
    return [
        {"title": title, "lines": [fit_row(line, SAY_MAX) for line in body]}
        for title, body in zip(PAGE_TITLES, bodies)
    ]
