"""Turn-based battle messages (pet.combat.*, pet.inventory).

The deck owns the battle flow, enemy HP and enemy AI; the companion owns
PULSE (HP, EN, moves, types, bag, mood) and every line PULSE says.
Exact protocol: docs/DUNGEON_INTEGRATION.md, "Battle protocol".
"""

from __future__ import annotations

import random
from typing import Any

from avatar.faces import face_mood, face_text
from link.protocol import (
    TYPE_PET_COMBAT_ACTION_OK,
    TYPE_PET_COMBAT_END_OK,
    TYPE_PET_COMBAT_HIT_OK,
    TYPE_PET_COMBAT_ITEM_OK,
    TYPE_PET_COMBAT_MOVES_OK,
    TYPE_PET_COMBAT_ROSTER_OK,
    TYPE_PET_COMBAT_RUN_OK,
    TYPE_PET_COMBAT_STATS_OK,
    TYPE_PET_INVENTORY_OK,
    Message,
)
from pet.battle import (
    MAX_ENERGY,
    MAX_LOADOUT,
    Bag,
    battle_move_payload,
    battle_say,
    chart_payload,
    clamp_say,
    display_name,
    loadout,
    normalize_enemy,
    pet_type,
    roster_payload,
    to_ascii,
)
from pet.combat import apply_combat_end, apply_hit, flee_chance, perform_action, pet_status, stats_payload, use_item
from pet.moves import MOVE_LIBRARY, find_move


def battle_face(creature: Any) -> str:
    """One-line plain-ASCII face for the battle screen."""
    try:
        text = face_text(face_mood(creature), creature.species.stage, ascii_mode=True)
    except Exception:  # pragma: no cover - faces are optional decoration
        text = "(o_o)"
    lines = [line for line in str(text).splitlines() if line.strip()]
    face = to_ascii(lines[-1] if lines else "(o_o)").strip()
    return clamp_say(face, 20) or "(o_o)"


def battle_pet(creature: Any, mood_word: str) -> dict[str, Any]:
    stats = creature.stats
    return {
        "name": display_name(creature),
        "species": creature.species.name,
        "type": pet_type(creature),
        "level": stats.level,
        "hp": stats.hp,
        "max_hp": stats.max_hp,
        "energy": int(round(stats.energy)),
        "max_energy": MAX_ENERGY,
        "mood": mood_word,
        "status": pet_status(creature),
        "face": battle_face(creature),
    }


