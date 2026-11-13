"""Network dungeon messages: pet.dungeons / pet.dungeon.enter|floor|leave.

The deck owns the map, movement and fights. The companion owns the list of
dungeons (one per discovered signal), each dungeon's seed and generation
parameters, progress (depth record, checkpoints), rewards and PULSE's lines.
See docs/DUNGEON_INTEGRATION.md, "Network dungeons".
"""

from __future__ import annotations

from typing import Any

from link.protocol import (
    Message,
    TYPE_PET_DUNGEON_ENTER_OK,
    TYPE_PET_DUNGEON_FLOOR_OK,
    TYPE_PET_DUNGEON_LEAVE_OK,
    TYPE_PET_DUNGEONS_OK,
)
from pet import dungeons as dgn
from pet.battle import Bag, battle_say, clamp_say, display_name
from pet.speech import load_pools

MAX_DECK_XP = 999
MAX_DECK_LOOT = 10


class DungeonMixin:
    """Mixed into CompanionHandler (needs creature, rng, now(), pet_dict(), _note_growth)."""

    # ------------------------------------------------------------- helpers
    def _dungeon_runs(self) -> dict[str, int]:
        runs = getattr(self, "_dungeon_run_state", None)
        if runs is None:
            runs = self._dungeon_run_state = {}
        return runs

    def _dungeon_lookup(self, message: Message, payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None, Message | None]:
        did = payload.get("id")
        if not isinstance(did, str) or not did.strip():
            return "", None, message.error("bad_payload", "id is required")
        did = did.strip().lower()
        life = self.creature.life
        life.update_home()
        rec = life.dungeons.get(did)
        if rec is None:
            return did, None, message.error("unknown_dungeon", "no dungeon with that id")
        return did, rec, None

    def _dsay(self, key: str, rec: dict[str, Any], **slots: Any) -> str:
        slots.setdefault("dungeon", rec.get("name", "the dungeon"))
        return dgn.keep_name(battle_say(self.creature, key, self.rng, slots), slots["dungeon"])

    def dungeon_entry(self, did: str, rec: dict[str, Any], now: float) -> dict[str, Any]:
        """One row of pet.dungeons."""
        depth = int(rec.get("depth", 0))
        return {
            "id": did,
            "name": rec["name"],
            "kind": rec["kind"],
            "type": rec["type"],
            "type2": rec.get("type2"),
            "rare": bool(rec.get("rare")),
            "difficulty": int(rec.get("difficulty", 4)),
            "levels": dgn.level_range(rec),
            "in_range": dgn.in_range(rec, now),
            "home": bool(rec.get("home")),
            "depth_record": depth,
            "checkpoint": dgn.checkpoint_for(depth),
            "starts": dgn.start_floors(depth),
            "xp_mult": dgn.xp_mult(rec, now),
            "clears": int(rec.get("clears", 0)),
            "boss_kills": int(rec.get("boss_kills", 0)),
            "new": not rec.get("entered"),
            "last_seen": int(rec.get("last_seen", 0)),
            "unlocked_at": int(rec.get("first_seen", 0)),
        }

    def dungeon_floor_info(self, did: str, rec: dict[str, Any], floor: int, say: str) -> dict[str, Any]:
        """The enter reply / the floor reply's `next`: everything the deck needs to build a floor."""
        now = self.now()
        return {
            "id": did,
            "name": rec["name"],
            "seed": dgn.seed_for(did),
            "floor": int(floor),
            "layout": dgn.layout(did, rec, floor),
            "theme": dgn.theme(did, rec),
            "enemies": dgn.enemies_for(did, rec, floor),
            "boss": dgn.boss_for(did, rec, floor),
            "checkpoint": dgn.is_checkpoint_floor(floor),
            "xp_mult": dgn.xp_mult(rec, now),
            "loot_mult": dgn.loot_mult(rec, floor),
            "home": bool(rec.get("home")),
            "in_range": dgn.in_range(rec, now),
            "say": clamp_say(say),
        }

    def _floor_entry_say(self, did: str, rec: dict[str, Any], floor: int) -> str:
        boss = dgn.boss_for(did, rec, floor)
        if boss:
            return self._dsay("dungeon.floor.boss", rec, floor=floor, boss=boss["title"])
        if dgn.is_checkpoint_floor(floor) and floor > 1:
            return self._dsay("dungeon.floor.checkpoint", rec, floor=floor)
        return self._dsay("dungeon.floor", rec, floor=floor)

    # ------------------------------------------------------------- messages
    def _dungeons(self, message: Message, payload: dict[str, Any]) -> Message:
        life = self.creature.life
        now = self.now()
        home = life.update_home()
        rows = [self.dungeon_entry(did, rec, now) for did, rec in life.dungeons.items()]
        rows.sort(key=lambda r: (not r["home"], not r["in_range"], -r["depth_record"], -r["last_seen"], r["name"]))
        return message.reply(TYPE_PET_DUNGEONS_OK, {
            "dungeons": rows,
            "count": len(rows),
            "in_range": sum(1 for r in rows if r["in_range"]),
            "home": home,
        })

    def _dungeon_enter(self, message: Message, payload: dict[str, Any]) -> Message:
        did, rec, err = self._dungeon_lookup(message, payload)
        if err is not None:
            return err
        raw_floor = payload.get("floor")
        if raw_floor is not None and (isinstance(raw_floor, bool) or not isinstance(raw_floor, int) or raw_floor < 1):
            return message.error("bad_payload", "floor must be a whole number >= 1")
        depth = int(rec.get("depth", 0))
        floor = dgn.snap_start(depth, raw_floor)
        now = self.now()
        first_time = not rec.get("entered")
        rec["entered"] = True
        rec["runs"] = int(rec.get("runs", 0)) + 1
        self._dungeon_runs()[did] = floor
        if rec.get("home"):
            say = self._dsay("dungeon.home", rec)
        else:
            say = self._dsay(f"dungeon.intro.{rec['type'].lower()}", rec)
        body = self.dungeon_floor_info(did, rec, floor, say)
        flavor = dgn.flavor(did, rec, load_pools())
        log = [f"{rec['name']}: floor {floor}."]
        if dgn.in_range(rec, now):
            log.append(f"In range: x{dgn.IN_RANGE_XP_MULT:g} XP!")
        if rec.get("home"):
            log.append("Home turf: safer floors, heals.")
        body.update({
            "kind": rec["kind"],
            "difficulty": int(rec.get("difficulty", 4)),
            "flavor": flavor,
            "first_visit": first_time,
            "depth_record": depth,
            "checkpoint_floor": dgn.checkpoint_for(depth),
            "starts": dgn.start_floors(depth),
            "log": [clamp_say(line) for line in log],
            "pet": self.pet_dict(),
        })
        return message.reply(TYPE_PET_DUNGEON_ENTER_OK, body)

    def _dungeon_floor(self, message: Message, payload: dict[str, Any]) -> Message:
        did, rec, err = self._dungeon_lookup(message, payload)
        if err is not None:
            return err
        floor = payload.get("floor")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
            return message.error("bad_payload", "floor must be a whole number >= 1")
        result = str(payload.get("result") or "").strip().lower()
        if result not in dgn.RESULTS:
            return message.error("bad_payload", "result must be cleared, stairs, fainted or fled")
        depth = int(rec.get("depth", 0))
        runs = self._dungeon_runs()
        if floor > max(depth + 1, runs.get(did, 0)):
            return message.error("bad_payload", f"floor {floor} is not reached yet")

        creature, life, now = self.creature, self.creature.life, self.now()
        before_species, before_level = creature.species_id, creature.stats.level
        before_moves = list(creature.known_move_ids)
        bond_before = life.bond
        name = display_name(creature)
        boss_floor = dgn.is_boss_floor(floor)
        beaten = payload.get("boss_beaten")
        boss_beaten = bool(beaten) if isinstance(beaten, bool) else (boss_floor and result == "cleared")
        boss_beaten = boss_beaten and boss_floor
        mult = dgn.xp_mult(rec, now)
        progressed = result in ("cleared", "stairs")
        log: list[str] = []

        deck_xp = payload.get("xp")
        deck_xp = max(0, min(MAX_DECK_XP, int(deck_xp))) if isinstance(deck_xp, (int, float)) and not isinstance(deck_xp, bool) else 0
        xp = int(round((dgn.floor_xp(floor, result, boss_beaten) + deck_xp) * mult))

        new_record = progressed and floor > depth
        old_cp = dgn.checkpoint_for(depth)
        if progressed:
            rec["depth"] = max(depth, floor)
            rec["clears"] = int(rec.get("clears", 0)) + 1
            log.append(f"Floor {floor} {'cleared' if result == 'cleared' else 'passed'}!")
        if boss_beaten:
            rec["boss_kills"] = int(rec.get("boss_kills", 0)) + 1
        new_cp = dgn.checkpoint_for(int(rec.get("depth", 0)))
        checkpoint_reached = new_cp > old_cp
        enemies = payload.get("enemies")
        if isinstance(enemies, list):
            rec["defeated"] = int(rec.get("defeated", 0)) + len(enemies)

        if xp > 0:
            creature.add_xp(xp)
            log.append(f"{name} gained {xp} XP!")

        bag = Bag(creature)
        loot: dict[str, dict[str, Any]] = {}

        def put(item: Any) -> None:
            try:
                info = bag.add(item)
            except (ValueError, TypeError):
                return
            row = loot.setdefault(info["id"], {"id": info["id"], "name": info["name"], "qty": 0})
            row["qty"] += 1

        if result == "cleared":
            for item in dgn.roll_loot(did, rec, floor, boss_beaten, int(rec.get("clears", 0))):
                put(item)
        deck_loot = payload.get("loot")
        if isinstance(deck_loot, list):
            for item in deck_loot[:MAX_DECK_LOOT]:
                if isinstance(item, (str, dict)):
                    put(item)
        for row in loot.values():
            log.append(f"Got {row['name'].upper()} x{row['qty']}.")

        if progressed:
            life.add_bond(0.5 + (2.0 if boss_beaten else 0.0) + (1.0 if new_record else 0.0))
        if new_record:
            log.append(f"New record: floor {floor}!")
        if checkpoint_reached:
            log.append(f"Checkpoint: floor {new_cp} unlocked!")

        healed = 0
        stats = creature.stats
        if progressed and rec.get("home") and stats.hp > 0:
            healed = min(max(1, round(stats.max_hp * dgn.HOME_HEAL_SHARE)), max(0, stats.max_hp - stats.hp))
            stats.hp += healed
            if healed:
                log.append(f"Home turf: {name} healed {healed} HP.")

        boss = dgn.boss_for(did, rec, floor)
        if boss_beaten:
            say = self._dsay("dungeon.milestone", rec, floor=floor, boss=(boss or {}).get("title", "the boss"))
        elif new_record and floor % 5 == 0:
            say = self._dsay("dungeon.milestone", rec, floor=floor, boss="the boss")
        elif new_record:
            say = self._dsay("dungeon.record", rec, floor=floor)
        elif healed:
            say = self._dsay("dungeon.home.heal", rec, hp=healed)
        else:
            say = self._dsay(f"dungeon.{result}", rec, floor=floor)

        if progressed:
            runs[did] = floor + 1
            nxt = self.dungeon_floor_info(did, rec, floor + 1, self._floor_entry_say(did, rec, floor + 1))
        else:
            # fainted or fled: the run ends; the next entry starts at the last checkpoint.
            runs.pop(did, None)
            nxt = None
            if result == "fainted":
                log.append(f"Back to floor {new_cp} next time.")
        self._note_growth(before_species, before_level, before_moves)
        depth_now = int(rec.get("depth", 0))
        return message.reply(TYPE_PET_DUNGEON_FLOOR_OK, {
            "id": did,
            "floor": floor,
            "result": result,
            "xp": xp,
            "xp_mult": mult,
            "loot": list(loot.values()),
            "bond": life.bond_tier(),
            "bond_delta": round(life.bond - bond_before, 1),
            "depth_record": depth_now,
            "new_record": new_record,
            "checkpoint_reached": checkpoint_reached,
            "checkpoint": new_cp,
            "boss_beaten": boss_beaten,
            "healed": healed,
            "next": nxt,
            "say": clamp_say(say),
            "log": [clamp_say(line) for line in log],
            "hp": stats.hp,
            "pet": self.pet_dict(),
        })

    def _dungeon_leave(self, message: Message, payload: dict[str, Any]) -> Message:
        did, rec, err = self._dungeon_lookup(message, payload)
        if err is not None:
            return err
        self._dungeon_runs().pop(did, None)
        depth = int(rec.get("depth", 0))
        return message.reply(TYPE_PET_DUNGEON_LEAVE_OK, {
            "id": did,
            "depth_record": depth,
            "checkpoint": dgn.checkpoint_for(depth),
            "say": self._dsay("dungeon.leave", rec),
            "pet": self.pet_dict(),
        })

    def dungeon_combat_tally(self, payload: dict[str, Any], outcome: str) -> float:
        """pet.combat.end {dungeon_id, floor}: count it per dungeon; returns the XP multiplier."""
        did = payload.get("dungeon_id")
        rec = self.creature.life.dungeons.get(str(did).strip().lower()) if isinstance(did, str) else None
        if rec is None:
            return 1.0
        tally = {"win": "wins", "lose": "losses", "flee": "flees"}[outcome]
        battles = rec.setdefault("battles", {"wins": 0, "losses": 0, "flees": 0})
        battles[tally] = int(battles.get(tally, 0)) + 1
        return dgn.xp_mult(rec, self.now())
