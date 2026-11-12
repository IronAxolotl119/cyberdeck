"""Life engine: every aliveness feature, with a pinned clock and seeded RNG."""

from __future__ import annotations

import random
import unittest
from datetime import datetime

from pet import life as L
from pet.creature import Creature
from pet.life import Life, flavor_of, is_treasure_name, place_label, tier_for
from pet.stats import Stats
from pet.world import World

# Thursday 2026-11-12 14:00 local: no holiday, afternoon.
T0 = datetime(2026, 11, 12, 14, 0).timestamp()


def make(seed: int = 1, now: float = T0, **stats) -> tuple[Creature, random.Random]:
    rng = random.Random(seed)
    pet = Creature(name="PULSE", stats=Stats(**stats) if stats else Stats(), life=Life.new(random.Random(seed), now))
    pet.life.last_now = now
    return pet, rng


def kinds(life: Life) -> list[str]:
    return [k for k, _d in life.take_outbox()]


def at(*args: int) -> float:
    return datetime(*args).timestamp()


class SeedAndSaveTests(unittest.TestCase):
    def test_hatch_seeds_prefs_quirks_rival(self) -> None:
        pet, _ = make()
        life = pet.life
        self.assertNotEqual(life.prefs["fav_food"], life.prefs["hated_food"])
        self.assertNotEqual(life.prefs["fav_signal"], life.prefs["hated_signal"])
        for key in ("catchphrase", "dance", "sneeze", "tic", "fav_idle"):
            self.assertTrue(life.quirks.get(key), key)
        self.assertTrue(life.rival["name"])
        self.assertEqual(life.hatched_at, T0)

    def test_different_seeds_give_different_pets(self) -> None:
        quirks = {make(seed)[0].life.quirks["catchphrase"] for seed in range(12)}
        self.assertGreater(len(quirks), 6)

    def test_round_trip(self) -> None:
        pet, rng = make()
        life = pet.life
        life.on_feed(pet, "voltberry", rng, T0)
        life.add_modifier("praised", 5, 600, T0)
        life.remember_network("Cafe Lumen", T0)
        life.add_to_shelf("shiny gear", "test", 2, T0)
        clone = Life.from_dict(life.to_dict(), now=T0)
        self.assertEqual(clone.to_dict(), life.to_dict())

    def test_old_save_without_life_gets_defaults(self) -> None:
        data = Creature(name="Old").to_dict()
        data.pop("life")
        pet = Creature.from_dict(data)
        self.assertEqual(pet.life.bond, L.BOND_START)
        self.assertTrue(pet.life.quirks)
        partial = Life.from_dict({"bond": 55.0}, now=T0)
        self.assertEqual(partial.bond, 55.0)
        self.assertTrue(partial.prefs and partial.quirks and partial.rival)


class MoodBondTests(unittest.TestCase):
    def test_modifiers_linger_and_fade(self) -> None:
        pet, _ = make()
        life = pet.life
        life.add_modifier("lost_fight", -12.0, 1000.0, T0)
        self.assertAlmostEqual(life.mood_offset(T0), -12.0)
        self.assertAlmostEqual(life.mood_offset(T0 + 500), -6.0)
        self.assertEqual(life.mood_offset(T0 + 1000), 0.0)
        self.assertEqual(life.active_modifiers(T0 + 100)[0]["kind"], "lost_fight")

    def test_bond_tiers(self) -> None:
        self.assertEqual(tier_for(5), "stranger")
        self.assertEqual(tier_for(25), "friendly")
        self.assertEqual(tier_for(50), "close")
        self.assertEqual(tier_for(75), "bonded")
        self.assertEqual(tier_for(95), "soulmate")
        pet, _ = make()
        pet.life.add_bond(500)
        self.assertEqual(pet.life.bond, 100.0)


