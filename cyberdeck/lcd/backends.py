"""backends.py — where the LCD's characters actually go.

In plain English: the deck always keeps a pretend copy of the screen in
memory (SimScreen) so the dashboard tile, `lcd status` and snapshots can
show it. On the Pi you can ALSO drive the real glass:

  sim   only the pretend screen (any computer; the default)
  i2c   the real LCD1602 with a PCF8574 I2C "backpack" (the 4-pin board
        soldered on the back). Uses the RPLCD library if installed, else a
        tiny built-in driver on top of smbus2. Both are imported only here,
        only in i2c mode, so your computer never needs them.
  none  the LCD feature is switched off.

Install on the Pi:  pip install -r requirements-pi.txt   (RPLCD + smbus2)
Wiring, enabling I2C and the address check are in docs/LCD.md.

Every backend has the same few methods: start(), write_line(row, text),
set_backlight(on), close(), describe(). Text arrives already mapped by
text.to_lcd(): plain ASCII plus chr(0)…chr(7) for the custom glyphs.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .text import GLYPHS, fit, sim_view


# ---------------------------------------------------------------------------
# The pretend screen (always present)
# ---------------------------------------------------------------------------
class SimScreen:
    """16x2 characters + a backlight flag, kept in memory."""

    def __init__(self, cols: int = 16, rows: int = 2, mirror: str | Path | None = None) -> None:
        self.cols, self.rows = cols, rows
        self.lines = [" " * cols for _ in range(rows)]
        self.backlight = True
        self.writes = 0                      # how many line updates happened (tests)
        self.mirror = Path(mirror) if mirror else None
        self._lock = threading.Lock()

    def write_line(self, row: int, text: str) -> None:
        with self._lock:
            self.lines[row] = fit(text, self.cols)
            self.writes += 1
        self._mirror()

    def set_backlight(self, on: bool) -> None:
        with self._lock:
            self.backlight = bool(on)
        self._mirror()

    def snapshot(self) -> tuple[list[str], bool]:
        with self._lock:
            return list(self.lines), self.backlight

    def render(self, ascii_only: bool = False) -> list[str]:
        """The screen as a little box of text, with a backlight indicator."""
        lines, light = self.snapshot()
        if ascii_only:
            top = "+" + "-" * self.cols + "+"
            body = [f"|{sim_view(line, True)}|" for line in lines]
            lamp = " BL:ON " if light else " BL:off"
            return [top + lamp] + body + [top]
        top = "┌" + "─" * self.cols + "┐"
        body = [f"│{sim_view(line)}│" for line in lines]
        lamp = " ☀ on" if light else " ☾ off"
        return [top + lamp] + body + ["└" + "─" * self.cols + "┘"]

    def _mirror(self) -> None:
        """--lcd-sim / [lcd] sim_mirror: keep a text copy for `watch cat`."""
        if self.mirror is None:
            return
        try:
            self.mirror.parent.mkdir(parents=True, exist_ok=True)
            self.mirror.write_text("\n".join(self.render(ascii_only=True)) + "\n", encoding="ascii", errors="replace")
        except OSError:
            self.mirror = None   # unwritable: stop trying


# ---------------------------------------------------------------------------
# Real hardware: LCD1602 + PCF8574 backpack on I2C
# ---------------------------------------------------------------------------
class LcdHardwareError(RuntimeError):
    pass


def probe_addresses(port: int = 1, candidates: list[int] | None = None) -> list[int]:
    """Which of the usual backpack addresses answer on /dev/i2c-<port>.

    Same idea as `i2cdetect -y 1`, but only for the LCD addresses
    (0x27 for PCF8574, 0x3F for PCF8574A). Needs smbus2.
    """
    try:
        from smbus2 import SMBus  # noqa: PLC0415 — only on the Pi
    except ImportError as exc:
        raise LcdHardwareError("smbus2 is not installed: pip install -r requirements-pi.txt") from exc
    found = []
    try:
        with SMBus(port) as bus:
            for addr in candidates or [0x27, 0x3F]:
                try:
                    bus.read_byte(addr)
                    found.append(addr)
                except OSError:
                    pass
    except (OSError, FileNotFoundError) as exc:
        raise LcdHardwareError(f"cannot open /dev/i2c-{port} ({exc}); enable I2C with sudo raspi-config") from exc
    return found


class RawPcf8574:
    """A minimal HD44780 driver through a PCF8574, using only smbus2.

    Used when RPLCD is not installed. The backpack wires P0=RS, P1=RW,
    P2=E (enable), P3=backlight, P4-P7 = LCD data D4-D7 (4-bit mode).
    """

    RS, EN, BL = 0x01, 0x04, 0x08
    ROW_OFFSETS = (0x00, 0x40, 0x14, 0x54)

    def __init__(self, address: int, port: int = 1) -> None:
        from smbus2 import SMBus  # noqa: PLC0415

        self.addr = address
        self.bus = SMBus(port)
        self.light = self.BL
        self._init_display()

    def _write4(self, bits: int) -> None:
        self.bus.write_byte(self.addr, bits | self.light)
        self.bus.write_byte(self.addr, bits | self.EN | self.light)   # pulse E high…
        time.sleep(0.0005)
        self.bus.write_byte(self.addr, (bits & ~self.EN) | self.light)  # …and low: latched
        time.sleep(0.0001)

    def _send(self, value: int, mode: int = 0) -> None:
        self._write4((value & 0xF0) | mode)
        self._write4(((value << 4) & 0xF0) | mode)

    def command(self, value: int) -> None:
        self._send(value, 0)
        if value in (0x01, 0x02):      # clear / home are slow
            time.sleep(0.002)

    def _init_display(self) -> None:
        time.sleep(0.05)
        for wait in (0.0045, 0.0045, 0.00015):       # the datasheet's "wake up" dance
            self._write4(0x30)
            time.sleep(wait)
        self._write4(0x20)                            # 4-bit mode
        for cmd in (0x28, 0x0C, 0x01, 0x06):          # 2 lines 5x8, display on, clear, left-to-right
            self.command(cmd)

    def create_char(self, slot: int, rows: list[int]) -> None:
        self.command(0x40 | ((slot & 7) << 3))
        for row in rows:
            self._send(row & 0x1F, self.RS)

    def write_at(self, row: int, col: int, text: str) -> None:
        self.command(0x80 | (self.ROW_OFFSETS[row] + col))
        for ch in text:
            self._send(ord(ch) & 0xFF, self.RS)

    def backlight(self, on: bool) -> None:
        self.light = self.BL if on else 0
        self.bus.write_byte(self.addr, self.light)

    def close(self) -> None:
        try:
            self.command(0x01)
            self.backlight(False)
        finally:
            self.bus.close()


class I2cLcd:
    """The real screen. start() opens it (call from the LCD thread)."""

    def __init__(self, cols: int = 16, rows: int = 2, address: int | None = None, port: int = 1,
                 driver: str = "auto", charmap: str = "A00", expander: str = "PCF8574",
                 candidates: list[int] | None = None) -> None:
        self.cols, self.rows = cols, rows
        self.address, self.port = address, port
        self.driver_name, self.charmap, self.expander = driver, charmap, expander
        self.candidates = candidates or [0x27, 0x3F]
        self.dev = None          # RPLCD CharLCD or RawPcf8574
        self.kind = ""           # "rplcd" or "smbus2"
        self.error = ""

    def describe(self) -> str:
        addr = f"0x{self.address:02X}" if self.address is not None else "auto"
        state = self.kind or ("error" if self.error else "not started")
        return f"i2c {self.expander} {addr} bus {self.port} via {state}"

    def start(self) -> None:
        if self.address is None:
            found = probe_addresses(self.port, self.candidates)
            if not found:
                raise LcdHardwareError(
                    f"no LCD backpack answered at {', '.join(hex(a) for a in self.candidates)} on bus {self.port}. "
                    "Run `i2cdetect -y 1` and set [i2c] address in config/lcd.ini")
            self.address = found[0]
        want = self.driver_name
        if want in ("auto", "rplcd"):
            try:
                from RPLCD.i2c import CharLCD  # noqa: PLC0415 — only on the Pi
            except ImportError:
                if want == "rplcd":
                    raise LcdHardwareError("RPLCD is not installed: pip install -r requirements-pi.txt")
            else:
                self.dev = CharLCD(i2c_expander=self.expander, address=self.address, port=self.port,
                                   cols=self.cols, rows=self.rows, dotsize=8, charmap=self.charmap,
                                   auto_linebreaks=False, backlight_enabled=True)
                self.kind = "rplcd"
        if self.dev is None:
            try:
                self.dev = RawPcf8574(self.address, self.port)
            except ImportError as exc:
                raise LcdHardwareError("neither RPLCD nor smbus2 is installed: pip install -r requirements-pi.txt") from exc
            self.kind = "smbus2"
        for slot, rows in GLYPHS.values():          # upload the 8 custom glyphs
            self.dev.create_char(slot, tuple(rows) if self.kind == "rplcd" else rows)

    def write_line(self, row: int, text: str) -> None:
        text = fit(text, self.cols)
        if self.kind == "rplcd":
            self.dev.cursor_pos = (row, 0)
            self.dev.write_string(text)
        else:
            self.dev.write_at(row, 0, text)

    def set_backlight(self, on: bool) -> None:
        if self.kind == "rplcd":
            self.dev.backlight_enabled = bool(on)
        else:
            self.dev.backlight(bool(on))

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            if self.kind == "rplcd":
                self.dev.backlight_enabled = False
                self.dev.close(clear=True)
            else:
                self.dev.close()
        except Exception:  # noqa: BLE001 — shutting down anyway
            pass
        self.dev = None
