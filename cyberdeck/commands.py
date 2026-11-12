"""commands.py — the command list and the built-in deck commands.

When you type `ping` or `pet feed` at the prompt, this file is what
turns that line of text into an action.

  * Registry holds every command by name (and alias).
  * Context is the shared handle commands use to read sensors, talk
    to the companion, or ask the UI to quit / clear the screen.
  * build_registry() registers the built-in commands and, by default,
    loads extras from the plugins/ folder.

To add your own command without editing this file, drop a Python file
in plugins/ — see plugins/_template.py and CUSTOMIZING.md. To add a
built-in, copy one of the small functions inside build_registry().
Every command returns a list of text lines (or None) so the same
handlers work in the full-screen UI and in headless mode.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cyberdeck import __version__
from cyberdeck.companion import CompanionError, CompanionLink
from cyberdeck.protocol import TYPE_PET_FEED, TYPE_PET_PLAY, TYPE_PET_STATUS, TYPE_PING
from cyberdeck.sensors import SystemSensors

# A command function takes the shared context plus argument words,
# and returns text lines to print (or None / an empty list).
Handler = Callable[["Context", list[str]], list[str] | None]


# Passed into every command. should_quit / should_clear are flags the
# UI and headless runner check after the command returns.
@dataclass
class Context:
    """Runtime state passed into every command."""

    sensors: SystemSensors
    companion: CompanionLink
    mode: str
    should_quit: bool = False
    should_clear: bool = False
    clock: Callable[[], float] = field(default=time.time)


# One registered command: its name, help text, and the function to run.
@dataclass(frozen=True, slots=True)
class Command:
    """A named handler plus the help text shown by `help`."""

    name: str
    handler: Handler
    summary: str
    usage: str = ""
    aliases: tuple[str, ...] = ()

    def help_line(self) -> str:
        """One padded line used in the help listing."""
        usage = self.usage or self.name
        return f"{usage:<22} {self.summary}"


# The lookup table for commands. New commands are added with register()
# or the @registry.command(...) decorator.
class Registry:
    """Name-to-command map. The first registered name is the official one."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._canonical: list[str] = []

    def register(self, command: Command) -> Command:
        """Add a command under its name and any aliases. Names are case-insensitive."""
        names = (command.name, *command.aliases)
        for name in names:
            key = name.lower()
            if key in self._commands:
                raise ValueError(f"command already registered: {name}")
            self._commands[key] = command
        self._canonical.append(command.name)
        return command

    def command(
        self,
        name: str,
        summary: str,
        usage: str = "",
        aliases: tuple[str, ...] = (),
    ) -> Callable[[Handler], Handler]:
        """Decorator that registers a function as a named command."""

        def decorator(fn: Handler) -> Handler:
            self.register(
                Command(
                    name=name,
                    handler=fn,
                    summary=summary,
                    usage=usage or name,
                    aliases=aliases,
                )
            )
            return fn

        return decorator

    def get(self, name: str) -> Command | None:
        """Look up a command by name or alias, or return None."""
        return self._commands.get(name.lower())

    def names(self) -> list[str]:
        """Official names in the order they were registered."""
        return list(self._canonical)

    def all(self) -> list[Command]:
        """Unique Command objects in registration order (aliases not repeated)."""
        seen: set[int] = set()
        out: list[Command] = []
        for name in self._canonical:
            cmd = self._commands[name]
            ident = id(cmd)
            if ident not in seen:
                seen.add(ident)
                out.append(cmd)
        return out

    def dispatch(self, line: str, ctx: Context) -> list[str]:
        """Parse a prompt line and run the matching command.

        Empty input does nothing. Unknown commands and errors become
        ordinary output lines so the UI can show them.
        """
        text = line.strip()
        if not text:
            return []
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return [f"parse error: {exc}"]
        name, args = parts[0], parts[1:]
        command = self.get(name)
        if command is None:
            return [f"unknown command: {name}  (try 'help')"]
        try:
            result = command.handler(ctx, args)
        except CompanionError as exc:
            return [f"companion error: {exc}"]
        except Exception as exc:  # show unexpected failures in the UI, do not crash
            return [f"command failed: {exc}"]
        return list(result or [])


