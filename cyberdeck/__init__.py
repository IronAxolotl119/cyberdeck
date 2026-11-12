"""cyberdeck — the main-board terminal interface package.

This folder is the Python package you run with `python -m cyberdeck`.
It is the "main" side of a two-board setup: this program draws the
screen, takes commands, and talks to a companion board that hosts a
Digimon-style pet.

Everything here is written to run on a normal Linux terminal first
(simulation mode). Later, the same code can run on a Raspberry Pi
Zero 2 W and speak to the companion over a real serial link.
"""

__version__ = "0.1.0"
__app_name__ = "CYBERDECK"