class BattleMixin:
    """Expects self.creature, self.rng, self.now(), self.pet_dict(), self._note_growth()."""

    def _mood_word(self) -> str:
        from link.handler import mood_word

        return mood_word(self.creature)

    def _battle_common(self) -> dict[str, Any]:
        stats = self.creature.stats
        return {
            "hp": stats.hp,
            "energy": int(round(stats.energy)),
            "status": pet_status(self.creature),
        }

    def full_stats(self) -> dict[str, Any]:
        """pet.stats / pet.status {full:true}: every stat, raw + pages."""
        from pet.fullstats import full_stats

        return full_stats(self.creature, self._mood_word(), self.now())

    # ------------------------------------------------------------- stats
    def _combat_stats(self, message: Message, payload: dict[str, Any]) -> Message:
        creature = self.creature
        body = stats_payload(creature)  # legacy sheet (all known moves)
        body["known_moves"] = body["moves"]
        body["moves"] = [battle_move_payload(creature, m) for m in loadout(creature)]
        body["bag"] = Bag(creature).payload()
        body["pet"] = battle_pet(creature, self._mood_word())
        body["type"] = pet_type(creature)
        body["energy"] = int(round(creature.stats.energy))
        body["max_energy"] = MAX_ENERGY
        enemy_raw = payload.get("enemy")
        say = ""
        if enemy_raw is not None:
            if not isinstance(enemy_raw, dict):
                return message.error("bad_payload", "pet.combat.stats enemy must be an object")
            foe = normalize_enemy(enemy_raw)
            body["enemy"] = foe
            body["flee_chance"] = flee_chance(creature, foe)
            if creature.life.in_tantrum(self.now()):
                key = "battle.tantrum"
            elif foe["boss"]:
                key = "battle.start.boss"
            elif self.rng.random() < 0.6:
                key = f"battle.start.{foe['type'].lower()}"
            else:
                key = "battle.start"
            say = battle_say(creature, key, self.rng, {"foe": foe["name"]})
            body["log"] = [clamp_say(f"A wild {foe['name'].upper()} appeared!"), clamp_say(f"Go, {display_name(creature)}!")]
            creature.life.touch(self.now())
        body["say"] = say
        return message.reply(TYPE_PET_COMBAT_STATS_OK, body)

    # ------------------------------------------------------------ action
    def _combat_action(self, message: Message, payload: dict[str, Any]) -> Message:
        move = payload.get("move")
        if move is not None and not isinstance(move, str):
            return message.error("bad_payload", "pet.combat.action move must be a string")
        enemy = payload.get("enemy")
        if enemy is not None and not isinstance(enemy, dict):
            return message.error("bad_payload", "pet.combat.action enemy must be an object")
        result = perform_action(self.creature, move, enemy or {}, self.rng)
        body = {
            # legacy fields
            "move": result["move"],
            "hit": result["hit"],
            "damage": result["damage"],
            "effect": result["effect"],
            "energy_spent": result["energy_spent"],
            "pet_hp": result["pet_hp"],
            "reaction": result["reaction"],
            # battle fields
            "move_used": result["move_used"],
            "type": result["type"],
            "effectiveness": result["effectiveness"],
            "effectiveness_label": result["effectiveness_label"],
            "crit": result["crit"],
            "missed": result["missed"],
            "obeyed": result["obeyed"],
            "energy_cost": result["energy_cost"],
            "say": result["say"],
            "log": result["log"],
            "enemy": result["enemy"],
            **self._battle_common(),
            "pet": self.pet_dict(),
        }
        if "turn" in payload:
            body["turn"] = payload.get("turn")
        if not result["acted"]:
            body["acted"] = False
            body["reason"] = result["reason"]
        else:
            body["acted"] = True
            if result.get("fallback"):
                body["fallback"] = True
            if result.get("auto"):
                body["auto"] = True
        for extra in ("disobeyed", "mood_effect", "cheer"):
            if result.get(extra):
                body[extra] = result[extra]
        return message.reply(TYPE_PET_COMBAT_ACTION_OK, body)

    # --------------------------------------------------------------- hit
    def _combat_hit(self, message: Message, payload: dict[str, Any]) -> Message:
        if "heal" not in payload and "damage" not in payload:
            return message.error("bad_payload", "pet.combat.hit needs payload.damage or payload.heal")
        enemy = payload.get("enemy")
        if enemy is not None and not isinstance(enemy, dict):
            return message.error("bad_payload", "pet.combat.hit enemy must be an object")
        result = apply_hit(self.creature, payload, self.rng)
        body = {
            "damage": result["damage"],
            "damage_taken": result["damage_taken"],
            "healed": result["healed"],
            "pet_hp": result["pet_hp"],
            "fainted": result["fainted"],
            "say": result["say"],
            "log": result["log"],
            **self._battle_common(),
            "pet": self.pet_dict(),
        }
        for extra in ("attack_type", "effectiveness", "effectiveness_label", "low_hp"):
            if extra in result:
                body[extra] = result[extra]
        return message.reply(TYPE_PET_COMBAT_HIT_OK, body)

    # -------------------------------------------------------------- item
    def _combat_item(self, message: Message, payload: dict[str, Any]) -> Message:
        ref = payload.get("item")
        if isinstance(ref, dict):
            ref = ref.get("id") or ref.get("name")
        if not isinstance(ref, str) or not ref.strip():
            return message.error("bad_payload", "pet.combat.item needs payload.item (an item id or name)")
        result = use_item(self.creature, ref.strip(), self.rng)
        if result.get("error") in {"none_left", "not_usable_in_battle"}:
            code = result["error"]
            text = "none of that item left in the bag" if code == "none_left" else "that item cannot be used in battle"
            err = message.error(code, text)
            # Keep the pet's line with the error so the deck can still show it.
            err.payload["say"] = result["say"]
            err.payload["item"] = result["item"]
            return err
        body = {
            "ok": result["ok"],
            "consumed": result["consumed"],
            "item": result["item"],
            "hp": result["hp"],
            "energy": result["energy"],
            "status_cleared": result["status_cleared"],
            "status": pet_status(self.creature),
            "say": result["say"],
            "log": result["log"],
            "bag": Bag(self.creature).payload(),
            "pet": self.pet_dict(),
        }
        if not result["ok"]:
            body["reason"] = result.get("reason", "no_effect")
        return message.reply(TYPE_PET_COMBAT_ITEM_OK, body)

    # ------------------------------------------------------------- moves
    def _combat_moves(self, message: Message, payload: dict[str, Any]) -> Message:
        creature = self.creature
        wanted = payload.get("set")
        if wanted is not None:
            if not isinstance(wanted, list) or not wanted or len(wanted) > MAX_LOADOUT:
                return message.error("bad_payload", f"pet.combat.moves set must list 1-{MAX_LOADOUT} move names")
            ids: list[str] = []
            for ref in wanted:
                found = find_move(str(ref)) if isinstance(ref, str) else None
                if found is None or found.id not in creature.known_move_ids:
                    return message.error("bad_payload", f"PULSE does not know the move {ref!r}")
                if found.id not in ids:
                    ids.append(found.id)
            creature.battle_moves = ids
        elif payload.get("auto") is True:
            creature.battle_moves = []
        known = [battle_move_payload(creature, MOVE_LIBRARY[m]) for m in creature.known_move_ids if m in MOVE_LIBRARY]
        return message.reply(TYPE_PET_COMBAT_MOVES_OK, {
            "moves": [battle_move_payload(creature, m) for m in loadout(creature)],
            "known": known,
            "auto": not creature.battle_moves,
        })

    # ------------------------------------------------------------ roster
    def _combat_roster(self, message: Message, payload: dict[str, Any]) -> Message:
        return message.reply(TYPE_PET_COMBAT_ROSTER_OK, dict(chart_payload(), enemies=roster_payload()))

    # --------------------------------------------------------------- run
    def _combat_run(self, message: Message, payload: dict[str, Any]) -> Message:
        """Optional: suggested flee odds plus both lines. The deck rolls."""
        enemy = payload.get("enemy")
        if enemy is not None and not isinstance(enemy, dict):
            return message.error("bad_payload", "pet.combat.run enemy must be an object")
        foe = normalize_enemy(enemy or {})
        slots = {"foe": foe["name"]}
        return message.reply(TYPE_PET_COMBAT_RUN_OK, {
            "chance": flee_chance(self.creature, foe),
            "say_ok": battle_say(self.creature, "battle.flee.ok", self.rng, slots),
            "say_fail": battle_say(self.creature, "battle.flee.fail", self.rng, slots),
        })

    # --------------------------------------------------------------- end
    def _combat_end(self, message: Message, payload: dict[str, Any]) -> Message:
        creature = self.creature
        life = creature.life
        now = self.now()
        before_level = creature.stats.level
        before_moves = list(creature.known_move_ids)
        before_species = creature.species_id
        bond_before = life.bond
        result = apply_combat_end(creature, payload)
        if not result["ok"]:
            return message.error("bad_payload", result["reason"])
        # Network dungeon fights: count them per dungeon; in range = bonus XP.
        mult = self.dungeon_combat_tally(payload, result["result"])
        if mult > 1.0 and result["result"] == "win" and result["xp_granted"] > 0:
            bonus = int(round(result["xp_granted"] * (mult - 1.0)))
            creature.add_xp(bonus)
            result["xp_granted"] += bonus
        self._note_growth(before_species, before_level, before_moves)
        life.on_combat_end(creature, result["result"], self.rng, now)  # lingering moods, memories
        outcome = result["result"]
        tally = {"win": "wins", "lose": "losses", "flee": "flees"}[outcome]
        life.record[tally] = int(life.record.get(tally, 0)) + 1
        if outcome == "win" and result["boss"]:
            life.record["bosses"] = int(life.record.get("bosses", 0)) + 1
        if result["fainted"]:
            life.record["faints"] = int(life.record.get("faints", 0)) + 1
        if outcome == "win":
            life.add_bond(4.0 if result["boss"] else 1.0)  # on top of +1 from on_combat_end
        elif outcome == "lose" and result["fainted"]:
            life.add_modifier("fainted", -10.0, 3600.0, now)
        learned = [MOVE_LIBRARY[m].name for m in creature.known_move_ids if m not in before_moves and m in MOVE_LIBRARY]
        level_up = creature.stats.level > before_level
        name = display_name(creature)
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        first = enemies[0] if enemies else None
        foe_name = (first.get("name") if isinstance(first, dict) else first) or "foe"
        slots = {"foe": str(foe_name)}
        log: list[str] = []
        if outcome == "win":
            log.append(f"{name} won the battle!")
            if result["xp_granted"]:
                log.append(f"{name} gained {result['xp_granted']} XP!")
            if level_up:
                log.append(f"{name} grew to Lv {creature.stats.level}!")
            for move_name in learned:
                log.append(f"{name} learned {move_name.upper()}!")
            if learned:
                key, slots["move"] = "battle.learned", learned[0]
            elif level_up:
                key = "battle.levelup"
            else:
                key = "battle.victory.boss" if result["boss"] else "battle.victory"
        elif outcome == "lose":
            log.append(f"{name} was defeated...")
            if result["fainted"]:
                log.append(f"{name} limps home with 1 HP.")
            key = "battle.defeat"
        else:
            # The deck prints its own "Got away safely!"; PULSE only reacts (say).
            key = "battle.fled"
        for item in result["loot_added"]:
            log.append(f"Put {item['name'].upper()} in the bag.")
        say = battle_say(creature, key, self.rng, slots)
        return message.reply(TYPE_PET_COMBAT_END_OK, {
            "result": outcome,
            "xp": result["xp_granted"],
            "xp_granted": result["xp_granted"],
            "level_up": level_up,
            "level": creature.stats.level,
            "learned_move": learned[0] if learned else None,
            "bond_delta": round(life.bond - bond_before, 1),
            "bond": life.bond_tier(),
            # A loss always reads as sad here, whatever the stats say.
            "mood": "sad" if outcome == "lose" else self._mood_word(),
            "fainted": result["fainted"],
            "loot_added": result["loot_added"],
            "reaction": result["reaction"],
            "say": say,
            "log": [clamp_say(line) for line in log],
            "xp_mult": mult,
            **self._battle_common(),
            "pet": self.pet_dict(),
        })

    # --------------------------------------------------------- inventory
    def _inventory(self, message: Message, payload: dict[str, Any]) -> Message:
        bag = Bag(self.creature)
        action = str(payload.get("action") or "list").strip().lower()
        if action in {"add", "drop"}:
            item = payload.get("item")
            if not (isinstance(item, str) and item.strip()) and not (isinstance(item, dict) and (item.get("name") or item.get("id"))):
                return message.error("bad_payload", f"pet.inventory {action} needs payload.item (a name or {{name, kind, power}})")
            try:
                qty = max(1, min(99, int(payload.get("qty", 1))))
            except (TypeError, ValueError):
                return message.error("bad_payload", "qty must be an integer")
            if action == "add":
                info = bag.add(item, qty)
                changed = {"id": info["id"], "name": info["name"], "qty": bag.qty(info["id"])}
            else:
                key = bag.find(item.get("id") or item.get("name") if isinstance(item, dict) else item)
                if key is None or bag.qty(key) <= 0:
                    return message.error("none_left", "that item is not in the bag")
                for _ in range(qty):
                    if not bag.take(key):
                        break
                changed = {"id": key, "qty": bag.qty(key)}
            return message.reply(TYPE_PET_INVENTORY_OK, {"action": action, "changed": changed, "bag": bag.payload()})
        if action != "list":
            return message.error("bad_payload", "pet.inventory action must be list, add, or drop")
        return message.reply(TYPE_PET_INVENTORY_OK, {"action": "list", "bag": bag.payload()})
