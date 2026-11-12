"""hardware.py — the `gpio` command: LEDs, buttons, and the buzzer.

  gpio                         backend, named pins, and their states
  gpio led on|off|toggle [pin] switch an LED (default: the "led" pin)
  gpio set <pin> on|off        drive any pin
  gpio read [pin]              read a button/input (default: "button")
  gpio beep [freq] [ms]        short beep on the buzzer (default 880 Hz, 120 ms)
  gpio sim press|release <pin> sim mode only: pretend a button is pressed

Pins can be numbers (BCM, e.g. 17) or names from config/hardware.ini
[pins] (led, button, buzzer…). The backend itself is in cyberdeck/gpio.py.
get_gpio(ctx) is how other features (music, pet alerts) reach the same
backend, so a beep from the pet and a song never fight over the pin.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cyberdeck.config import Settings
from cyberdeck.gpio import GpioBackend, GpioError, SimGpio, open_gpio

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


@dataclass
class Hardware:
    """The shared GPIO backend plus the pin names from hardware.ini."""

    gpio: GpioBackend
    pins: dict[str, int] = field(default_factory=dict)
    buzzer_type: str = "passive"
    note: str = ""
    # Only one thing may use the buzzer at a time (song vs. alert beep).
    buzzer_lock: threading.Lock = field(default_factory=threading.Lock)

    def pin(self, ref: str | int | None, default: str) -> int:
        """Resolve '17', 17, or a name like 'led' to a BCM pin number."""
        ref = default if ref in (None, "") else ref
        if isinstance(ref, int) or str(ref).isdigit():
            return int(ref)
        if str(ref).lower() in self.pins:
            return self.pins[str(ref).lower()]
        raise GpioError(f"no pin named '{ref}' (see config/hardware.ini [pins])")

    @property
    def buzzer(self) -> int:
        return self.pin(None, "buzzer")

    def beep(self, freq: float = 880.0, ms: float = 120.0) -> bool:
        """A short beep, skipped (False) if a song is using the buzzer."""
        if not self.buzzer_lock.acquire(blocking=False):
            return False
        try:
            self.gpio.tone(self.buzzer, freq, ms / 1000.0)
        finally:
            self.buzzer_lock.release()
        return True


DEFAULT_PINS = {"led": 17, "button": 27, "buzzer": 18}


def load_hardware(ctx: "Context", settings: Settings | None = None) -> Hardware:
    """Build the Hardware object from config/hardware.ini."""
    s = settings or Settings.load("hardware.ini", ctx.config_dir)
    pins = dict(DEFAULT_PINS)
    for name, value in s.section("pins").items():
        if value.strip().isdigit():
            pins[name.lower()] = int(value)
    buzzer_type = s.get_str("buzzer", "type", "passive").lower()
    gpio, note = open_gpio(ctx.mode, s.get_str("gpio", "backend", "auto"), buzzer_type)
    return Hardware(gpio, pins, buzzer_type, note)


def get_hardware(ctx: "Context") -> Hardware:
    """The shared Hardware service (created on first use)."""
    return ctx.service("hardware", lambda: load_hardware(ctx))


def register(reg: "Registry") -> None:
    """Add the gpio command."""

    def complete(ctx: "Context", args: list[str]) -> list[str]:
        if len(args) == 1 and args[0] == "led":
            return ["on", "off", "toggle"]
        if len(args) == 1 and args[0] == "sim":
            return ["press", "release"]
        return list(get_hardware(ctx).pins) if args else []

    @reg.command("gpio", "LEDs, buttons, buzzer: led, set, read, beep, sim.",
                 usage="gpio [led|set|read|beep|sim] ...",
                 subcommands=("led", "set", "read", "beep", "sim", "status"), completer=complete)
    def cmd_gpio(ctx: "Context", args: list[str]) -> list[str]:
        hw = get_hardware(ctx)
        action = args[0].lower() if args else "status"
        try:
            if action == "status":
                lines = [f"backend  {hw.gpio.name}  (buzzer: {hw.buzzer_type})"]
                if hw.note:
                    lines.append(f"note     {hw.note}")
                for name, pin in hw.pins.items():
                    state = hw.gpio.outputs.get(pin)
                    shown = "on" if state else "off" if state is not None else "-"
                    lines.append(f"  {name:<10} GPIO{pin:<3} {shown}")
                return lines
            if action == "led":
                verb = args[1].lower() if len(args) > 1 else "toggle"
                pin = hw.pin(args[2] if len(args) > 2 else None, "led")
                if verb == "toggle":
                    on = not hw.gpio.outputs.get(pin, False)
                elif verb in {"on", "off"}:
                    on = verb == "on"
                else:
                    return ["usage: gpio led on|off|toggle [pin]"]
                hw.gpio.set_output(pin, on)
                return [f"GPIO{pin} {'ON ●' if on else 'off ○'}"]
            if action == "set":
                if len(args) < 3 or args[2].lower() not in {"on", "off", "1", "0", "high", "low"}:
                    return ["usage: gpio set <pin> on|off"]
                pin = hw.pin(args[1], "led")
                on = args[2].lower() in {"on", "1", "high"}
                hw.gpio.set_output(pin, on)
                return [f"GPIO{pin} -> {'high' if on else 'low'}"]
            if action == "read":
                pin = hw.pin(args[1] if len(args) > 1 else None, "button")
                value = hw.gpio.read_input(pin)
                return [f"GPIO{pin} reads {'HIGH (pressed)' if value else 'low'}"]
            if action == "beep":
                freq = float(args[1]) if len(args) > 1 else 880.0
                ms = float(args[2]) if len(args) > 2 else 120.0
                if not 20 <= freq <= 20000 or not 0 < ms <= 5000:
                    return ["beep: freq 20-20000 Hz, length 1-5000 ms"]
                if not hw.beep(freq, ms):
                    return ["buzzer busy (music playing) — try `music stop`"]
                return [f"beep {freq:.0f} Hz for {ms:.0f} ms on GPIO{hw.buzzer} ({hw.gpio.name})"]
            if action == "sim":
                if not isinstance(hw.gpio, SimGpio):
                    return ["gpio sim only works with the sim backend"]
                if len(args) < 3 or args[1] not in {"press", "release"}:
                    return ["usage: gpio sim press|release <pin>"]
                pin = hw.pin(args[2], "button")
                hw.gpio.press(pin, args[1] == "press")
                return [f"sim GPIO{pin} {'pressed' if args[1] == 'press' else 'released'}"]
        except GpioError as exc:
            return [f"gpio: {exc}"]
        except ValueError:
            return ["gpio: numbers expected (e.g. gpio beep 440 200)"]
        return ["usage: gpio [led|set|read|beep|sim] ..."]