class FoodTests(unittest.TestCase):
    def test_flavor_keywords_and_hash(self) -> None:
        self.assertEqual(flavor_of("Chili Chips"), "spicy")
        self.assertEqual(flavor_of("volt bar"), "electric")
        self.assertEqual(flavor_of("mystery mush"), flavor_of("MYSTERY MUSH"))

    def test_favorite_food_is_learned(self) -> None:
        pet, rng = make(hunger=40)
        life = pet.life
        life.prefs["fav_food"] = "sweet"
        out = life.on_feed(pet, "cache cookie", rng, T0)
        self.assertEqual(out["reaction"], "like")
        self.assertIn("fav_food", life.known_preferences())
        self.assertGreater(life.mood_offset(T0), 0)
        self.assertIn("preference_learned", kinds(life))

    def test_hated_food(self) -> None:
        pet, rng = make(hunger=40)
        pet.life.prefs.update({"fav_food": "sweet", "hated_food": "sour"})
        out = pet.life.on_feed(pet, "lemon drop", rng, T0)
        self.assertEqual(out["reaction"], "hate")
        self.assertLess(pet.life.mood_offset(T0), 0)

    def test_boredom_then_refusal(self) -> None:
        pet, rng = make(hunger=10)
        life = pet.life
        life.prefs.update({"fav_food": "minty", "hated_food": "sour"})
        factors = []
        for i in range(6):
            pet.stats.hunger = 10
            factors.append(life.on_feed(pet, "ration", rng, T0 + i)["factor"])
        self.assertEqual(factors, [1.0, 1.0, 0.75, 0.5, 0.25, 0.0])
        # Something different is fine again.
        self.assertEqual(life.on_feed(pet, "noodle", rng, T0 + 10)["factor"], 1.0)

    def test_boredom_resets_after_the_window(self) -> None:
        pet, rng = make(hunger=10)
        for i in range(5):
            pet.life.on_feed(pet, "ration", rng, T0 + i)
        later = T0 + L.BORED_WINDOW + 10
        self.assertEqual(pet.life.on_feed(pet, "ration", rng, later)["factor"], 1.0)

    def test_play_boredom(self) -> None:
        pet, rng = make()
        results = [pet.life.on_play(pet, "fetch", "attack", rng, T0 + i) for i in range(6)]
        self.assertTrue(results[0]["allowed"])
        self.assertFalse(results[5]["allowed"])
        self.assertEqual(results[5]["reason"], "bored")


class SicknessTests(unittest.TestCase):
    def test_overfeeding_causes_a_glitch(self) -> None:
        pet, rng = make(hunger=95)
        foods = ["a", "b", "c"]
        for i, food in enumerate(foods):
            pet.stats.hunger = 95
            pet.life.on_feed(pet, food, rng, T0 + i)
        self.assertEqual(pet.life.sick["kind"], "glitch")
        self.assertIn("sick", kinds(pet.life))

    def test_neglect_can_corrupt_and_heal_cures(self) -> None:
        pet, _ = make()
        rng = random.Random(0)
        rng.random = lambda: 0.0  # type: ignore[method-assign]
        pet.stats.care_mistakes = 2
        pet.life._sickness(pet, rng, T0)
        self.assertEqual(pet.life.sick["kind"], "corrupted")
        pet.life.take_outbox()
        say = pet.life.heal(pet, random.Random(1), T0 + 5)
        self.assertIsNone(pet.life.sick)
        self.assertTrue(say)
        self.assertIn("healed", kinds(pet.life))

    def test_sick_pet_will_not_play(self) -> None:
        pet, rng = make()
        pet.life.make_sick(pet, "glitch", rng, T0)
        self.assertFalse(pet.life.on_play(pet, "fetch", "attack", rng, T0)["allowed"])

    def test_glitch_heals_itself_eventually(self) -> None:
        pet, rng = make()
        pet.life.make_sick(pet, "glitch", rng, T0)
        pet.life._sickness(pet, rng, T0 + L.GLITCH_SELF_HEAL + 1)
        self.assertIsNone(pet.life.sick)


