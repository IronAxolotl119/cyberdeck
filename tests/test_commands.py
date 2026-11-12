"""tests/test_commands.py — command registry and built-in handlers.

These tests drive the same Registry the UI uses, but in headless
form, so we can check parsing, help, quit/clear flags, and that
pet/ping talk to a simulated companion.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from cyberdeck.commands import Context, build_registry
from cyberdeck.companion import SimCompanionLink
from cyberdeck.headless import run_headless
from cyberdeck.plugins import load_user_commands
from cyberdeck.sensors import SimSensors


def _ctx() -> Context:
    """A fresh sim context with the companion already connected."""
    sensors = SimSensors(seed=1)
    link = SimCompanionLink()
    link.connect()
    return Context(sensors=sensors, companion=link, mode="sim")


class RegistryTests(unittest.TestCase):
    """Built-in command names, dispatch, and flags."""

    def setUp(self):
        # Tests of built-ins leave plugins off so a local extra file cannot surprise us.
        self.reg = build_registry(include_plugins=False)
        self.ctx = _ctx()

    def test_expected_builtins_are_registered(self):
        names = set(self.reg.names())
        for name in ("help", "status", "clear", "quit", "sys", "pet", "ping"):
            self.assertIn(name, names)

    def test_aliases_point_at_the_same_command(self):
        self.assertIs(self.reg.get("q"), self.reg.get("quit"))
        self.assertIs(self.reg.get("exit"), self.reg.get("quit"))
        self.assertIs(self.reg.get("cls"), self.reg.get("clear"))

    def test_unknown_command_is_reported(self):
        lines = self.reg.dispatch("blorp", self.ctx)
        self.assertTrue(any("unknown command" in line for line in lines))

    def test_empty_line_does_nothing(self):
        self.assertEqual(self.reg.dispatch("   ", self.ctx), [])

    def test_help_lists_commands(self):
        text = "\n".join(self.reg.dispatch("help", self.ctx))
        self.assertIn("ping", text)
        self.assertIn("pet", text)

    def test_help_for_one_command(self):
        text = "\n".join(self.reg.dispatch("help ping", self.ctx))
        self.assertIn("ping", text)

    def test_quit_sets_flag(self):
        lines = self.reg.dispatch("quit", self.ctx)
        self.assertTrue(self.ctx.should_quit)
        self.assertTrue(any("closing" in line for line in lines))

    def test_clear_sets_flag(self):
        self.reg.dispatch("clear", self.ctx)
        self.assertTrue(self.ctx.should_clear)

    def test_status_and_sys_include_sensor_fields(self):
        status = "\n".join(self.reg.dispatch("status", self.ctx))
        self.assertIn("mode=sim", status)
        self.assertIn("companion", status)
        sys_text = "\n".join(self.reg.dispatch("sys", self.ctx))
        self.assertIn("temp", sys_text)
        self.assertIn("battery", sys_text)

    def test_ping_reports_pong(self):
        text = "\n".join(self.reg.dispatch("ping", self.ctx))
        self.assertIn("pong", text)
        self.assertIn("rtt=", text)

    def test_pet_status_shows_pulse(self):
        text = "\n".join(self.reg.dispatch("pet", self.ctx))
        self.assertIn("PULSE", text)
        self.assertIn("hunger", text)

    def test_pet_feed_and_play(self):
        feed = "\n".join(self.reg.dispatch("pet feed ration", self.ctx))
        self.assertIn("fed ration", feed)
        play = "\n".join(self.reg.dispatch("pet play spar", self.ctx))
        self.assertIn("played spar", play)

    def test_pet_unknown_action_shows_usage(self):
        text = "\n".join(self.reg.dispatch("pet dance", self.ctx))
        self.assertIn("usage", text)

    def test_headless_runs_a_short_script(self):
        # Headless mode is how tests (and the user) run the deck without curses.
        import io

        buf = io.StringIO()
        code = run_headless(self.ctx, self.reg, commands=["ping", "quit"], out=buf)
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("> ping", output)
        self.assertIn("pong", output)
        self.assertIn("> quit", output)


class PluginLoadTests(unittest.TestCase):
    """The example hello plugin should load and run."""

    def test_example_hello_plugin_is_loadable(self):
        reg = build_registry(include_plugins=False)
        loaded = load_user_commands(reg)
        self.assertIn("hello", loaded)
        ctx = _ctx()
        text = "\n".join(reg.dispatch("hello world", ctx))
        self.assertEqual(text, "hello, world.")
        # The template file must not be registered.
        self.assertIsNone(reg.get("mycommand"))

    def test_build_registry_includes_plugins_by_default(self):
        # Default path used by the live app.
        plugins = Path(__file__).resolve().parents[1] / "plugins"
        self.assertTrue((plugins / "hello.py").is_file())
        reg = build_registry()
        self.assertIsNotNone(reg.get("hello"))
        self.assertIsNotNone(reg.get("hi"))  # alias from the example plugin


if __name__ == "__main__":
    unittest.main()
