"""tests/test_pet_care.py — rest/sleep, evolution, faces, pop-ups, dungeon combat.

  * petdata: asleep, evolution {progress, choices}, text faces (and the
    sprite fallback), combat stats, the new event texts
  * the sim companion: sleep refills and 'woke', rest, the branching
    evolution tree, pet.evolve answers, evo chips, combat messages
  * commands: pet rest / sleep / evolve / avatar, completion, launcher,
    loot add, and polite messages for a companion that lacks them
  * popups: queue, timeout, dismiss, config toggle, which events pop up
  * the dungeon: PULSE joins with real stats, ask / auto turns, enemies
    hitting PULSE, fainting, repair kits, pet.combat.end, solo fallbacks
  * end to end: tools/fake_companion.py over TCP, and the real
    companion/companion.py (whatever it supports today)
"""

from __future__ import annotations

import random
import subprocess
import sys
import time
import unittest
from pathlib import Path

from cyberdeck import petdata
from cyberdeck.commands import build_registry
from cyberdeck.companion import CompanionError, PetState, SimCompanionLink, sim_avatar
from cyberdeck.completion import complete_line as complete
from cyberdeck.config import Settings
from cyberdeck.features import dungeon, petwatch
from cyberdeck.features.dungeon import Game, PulseAlly, pulse_hud
from cyberdeck.popups import Popup, PopupQueue, PopupSettings, popup_for_event, render_popup
from cyberdeck.protocol import Message

from helpers import make_ctx, write_config

ROOT = Path(__file__).resolve().parent.parent


def sim_link(**pet) -> SimCompanionLink:
    link = SimCompanionLink(pet=PetState(**pet), clock=lambda: 0.0)
    link.connect()
    return link


class UnknownCompanion:
    """A linked companion that only knows the old messages."""

    def __init__(self, known=("pet.status",)) -> None:
        self.known = known

    def is_linked(self) -> bool:
        return True

    def request(self, typ, payload=None, timeout=1.0):
        msg = Message.request(typ, payload or {})
        if typ in self.known:
            return msg.reply(typ + ".ok", {"pet": {"name": "PULSE", "stage": "rookie", "energy": 50}})
        return msg.error("unknown_type", f"unsupported message type: {typ}")

    def drain_events(self):
        return []

    def pet_snapshot(self):
        return None

    def link_label(self):
        return "LINK UP"


