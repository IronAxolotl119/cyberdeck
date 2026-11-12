"""CYBERDECK // DASH - a Rich-powered awareness dashboard for the main deck.

In plain English: this package draws a full-screen, cyberdeck-styled
dashboard in the terminal. It watches the radios around you (Wi-Fi,
Bluetooth, your own LAN, an RFID reader), writes what it finds to a
shared journal, and lets the PULSE companion pet earn XP from those
finds.

These are awareness and logging tools for personal, educational use.
They only listen passively to your own surroundings and your own
subnet. They are NOT skimmer detectors and never attack, deauth, or
join anyone's network.

Run it with:  python3 -m cyberdeck_dash        (simulation, any computer)
              python3 -m cyberdeck_dash --mode real   (on the Pi)
"""

__version__ = "0.1.0"