class DisciplineTests(unittest.TestCase):
    def test_scolding_three_times_starts_a_tantrum(self) -> None:
        pet, rng = make()
        results = [pet.life.on_scold(pet, rng, T0 + i) for i in range(3)]
        self.assertTrue(results[-1]["tantrum"])
        self.assertTrue(pet.life.in_tantrum(T0 + 10))
        self.assertFalse(pet.life.in_tantrum(T0 + 2 + L.TANTRUM_SECONDS + 1))
        self.assertIn("tantrum", kinds(pet.life))
        pet.life.update(pet, T0 + 2 + L.TANTRUM_SECONDS + 1, rng)
        self.assertIn("tantrum_over", kinds(pet.life))

    def test_deserved_scold_raises_obedience(self) -> None:
        pet, rng = make()
        pet.life.note_misbehave(T0)
        before = pet.life.obedience
        out = pet.life.on_scold(pet, rng, T0 + 5)
        self.assertTrue(out["deserved"])
        self.assertGreater(pet.life.obedience, before)

    def test_undeserved_scold_hurts_bond(self) -> None:
        pet, rng = make()
        before = pet.life.bond
        pet.life.on_scold(pet, rng, T0)
        self.assertLess(pet.life.bond, before)
        self.assertLess(pet.life.mood_offset(T0), 0)

    def test_praise_and_praise_spam(self) -> None:
        pet, rng = make()
        effects = [pet.life.on_praise(pet, rng, T0 + i)["effect"] for i in range(5)]
        self.assertEqual(effects[3:], ["spam", "spam"])
        self.assertGreater(pet.life.obedience, L.OBEDIENCE_START)

    def test_toy_ends_a_tantrum(self) -> None:
        pet, rng = make()
        pet.life.start_tantrum(pet, rng, T0, "test")
        pet.life.on_gift(pet, "toy", "squeaky mouse", rng, T0 + 5)
        self.assertFalse(pet.life.in_tantrum(T0 + 6))


class RenameTests(unittest.TestCase):
    def test_low_bond_is_confused_high_bond_is_happy(self) -> None:
        pet, rng = make()
        pet.life.bond = 10
        self.assertEqual(pet.life.on_rename(pet, "Bitsy", rng, T0)["reaction"], "confused")
        pet2, rng2 = make(seed=2)
        pet2.life.bond = 80
        out = pet2.life.on_rename(pet2, "Sparky", rng2, T0)
        self.assertEqual(out["reaction"], "happy")
        self.assertEqual(pet2.life.display_name(), "Sparky")
        self.assertEqual(pet2.life.on_rename(pet2, "Zed", rng2, T0 + 30)["reaction"], "again")


class AskTests(unittest.TestCase):
    def test_hungry_pet_asks_for_food_and_thanks_you(self) -> None:
        pet, rng = make(hunger=20)
        pet.life.link_up = True
        pet.life._ask_tick(pet, rng, T0)
        self.assertEqual(pet.life.ask["kind"], "food")
        self.assertIn("ask", kinds(pet.life))
        bond = pet.life.bond
        pet.life.on_feed(pet, "ration", rng, T0 + 10)
        self.assertIsNone(pet.life.ask)
        self.assertGreater(pet.life.bond, bond)
        self.assertIn("ask_answered", kinds(pet.life))

    def test_no_asks_without_a_link(self) -> None:
        pet, rng = make(hunger=20)
        pet.life._ask_tick(pet, rng, T0)
        self.assertIsNone(pet.life.ask)

    def test_ignored_asks_lead_to_a_tantrum(self) -> None:
        pet, rng = make(hunger=20)
        life = pet.life
        life.link_up = True
        t = T0
        for _ in range(L.IGNORED_FOR_TANTRUM):
            life.cooldowns["ask"] = 0
            life._ask_tick(pet, rng, t)
            self.assertIsNotNone(life.ask)
            t += L.ASK_TIMEOUT + 1
            life._ask_tick(pet, rng, t)
        self.assertTrue(life.in_tantrum(t))
        self.assertIn("tantrum", kinds(life))


