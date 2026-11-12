"""Protocol handlers for the "aliveness" messages (mixed into CompanionHandler).

All of these are optional for the deck. Existing messages keep their exact
shapes; replies here add a ``say`` field with the pet's speech bubble.
"""

from __future__ import annotations

from typing import Any

from link.protocol import (
    TYPE_PET_ACTIVITY_OK,
    TYPE_PET_ADVENTURE_OK,
    TYPE_PET_DIARY_OK,
    TYPE_PET_EGG_OK,
    TYPE_PET_HEAL_OK,
    TYPE_PET_LIFE_OK,
    TYPE_PET_PRAISE_OK,
    TYPE_PET_RENAME_OK,
    TYPE_PET_RESET_OK,
    TYPE_PET_RIVAL_OK,
    TYPE_PET_SAY_OK,
    TYPE_PET_SCOLD_OK,
    TYPE_PET_SHELF_OK,
    Message,
)
from pet.evolution import FINAL_STAGE
from pet.life import tier_for
from pet.moves import MOVE_LIBRARY, find_move
from pet.speech import load_pools

NICKNAME_MAX = 16
SAY_TOPICS = ("chatter", "funfact", "greeting", "dream", "ramble", "cheer", "ask")


def obedience_tier(value: float) -> str:
    if value < 35:
        return "wild"
    if value < 55:
        return "willful"
    if value < 75:
        return "good"
    return "loyal"


def life_payload(creature: Any) -> dict[str, Any]:
    """Detailed (but still not exact) life summary for pet.status / pet.life."""
    life = creature.life
    now = life.last_now
    adv = life.adventure
    rival = life.rival or {}
    active = rival.get("active")
    usual = life.usual_time_label()
    return {
        "bond": life.bond_tier(),
        "obedience": obedience_tier(life.obedience),
        "nickname": life.nickname,
        "generation": life.generation,
        "modifiers": life.active_modifiers(now),
        "mood_offset": round(life.mood_offset(now), 1),
        "sick": dict(life.sick) if life.sick else None,
        "tantrum": {"active": life.in_tantrum(now), "seconds_left": max(0, int(life.tantrum_until - now))},
        "ask": {"kind": life.ask["kind"], "expires_in": max(0, int(life.ask["until"] - now))} if life.ask else None,
        "likes": life.known_preferences(),
        "quirks": {k: v for k, v in life.quirks.items() if k in {"catchphrase", "dance", "sneeze"}},
        "time_of_day": life.time_of_day(now),
        "temp": dict(life.temp) if life.temp else None,
        "weather": dict(life.weather) if life.weather else None,
        "costume": life.costume(now),
        "place": life.current_place(),
        "turf": life.turf()[:5],
        "networks_known": len(life.networks),
        "shelf_count": len(life.shelf),
        "adventure": {"away": True, "place": adv["place"], "return_in": max(0, int(adv["until"] - now))} if adv else {"away": False},
        "rival": {
            "name": rival.get("name"),
            "wins": rival.get("wins", 0),
            "losses": rival.get("losses", 0),
            "present": bool(active),
            "level": active.get("level") if active else None,
        },
        "usual_time": usual,
        "egg": dict(life.egg) if life.egg else None,
        "link": "up" if life.link_up else ("separated" if life.separated_at is not None else "idle"),
    }


