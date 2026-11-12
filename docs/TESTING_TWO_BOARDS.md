# Testing the deck and the pet together

The main deck (this repo) and the companion pet are two separate
programs. They talk through newline-delimited JSON
([PROTOCOL.md](PROTOCOL.md)) over TCP or a serial line. This guide
goes from easiest to most real:

- **A.** Two terminals on one computer, over TCP
- **B.** The serial code on a desktop, with a virtual cable (socat)
- **C.** Two Raspberry Pis wired UART to UART

The companion can be the real pet program or `tools/fake_companion.py`.
The fake companion is the deck's own simulated pet served over a wire,
and it prints every message so you can watch the conversation.

---

## A. Two terminals on one computer (TCP)

**Terminal 1: start the companion (it is the server)**

```bash
cd cyberdeck-main
python3 tools/fake_companion.py              # listens on 127.0.0.1:7777
# Faster pet life, so hunger events come sooner:
python3 tools/fake_companion.py --speed 30 --demo-every 20
```

To use the real pet program instead, run it from its own folder with
`python3 companion.py --tcp 127.0.0.1:7777`.

**Terminal 2: start the deck and point it at the companion**

```bash
cd cyberdeck-main
python3 -m cyberdeck --link tcp               # 127.0.0.1:7777 by default
python3 -m cyberdeck --link tcp --host 192.168.1.50 --port 7777
```

The status bar should say `LINK UP · TCP 7777`. Things to try:

| Type this | What you should see |
|-----------|---------------------|
| `ping` | `pong rtt=…ms via fake_companion` (or `via sim` for the real pet) |
| `pet` / `pet feed` / `pet play` | The pet's numbers change. Terminal 1 logs `<-` and `->` lines. |
| `link` | Where it is connected, whether events are on, message counters |
| `loot`, then `pet gift 1` | The gift reaction (play `dungeon` first to find loot) |
| wait a bit | Pushed events such as "PULSE is hungry!" and "PULSE sniffed out a signal" appear on the ticker line and in `pet log` |

Now stop the companion with Ctrl-C. The deck shows `LINK DOWN`, and
`link` shows why and when it will retry. Start the companion again,
and within a few seconds the deck reconnects by itself.

**Scripted check, no full-screen UI:**

```bash
printf 'ping\npet status\npet feed\npet play\nlink\n' | python3 -m cyberdeck --headless --link tcp
```

Pushed events are printed as `[pet hungry] …` lines after each command.

To make TCP the default, set `mode = tcp` under `[link]` in
`config/hardware.ini`.

---

## B. Serial code on a desktop (socat virtual cable)

`socat` can create two connected fake serial ports. Install it with
`sudo apt install socat` (Linux) or `brew install socat` (macOS).

**Terminal 1: the virtual cable**

```bash
socat -d -d pty,raw,echo=0 pty,raw,echo=0
# … N PTY is /dev/pts/5
# … N PTY is /dev/pts/6
```

Leave it running. Note the two names; yours will differ.

**Terminal 2: the companion on one end**

```bash
python3 tools/fake_companion.py --serial /dev/pts/5
# or the real pet:  python3 companion.py --serial /dev/pts/5
```

**Terminal 3: the deck on the other end**

```bash
python3 -m cyberdeck --link serial --device /dev/pts/6
```

The status bar says `LINK UP · UART 6`. Everything from part A works the
same way. Stopping socat (unplugging the cable) makes the deck show
`LINK DOWN`. After a restart, socat gives new `/dev/pts/N` names, so
restart both programs with the new names.

The deck uses only the standard library (termios) for serial. If
`pyserial` is installed it is used instead (`serial_driver = auto` in
hardware.ini; force one with `stdlib` or `pyserial`).

---

## C. Two Raspberry Pis over UART

### 1. Wiring (3 wires, crossed)

Use the 40-pin header on both Pis. **Power both Pis off while wiring.**

| Main deck Pi | | Companion Pi |
|--------------|---|--------------|
| pin 8  (GPIO14, **TX**) | → | pin 10 (GPIO15, **RX**) |
| pin 10 (GPIO15, **RX**) | ← | pin 8  (GPIO14, **TX**) |
| pin 6  (**GND**)        | — | pin 6  (**GND**) |

- TX goes to RX both ways. TX to TX does nothing.
- A shared GND is required. Without it you get garbage or nothing.
- Both Pis use 3.3 V logic, so no level shifter is needed. **Never
  connect a 5 V serial device directly to a Pi.**
- Keep the wires short (under about 50 cm) at 115200 baud.

### 2. Turn the UART on (on BOTH Pis)

```bash
sudo raspi-config
```

1. **Interface Options → Serial Port**
2. "Would you like a login shell to be accessible over serial?" → **No**.
   Otherwise Linux prints a login prompt into your cable.
3. "Would you like the serial port hardware to be enabled?" → **Yes**
4. Finish and **reboot**.

Check the result:

```bash
ls -l /dev/serial0       # should point to ttyS0 or ttyAMA0
```

On a Pi Zero 2 W, Bluetooth normally uses the better UART (PL011), and
`/dev/serial0` is the "mini UART" (`ttyS0`). That is fine at 115200. If
you see dropped characters, give the PL011 to the header instead by
adding `dtoverlay=disable-bt` to `/boot/firmware/config.txt` (older
systems: `/boot/config.txt`) and rebooting. This turns Bluetooth off,
so skip it if the pet uses Bluetooth for scanning
(`dtoverlay=miniuart-bt` keeps Bluetooth on the mini UART instead).

### 3. Permission to use the port (on BOTH Pis)

```bash
sudo usermod -aG dialout $USER
# log out and back in (or reboot), then check:
groups                   # should list dialout
```

Without this, the deck reports
`permission denied (add yourself to the dialout group)`.

### 4. Run it

On the companion Pi:

```bash
python3 companion.py --serial /dev/serial0          # the real pet
# or: python3 tools/fake_companion.py --serial /dev/serial0
```

On the main deck Pi:

```bash
python3 -m cyberdeck --mode real --link serial      # /dev/serial0, 115200 8N1
```

`--mode real` uses the Pi's real sensors, and `--link serial` uses the
cable. To make this the default, set `mode = serial` under `[link]` in
`config/hardware.ini`.

### 5. If it does not link

| Symptom | Check |
|---------|-------|
| `no such device /dev/serial0` | The serial port hardware is not enabled (step 2), or you have not rebooted yet |
| `permission denied` | dialout group (step 3), then log out and back in |
| `no answer to hello`, retrying | TX/RX not crossed, no GND, companion not running, or different baud rates |
| Link drops, `bad_lines` climbing in `link` | Serial console still on (step 2), loose wire, or mini UART timing (try `dtoverlay=disable-bt`) |
| Works on the desktop, not on the Pis | Run `link` on the deck and check `counters`: `sent` rising while `received` stays at 0 means nothing is coming back on RX |

You can check the wire alone, without either program: run
`python3 tools/fake_companion.py --serial /dev/serial0` on one Pi and
`python3 -m cyberdeck --link serial --headless` on the other, then type
`ping`.
