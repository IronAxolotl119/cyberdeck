"""Input: a non-blocking keyboard reader plus GPIO buttons/joystick.

In plain English: the dashboard never waits for a key. Each frame it
asks "any keys since last time?" and gets a list of names like "1",
"q", "up", "enter". On the Pi, physical buttons wired to GPIO pins push
the SAME names into the same list, so a button press and a key press do
exactly the same thing. Pins are set in [buttons] in config/dash.ini.

Key names produced: single characters, plus up/down/left/right, enter,
esc, tab, backspace.
"""

from __future__ import annotations

import os
import queue
import select
import sys

# Which key each [buttons] entry acts like.
BUTTON_KEYS = {
    "scan": "s", "use_move": "u", "field": "f", "next_panel": "tab",
    "joy_up": "up", "joy_down": "down", "joy_left": "left", "joy_right": "right", "joy_press": "enter",
}

_ESCAPES = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "OA": "up", "OB": "down",
            "OC": "right", "OD": "left"}


class KeyReader:
    """Puts the terminal in cbreak mode and reads keys without blocking."""

    def __init__(self) -> None:
        self.fd = None
        self.saved = None
        self.extra: "queue.Queue[str]" = queue.Queue()   # GPIO / injected keys

    def __enter__(self) -> "KeyReader":
        if sys.stdin.isatty():
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc) -> None:
        if self.fd is not None and self.saved is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def inject(self, key: str) -> None:
        self.extra.put(key)

    def read(self, timeout: float = 0.0) -> list[str]:
        keys: list[str] = []
        while not self.extra.empty():
            keys.append(self.extra.get_nowait())
        if self.fd is None:
            return keys
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return keys
        data = os.read(self.fd, 64).decode("utf-8", "ignore")
        return keys + parse_keys(data)


def parse_keys(data: str) -> list[str]:
    """Turn raw terminal bytes into key names (handles arrow escapes)."""
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            seq = data[i + 1:i + 3]
            if seq in _ESCAPES:
                keys.append(_ESCAPES[seq])
                i += 3
                continue
            keys.append("esc")
        elif ch in ("\r", "\n"):
            keys.append("enter")
        elif ch == "\t":
            keys.append("tab")
        elif ch in ("\x7f", "\x08"):
            keys.append("backspace")
        else:
            keys.append(ch)
        i += 1
    return keys


class GpioButtons:
    """Real buttons via gpiozero (lazy import). Each press injects a key."""

    def __init__(self, pins: dict[str, str], sink: KeyReader) -> None:
        self.buttons = []
        self.error = ""
        try:
            from gpiozero import Button  # lazy: only on the Pi
        except Exception as exc:
            self.error = f"gpiozero unavailable: {exc}"[:60]
            return
        for name, pin in pins.items():
            if name not in BUTTON_KEYS or not str(pin).strip():
                continue
            try:
                btn = Button(int(pin), pull_up=True, bounce_time=0.05)
                btn.when_pressed = lambda key=BUTTON_KEYS[name]: sink.inject(key)
                self.buttons.append(btn)
            except Exception as exc:
                self.error = f"pin {pin}: {exc}"[:60]


class AlertLed:
    """Optional LED that blinks on alerts. Does nothing without gpiozero/pin."""

    def __init__(self, pin: str) -> None:
        self.led = None
        if pin.strip():
            try:
                from gpiozero import LED  # lazy
                self.led = LED(int(pin))
            except Exception:
                self.led = None

    def blink(self) -> None:
        if self.led:
            self.led.blink(on_time=0.1, off_time=0.1, n=3, background=True)
