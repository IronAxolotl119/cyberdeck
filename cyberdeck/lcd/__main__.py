"""python3 -m cyberdeck.lcd — try the 16x2 LCD on its own (no deck needed).

In plain English: handy right after wiring the screen to the Pi. It runs
the LCD service with a pretend PULSE and plays a short show: the custom
glyphs, a notification with a backlight flash, a scrolling line, PULSE
blurbs and the clock page.

    python3 -m cyberdeck.lcd --demo                 # sim screen, drawn in this terminal
    python3 -m cyberdeck.lcd --demo --backend i2c   # the real LCD (config/lcd.ini [i2c])
    python3 -m cyberdeck.lcd --demo --backend i2c --address 0x3F
    python3 -m cyberdeck.lcd --scan                 # which I2C address answers (0x27 / 0x3F)
    python3 -m cyberdeck.lcd --say "hello world"    # show one message for 8 seconds
    python3 -m cyberdeck.lcd --snapshot docs/lcd_sim.txt   # write demo frames as text
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace

from cyberdeck.config import Settings, repo_root

from .backends import LcdHardwareError, probe_addresses
from .messages import ALERT, NOTICE
from .service import LcdService
from .settings import load_lcd_settings, parse_address

DEMO_PET = {"name": "PULSE", "species": "Aethling", "level": 7, "mood": "happy", "hunger": 35, "energy": 82,
            "happiness": 76, "asleep": False}


def build(args) -> LcdService:
    s = load_lcd_settings(Settings.load("lcd.ini", args.config_dir))
    if args.backend:
        s = replace(s, backend=args.backend)
    if args.address:
        s = replace(s, address=args.address)
    import random

    lcd = LcdService(s, threaded=False, base_dir=repo_root(), rng=random.Random(getattr(args, "seed", None)))
    lcd.set_pet(DEMO_PET)
    lcd.set_link(True, "LINK UP")
    lcd.set_battery(87)
    return lcd


# The scripted show: (seconds from start, what to do).
def demo_script(lcd: LcdService):
    return [
        (0.0, lambda now: lcd.notify("0123456789ABCDEF", "".join(chr(i) for i in range(8)) + " TEST", 4, "test",
                                     hold=3, now=now)),
        (3.5, lambda now: lcd.pet_event("learned_move", "PULSE learned Tag Bite!",
                                        {"detail": {"move": "Tag Bite"}}, now=now)),
        (4.0, lambda now: lcd.add_blurb("PULSE sniffed out a new Wi-Fi network! (+7 xp)", now)),
        (10.0, lambda now: lcd.awareness_alert("rfid", "unknown_tag", "unknown tag", "de:ad:10:42", 3, now=now)),
        (21.0, lambda now: lcd.notify("Long notifications scroll across line two like a ticker", "{bell}NOTICE",
                                      NOTICE, "demo", now=now)),
    ]


def run_demo(lcd: LcdService, seconds: float, live: bool, out=sys.stdout) -> None:
    script = demo_script(lcd)
    if lcd.hw is not None:
        lcd._open_hw()
        if lcd.hw_error:
            print(f"lcd: {lcd.hw_error}\n(showing the sim screen here instead)", file=sys.stderr)
    start = time.time()
    last = None
    try:
        while True:
            now = time.time()
            t = now - start
            while script and script[0][0] <= t:
                script.pop(0)[1](now)
            lcd.tick(now)
            frame = lcd.screen.render()
            if live and frame != last:
                last = frame
                out.write("\x1b[H\x1b[2J" + "\n".join(frame) + f"\n\n {t:4.1f}s / {seconds:.0f}s · Ctrl+C stops\n")
                out.flush()
            if t >= seconds:
                break
            time.sleep(1.0 / lcd.s.fps)
    except KeyboardInterrupt:
        pass
    finally:
        lcd._close_hw()


def snapshot(lcd: LcdService, path: str, seconds: float = 75.0) -> list[str]:
    """Run the demo on a fake clock and save one frame of every screen shown."""
    start = 1_800_000_000.0
    script = demo_script(lcd)
    out = ["CYBERDECK//MAIN - simulated 16x2 LCD (python3 -m cyberdeck.lcd --snapshot)",
           "One frame from each screen the demo shows, 2.5 s after it appears.", ""]
    t, seen, pending = 0.0, None, None
    while t <= seconds:
        now = start + t
        while script and script[0][0] <= t:
            script.pop(0)[1](now)
        lcd.tick(now)
        here = lcd.current_screen if lcd.current is not None else lcd.page
        if here is not seen:
            seen, pending = here, t + 2.5
        if pending is not None and t >= pending:
            pending = None
            label = (f"notification ({lcd.current.kind})" if lcd.current is not None
                     else f"idle page: {lcd.page.name}")
            out.append(f"t={t:4.1f}s  {label}")
            out += ["  " + line for line in lcd.screen.render()] + [""]
        t = round(t + 1.0 / lcd.s.fps, 4)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m cyberdeck.lcd", description="Test the 16x2 LCD on its own.")
    p.add_argument("--demo", action="store_true", help="play the demo show (default action)")
    p.add_argument("--seconds", type=float, default=40, help="demo length (default 40)")
    p.add_argument("--backend", choices=("sim", "i2c"), default=None, help="override config/lcd.ini backend")
    p.add_argument("--address", default=None, help="I2C address, e.g. 0x27 or 0x3F (default: lcd.ini / auto)")
    p.add_argument("--say", default=None, metavar="TEXT", help="show this text for 8 seconds and exit")
    p.add_argument("--scan", action="store_true", help="look for the LCD backpack on I2C bus 1 and exit")
    p.add_argument("--snapshot", metavar="FILE", default=None, help="write demo frames as text to FILE and exit")
    p.add_argument("--seed", type=int, default=7, help="random seed for blurb order in --snapshot")
    p.add_argument("--config-dir", default=None, help="folder with lcd.ini (default ./config)")
    args = p.parse_args(argv)

    if args.scan:
        s = load_lcd_settings(Settings.load("lcd.ini", args.config_dir))
        try:
            found = probe_addresses(s.port, [a for a in (parse_address(c) for c in s.candidates) if a is not None])
        except LcdHardwareError as exc:
            print(f"lcd: {exc}", file=sys.stderr)
            return 1
        if not found:
            print("no LCD backpack at 0x27 / 0x3F. Check wiring, then `i2cdetect -y 1` (docs/LCD.md).")
            return 1
        print("LCD backpack found at: " + ", ".join(f"0x{a:02X}" for a in found))
        print(f"put  address = 0x{found[0]:02X}  under [i2c] in config/lcd.ini (or leave address = auto)")
        return 0
    lcd = build(args)
    if args.snapshot:
        for line in snapshot(lcd, args.snapshot):
            print(line)
        return 0
    if args.say:
        lcd._open_hw()
        lcd.say(args.say, seconds=8)
        end = time.time() + 8
        try:
            while time.time() < end:
                lcd.tick(time.time())
                time.sleep(1.0 / lcd.s.fps)
        except KeyboardInterrupt:
            pass
        print("\n".join(lcd.screen.render()))
        lcd._close_hw()
        return 0
    run_demo(lcd, args.seconds, live=sys.stdout.isatty() or lcd.hw is None)
    print("\n".join(lcd.screen.render()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