class AliveMixin:
    """Expects self.creature, self.rng, self.now(), self.pet_dict()."""

    # ---------------------------------------------------------------- rename
    def _rename(self, message: Message, payload: dict[str, Any]) -> Message:
        raw = payload.get("name")
        if not isinstance(raw, str) or not raw.strip():
            return message.error("bad_payload", "pet.rename needs payload.name as a non-empty string")
        name = " ".join(raw.split())
        if len(name) > NICKNAME_MAX or not name.isprintable():
            return message.error("bad_payload", f"nickname must be printable and at most {NICKNAME_MAX} characters")
        life, now = self.creature.life, self.now()
        if life.in_tantrum(now):
            return message.reply(TYPE_PET_RENAME_OK, {
                "accepted": False, "reason": "tantrum", "nickname": life.nickname,
                "say": life.tantrum_reply(self.creature, self.rng, now), "pet": self.pet_dict(),
            })
        result = life.on_rename(self.creature, name, self.rng, now)
        return message.reply(TYPE_PET_RENAME_OK, {
            "accepted": True, "nickname": name, "old": result["old"],
            "reaction": result["reaction"], "say": result["say"], "pet": self.pet_dict(),
        })

    # ---------------------------------------------------------- praise/scold
    def _praise(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        if life.in_tantrum(now):
            return message.reply(TYPE_PET_PRAISE_OK, {
                "accepted": False, "reason": "tantrum",
                "say": life.tantrum_reply(self.creature, self.rng, now), "pet": self.pet_dict(),
            })
        result = life.on_praise(self.creature, self.rng, now)
        return message.reply(TYPE_PET_PRAISE_OK, {
            "accepted": True, "effect": result["effect"], "obedience": obedience_tier(life.obedience),
            "say": result["say"], "pet": self.pet_dict(),
        })

    def _scold(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        result = life.on_scold(self.creature, self.rng, now)
        return message.reply(TYPE_PET_SCOLD_OK, {
            "deserved": result["deserved"], "tantrum": result["tantrum"],
            "obedience": obedience_tier(life.obedience), "say": result["say"], "pet": self.pet_dict(),
        })

    # ------------------------------------------------------------------ heal
    def _heal(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        was = life.sick.get("kind") if life.sick else None
        say = life.heal(self.creature, self.rng, now)
        return message.reply(TYPE_PET_HEAL_OK, {"healed": was is not None, "was": was, "say": say, "pet": self.pet_dict()})

    # ----------------------------------------------------------- diary/shelf
    def _diary(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        try:
            limit = int(payload.get("limit", 7))
        except (TypeError, ValueError):
            return message.error("bad_payload", "pet.diary limit must be an integer")
        limit = max(1, min(30, limit))
        body: dict[str, Any] = {"entries": life.diary[-limit:], "count": len(life.diary)}
        if payload.get("today"):
            body["today"] = life.write_diary(self.creature, self.rng, now, draft=True)
        return message.reply(TYPE_PET_DIARY_OK, body)

    def _shelf(self, message: Message, payload: dict[str, Any]) -> Message:
        life = self.creature.life
        favorite = life.favorite_trinket()
        body: dict[str, Any] = {"items": life.shelf, "count": len(life.shelf), "favorite": favorite}
        if payload.get("show_off") and favorite:
            body["say"] = life.say(self.creature, "idle.show_off", self.rng, self.now(), {"trinket": favorite, "count": len(life.shelf)}, decorate=False)
            life.set_anim("show_off", self.now(), 6.0)
        return message.reply(TYPE_PET_SHELF_OK, body)

    # --------------------------------------------------------------- activity
    def _activity(self, message: Message, payload: dict[str, Any]) -> Message:
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            return message.error("bad_payload", "pet.activity needs payload.kind as a non-empty string")
        name = payload.get("name")
        result = payload.get("result")
        name_s = str(name).strip() if name is not None and str(name).strip() else None
        result_s = str(result).strip().lower() if result is not None and str(result).strip() else None
        life, now = self.creature.life, self.now()
        say = life.on_activity(self.creature, kind.strip().lower(), name_s, result_s, self.rng, now)
        body: dict[str, Any] = {"kind": kind.strip().lower(), "say": say, "pet": self.pet_dict()}
        if kind.strip().lower() in {"dungeon", "dungeon_start"} and not result_s and life.bond >= 45:
            body["cheer"] = life.say(self.creature, "cheer", self.rng, now, decorate=False)
        return message.reply(TYPE_PET_ACTIVITY_OK, body)

    # -------------------------------------------------------------------- say
    def _say(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        topic = str(payload.get("topic") or "chatter").strip().lower()
        if topic not in SAY_TOPICS:
            return message.error("bad_payload", f"unknown topic; use one of {', '.join(SAY_TOPICS)}")
        if life.in_tantrum(now):
            text = life.tantrum_reply(self.creature, self.rng, now)
        elif topic == "funfact":
            if life.networks:
                text = life.say(self.creature, "gift_back.fact", self.rng, now, {"fact": life.fun_fact(self.rng, now)}, decorate=False)
            else:
                text = life.say(self.creature, "chatter", self.rng, now)
        elif topic == "greeting":
            text = life.say(self.creature, "greet.short", self.rng, now)
        elif topic == "dream":
            text = life.say(self.creature, "dream.generic", self.rng, now, decorate=False)
        elif topic == "ask":
            kind = life.ask["kind"] if life.ask else "attention"
            text = life.say(self.creature, f"ask.{kind}", self.rng, now)
        else:
            text = life.say(self.creature, topic, self.rng, now)
        life.touch(now)
        return message.reply(TYPE_PET_SAY_OK, {"topic": topic, "say": text, "pet": self.pet_dict()})

    # ------------------------------------------------------------------ rival
    def _rival(self, message: Message, payload: dict[str, Any]) -> Message:
        from pet.rival import fight_rival

        life, now = self.creature.life, self.now()
        action = str(payload.get("action") or "status").strip().lower()
        rival = life.rival
        active = rival.get("active")
        record = lambda: {"wins": rival.get("wins", 0), "losses": rival.get("losses", 0)}  # noqa: E731
        if action == "status":
            return message.reply(TYPE_PET_RIVAL_OK, {"action": action, "rival": rival.get("name"), "present": bool(active), "foe": active, "record": record()})
        if action == "challenge":
            if not active:
                active = life.rival_appear(self.creature, self.rng, now)
            action = "fight"
        if action == "ignore":
            if not active:
                return message.reply(TYPE_PET_RIVAL_OK, {"action": action, "ok": False, "reason": "no rival here", "record": record()})
            rival["active"] = None
            rival["next_at"] = now + 3600.0
            say = life.say(self.creature, "rival.ignore", self.rng, now, {"rival": rival.get("name")})
            return message.reply(TYPE_PET_RIVAL_OK, {"action": action, "ok": True, "say": say, "record": record(), "pet": self.pet_dict()})
        if action != "fight":
            return message.error("bad_payload", "pet.rival action must be status, fight, ignore, or challenge")
        if not active:
            return message.reply(TYPE_PET_RIVAL_OK, {"action": action, "ok": False, "reason": "no rival here", "record": record()})
        if self.creature.asleep or self.creature.fainted:
            return message.reply(TYPE_PET_RIVAL_OK, {"action": action, "ok": False, "reason": "asleep" if self.creature.asleep else "fainted", "record": record()})
        life.touch(now)
        slots = life.slots(self.creature, now, {"rival": rival.get("name")})
        retort = life.say(self.creature, "rival.retort", self.rng, now, {"rival": rival.get("name")}, decorate=False)
        outcome = fight_rival(self.creature, active, self.rng, picker=life.picker, slots=slots)
        rival["active"] = None
        rival["next_at"] = now + 14400.0
        level = int(active.get("level", 1))
        if outcome["result"] == "win":
            rival["losses"] = int(rival.get("losses", 0)) + 1
            xp = 6 + 2 * level
            life.add_modifier("won_fight", 10.0, 1200.0, now)
            life.add_bond(2.0)
            life.count("rival_wins")
            life.count("wins")
            loot = None
            if self.rng.random() < 0.5:
                from pet.stories import trinket_name

                loot = trinket_name(life.picker, self.rng)
                life.add_to_shelf(loot, "rival", 2, now)
            say = life.say(self.creature, "rival.win", self.rng, now, {"rival": rival.get("name"), "count": rival["losses"]})
        else:
            rival["wins"] = int(rival.get("wins", 0)) + 1
            xp = 2
            loot = None
            life.add_modifier("lost_fight", -10.0, 1500.0, now)
            life.count("losses")
            say = life.say(self.creature, "rival.lose", self.rng, now, {"rival": rival.get("name")})
        before_species = self.creature.species_id
        before_level = self.creature.stats.level
        before_moves = list(self.creature.known_move_ids)
        self.creature.add_xp(xp)
        self._note_growth(before_species, before_level, before_moves)
        life.remember("fight", rival.get("name"), now)
        detail = {"rival": rival.get("name"), "result": outcome["result"], "xp": xp, "trinket": loot, "log": outcome["log"], "say": say}
        life.emit("rival_result", detail)
        return message.reply(TYPE_PET_RIVAL_OK, {
            "action": "fight", "ok": True, "result": outcome["result"], "retort": retort,
            "log": outcome["log"], "xp": xp, "trinket": loot, "say": say, "record": record(), "pet": self.pet_dict(),
        })

    # -------------------------------------------------------------- adventure
    def _adventure(self, message: Message, payload: dict[str, Any]) -> Message:
        life, now = self.creature.life, self.now()
        action = str(payload.get("action") or "status").strip().lower()
        if action == "go":
            if life.in_tantrum(now):
                return message.reply(TYPE_PET_ADVENTURE_OK, {"action": action, "ok": False, "reason": "tantrum"})
            if self.creature.stats.energy < 25 or life.sick:
                return message.reply(TYPE_PET_ADVENTURE_OK, {"action": action, "ok": False, "reason": "sick" if life.sick else "too tired"})
            started = life.start_adventure(self.creature, self.rng, now)
            if not started:
                return message.reply(TYPE_PET_ADVENTURE_OK, {"action": action, "ok": False, "reason": "already away" if life.adventure else "cannot go now"})
            return message.reply(TYPE_PET_ADVENTURE_OK, {"action": action, "ok": True, "place": started["place"], "return_in": int(started["until"] - now), "say": life.current_bubble(now)["text"] if life.current_bubble(now) else ""})
        if action == "recall":
            detail = life.finish_adventure(self.creature, self.rng, now, cut_short=True)
            if detail is None:
                return message.reply(TYPE_PET_ADVENTURE_OK, {"action": action, "ok": False, "reason": "not away"})
            return message.reply(TYPE_PET_ADVENTURE_OK, dict(detail, action=action, ok=True, pet=self.pet_dict()))
        if action != "status":
            return message.error("bad_payload", "pet.adventure action must be status, go, or recall")
        adv = life.adventure
        return message.reply(TYPE_PET_ADVENTURE_OK, {
            "action": "status", "away": bool(adv), "place": adv["place"] if adv else None,
            "return_in": max(0, int(adv["until"] - now)) if adv else 0, "adventures": life.adventures,
        })

    # -------------------------------------------------------------------- egg
    def _egg(self, message: Message, payload: dict[str, Any]) -> Message:
        creature = self.creature
        life, now = creature.life, self.now()
        action = str(payload.get("action") or "status").strip().lower()
        if action == "status":
            return message.reply(TYPE_PET_EGG_OK, {
                "action": action, "egg": life.egg, "can_lay": creature.species.stage == FINAL_STAGE and not life.egg,
                "generation": life.generation, "lineage": life.lineage,
            })
        if action == "lay":
            if life.egg:
                return message.reply(TYPE_PET_EGG_OK, {"action": action, "ok": False, "reason": "already has an egg", "egg": life.egg, "say": life.say(creature, "egg.already", self.rng, now)})
            if creature.species.stage != FINAL_STAGE:
                return message.reply(TYPE_PET_EGG_OK, {"action": action, "ok": False, "reason": f"only a {FINAL_STAGE} form can lay an egg", "say": life.say(creature, "egg.not_ready", self.rng, now)})
            move_raw = payload.get("move")
            move_id = None
            if isinstance(move_raw, str) and move_raw.strip():
                found = find_move(move_raw.strip())
                move_id = found.id if found is not None else None
            egg = life.lay_egg(creature, self.rng, now, move_id=move_id, trait=payload.get("trait"))
            move_name = MOVE_LIBRARY[egg["move"]].name if egg["move"] in MOVE_LIBRARY else egg["move"]
            say = life.say(creature, "egg.laid", self.rng, now, {"move": move_name, "trait": egg["trait"].replace("_", " ")})
            life.emit("egg_laid", {"egg": egg, "say": say})
            return message.reply(TYPE_PET_EGG_OK, {"action": action, "ok": True, "egg": egg, "say": say, "pet": self.pet_dict()})
        if action == "hatch":
            if not life.egg:
                return message.reply(TYPE_PET_EGG_OK, {"action": action, "ok": False, "reason": "no egg to hatch"})
            if payload.get("confirm") is not True:
                return message.error("bad_payload", "pet.egg hatch replaces the current pet; send confirm: true")
            legacy = creature.hatch_from_egg(now, self.rng)
            move_name = MOVE_LIBRARY[legacy["move"]].name if legacy.get("move") in MOVE_LIBRARY else legacy.get("move")
            say = creature.life.say(creature, "egg.hatched", self.rng, now, {"move": move_name, "generation": legacy["generation"]})
            creature.life.emit("egg_hatched", {"legacy": legacy, "say": say})
            self._seen = self._snapshot()
            return message.reply(TYPE_PET_EGG_OK, {"action": action, "ok": True, "legacy": legacy, "say": say, "pet": self.pet_dict()})
        return message.error("bad_payload", "pet.egg action must be status, lay, or hatch")

    # ------------------------------------------------------------ life/reset
    def _life(self, message: Message, payload: dict[str, Any]) -> Message:
        return message.reply(TYPE_PET_LIFE_OK, {"life": life_payload(self.creature)})

    def _reset(self, message: Message, payload: dict[str, Any]) -> Message:
        if payload.get("confirm") is not True:
            return message.error("bad_payload", "pet.reset erases the pet; send confirm: true")
        self.creature.reset(now=self.now(), rng=self.rng)
        self._seen = self._snapshot()
        return message.reply(TYPE_PET_RESET_OK, {"reset": True, "pet": self.pet_dict()})


def pools_loaded() -> int:
    return len(load_pools())


__all__ = ["AliveMixin", "life_payload", "obedience_tier", "tier_for"]
