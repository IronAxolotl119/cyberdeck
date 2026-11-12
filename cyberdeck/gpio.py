"""gpio.py — LEDs, buttons, and a buzzer, with or without a real Pi.

Like sensors.py, this is a "plug" with several backends behind one
interface (GpioBackend). Commands call set_output / read_input / tone
and never care which backend is plugged in.

  * SimGpio       — no hardware. Remembers pin states, lets you
                    "press" sim buttons, and logs every beep so the
                    music player and visualizer still work.
  * GpiozeroGpio  — the friendly gpiozero library (preinstalled on
                    Raspberry Pi OS). Imported only when chosen, so a
                    laptop without it is fine.
  * SysfsGpio     — the old /sys/class/gpio files, a fallback when
                    gpiozero is missing. Tones are bit-banged in
                    software (rough, but fine for a piezo).

Choose with config/hardware.ini [gpio] backend = auto | sim | gpiozero | sysfs.
"auto" means sim in --mode sim, and in --mode real tries gpiozero,
then sysfs, then falls back to sim (with a note in `gpio status`).

Pin numbers are BCM numbers (GPIO17 = 17), not physical header pins.
Buzzers: a *passive* buzzer needs a tone (PWM) to make sound and can
play music; an *active* buzzer beeps on its own when powered, so it can
only do on/off beeps. Set [buzzer] type in hardware.ini.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any

# BCM pins exposed on the 40-pin header of a Pi Zero 2 W.
VALID_PINS = range(0, 28)


class GpioError(RuntimeError):
    """A pin could not be used (bad number, permission, missing library)."""


def check_pin(pin: int) -> int:
    """Reject pin numbers that do not exist on the header."""
    if pin not in VALID_PINS:
        raise GpioError(f"GPIO{pin} is not a valid BCM pin (0-27)")
    return pin


class GpioBackend(ABC):
    """What every GPIO backend can do."""

    name = "base"

    def __init__(self) -> None:
        # Set by stop_tone() so a long note can be cut short.
        self._stop = threading.Event()
        # Remembered output states, for `gpio status`.
        self.outputs: dict[int, bool] = {}

    @abstractmethod
    def set_output(self, pin: int, on: bool) -> None:
        """Drive a pin high (on) or low (off)."""

    @abstractmethod
    def read_input(self, pin: int) -> bool:
        """True when the pin reads high / the button is pressed."""

    @abstractmethod
    def _tone_on(self, pin: int, freq: float) -> None:
        """Start a square wave at freq Hz (or just switch an active buzzer on)."""

    @abstractmethod
    def _tone_off(self, pin: int) -> None:
        """Silence the buzzer pin."""

    def tone(self, pin: int, freq: float, seconds: float) -> None:
        """Play one note and block for its length. freq <= 0 is a rest."""
        self._stop.clear()
        if freq > 0:
            self._tone_on(pin, freq)
        try:
            self._stop.wait(max(0.0, seconds))
        finally:
            if freq > 0:
                self._tone_off(pin)

    def stop_tone(self) -> None:
        """Interrupt a note that is playing in another thread."""
        self._stop.set()

    def close(self) -> None:
        """Release pins. Safe to call more than once."""


# --- simulation ---------------------------------------------------------

class SimGpio(GpioBackend):
    """Pretend pins. Everything is remembered so it can be shown on screen."""

    name = "sim"

    def __init__(self, sleep: bool = True) -> None:
        super().__init__()
        self.inputs: dict[int, bool] = {}
        # Recent beeps as (time, pin, freq): the visualizer and tests read this.
        self.tones: deque[tuple[float, int, float]] = deque(maxlen=64)
        self.current_freq = 0.0
        self.sleep = sleep  # tests turn this off so songs "play" instantly

    def set_output(self, pin: int, on: bool) -> None:
        self.outputs[check_pin(pin)] = bool(on)

    def read_input(self, pin: int) -> bool:
        return self.inputs.get(check_pin(pin), False)

    def press(self, pin: int, down: bool = True) -> None:
        """Simulate a button press (`gpio sim press 22`)."""
        self.inputs[check_pin(pin)] = down

    def _tone_on(self, pin: int, freq: float) -> None:
        self.tones.append((time.time(), pin, freq))
        self.current_freq = freq

    def _tone_off(self, pin: int) -> None:
        self.current_freq = 0.0

    def tone(self, pin: int, freq: float, seconds: float) -> None:
        if self.sleep:
            super().tone(pin, freq, seconds)
        else:  # instant mode for tests
            if freq > 0:
                self._tone_on(pin, freq)
                self._tone_off(pin)


# --- gpiozero (preferred on a real Pi) ------------------------------------

class GpiozeroGpio(GpioBackend):
    """Real pins through gpiozero. Devices are created on first use."""

    name = "gpiozero"

    def __init__(self, buzzer_type: str = "passive") -> None:
        super().__init__()
        try:
            import gpiozero  # noqa: F401 — imported lazily on purpose
        except ImportError as exc:
            raise GpioError("gpiozero is not installed (sudo apt install python3-gpiozero)") from exc
        self._gz = gpiozero
        self._devices: dict[int, Any] = {}
        self.buzzer_type = buzzer_type

    def _device(self, pin: int, kind: str) -> Any:
        dev = self._devices.get(check_pin(pin))
        wanted = {"out": self._gz.DigitalOutputDevice, "in": self._gz.Button, "pwm": self._gz.PWMOutputDevice}[kind]
        if dev is not None and not isinstance(dev, wanted):
            dev.close()
            dev = None
        if dev is None:
            dev = wanted(pin) if kind != "in" else wanted(pin, pull_up=True)
            self._devices[pin] = dev
        return dev

    def set_output(self, pin: int, on: bool) -> None:
        dev = self._device(pin, "out")
        dev.on() if on else dev.off()
        self.outputs[pin] = bool(on)

    def read_input(self, pin: int) -> bool:
        return bool(self._device(pin, "in").is_pressed)

    def _tone_on(self, pin: int, freq: float) -> None:
        if self.buzzer_type == "active":
            self._device(pin, "out").on()
            return
        dev = self._device(pin, "pwm")
        dev.frequency = freq
        dev.value = 0.5  # 50% duty cycle square wave

    def _tone_off(self, pin: int) -> None:
        dev = self._devices.get(pin)
        if dev is not None:
            dev.off()

    def close(self) -> None:
        for dev in self._devices.values():
            try:
                dev.close()
            except Exception:
                pass
        self._devices.clear()


# --- sysfs fallback -------------------------------------------------------

class SysfsGpio(GpioBackend):
    """The legacy /sys/class/gpio interface. Needs the gpio group or root."""

    name = "sysfs"
    ROOT = Path("/sys/class/gpio")

    def __init__(self, buzzer_type: str = "passive", root: Path | None = None) -> None:
        super().__init__()
        self.root = root or self.ROOT
        if not (self.root / "export").exists():
            raise GpioError(f"{self.root} is not available on this system")
        self.buzzer_type = buzzer_type
        self._directions: dict[int, str] = {}
        self._wave: threading.Thread | None = None
        self._wave_stop = threading.Event()

    def _export(self, pin: int, direction: str) -> Path:
        base = self.root / f"gpio{check_pin(pin)}"
        try:
            if not base.exists():
                (self.root / "export").write_text(str(pin))
                time.sleep(0.05)  # udev needs a moment to fix permissions
            if self._directions.get(pin) != direction:
                (base / "direction").write_text(direction)
                self._directions[pin] = direction
        except OSError as exc:
            raise GpioError(f"GPIO{pin}: {exc.strerror or exc}") from exc
        return base

    def set_output(self, pin: int, on: bool) -> None:
        (self._export(pin, "out") / "value").write_text("1" if on else "0")
        self.outputs[pin] = bool(on)

    def read_input(self, pin: int) -> bool:
        return (self._export(pin, "in") / "value").read_text().strip() == "1"

    def _tone_on(self, pin: int, freq: float) -> None:
        if self.buzzer_type == "active":
            self.set_output(pin, True)
            return
        value = self._export(pin, "out") / "value"
        self._wave_stop.clear()
        half = 1.0 / (2 * freq)

        def wave() -> None:  # software square wave: rough, but audible
            with open(value, "w") as fh:
                while not self._wave_stop.is_set():
                    fh.write("1"); fh.flush(); time.sleep(half)
                    fh.write("0"); fh.flush(); time.sleep(half)

        self._wave = threading.Thread(target=wave, daemon=True)
        self._wave.start()

    def _tone_off(self, pin: int) -> None:
        self._wave_stop.set()
        if self._wave is not None:
            self._wave.join(timeout=0.2)
            self._wave = None
        try:
            self.set_output(pin, False)
        except GpioError:
            pass


# --- factory -----------------------------------------------------------------

def open_gpio(mode: str, backend: str = "auto", buzzer_type: str = "passive") -> tuple[GpioBackend, str]:
    """Build the configured backend. Returns (backend, note about fallbacks)."""
    want = (backend or "auto").lower()
    if want == "sim" or (want == "auto" and mode == "sim"):
        return SimGpio(), ""
    attempts = ["gpiozero", "sysfs"] if want == "auto" else [want]
    errors: list[str] = []
    for name in attempts:
        try:
            if name == "gpiozero":
                return GpiozeroGpio(buzzer_type), ""
            if name == "sysfs":
                return SysfsGpio(buzzer_type), ""
            errors.append(f"unknown backend '{name}'")
        except GpioError as exc:
            errors.append(f"{name}: {exc}")
    return SimGpio(), "using sim pins — " + "; ".join(errors)

