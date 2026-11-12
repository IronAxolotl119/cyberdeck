"""Dialogue pools, the no-repeat picker, template slots, and stage voices."""

from __future__ import annotations

import random
import unittest

from pet import stories
from pet.speech import (
    LinePicker,
    Voice,
    babble,
    fill,
    load_pools,
    parse_lines,
    resolve_key,
    sentence_case,
    tidy,
    simplify,
)

# Major categories that must have big pools so the pet does not repeat itself.
BIG_POOLS = (
    "chatter.happy", "chatter.content", "feed.normal", "play.fun", "ramble", "babble",
)
MEDIUM_POOLS = (
    "chatter.sad", "chatter.grumpy", "feed.like", "feed.hate", "praise.normal", "greet.short",
    "greet.medium", "greet.reunion", "ask.food", "ask.play", "idle.look_around", "idle.chase_bug",
    "dream.generic", "treasure", "territory.recognize", "adv.setting", "adv.place", "adv.encounter",
    "adv.ending", "diary.filler", "trinket.adj", "trinket.noun", "rival.appear", "rival.taunt",
    "catch.a", "catch.b", "funfact", "gift_back.item", "tantrum.start", "birthday",
)


class PoolTests(unittest.TestCase):
    def test_parse_sections_and_comments(self) -> None:
        pools = parse_lines("# hi\n[a.b]\none\n two\n\n[c]\nthree\n")
        self.assertEqual(pools["a.b"], ["one", " two"])
        self.assertEqual(pools["c"], ["three"])

    def test_big_pools_are_big(self) -> None:
        pools = load_pools()
        for key in BIG_POOLS:
            self.assertGreaterEqual(len(pools.get(key, [])), 30, key)
        for key in MEDIUM_POOLS:
            self.assertGreaterEqual(len(pools.get(key, [])), 10, key)

    def test_total_line_count(self) -> None:
        total = sum(len(v) for v in load_pools().values())
        self.assertGreater(total, 1800)

    def test_lines_are_unique_within_a_pool(self) -> None:
        for key, lines in load_pools().items():
            self.assertEqual(len(lines), len(set(lines)), key)

    def test_every_line_formats(self) -> None:
        for key, lines in load_pools().items():
            for line in lines:
                out = fill(line, {"name": "Pulse"})
                self.assertNotIn("{", out, (key, line))

    def test_resolve_falls_back(self) -> None:
        self.assertEqual(resolve_key("feed.like.happy"), "feed.like")
        self.assertEqual(resolve_key("chatter.nonsense"), None if not load_pools().get("chatter") else "chatter")
        self.assertIsNone(resolve_key("no.such.pool"))


class PickerTests(unittest.TestCase):
    def test_no_repeat_picker_cycles_a_pool(self) -> None:
        picker = LinePicker()
        rng = random.Random(1)
        size = 20
        cap = int(size * 0.8)
        history: list[int] = []
        for _ in range(500):
            history.append(picker.pick_index("k", size, rng))
        # Never repeats inside the blocked window...
        for i in range(len(history)):
            window = history[max(0, i - cap):i]
            self.assertNotIn(history[i], window)
        # ...and the first cap+1 picks cover cap+1 distinct lines.
        self.assertEqual(len(set(history[: cap + 1])), cap + 1)
        # Over time every line is used.
        self.assertEqual(set(history), set(range(size)))

    def test_real_pool_does_not_repeat_until_most_used(self) -> None:
        picker = LinePicker()
        rng = random.Random(5)
        pool = load_pools()["chatter.happy"]
        seen = [picker.pick("chatter.happy", rng) for _ in range(int(len(pool) * 0.8))]
        self.assertEqual(len(seen), len(set(seen)))

    def test_history_survives_a_round_trip(self) -> None:
        picker = LinePicker()
        rng = random.Random(2)
        for _ in range(5):
            picker.pick("chatter.content", rng)
        clone = LinePicker(picker.to_dict())
        self.assertEqual(clone.history, picker.history)

    def test_single_line_pool(self) -> None:
        self.assertEqual(LinePicker().pick_index("x", 1, random.Random()), 0)