class GreetingTests(unittest.TestCase):
    def greet(self, pet, rng, now) -> tuple[str, dict]:
        pet.life.on_hello(pet, rng, now)
        pet.life._flush_greeting(pet, rng, now)
        events = pet.life.take_outbox()
        return events[-1]

    def test_first_hello(self) -> None:
        pet, rng = make()
        kind, detail = self.greet(pet, rng, T0)
        self.assertEqual((kind, detail["kind"]), ("greeting", "first"))
        self.assertTrue(detail["say"])

    def test_absence_buckets(self) -> None:
        cases = [(60, "quick"), (1800, "short"), (3 * 3600, "medium"), (20 * 3600, "long"), (5 * 86400, "very_long")]
        for gap, expected in cases:
            pet, rng = make(seed=3)
            pet.life.bond = 60  # no sulking
            pet.life.last_seen = T0 - gap
            pet.life.last_session_day = "2026-11-12"
            _kind, detail = self.greet(pet, rng, T0)
            self.assertIn(detail["kind"], {expected, "night"}, gap)

    def test_reunion_after_link_drop(self) -> None:
        pet, rng = make()
        self.greet(pet, rng, T0)
        pet.life.on_link(False, T0 + 10)
        pet.life._separation(pet, rng, T0 + 10 + L.LONELY_AFTER + 1)
        self.assertIn("lonely", kinds(pet.life))
        kind, detail = self.greet(pet, rng, T0 + 200)
        self.assertEqual(kind, "reunion")
        self.assertGreater(pet.life.mood_offset(T0 + 200), 0)

    def test_learns_your_schedule(self) -> None:
        pet, rng = make()
        life = pet.life
        for day in range(1, 6):
            now = at(2026, 11, day, 18, 5)
            life.link_up = False
            life.separated_at = None
            life.last_seen = now - 86400
            life.on_hello(pet, rng, now)
            life.pending_greeting = None
        self.assertEqual(life.usual_hour(), 18)
        self.assertEqual(life.usual_time_label(), "6pm")
        life.link_up = False
        life.last_seen = at(2026, 11, 6, 18, 0)
        _kind, detail = self.greet(pet, rng, at(2026, 11, 7, 18, 10))
        self.assertEqual(detail["kind"], "on_time")


class IdleDreamQuirkTests(unittest.TestCase):
    def test_idle_life_after_a_quiet_minute(self) -> None:
        pet, rng = make()
        pet.life.last_interaction_at = T0
        pet.life._idle(pet, rng, T0 + 10)
        self.assertEqual(kinds(pet.life), [])
        pet.life._idle(pet, rng, T0 + L.IDLE_AFTER + 1)
        events = pet.life.take_outbox()
        self.assertEqual(events[0][0], "idle")
        self.assertIn(events[0][1]["action"], L.IDLE_ACTIONS + ("waiting",))
        self.assertIsNotNone(pet.life.current_anim(T0 + L.IDLE_AFTER + 2))

    def test_idle_actions_vary(self) -> None:
        pet, rng = make(mood=80)
        seen = set()
        t = T0 + 1000
        for i in range(80):
            pet.life.cooldowns["idle"] = 0
            pet.life._idle(pet, rng, t + i)
            seen |= {d["action"] for _k, d in pet.life.take_outbox()}
        self.assertGreaterEqual(len(seen), 6)

    def test_long_absence_makes_it_stare(self) -> None:
        pet, rng = make(mood=50)
        actions = set()
        for i in range(60):
            pet.life.cooldowns["idle"] = 0
            pet.life._idle(pet, rng, T0 + 700 + i)
            actions |= {d["action"] for _k, d in pet.life.take_outbox()}
        self.assertIn("stare", actions)

    def test_dreams_reference_memories(self) -> None:
        pet, rng = make()
        pet.asleep = True
        pet.life.remember("discovery", "LAB-NET", T0)
        abouts = set()
        for i in range(30):
            pet.life.cooldowns["dream"] = 0
            pet.life._dream(pet, rng, T0 + i)
            abouts |= {d["about"] for _k, d in pet.life.take_outbox()}
        self.assertIn("discovery", abouts)

    def test_sneezes_more_likely_when_stats_are_off(self) -> None:
        def count(**stats) -> int:
            pet, rng = make(**stats)
            n = 0
            for i in range(400):
                pet.life.cooldowns["quirk"] = 0
                pet.life._quirks(pet, rng, T0 + i)
                n += len(pet.life.take_outbox())
            return n

        self.assertGreater(count(hunger=5, energy=5, mood=5), count(hunger=90, energy=90, mood=90) + 5)


