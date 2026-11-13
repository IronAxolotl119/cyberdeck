"""simdungeons.py — network dungeons for the deck's built-in sim pet.

In plain English: when no real companion is connected, the deck's sim pet
(cyberdeck/companion.py) still answers pet.dungeons, pet.dungeon.enter,
pet.dungeon.floor and pet.dungeon.leave, with the same fields and the
same numbers as the real companion (companion/link/dungeon.py). The
generation rules are shared through cyberdeck/netdungeons.py.

The sim starts with a few fake networks so there is always something to
explore: the home network, a cafe, a speaker, a printer, an old office
that is out of range, and a rare hidden (VOID) one. Sightings from the
dashboard's pet.discovery unlock more. Raw ids are only hashed here,
never kept or sent.

Everything here works on a SimDungeons object that the sim's PetState
owns, and takes `now` (epoch seconds), so tests can fake the clock.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from cyberdeck import netdungeons as dgn
from cyberdeck.battledata import ITEMS, clamp_say

MAX_DECK_XP = 999
MAX_DECK_LOOT = 10

# kind, made-up id, name, rssi, secured, rare, seconds since last seen, home.
# Fake ids are not real MACs; the sim hashes them like the companion would.
SIM_NETWORKS: tuple[tuple[str, str, str, int | None, bool, bool, float, bool], ...] = (
    ("wifi", "sim-home", "HomeNet", -45, True, False, 0.0, True),
    ("wifi", "sim-cafe", "CafeNet-5G", -62, True, False, 10.0, False),
    ("bt", "sim-speaker", "JBL Flip 5", -70, False, False, 20.0, False),
    ("wifi", "sim-hidden", "", -86, False, True, 30.0, False),
    ("lan", "sim-printer", "printer.local", None, False, False, 40.0, False),
    ("wifi", "sim-office", "OldOffice", -78, True, False, 3600.0, False),
)

# A few short lines per moment (the real companion has dozens).
SIM_LINES: dict[str, tuple[str, ...]] = {
    "found": ("New dungeon: {dungeon}!", "Map unlocked: {dungeon}!"),
    "found.rare": ("A rare one! {dungeon}!", "{dungeon} hums in the dark."),
    "found.many": ("Found {count} more dungeons!", "{count} new places to explore!"),
    "intro.net": ("Packets drifting like snow here.", "Every room here has a router."),
    "intro.volt": ("My fur stands up in here.", "It crackles. I like it."),
    "intro.data": ("Rows and rows of old data.", "Smells like dusty tables."),
    "intro.void": ("It is so quiet it hums.", "The map here keeps blinking."),
    "home": ("Home turf! I know every corner.", "Our own network. Cozy."),
    "home.heal": ("Home heals me. +{hp} HP.",),
    "floor": ("Floor {floor}. Look out for loot!", "Down to floor {floor}."),
    "floor.boss": ("Big room. Bigger boss.", "{boss} waits on floor {floor}."),
    "floor.checkpoint": ("Checkpoint: floor {floor}.", "A safe save on floor {floor}."),
    "cleared": ("That's floor {floor}. Next please!", "Floor {floor}: spotless."),
    "stairs": ("Skipped ahead. Sneaky!", "Stairs! We'll come back."),
    "fainted": ("Ow... let's try again later.", "I need a nap. Next time."),
    "fled": ("Out we go. Good call.", "Enough dungeon for now."),
    "leave": ("Dungeon closed. For today.", "Back to the surface!"),
    "record": ("New record: floor {floor}!", "Deepest yet: floor {floor}!"),
    "milestone": ("We beat {boss}!", "{boss} is down. Floor {floor}!"),
}
FLAVOR: dict[str, tuple[str, ...]] = {
    "NET": ("A tunnel net spun by worms.", "Routers stacked like bricks."),
    "VOLT": ("Coils hum behind every wall.", "A cave lit by stray sparks."),
    "DATA": ("Old tables, filed and forgotten.", "A maze of indexes and logs."),
    "VOID": ("A place no scan should find.", "Static where the walls should be."),
}


@dataclass
class SimDungeons:
    """The sim pet's dungeon save: {id: record}, the open runs, pending news."""

    dungeons: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, int] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)
    cool_until: float = 0.0
    seeded: bool = False
    rng: random.Random = field(default_factory=lambda: random.Random(5), repr=False)

    # --- setup -------------------------------------------------------------
    def seed_fakes(self, now: float) -> None:
        """Add the fake networks once (they count as already found: no event)."""
        if self.seeded:
            return
        self.seeded = True
        for kind, ident, name, rssi, secured, rare, ago, home in SIM_NETWORKS:
            did, rec = dgn.new_record(kind, ident, name, rssi, now - ago, secured=secured, rare=rare)
            rec["home"] = home
            self.dungeons.setdefault(did, rec)

    def say(self, key: str, rec: dict[str, Any], **slots: Any) -> str:
        lines = SIM_LINES.get(key) or ("...",)
        slots.setdefault("dungeon", rec.get("name", "the dungeon"))
        text = self.rng.choice(lines)
        for k, v in slots.items():
            text = text.replace("{" + k + "}", str(v))
        return clamp_say(text)

    # --- sightings ---------------------------------------------------------------
    def sighting(self, kind: str, ident: str, name: str, rssi: int | None, now: float, secured: bool = False,
                 rare: bool = False, events: list | None = None) -> tuple[str | None, bool]:
        """A discovery: unlock (or refresh) the dungeon for this signal. (id, new)."""
        self.seed_fakes(now)
        if kind not in dgn.DUNGEON_KINDS or not (ident or name):
            return None, False
        did = dgn.dungeon_id(kind, ident, name)
        rec = self.dungeons.get(did)
        if rec is not None:
            rec["last_seen"] = max(float(rec.get("last_seen", 0.0)), now)
            if secured and not rec.get("secured"):
                rec["secured"] = True
                rec["difficulty"] = min(10, int(rec.get("difficulty", 4)) + 1)
            return did, False
        did, rec = dgn.new_record(kind, ident, name, rssi, now, secured=secured, rare=rare)
        self.dungeons[did] = rec
        self.pending.append(did)
        self.flush(now, events)
        return did, True

    def flush(self, now: float, events: list | None) -> None:
        """dungeon_found at most once per 30 s; the rest become one dungeons_found."""
        if events is None or not self.pending or now < self.cool_until:
            return
        pending, self.pending = [d for d in self.pending if d in self.dungeons], []
        if not pending:
            return
        self.cool_until = now + dgn.FOUND_COOLDOWN
        if len(pending) == 1:
            rec = self.dungeons[pending[0]]
            text = self.say("found.rare" if rec.get("rare") else "found", rec)
            events.append(("dungeon_found", {"id": pending[0], "name": rec["name"], "kind": rec["kind"],
                                             "type": rec["type"], "rare": bool(rec.get("rare")), "say": text}))
            return
        names = [self.dungeons[d]["name"] for d in pending]
        text = self.say("found.many", self.dungeons[pending[0]], count=len(pending))
        events.append(("dungeons_found", {"count": len(pending), "ids": pending, "names": names[:5], "say": text}))

    # --- the messages ------------------------------------------------------------
    def entry(self, did: str, rec: dict[str, Any], now: float) -> dict[str, Any]:
        depth = int(rec.get("depth", 0))
        return {"id": did, "name": rec["name"], "kind": rec["kind"], "type": rec["type"], "type2": rec.get("type2"),
                "rare": bool(rec.get("rare")), "difficulty": int(rec.get("difficulty", 4)),
                "levels": dgn.level_range(rec), "in_range": dgn.in_range(rec, now), "home": bool(rec.get("home")),
                "depth_record": depth, "checkpoint": dgn.checkpoint_for(depth), "starts": dgn.start_floors(depth),
                "xp_mult": dgn.xp_mult(rec, now), "clears": int(rec.get("clears", 0)),
                "boss_kills": int(rec.get("boss_kills", 0)), "new": not rec.get("entered"),
                "last_seen": int(rec.get("last_seen", 0)), "unlocked_at": int(rec.get("first_seen", 0))}

    def listing(self, now: float) -> dict[str, Any]:
        self.seed_fakes(now)
        rows = [self.entry(did, rec, now) for did, rec in self.dungeons.items()]
        rows.sort(key=lambda r: (not r["home"], not r["in_range"], -r["depth_record"], -r["last_seen"], r["name"]))
        home = next((did for did, rec in self.dungeons.items() if rec.get("home")), None)
        return {"dungeons": rows, "count": len(rows), "in_range": sum(1 for r in rows if r["in_range"]), "home": home}

    def floor_info(self, did: str, rec: dict[str, Any], floor: int, say: str, now: float) -> dict[str, Any]:
        return {"id": did, "name": rec["name"], "seed": dgn.seed_for(did), "floor": int(floor),
                "layout": dgn.layout(did, rec, floor), "theme": dgn.theme(did, rec),
                "enemies": dgn.enemies_for(did, rec, floor), "boss": dgn.boss_for(did, rec, floor),
                "checkpoint": dgn.is_checkpoint_floor(floor), "xp_mult": dgn.xp_mult(rec, now),
                "loot_mult": dgn.loot_mult(rec, floor), "home": bool(rec.get("home")),
                "in_range": dgn.in_range(rec, now), "say": clamp_say(say)}

    def entry_say(self, did: str, rec: dict[str, Any], floor: int) -> str:
        boss = dgn.boss_for(did, rec, floor)
        if boss:
            return self.say("floor.boss", rec, floor=floor, boss=boss["title"])
        if dgn.is_checkpoint_floor(floor) and floor > 1:
            return self.say("floor.checkpoint", rec, floor=floor)
        return self.say("floor", rec, floor=floor)

    def lookup(self, payload: dict[str, Any], now: float) -> tuple[str, dict[str, Any] | None, tuple[str, str] | None]:
        self.seed_fakes(now)
        did = payload.get("id")
        if not isinstance(did, str) or not did.strip():
            return "", None, ("bad_payload", "id is required")
        did = did.strip().lower()
        rec = self.dungeons.get(did)
        if rec is None:
            return did, None, ("unknown_dungeon", "no dungeon with that id")
        return did, rec, None

    def enter(self, payload: dict[str, Any], now: float, pet: dict[str, Any]) -> tuple[dict | None, tuple | None]:
        did, rec, err = self.lookup(payload, now)
        if err:
            return None, err
        raw = payload.get("floor")
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 1):
            return None, ("bad_payload", "floor must be a whole number >= 1")
        depth = int(rec.get("depth", 0))
        floor = dgn.snap_start(depth, raw)
        first = not rec.get("entered")
        rec["entered"] = True
        rec["runs"] = int(rec.get("runs", 0)) + 1
        self.runs[did] = floor
        say = self.say("home", rec) if rec.get("home") else self.say(f"intro.{rec['type'].lower()}", rec)
        body = self.floor_info(did, rec, floor, say, now)
        log = [f"{rec['name']}: floor {floor}."]
        if dgn.in_range(rec, now):
            log.append(f"In range: x{dgn.IN_RANGE_XP_MULT:g} XP!")
        if rec.get("home"):
            log.append("Home turf: safer floors, heals.")
        flavors = FLAVOR.get(rec["type"], FLAVOR["NET"])
        body.update(kind=rec["kind"], difficulty=int(rec.get("difficulty", 4)),
                    flavor=clamp_say(flavors[dgn.seed_for(did) % len(flavors)]), first_visit=first,
                    depth_record=depth, checkpoint_floor=dgn.checkpoint_for(depth), starts=dgn.start_floors(depth),
                    log=[clamp_say(x) for x in log], pet=pet)
        return body, None

    def floor(self, payload: dict[str, Any], now: float, pet_state: Any) -> tuple[dict | None, tuple | None]:
        """pet.dungeon.floor: progress, floor XP (never battle XP), loot into the bag, next floor."""
        did, rec, err = self.lookup(payload, now)
        if err:
            return None, err
        floor = payload.get("floor")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
            return None, ("bad_payload", "floor must be a whole number >= 1")
        result = str(payload.get("result") or "").strip().lower()
        if result not in dgn.RESULTS:
            return None, ("bad_payload", "result must be cleared, stairs, fainted or fled")
        depth = int(rec.get("depth", 0))
        if floor > max(depth + 1, self.runs.get(did, 0)):
            return None, ("bad_payload", f"floor {floor} is not reached yet")
        boss_floor = dgn.is_boss_floor(floor)
        beaten = payload.get("boss_beaten")
        boss_beaten = (bool(beaten) if isinstance(beaten, bool) else (boss_floor and result == "cleared")) and boss_floor
        mult = dgn.xp_mult(rec, now)
        progressed = result in ("cleared", "stairs")
        log: list[str] = []
        deck_xp = payload.get("xp")
        deck_xp = (max(0, min(MAX_DECK_XP, int(deck_xp)))
                   if isinstance(deck_xp, (int, float)) and not isinstance(deck_xp, bool) else 0)
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
        if isinstance(payload.get("enemies"), list):
            rec["defeated"] = int(rec.get("defeated", 0)) + len(payload["enemies"])
        name = pet_state.name.upper()
        level_before = pet_state.level
        if xp > 0:
            pet_state.gain_xp(xp)
            log.append(f"{name} gained {xp} XP!")
        loot: dict[str, dict[str, Any]] = {}

        def put(item: Any) -> None:
            entry = pet_state.bag_add(item if isinstance(item, dict) else {"name": str(item).replace("_", " ")})
            if entry:
                row = loot.setdefault(entry["id"], {"id": entry["id"], "name": entry["name"], "qty": 0})
                row["qty"] += 1

        if result == "cleared":
            for item in dgn.roll_loot(did, rec, floor, boss_beaten, int(rec.get("clears", 0))):
                put({"name": ITEMS[item][0]} if item in ITEMS else item)
        deck_loot = payload.get("loot")
        if isinstance(deck_loot, list):
            for item in deck_loot[:MAX_DECK_LOOT]:
                if isinstance(item, (str, dict)):
                    put({"name": ITEMS[item][0]} if isinstance(item, str) and item in ITEMS else item)
        for row in loot.values():
            log.append(f"Got {row['name'].upper()} x{row['qty']}.")
        bond_before = pet_state.bond
        if progressed:
            pet_state.bond = min(100.0, pet_state.bond + 0.5 + (2.0 if boss_beaten else 0.0)
                                 + (1.0 if new_record else 0.0))
        if new_record:
            log.append(f"New record: floor {floor}!")
        if new_cp > old_cp:
            log.append(f"Checkpoint: floor {new_cp} unlocked!")
        healed = 0
        if progressed and rec.get("home") and pet_state.hp > 0:
            healed = min(max(1, round(pet_state.max_hp * dgn.HOME_HEAL_SHARE)), max(0, pet_state.max_hp - pet_state.hp))
            pet_state.hp += healed
            if healed:
                log.append(f"Home turf: {name} healed {healed} HP.")
        boss = dgn.boss_for(did, rec, floor)
        if boss_beaten:
            say = self.say("milestone", rec, floor=floor, boss=(boss or {}).get("title", "the boss"))
        elif new_record and floor % 5 == 0:
            say = self.say("milestone", rec, floor=floor, boss="the boss")
        elif new_record:
            say = self.say("record", rec, floor=floor)
        elif healed:
            say = self.say("home.heal", rec, hp=healed)
        else:
            say = self.say(result, rec, floor=floor)
        if progressed:
            self.runs[did] = floor + 1
            nxt = self.floor_info(did, rec, floor + 1, self.entry_say(did, rec, floor + 1), now)
        else:
            self.runs.pop(did, None)
            nxt = None
            if result == "fainted":
                log.append(f"Back to floor {new_cp} next time.")
        if pet_state.level > level_before:
            log.append(f"{name} grew to Lv {pet_state.level}!")
        return {"id": did, "floor": floor, "result": result, "xp": xp, "xp_mult": mult, "loot": list(loot.values()),
                "bond": pet_state.bond_tier(), "bond_delta": round(pet_state.bond - bond_before, 1),
                "depth_record": int(rec.get("depth", 0)), "new_record": new_record,
                "checkpoint_reached": new_cp > old_cp, "checkpoint": new_cp, "boss_beaten": boss_beaten,
                "healed": healed, "next": nxt, "say": clamp_say(say), "log": [clamp_say(x) for x in log],
                "hp": pet_state.hp, "pet": pet_state.as_payload()}, None

    def leave(self, payload: dict[str, Any], now: float, pet: dict[str, Any]) -> tuple[dict | None, tuple | None]:
        did, rec, err = self.lookup(payload, now)
        if err:
            return None, err
        self.runs.pop(did, None)
        depth = int(rec.get("depth", 0))
        return {"id": did, "depth_record": depth, "checkpoint": dgn.checkpoint_for(depth),
                "say": self.say("leave", rec), "pet": pet}, None

    def combat_tally(self, payload: dict[str, Any], outcome: str, now: float) -> float:
        """pet.combat.end {dungeon_id}: count the fight for that dungeon; returns the XP multiplier."""
        did = payload.get("dungeon_id")
        rec = self.dungeons.get(did.strip().lower()) if isinstance(did, str) else None
        if rec is None:
            return 1.0
        tally = {"win": "wins", "lose": "losses", "flee": "flees"}[outcome]
        battles = rec.setdefault("battles", {"wins": 0, "losses": 0, "flees": 0})
        battles[tally] = int(battles.get(tally, 0)) + 1
        return dgn.xp_mult(rec, now)

    def summary(self, now: float) -> dict[str, Any]:
        self.seed_fakes(now)
        return dgn.summary(self.dungeons, now)


WallClock = Callable[[], float]