class VoiceTests(unittest.TestCase):
    def test_fill_unknown_slot_gets_default(self) -> None:
        self.assertEqual(fill("hi {name}, {food}!"), "hi PULSE, snack!")
        self.assertEqual(fill("hi {name}", {"name": "Bit"}), "hi Bit")
        self.assertEqual(fill("{weird}"), "weird")

    def test_baby_babbles(self) -> None:
        rng = random.Random(3)
        out = babble("I am so hungry, can I have a snack please?", rng)
        self.assertLess(len(out.split()), 8)
        self.assertTrue(out)

    def test_in_training_is_simple(self) -> None:
        out = simplify("I am REALLY hungry and I would absolutely love the delicious cookie right now", random.Random(1))
        self.assertEqual(out, out.lower())
        self.assertLessEqual(len(out.split()), 10)
        self.assertIn("me", out.split())

    def test_stage_voices_differ(self) -> None:
        def speak(stage: str, seed: int) -> str:
            return Voice(LinePicker(), random.Random(seed)).speak(
                "chatter", {"name": "Bit"}, mood="content", stage=stage, decorate=False
            )

        rookie = [speak("rookie", s) for s in range(20)]
        baby = [speak("baby", s) for s in range(20)]
        self.assertGreater(sum(len(x) for x in rookie), sum(len(x) for x in baby))
        filled = {sentence_case(fill(line, {"name": "Bit"})) for line in load_pools()["chatter.content"]}
        self.assertTrue(all(x in filled for x in rookie))

    def test_variety_over_many_lines(self) -> None:
        voice = Voice(LinePicker(), random.Random(9))
        lines = [voice.speak("chatter", {"name": "Bit"}, mood="happy", stage="champion") for _ in range(200)]
        self.assertGreater(len(set(lines)), 120)

    def test_late_night_rambles(self) -> None:
        voice = Voice(LinePicker(), random.Random(4))
        lines = [voice.speak("chatter", {}, mood="content", stage="rookie", time_of_day="night") for _ in range(40)]
        self.assertTrue(any("... and" in line for line in lines))


class StoryTests(unittest.TestCase):
    def test_adventure_story_has_every_part(self) -> None:
        story = stories.adventure_story(LinePicker(), random.Random(1), "great", {"place": "the Glitch Forest", "move": "Chirp"}, "shiny gear")
        self.assertIn("the Glitch Forest", story)
        self.assertIn("shiny gear", story)
        self.assertGreater(len(story.split(".")), 3)

    def test_diary_entry_mentions_the_day(self) -> None:
        day = {"date": "2026-11-12", "c": {"fed": 3, "wins": 1, "visits": 2}, "h": {"food": "voltberry"}}
        text = stories.diary_entry(LinePicker(), random.Random(2), day, {"name": "Bit", "species": "Chirplet"}, 80.0, "Bit")
        self.assertTrue(text.endswith("- Bit"))
        self.assertGreater(len(text), 80)

    def test_combinations_are_huge(self) -> None:
        est = stories.combination_estimates()
        self.assertGreater(est["adventure_stories"], 1_000_000)
        self.assertGreater(est["diary_entries"], 1_000_000)
        self.assertGreaterEqual(est["trinkets"], 900)
        self.assertGreaterEqual(est["rival_names"], 300)
        self.assertGreaterEqual(est["catchphrases"], 400)

    def test_generated_names(self) -> None:
        rng = random.Random(3)
        self.assertTrue(stories.rival_name(rng))
        self.assertTrue(stories.catchphrase(rng))
        self.assertEqual(len(stories.trinket_name(LinePicker(), rng).split()), 2)



class TidyTests(unittest.TestCase):
    def test_grammar_fixes(self) -> None:
        self.assertEqual(tidy("gone 1 hours, a Aethling, a unicorn"), "gone 1 hour, an Aethling, a unicorn")
        self.assertEqual(sentence_case("oh, i think i'm ok"), "Oh, I think I'm ok")
        self.assertEqual(sentence_case("*hops* hi"), "*hops* hi")


if __name__ == "__main__":
    unittest.main()