class PetDataTests(unittest.TestCase):
    def test_asleep_and_evolution(self):
        pet = petdata.normalize_pet({"asleep": True, "evolution": {
            "next_stage": "champion", "progress": 1.7, "ready": True,
            "choices": [{"species": "Voltshard", "stage": "champion", "hint": "needs level 5+", "available": False},
                        {"species": "Aethlance", "stage": "champion"}, {"hint": "no name"}]}})
        self.assertTrue(pet["asleep"])
        evo = pet["evolution"]
        self.assertEqual((evo["progress"], evo["ready"]), (1.0, True))
        self.assertEqual([c["species"] for c in evo["choices"]], ["Voltshard", "Aethlance"])
        self.assertFalse(evo["choices"][0]["available"])
        self.assertTrue(evo["choices"][1]["available"])        # missing = available
        self.assertIn("locked: needs level 5+", petdata.choice_line(evo["choices"][0]))
        self.assertIn("100%  ready!", petdata.evolution_line(evo))
        self.assertIsNone(petdata.normalize_pet({})["evolution"])
        self.assertIsNone(petdata.normalize_pet({"asleep": "yes"})["asleep"])
        self.assertEqual(petdata.evolution_line({"next_stage": None, "progress": 1, "ready": False}), "final form")

    def test_faces(self):
        face = petdata.parse_face({"style": "face", "mood": "playful", "frames": ["(^_^)", "(^_~)", 5, ""],
                                   "interval_ms": 500})
        self.assertEqual(face.frames, ("(^_^)", "(^_~)"))
        self.assertEqual((face.at(0.0), face.at(0.5), face.at(1.0)), ("(^_^)", "(^_~)", "(^_^)"))
        self.assertIsNone(petdata.parse_face(sim_avatar("rookie", "content", 0)))   # sprite, not a face
        self.assertEqual(petdata.parse_face({"text": "(o_o)\x1b[31m"}).frames, ("(o_o)[31m",))
        pet = petdata.normalize_pet({"mood": "content", "asleep": True,
                                     "avatar": {"style": "face", "mood": "content", "text": "(o_o)"}})
        self.assertEqual(petdata.pet_face(pet, 0.0), "(o_o) zZ")
        self.assertEqual(petdata.pet_face(dict(pet, asleep=False), 0.0), "(o_o)")
        self.assertTrue(petdata.is_face_avatar({"style": "face"}))
        self.assertFalse(petdata.is_face_avatar({"form": "x", "frame": 1}))

    def test_combat_stats(self):
        stats = petdata.combat_stats({"hp": 20, "max_hp": 30, "attack": 7, "level": 4, "energy": 50,
                                      "moves": ["peck", {"name": "zap", "power": 6, "accuracy": 90, "energy_cost": 5},
                                                {"power": 3}]})
        self.assertEqual((stats.hp, stats.max_hp, stats.level, stats.energy), (20, 30, 4, 50))
        self.assertEqual([m.name for m in stats.moves], ["peck", "zap"])
        self.assertAlmostEqual(stats.moves[1].accuracy, 0.9)
        self.assertIsNone(petdata.combat_stats({"attack": 3}))
        nested = petdata.combat_stats({"stats": {"hp": 5}, "pet": {"energy": 12, "asleep": True}})
        self.assertEqual((nested.energy, nested.asleep, nested.max_hp), (12, True, 5))

    def test_event_texts(self):
        texts = {kind: petdata.event_notice({"event": kind, "detail": detail, "pet": {"energy": 100}})
                 for kind, detail in {
                     "evolve_soon": {"next_stage": "champion", "eta_hours": 2},
                     "evolve_choice": {"choices": [{"species": "A"}, {"species": "B", "available": False}]},
                     "evolving": {"from": "Aethling", "to": "Voltshard"},
                     "woke": {"reason": "rested"}}.items()}
        self.assertIn("champion (in about 2h)", texts["evolve_soon"][2])
        self.assertIn("choose: A —", texts["evolve_choice"][2])
        self.assertEqual(texts["evolve_choice"][1], "alert")
        self.assertIn("Aethling → Voltshard", texts["evolving"][2])
        self.assertIn("woke up (rested) — energy 100", texts["woke"][2])

    def test_any_stage_or_species_is_shown(self):
        pet = petdata.normalize_pet({"stage": "hyper-mega", "species": "Zorbtron"})
        self.assertEqual((pet["stage"], pet["species"]), ("hyper-mega", "Zorbtron"))
        ctx = make_ctx()
        ctx.companion.pet._evolve_to("ultimate", "Stormcore")
        lines = build_registry(include_plugins=False).dispatch("pet status", ctx)
        self.assertIn("Stormcore  ·  ultimate", lines[0])