class WorldReactionTests(unittest.TestCase):
    def test_hot_cpu(self) -> None:
        pet, rng = make()
        pet.life._awareness(pet, rng, T0, World(temp_reader=lambda: 78.0))
        events = dict(pet.life.take_outbox())
        self.assertIn("hot", events)
        self.assertEqual(pet.life.temp["source"], "sensor")

    def test_weather_changes_mood(self) -> None:
        pet, rng = make()
        world = World(temp_reader=lambda: 50.0, lat=1, lon=2, weather_fetch=lambda a, b: {"weathercode": 63}, threaded=False)
        pet.life._awareness(pet, rng, T0, world)
        self.assertIn("weather", dict(pet.life.take_outbox()))
        self.assertLess(pet.life.mood_offset(T0), 0)

    def test_offline_weather_is_harmless(self) -> None:
        def boom(a, b):
            raise OSError("no network")

        pet, rng = make()
        pet.life._awareness(pet, rng, T0, World(temp_reader=lambda: 50.0, lat=1, lon=2, weather_fetch=boom, threaded=False))
        self.assertNotIn("weather", dict(pet.life.take_outbox()))

    def test_late_night_sleepy_and_afternoon_hyper(self) -> None:
        pet, rng = make()
        pet.life._awareness(pet, rng, at(2026, 11, 12, 23, 30), None)
        self.assertIn("sleepy", dict(pet.life.take_outbox()))
        pet2, rng2 = make()
        pet2.life._awareness(pet2, rng2, at(2026, 11, 12, 15, 0), None)
        self.assertIn("hyper", dict(pet2.life.take_outbox()))


class SignalTests(unittest.TestCase):
    def test_place_labels(self) -> None:
        self.assertEqual(place_label("Cafe Lumen"), "the café")
        self.assertEqual(place_label("SmithFamilyHome"), "home")
        self.assertEqual(place_label("LAB-NET"), "work")
        self.assertEqual(place_label("NETGEAR42", night_share=0.8, count=5), "home")
        self.assertEqual(place_label("Zork"), "the Zork place")

    def test_treasure_names(self) -> None:
        self.assertTrue(is_treasure_name("wifi", ""))
        self.assertTrue(is_treasure_name("bt", "??-BEACON"))
        self.assertTrue(is_treasure_name("wifi", "fx-hidden"))
        self.assertTrue(is_treasure_name("wifi", "Loud", rssi=-30))
        self.assertFalse(is_treasure_name("wifi", "Cafe Lumen", rssi=-60))

    def test_treasure_goes_on_the_shelf_once(self) -> None:
        pet, rng = make()
        self.assertTrue(pet.life.find_treasure(pet, "bt", "b1", "??-BEACON", rng, T0))
        self.assertFalse(pet.life.find_treasure(pet, "bt", "b1", "??-BEACON", rng, T0 + 1))
        self.assertEqual(len(pet.life.shelf), 1)
        self.assertEqual(pet.life.favorite_trinket(), "??-BEACON echo")

    def test_recognizes_a_known_network(self) -> None:
        pet, rng = make()
        life = pet.life
        for i in range(4):
            life.on_signals(pet, [("wifi", "w1", "Cafe Lumen", -50)], set(), rng, T0 + i * 120)
        life.take_outbox()
        life.on_signals(pet, [("wifi", "w1", "Cafe Lumen", -50)], set(), rng, T0 + 4 * 120 + L.RECOGNIZE_GAP)
        events = dict(life.take_outbox())
        self.assertEqual(events["recognize"]["place"], "the café")

    def test_turf_and_suspicious_newcomer(self) -> None:
        pet, rng = make()
        life = pet.life
        life.networks["HomeNet"] = {"count": L.TURF_AT + 1, "first": T0, "last": T0, "night": 0}
        life.on_signals(pet, [("wifi", "h", "HomeNet", -40), ("wifi", "x", "EvilTwin", -60)], {"x"}, rng, T0 + 100)
        self.assertIn("suspicious", dict(life.take_outbox()))
        self.assertEqual(life.turf(), ["HomeNet"])

    def test_new_place_makes_it_nervous(self) -> None:
        pet, rng = make()
        life = pet.life
        for name in ("A", "B", "C", "D"):
            life.networks[name] = {"count": 5, "first": T0, "last": T0, "night": 0}
        life.on_signals(pet, [("wifi", "p", "Plaza1", -50), ("wifi", "q", "Plaza2", -55)], {"p", "q"}, rng, T0 + 10)
        self.assertIn("new_place", dict(life.take_outbox()))

    def test_favorite_signal_is_learned(self) -> None:
        pet, rng = make()
        pet.life.prefs["fav_signal"] = "bt"
        pet.life.on_signals(pet, [("bt", "b9", "Buds", -60)], {"b9"}, rng, T0)
        self.assertIn("fav_signal", pet.life.known_preferences())