def build_registry(include_plugins: bool = True) -> Registry:
    """Return a registry with built-in commands, and user plugins if requested.

    Pass include_plugins=False in unit tests that only care about the
    built-ins. The live app leaves the default on so plugins/ is loaded.
    """
    reg = Registry()

    # help: list every command, or describe one by name.
    @reg.command("help", "List commands, or describe one.", usage="help [command]")
    def cmd_help(ctx: Context, args: list[str]) -> list[str]:
        del ctx
        if args:
            target = reg.get(args[0])
            if target is None:
                return [f"no such command: {args[0]}"]
            alias = f"  aliases: {', '.join(target.aliases)}" if target.aliases else ""
            return [target.help_line() + alias]
        lines = ["commands:"]
        for command in reg.all():
            lines.append(f"  {command.help_line()}")
        return lines

    # status: one screen of deck sensors plus companion link health.
    @reg.command("status", "Show deck sensors and companion link state.")
    def cmd_status(ctx: Context, args: list[str]) -> list[str]:
        del args
        reading = ctx.sensors.read()
        linked = ctx.companion.is_linked()
        cpu = f"{reading.cpu_percent:.1f}%" if reading.cpu_percent is not None else "--"
        mem = f"{reading.mem_percent:.1f}%" if reading.mem_percent is not None else "--"
        lines = [
            f"deck          {reading.hostname}  mode={ctx.mode}  v{__version__}",
            f"uptime        {_fmt_uptime(reading.uptime_s)}",
            f"cpu           {reading.temp_label()}  load {cpu}  mem {mem}",
            f"power         {reading.battery_label()}",
            f"radio         {reading.wifi_label()}",
            f"companion     {ctx.companion.link_label()}",
        ]
        if linked:
            try:
                reply = ctx.companion.request(TYPE_PING, {})
                lines.append(f"handshake     {reply.type}")
            except CompanionError as exc:
                lines.append(f"handshake     failed ({exc})")
        return lines

    # clear: ask the UI (or headless runner) to wipe the output pane.
    @reg.command("clear", "Clear the output pane.", aliases=("cls",))
    def cmd_clear(ctx: Context, args: list[str]) -> list[str]:
        del args
        ctx.should_clear = True
        return []

    # quit: leave the app. exit and q do the same thing.
    @reg.command("quit", "Leave the deck UI.", usage="quit", aliases=("exit", "q"))
    def cmd_quit(ctx: Context, args: list[str]) -> list[str]:
        del args
        ctx.should_quit = True
        return ["closing the deck."]

    # sys: a slightly more detailed sensor dump than the status bar.
    @reg.command("sys", "Print a slightly deeper system snapshot.")
    def cmd_sys(ctx: Context, args: list[str]) -> list[str]:
        del args
        reading = ctx.sensors.read()
        charge = "charging" if reading.charging else "discharging"
        if reading.charging is None:
            charge = "n/a"
        return [
            f"host     {reading.hostname}",
            f"uptime   {_fmt_uptime(reading.uptime_s)}",
            f"temp     {reading.temp_label()}",
            f"cpu      {reading.cpu_percent if reading.cpu_percent is not None else '--'}%",
            f"memory   {reading.mem_percent if reading.mem_percent is not None else '--'}%",
            f"battery  {reading.battery_label()} ({charge})",
            f"wifi     {reading.wifi_label()}",
            f"mode     {ctx.mode}",
        ]

    # pet: talk to the companion. Default action is status.
    @reg.command(
        "pet",
        "Query the companion pet: status, feed, or play.",
        usage="pet [status|feed|play]",
    )
    def cmd_pet(ctx: Context, args: list[str]) -> list[str]:
        action = (args[0].lower() if args else "status")
        if action in {"status", "st"}:
            reply = ctx.companion.request(TYPE_PET_STATUS, {})
            pet = (reply.payload or {}).get("pet") or {}
            return _format_pet(pet)
        if action == "feed":
            item = args[1] if len(args) > 1 else "ration"
            reply = ctx.companion.request(TYPE_PET_FEED, {"item": item})
            payload = reply.payload or {}
            pet = payload.get("pet") or {}
            delta = payload.get("hunger_delta", "?")
            header = f"fed {payload.get('item', item)}  hunger {delta:+}" if isinstance(delta, int) else f"fed {item}"
            return [header, *_format_pet(pet)]
        if action == "play":
            game = args[1] if len(args) > 1 else "spar"
            reply = ctx.companion.request(TYPE_PET_PLAY, {"game": game})
            payload = reply.payload or {}
            pet = payload.get("pet") or {}
            if payload.get("played") is False:
                reason = payload.get("reason", "not now")
                return [f"PULSE sits this one out ({reason}).", *_format_pet(pet)]
            return [f"played {payload.get('game', game)}", *_format_pet(pet)]
        return [f"usage: pet [status|feed [item]|play [game]]  (unknown action: {action})"]

    # ping: send a ping and show how quickly the companion answered.
    @reg.command("ping", "Ping the companion board and report the reply.")
    def cmd_ping(ctx: Context, args: list[str]) -> list[str]:
        del args
        started = time.perf_counter()
        reply = ctx.companion.request(TYPE_PING, {})
        rtt_ms = (time.perf_counter() - started) * 1000.0
        source = (reply.payload or {}).get("source", "companion")
        return [f"{reply.type}  rtt={rtt_ms:.1f}ms  id={reply.id[:8]}  via {source}"]

    if include_plugins:
        from cyberdeck.plugins import load_user_commands

        load_user_commands(reg)

    return reg


# --- small formatting helpers used by pet / status / sys ---

def _format_pet(pet: dict[str, Any]) -> list[str]:
    """Turn a pet payload dict into readable lines with need bars."""
    if not pet:
        return ["pet: (empty payload)"]
    name = pet.get("name", "?")
    species = pet.get("species", "?")
    stage = pet.get("stage", "?")
    mood = pet.get("mood", "?")
    age = pet.get("age_hours", "?")
    lines = [
        f"{name}  ·  {species}  ·  {stage}",
        f"mood     {mood}",
        f"hunger   {_bar(pet.get('hunger'))}  {pet.get('hunger', '--')}",
        f"happy    {_bar(pet.get('happiness'))}  {pet.get('happiness', '--')}",
        f"energy   {_bar(pet.get('energy'))}  {pet.get('energy', '--')}",
        f"age      {age}h",
    ]
    return lines


def _bar(value: Any, width: int = 10) -> str:
    """A simple [####------] bar for a 0-100 value."""
    try:
        n = int(round(float(value) / 100.0 * width))
    except (TypeError, ValueError):
        return "[" + ("?" * width) + "]"
    n = max(0, min(width, n))
    return "[" + ("#" * n) + ("-" * (width - n)) + "]"


def _fmt_uptime(seconds: float) -> str:
    """Human-readable uptime such as 1h 02m 03s."""
    total = int(max(0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