class SimPetTests(unittest.TestCase):
    def test_sleep_refills_and_wakes(self):
        pet = PetState(energy=40)
        self.assertTrue(pet.sleep(True)["asleep"])
        self.assertEqual(pet.as_payload()["avatar"]["text"][-2:].lower(), "zz")
        pet.tick(60 * 10)          # 10 minutes asleep: +80
        self.assertFalse(pet.asleep)
        self.assertEqual(pet.events[-1], ("woke", {"reason": "rested"}))
        pet.hp = pet.max_hp
        refused = pet.sleep(True)
        self.assertEqual((refused["changed"], refused["reason"]), (False, "PULSE is already fully rested"))
        pet.energy = 50
        self.assertEqual(pet.sleep(True)["changed"], True)
        pet.sleep(False)                                 # woken by hand: no 'woke' push
        self.assertNotEqual(pet.events[-1], ("woke", {"reason": "woken"}))

    def test_rest(self):
        pet = PetState(energy=50, hp=10)
        reply = pet.rest()
        self.assertEqual((reply["rested"], reply["energy_delta"]), (True, 20))
        self.assertEqual(pet.hp, 16)
        pet.energy = 100
        self.assertTrue(pet.rest()["rested"])           # still missing HP: may rest
        pet.hp = pet.max_hp
        self.assertEqual(pet.rest()["reason"], "energy is already full")
        pet.hp = 0
        self.assertTrue(pet.rest()["rested"])           # a fainted pet is never stuck

    def test_evolution_tree_and_events(self):
        pet = PetState(stage="rookie", happiness=95, hunger=0, evolution_speed=100)
        pet.tick(3600 * 0.33)                       # 33 stage-hours of 36: 92%
        self.assertEqual([e for e, _ in pet.events if e.startswith("evolve")], ["evolve_soon"])
        soon = next(d for e, d in pet.events if e == "evolve_soon")
        self.assertEqual(set(soon), {"from", "to_candidates", "next_stage", "eta_hours", "progress"})
        self.assertEqual(pet.evolve("Aethlance")["reason"], "not ready")
        pet.tick(3600 * 0.1)
        kinds = [e for e, _ in pet.events if e.startswith("evolve")]
        self.assertEqual(kinds, ["evolve_soon", "evolve_choice"])
        pet.tick(3600 * 0.1)                        # never repeats, never auto-evolves
        self.assertEqual([e for e, _ in pet.events if e.startswith("evolve")], kinds)
        self.assertEqual(pet.stage, "rookie")
        refused = pet.evolve("Glitchwyrm")
        self.assertFalse(refused["accepted"])
        self.assertIn("3+ unique discoveries", refused["reason"])
        self.assertIsNone(pet.evolve("Nonsensemon"))
        pet.happiness = 90                          # the Aethlance branch wants a happy pet
        ok = pet.evolve("aethlance")
        self.assertTrue(ok["accepted"])
        self.assertEqual([e for e, _ in pet.events][-2:], ["evolving", "evolved"])
        self.assertEqual((pet.stage, pet.species, pet.evolution()["progress"]), ("champion", "Aethlance", 0.0))

    def test_protocol_answers(self):
        link = sim_link(stage="rookie")
        status = link.request("pet.status", {})
        self.assertEqual(set(status.payload["evolution"]), {"next_stage", "progress", "ready", "choices"})
        self.assertEqual(link.request("pet.evolve", {"species": "Zzz"}).payload["code"], "bad_payload")
        self.assertEqual(link.request("pet.sleep", {}).payload["code"], "bad_payload")
        avatar = link.request("pet.avatar", {}).payload
        self.assertEqual((avatar["style"], len(avatar["frames"]), avatar["interval_ms"]), ("face", 2, 600))
        self.assertEqual(link.pet.as_payload()["avatar"]["style"], "face")

    def test_last_form_has_no_choices(self):
        pet = PetState(stage="mega")
        self.assertEqual(pet.evolution()["next_stage"], None)
        self.assertFalse(pet.gift({"name": "evo chip", "kind": "evolve"})["accepted"])

    def test_combat_messages(self):
        link = sim_link(energy=5)
        raw = link.request("pet.combat.stats", {}).payload
        self.assertEqual({"fainted", "asleep", "guarding"} <= set(raw), True)
        stats = petdata.combat_stats(raw)
        self.assertEqual([m.name for m in stats.moves], ["peck", "static zap", "wave dash"])
        self.assertAlmostEqual(stats.moves[0].accuracy, 0.95)     # sent as 0-100
        # Too expensive (or unknown) -> the pet's fallback: Guard here (it knows no Chirp).
        for move in ("wave dash", "laser"):
            body = link.request("pet.combat.action", {"move": move, "enemy": {"name": "bug", "hp": 3}}).payload
            self.assertEqual((body["acted"], body["fallback"], body["move"], body["effect"], body["energy_spent"]),
                             (True, True, "Guard", "guard", 0), move)
        self.assertEqual(link.request("pet.combat.action", {"move": 5}).payload["code"], "bad_payload")
        own = link.request("pet.combat.action", {"enemy": {"name": "bug"}}).payload      # pet picks
        self.assertEqual((own["acted"], own["move"]), (True, "static zap"))
        guarded = link.request("pet.combat.hit", {"damage": 8}).payload
        self.assertEqual(guarded["damage_taken"], (8 - 6 // 4) // 2)                   # Guard halves it
        hit = link.request("pet.combat.hit", {"damage": 100}).payload
        self.assertTrue(hit["fainted"])
        down = link.request("pet.combat.action", {"move": "peck"}).payload
        self.assertEqual((down["acted"], down["reason"]), (False, "fainted"))
        healed = link.request("pet.combat.hit", {"heal": 5}).payload
        self.assertEqual((healed["healed"], healed["pet_hp"], healed["fainted"]), (5, 5, False))
        self.assertEqual(link.request("pet.combat.hit", {"damage": -3}).payload["pet_hp"], 8)
        self.assertEqual(link.request("pet.combat.hit", {}).payload["code"], "bad_payload")
        self.assertEqual(link.request("pet.combat.end", {"result": "won"}).payload["code"], "bad_payload")
        end = link.request("pet.combat.end", {"result": "win", "xp": 200, "enemies": ["bug"]}).payload
        self.assertEqual((end["result"], end["xp_granted"]), ("win", 200))
        kinds = [m.payload["event"] for m in link.drain_events()]
        self.assertIn("level_up", kinds)
        self.assertIn("learned_move", kinds)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        self.reg = build_registry(include_plugins=False)

    def run_cmd(self, line):
        return self.reg.dispatch(line, self.ctx)

    def test_rest_and_sleep(self):
        self.ctx.companion.pet.energy = 30
        self.assertIn("energy +20 → 50", self.run_cmd("pet rest")[0])
        self.assertIn("falls asleep", self.run_cmd("pet sleep")[0])
        self.assertIn("asleep zZ", self.run_cmd("pet status")[0])
        self.assertIn("(asleep)", self.run_cmd("pet rest")[0])
        self.assertIn("is awake", self.run_cmd("pet sleep off")[0])
        self.assertEqual(self.run_cmd("pet sleep maybe"), ["usage: pet sleep [on|off]"])
        self.assertIn("rested", "\n".join(self.run_cmd("pet log")))

    def test_evolve_flow(self):
        lines = self.run_cmd("pet evolve")
        self.assertIn("→ champion", lines[0])
        self.assertTrue(any(l.startswith("  × Voltshard") for l in lines))
        self.assertIsNone(self.ctx.activity)
        self.run_cmd("pet sim ready yes")
        self.assertIn("pick one: pet evolve <species>", self.run_cmd("pet evolve")[-1])
        self.assertIn("locked", "\n".join(self.run_cmd("pet status")))
        self.assertIn("level 5+", "\n".join(self.run_cmd("pet evolve Voltshard")))
        self.assertIn("becomes Aethlance", self.run_cmd("pet evolve Aethlance")[0])
        self.assertIn("unknown species", self.run_cmd("pet evolve Zzz")[0])

    def test_evolve_picker_in_tui(self):
        self.ctx.interactive = True
        self.ctx.companion.pet.chip_ready = True
        self.ctx.companion.pet.level = 1
        self.run_cmd("pet evolve")
        picker = self.ctx.activity
        self.assertIsInstance(picker, __import__("cyberdeck.features.petcare", fromlist=["x"]).EvolvePicker)
        text = "\n".join("".join(t for t, _ in row) for row in picker.render(54, 18))
        self.assertIn("Voltshard", text)
        picker.choices[1]["available"] = False          # pretend Voltshard is locked
        picker.on_key("down")
        self.assertEqual(picker.choices[picker.index]["species"], "Glitchwyrm")   # skips the locked one
        picker.on_key("enter")
        self.assertEqual(picker.result_command, "pet evolve Glitchwyrm")
        locked = [row for row in picker.render(54, 18) if "Voltshard" in "".join(t for t, _ in row)]
        self.assertEqual(locked[0][0][1], "dim")

    def test_avatar_headless_and_sprite_fallback(self):
        lines = self.run_cmd("pet avatar")
        self.assertIn("2 frame(s) every 600 ms", lines[0])
        self.assertTrue(lines[1].startswith("  frame 1: ("))

        class OldCompanion(UnknownCompanion):
            def request(self, typ, payload=None, timeout=1.0):
                msg = Message.request(typ, payload or {})
                return msg.reply("pet.avatar.ok", sim_avatar("rookie", "content", 0))

        self.ctx.companion = OldCompanion()
        lines = self.run_cmd("pet avatar")
        self.assertIn("older companion sprite", lines[0])
        self.assertEqual(len(lines), 17)
        self.ctx.interactive = True
        self.run_cmd("pet avatar")
        self.assertIsNotNone(self.ctx.activity.sprite)

    def test_unsupported_companion_is_polite(self):
        self.ctx.companion = UnknownCompanion()
        for line in ("pet rest", "pet sleep", "pet evolve Aethlance"):
            self.assertIn("does not support", self.run_cmd(line)[0], line)
        self.assertIn("does not report evolution", self.run_cmd("pet evolve")[0])

    def test_link_down(self):
        self.ctx.companion.close()
        self.assertIn("could not reach PULSE", self.run_cmd("pet rest")[0])

    def test_completion_and_launcher(self):
        self.assertEqual(complete("pet sl", self.reg, self.ctx)[0], "pet sleep ")
        self.assertEqual(sorted(complete("pet sleep o", self.reg, self.ctx)[1]), ["off", "on"])
        self.ctx.companion.pet.chip_ready = True
        self.assertEqual(set(complete("pet evolve ", self.reg, self.ctx)[1]), {"Aethlance", "Voltshard", "Glitchwyrm"})
        launcher = Settings.load("keys.ini").section("launcher") if hasattr(Settings, "section") else None
        text = (ROOT / "config" / "keys.ini").read_text()
        for action in ("pet rest", "pet sleep on", "pet sleep off", "pet evolve", "pet avatar"):
            self.assertIn(f"= {action}\n", text)
        del launcher

    def test_loot_add_and_empty_bag(self):
        self.assertIn("loot add <item>", self.run_cmd("pet gift")[0])
        self.assertIn("loot add <item>", self.run_cmd("pet gift 1")[0])
        self.assertIn("no loot called", self.run_cmd("loot add zzz")[0])
        self.assertIn("added data cookie", self.run_cmd("loot add data")[0])
        self.assertIn("munches the data cookie", self.run_cmd("pet gift 1")[0])
        self.run_cmd("loot add evo chip")
        self.assertIn("ready to evolve", self.run_cmd("pet gift 1")[0])
        self.assertEqual(complete("loot add ev", self.reg, self.ctx)[0], "loot add evo ")


class PopupTests(unittest.TestCase):
    def event(self, kind, **detail):
        return {"event": kind, "detail": detail, "pet": {"name": "PULSE", "species": "Aethling", "stage": "rookie"}}

    def test_queue_timeout_and_dismiss(self):
        q = PopupQueue(PopupSettings(seconds=5, max_queue=2))
        for title in ("a", "b", "c"):
            q.push(Popup("evolved", title, ["x"]))
        self.assertEqual(q.active(0.0).title, "b")        # 'a' dropped: queue holds 2
        self.assertEqual(q.active(4.9).title, "b")
        self.assertEqual(q.active(5.0).title, "c")        # timed out -> next
        self.assertTrue(q.dismiss())
        self.assertIsNone(q.active(5.1))
        self.assertFalse(q.dismiss())
        off = PopupQueue(PopupSettings(enabled=False))
        off.push(Popup("evolved", "x", []))
        self.assertIsNone(off.active(0))
        forever = PopupQueue(PopupSettings(seconds=0))
        forever.push(Popup("evolved", "x", []))
        self.assertIsNotNone(forever.active(10_000))

    def test_which_events_pop_up(self):
        self.assertIsNone(popup_for_event(self.event("hungry")))
        soon = popup_for_event(self.event("evolve_soon", next_stage="champion", eta_hours=1.5))
        self.assertIn("champion (in about 1.5h)", soon.lines[1])
        choice = popup_for_event(self.event("evolve_choice", choices=[
            {"species": "A", "available": True}, {"species": "B", "available": False, "hint": "needs 3+"}]))
        self.assertIn("× B — locked: needs 3+", choice.lines)
        evolving = popup_for_event(self.event("evolving", **{"from": "Aethling", "to": "Voltshard"}))
        self.assertTrue(evolving.animate)
        frames = {tuple(t for row in render_popup(evolving, now / 8, 30) for t, _ in row) for now in range(6)}
        self.assertGreater(len(frames), 1)                 # the shimmer moves
        self.assertIn("evolved into Voltshard", popup_for_event(self.event("evolved", to="Voltshard")).lines[0])

    def test_watcher_feeds_popups_only_in_the_tui(self):
        for interactive, expected in ((False, 0), (True, 2)):
            ctx = make_ctx()
            ctx.interactive = interactive
            ctx.companion.pet.chip_ready = True
            ctx.companion.pet._check_evolution()
            ctx.companion.request("pet.evolve", {"species": "Aethlance"})
            petwatch.get_watcher(ctx).pushed(ctx, time.time())
            queue = ctx.services.get("popups")
            count = len(queue.waiting) if queue else 0
            self.assertEqual(count, expected + (2 if interactive else 0))   # + evolve_soon, evolve_choice

    def test_config_toggle(self):
        cfg = write_config({"pet.ini": "[popups]\nenabled = no\nseconds = 3\n"})
        ctx = make_ctx(cfg)
        from cyberdeck.popups import get_popups
        self.assertEqual((get_popups(ctx).settings.enabled, get_popups(ctx).settings.seconds), (False, 3.0))

    def test_asleep_badge(self):
        watcher = petwatch.PetWatcher(petwatch.AlertSettings(), petwatch.PetLog(Path("/dev/null")))
        watcher.check({"name": "PULSE", "hunger": 10, "happiness": 80, "energy": 50, "asleep": True}, 0)
        self.assertEqual(watcher.status_badge(), ("zZ ASLEEP", "dim"))
        watcher.check({"name": "PULSE", "hunger": 90, "happiness": 80, "energy": 50, "asleep": True}, 1)
        self.assertEqual(watcher.status_badge(), ("! HUNGRY zZ", "alert"))


class DungeonCombatTests(unittest.TestCase):
    def game(self, link=None, mode="auto", chance=0.0, **pet) -> tuple[Game, PulseAlly, SimCompanionLink]:
        link = link or sim_link(**pet)
        ally = PulseAlly(link.request)
        ally.join()
        g = Game(rng=random.Random(5), ally=ally, pulse_moves=mode, pulse_target_chance=chance)
        return g, ally, link

    def put_monster_next_to_you(self, g: Game, hp: int = 50) -> dungeon.Monster:
        g.monsters = [dungeon.Monster("worm", "w", g.px + 1, g.py, hp, 3)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        return g.monsters[0]

    def test_join_uses_real_stats(self):
        g, ally, _ = self.game(hp=21, max_hp=30, energy=64)
        self.assertEqual((ally.hp, ally.max_hp, ally.energy), (21, 30, 64))
        hud = "".join(t for t, _ in pulse_hud(g))
        self.assertIn("PULSE HP 21/30 EN 64", hud)

    def test_ask_mode_waits_for_your_pick(self):
        g, ally, _ = self.game(mode="ask")
        m = self.put_monster_next_to_you(g)
        g.wait()
        self.assertIs(g.awaiting, m)
        g.move(0, 1)                                    # blocked until PULSE moves
        self.assertIs(g.awaiting, m)
        before_energy = ally.energy
        g.pet_choose(1)                                  # static zap
        self.assertIsNone(g.awaiting)
        self.assertEqual(ally.energy, before_energy - 5)
        self.assertLess(m.hp, 50)
        g.wait()
        g.pet_choose(-1)                                 # hold back
        self.assertIn("holds back", "\n".join(g.messages))

    def test_auto_mode_lets_the_pet_pick_and_tracks_energy(self):
        g, ally, _ = self.game(energy=6)
        self.put_monster_next_to_you(g)
        g.wait()
        self.assertIn("static zap", "\n".join(g.messages))    # 5 energy: the best it can afford
        self.assertEqual(ally.energy, 1)                       # copied from the pet object
        g.wait()
        self.assertIn("peck", "\n".join(g.messages))

    def test_unaffordable_pick_falls_back(self):
        g, ally, _ = self.game(mode="ask", energy=3)
        self.put_monster_next_to_you(g)
        g.wait()
        g.pet_choose(2)                                        # wave dash costs 8
        self.assertIn("(can't use wave dash)", "\n".join(g.messages))
        self.assertEqual(ally.energy, 3)

    def test_enemy_hits_pulse_and_faints_then_kit_revives(self):
        g, ally, _ = self.game(chance=1.0, hp=3, max_hp=28)
        self.put_monster_next_to_you(g)
        start_hp = g.hp
        for _ in range(6):
            g.wait()
        self.assertTrue(ally.fainted)
        self.assertIn("fainted", "".join(t for t, _ in pulse_hud(g)))
        self.assertLess(g.hp, start_hp)                 # then the monster turns on you
        g.monsters = []
        g.items[(g.px, g.py + 1)] = {"name": "repair kit", "kind": "heal", "power": 8}
        g.grid[g.py + 1][g.px] = dungeon.FLOOR
        g.move(0, 1)
        self.assertEqual(ally.hp, 8)
        self.assertIn("back on its feet", "\n".join(g.messages))

    def test_end_reports_xp(self):
        g, ally, link = self.game(xp=118)
        m = self.put_monster_next_to_you(g, hp=1)
        g.move(1, 0)
        self.assertNotIn(m, g.monsters)
        self.assertEqual(ally.defeated, ["worm"])
        lines = g.finish()
        self.assertTrue(lines[0].startswith("PULSE: +5 xp — PULSE cheers!"), lines)
        self.assertEqual(g.end_payload["result"], "win")
        self.assertIn("level_up", [e.payload["event"] for e in link.drain_events()])
        self.assertIsNone(ally.end("won", 5))            # only one pet.combat.end per run

    def test_solo_fallbacks(self):
        old = PulseAlly(UnknownCompanion().request)
        self.assertFalse(old.join())
        self.assertIn("can't fight", old.note)
        asleep = PulseAlly(sim_link(asleep=True).request)
        self.assertFalse(asleep.join())
        self.assertIn("asleep", asleep.note)

        def down(*_a, **_k):
            raise CompanionError("companion link is down")

        g, ally, _ = self.game()
        ally.request = down
        self.put_monster_next_to_you(g)
        g.wait()                                          # PULSE drops out, the game goes on
        self.assertFalse(ally.online)
        self.assertIn("solo", "".join(t for t, _ in pulse_hud(g)))
        self.assertEqual(g.finish(), ["PULSE: lost contact (companion link is down)"])

    def test_activity_keys_hud_and_end(self):
        ctx = make_ctx(write_config({"fx.ini": "[dungeon]\npulse = yes\npulse_moves = ask\n"}))
        game = dungeon.new_game(ctx, seed=3)
        self.assertTrue(game.ally.online)
        act = dungeon.DungeonActivity(ctx, game)
        self.put_monster_next_to_you(game)
        act.on_key(".")
        rows = "\n".join("".join(t for t, _ in row) for row in act.render(80, 24))
        self.assertIn("PULSE vs worm", rows)
        self.assertIn("1:peck(0)", rows)
        act.on_key(" ")
        self.assertIsNone(game.awaiting)
        act.on_key("q")
        self.assertTrue(any(l.startswith("PULSE: +") for l in act.result_lines))
        self.assertIn("dungeon run", "\n".join(build_registry(include_plugins=False).dispatch("pet log", ctx)))

    def test_toggle_off_and_link_down(self):
        ctx = make_ctx(write_config({"fx.ini": "[dungeon]\npulse = no\n"}))
        self.assertIsNone(dungeon.new_game(ctx, seed=1).ally)
        ctx = make_ctx()
        ctx.companion.close()
        game = dungeon.new_game(ctx, seed=1)
        self.assertFalse(game.ally.online)
        self.assertIn("link down", game.messages[-1] + game.messages[0])

    def test_solo_game_replays_unchanged(self):
        # Without PULSE the rng sequence (and so old replays) must not change.
        a, b = Game(rng=random.Random(9)), Game(rng=random.Random(9), ally=None)
        for _ in range(30):
            a.move(1, 0)
            b.move(1, 0)
        self.assertEqual((a.hp, a.px, a.py, a.score), (b.hp, b.px, b.py, b.score))


# --- end to end ------------------------------------------------------------------------------

class FakeCompanionEndToEnd(unittest.TestCase):
    """tools/fake_companion.py over TCP: every new message on a real wire."""

    def test_everything_over_tcp(self):
        from test_link import FakeCompanionProcess, fast, free_port

        from cyberdeck.link import StreamCompanionLink, TcpTransport

        port = free_port()
        server = FakeCompanionProcess(port, "--evolution-speed", "100000")
        link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", port), fast(port=port))
        try:
            link.connect()
            ctx = make_ctx()
            ctx.companion = link
            reg = build_registry(include_plugins=False)
            self.assertIn("frame 1:", "\n".join(reg.dispatch("pet avatar", ctx)))
            self.assertTrue(reg.dispatch("pet rest", ctx)[0].startswith("PULSE"))
            # 100000x evolution speed: ready almost at once, and evolve_choice is pushed.
            seen: list = []
            deadline = time.time() + 10
            while time.time() < deadline and not any(m.payload["event"] == "evolve_choice" for m in seen):
                reg.dispatch("pet status", ctx)
                seen += link.drain_events()
                time.sleep(0.1)
            self.assertIn("evolve_choice", [m.payload["event"] for m in seen])
            self.assertIn("becomes Aethlance", reg.dispatch("pet evolve Aethlance", ctx)[0])
            ally = PulseAlly(link.request)
            self.assertTrue(ally.join())
            g = Game(rng=random.Random(2), ally=ally, pulse_moves="auto", pulse_target_chance=1.0)
            g.monsters = [dungeon.Monster("bug", "b", g.px + 1, g.py, 40, 2)]
            g.grid[g.py][g.px + 1] = dungeon.FLOOR
            for _ in range(3):
                g.wait()
            self.assertLess(g.monsters[0].hp if g.monsters else 0, 40)
            self.assertTrue(g.finish()[0].startswith("PULSE: +"))
        finally:
            link.close()
            server.stop()


class RealCompanionEndToEnd(unittest.TestCase):
    """companion/companion.py (not ours: run as-is, never edited), over TCP.

    The full flow from companion/docs/DUNGEON_INTEGRATION.md: rest, sleep,
    text faces, an evo chip -> evolve_choice -> pet evolve -> evolving /
    evolved, and a dungeon fight (stats, action, hit, heal, end). If a
    future companion drops a message, the deck's polite fallback is checked.
    """

    def setUp(self):
        from test_link import fast, free_port, wait_for

        from cyberdeck.link import StreamCompanionLink, TcpTransport

        script = ROOT / "companion" / "companion.py"
        if not script.exists():
            self.skipTest("no companion/ checkout")
        port = free_port()
        self.proc = subprocess.Popen([sys.executable, str(script), "--tcp", f"127.0.0.1:{port}",
                                      "--event-interval", "0"],
                                     cwd=str(script.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import socket

        def listening():
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return True
            except OSError:
                return False

        self.assertTrue(wait_for(listening, 10), "companion did not start")
        self.link = StreamCompanionLink(lambda: TcpTransport("127.0.0.1", port), fast(port=port))
        self.link.connect()
        self.assertTrue(self.link.is_linked(), self.link.reason)
        self.ctx = make_ctx()
        self.ctx.companion = self.link
        self.reg = build_registry(include_plugins=False)
        self.events: list = []

    def tearDown(self):
        self.link.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def supports(self, typ: str) -> bool:
        return self.link.request(typ, {}).payload.get("code") != "unknown_type"

    def wait_event(self, kind: str, seconds: float = 5.0) -> dict:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.events += [m.payload for m in self.link.drain_events()]
            for event in self.events:
                if event["event"] == kind:
                    return event
            time.sleep(0.05)
        self.fail(f"no {kind} event; got {[e['event'] for e in self.events]}")

    def test_care_faces_and_evolution(self):
        run = lambda line: self.reg.dispatch(line, self.ctx)
        self.assertIn("PULSE", run("pet status")[0])
        avatar = run("pet avatar")
        self.assertIn("frame(s) every", avatar[0])
        self.assertNotIn("'", avatar[1])                         # quotes stripped
        if not self.supports("pet.rest"):
            self.assertIn("does not support", run("pet rest")[0])
            return
        self.assertTrue(run("pet rest")[0].startswith("PULSE"))
        self.assertTrue(run("pet sleep off")[0].startswith("PULSE"))
        run("loot add evo chip")
        self.assertIn("evo chip", run("pet gift 1")[0])
        soon, choice = self.wait_event("evolve_soon"), self.wait_event("evolve_choice")
        self.assertIn("next_stage", soon["detail"])
        picks = [c for c in petdata.evolution_choices(choice["detail"]["choices"]) if c["available"]]
        self.assertTrue(picks)
        evolve = run("pet evolve")
        self.assertIn("ready!", evolve[0])
        out = run(f"pet evolve {picks[0]['species']}")
        self.assertNotIn("did not evolve", out[0])
        self.wait_event("evolving")
        evolved = self.wait_event("evolved")
        self.assertIn(picks[0]["species"].lower(), str(evolved["detail"]).lower())
        self.assertIn(picks[0]["species"], run("pet status")[0])
        self.assertIn("bad_payload", str(self.link.request("pet.evolve", {"species": "Nonsensemon"}).payload))

    def test_dungeon_fight(self):
        ally = PulseAlly(self.link.request)
        if not self.supports("pet.combat.stats"):
            self.assertFalse(ally.join())
            self.assertIn("can't fight", ally.note)
            return
        self.reg.dispatch("pet sleep off", self.ctx)
        self.assertTrue(ally.join(), ally.note)
        self.assertIsNotNone(ally.energy)                         # read from the pet object
        self.assertTrue(ally.moves)
        g = Game(rng=random.Random(2), ally=ally, pulse_moves="ask", pulse_target_chance=1.0)
        g.monsters = [dungeon.Monster("worm", "w", g.px + 1, g.py, 60, 4, 1)]
        g.grid[g.py][g.px + 1] = dungeon.FLOOR
        g.wait()
        self.assertIsNotNone(g.awaiting)
        g.pet_choose(0)
        self.assertLess(g.monsters[0].hp if g.monsters else 0, 60 + 1)
        self.assertTrue(any("PULSE" in m for m in g.messages))
        g.wait()
        g.pet_choose(None)                                        # let the pet pick
        hp = ally.hp
        healed = ally.heal(5)
        self.assertIsNotNone(healed, ally.note)
        self.assertGreaterEqual(ally.hp, hp)
        lines = g.finish()
        self.assertTrue(lines[0].startswith("PULSE: +"), lines)
        self.assertIn(g.end_payload["result"], {"win", "flee"})


if __name__ == "__main__":
    unittest.main()