class OutingTests(unittest.TestCase):
    def test_adventure_story_and_reward(self) -> None:
        pet, rng = make(energy=90, mood=90)
        life = pet.life
        start = life.start_adventure(pet, rng, T0)
        self.assertIsNotNone(start)
        self.assertTrue(life.away())
        life.update(pet, start["until"] + 1, rng)
        events = dict(life.take_outbox())
        ret = events["adventure_return"]
        self.assertIn(ret["outcome"], {"great", "good", "meh", "bad"})
        self.assertIn(start["place"], ret["story"])
        self.assertFalse(life.away())

    def test_better_stats_mean_better_adventures(self) -> None:
        def score(**stats) -> float:
            good = {"great": 3, "good": 2, "meh": 1, "bad": 0}
            total = 0
            for seed in range(40):
                pet, rng = make(seed=seed, **stats)
                pet.life.start_adventure(pet, rng, T0)
                total += good[pet.life.finish_adventure(pet, rng, T0 + 1)["outcome"]]
            return total

        strong = score(mood=100, energy=100, attack=30, defense=30, speed=30, level=10)
        weak = score(mood=10, energy=45, attack=1, defense=1, speed=1)
        self.assertGreater(strong, weak)

    def test_idle_pet_wanders_off(self) -> None:
        pet, rng = make(energy=90)
        rng.random = lambda: 0.0  # type: ignore[method-assign]
        pet.life.last_interaction_at = T0 - L.ADVENTURE_IDLE - 1
        pet.life._maybe_adventure(pet, rng, T0)
        self.assertTrue(pet.life.away())

    def test_rival_appears_then_leaves(self) -> None:
        pet, rng = make()
        life = pet.life
        life.rival["next_at"] = T0
        life._rival_tick(pet, rng, T0)
        appear = dict(life.take_outbox())["rival_appears"]
        self.assertEqual(appear["rival"], life.rival["name"])
        self.assertTrue(appear["taunt"])
        life._rival_tick(pet, rng, T0 + L.RIVAL_WAIT + 1)
        self.assertIn("rival_leaves", dict(life.take_outbox()))

    def test_rival_scales_with_level(self) -> None:
        from pet.rival import make_rival

        low = make_rival("X", 2, random.Random(1))
        high = make_rival("X", 20, random.Random(1))
        self.assertGreater(high["hp"], low["hp"])
        self.assertGreater(high["attack"], low["attack"])

    def test_gift_back_needs_a_strong_bond(self) -> None:
        pet, rng = make()
        rng.random = lambda: 0.5  # type: ignore[method-assign]
        pet.life.cooldowns["gift"] = 0
        pet.life._gift_back(pet, rng, T0)
        self.assertNotIn("gift_back", dict(pet.life.take_outbox()))
        pet.life.bond = 95
        rng.random = lambda: 0.1  # type: ignore[method-assign]
        pet.life._gift_back(pet, rng, T0 + 1)
        gift = dict(pet.life.take_outbox())["gift_back"]
        self.assertIn(gift["kind"], {"item", "fact", "evo"})
        self.assertTrue(gift["say"])

    def test_fun_facts_use_memory(self) -> None:
        pet, rng = make()
        pet.life.remember_network("LAB-NET", T0)
        fact = pet.life.fun_fact(rng, T0)
        self.assertNotIn("{", fact)

    def test_jealousy(self) -> None:
        pet, rng = make()
        life = pet.life
        life.last_interaction_at = T0
        for _ in range(L.JEALOUS_ACTIVITIES):
            life.on_activity(pet, "app", "Snake", None, rng, T0 + 10)
        life._jealousy(pet, rng, T0 + L.JEALOUS_AFTER + 1)
        self.assertEqual(dict(life.take_outbox())["jealous"]["app"], "Snake")


class CalendarLifeTests(unittest.TestCase):
    def test_birthday_party_and_boost(self) -> None:
        hatch = at(2025, 11, 12, 9, 0)
        pet, rng = make(now=hatch)
        before = pet.stats.attack
        pet.life._calendar(pet, rng, T0)
        events = dict(pet.life.take_outbox())
        self.assertEqual(events["birthday"]["years"], 1)
        self.assertEqual(pet.stats.attack, before + 1)
        self.assertEqual(pet.life.costume(T0), "party")
        pet.life._calendar(pet, rng, T0 + 60)
        self.assertNotIn("birthday", dict(pet.life.take_outbox()))  # once a year

    def test_monthly_hatchday(self) -> None:
        pet, rng = make(now=at(2026, 9, 12, 9, 0))
        pet.life._calendar(pet, rng, T0)
        self.assertEqual(dict(pet.life.take_outbox())["hatchday"]["months"], 2)

    def test_holiday_costume(self) -> None:
        halloween = at(2026, 10, 31, 12, 0)
        pet, rng = make(now=at(2026, 10, 1, 12, 0))
        pet.life._calendar(pet, rng, halloween)
        self.assertEqual(dict(pet.life.take_outbox())["holiday"]["holiday"], "halloween")
        self.assertEqual(pet.life.costume(halloween), "halloween")

    def test_diary_at_day_rollover(self) -> None:
        pet, rng = make()
        life = pet.life
        life.on_feed(pet, "ration", rng, T0)
        life.on_play(pet, "fetch", "attack", rng, T0 + 5)
        life.take_outbox()
        life._calendar(pet, rng, at(2026, 11, 13, 0, 1))
        entry = dict(life.take_outbox())["diary"]
        self.assertEqual(entry["date"], "2026-11-12")
        self.assertIn("PULSE", entry["text"])
        self.assertEqual(life.day["date"], "2026-11-13")
        self.assertEqual(len(life.diary), 1)

    def test_diaries_differ_day_to_day(self) -> None:
        pet, rng = make()
        texts = set()
        for d in range(10):
            pet.life.day = {"date": f"2026-11-{d + 1:02d}", "c": {"fed": 2, "played": 1}, "h": {}}
            texts.add(pet.life.write_diary(pet, rng, T0, draft=True)["text"])
        self.assertEqual(len(texts), 10)


class EggTests(unittest.TestCase):
    def test_lay_and_hatch_inherits(self) -> None:
        pet, rng = make()
        pet.species_id = "aethernova"
        pet.known_move_ids = ["chirp", "packet_burst"]
        pet.life.nickname = "Elder"
        pet.life.remember_network("HomeNet", T0)
        pet.life.add_to_shelf("golden key", "test", 3, T0)
        egg = pet.life.lay_egg(pet, rng, T0, trait="catchphrase")
        self.assertEqual(egg["move"], "packet_burst")
        phrase = pet.life.quirks["catchphrase"]
        legacy = pet.hatch_from_egg(T0 + 10, rng)
        self.assertEqual(pet.species_id, "chirplet")
        self.assertIn("packet_burst", pet.known_move_ids)
        self.assertEqual(pet.life.quirks["catchphrase"], phrase)
        self.assertEqual(pet.life.generation, 2)
        self.assertEqual(legacy["generation"], 2)
        self.assertIn("HomeNet", pet.life.networks)
        self.assertEqual(pet.life.shelf[0]["name"], "golden key")
        self.assertEqual(pet.life.lineage[-1]["name"], "Elder")
        self.assertIsNone(pet.life.nickname)
        self.assertGreater(pet.life.bond, L.BOND_START)

    def test_reset_is_a_clean_slate(self) -> None:
        pet, rng = make()
        pet.life.nickname = "Old"
        pet.stats.level = 9
        pet.reset(now=T0, rng=rng)
        self.assertEqual(pet.stats.level, 1)
        self.assertIsNone(pet.life.nickname)


if __name__ == "__main__":
    unittest.main()
