"""statusline.py — squeeze the status line into whatever width there is.

The line under the title shows temperature, CPU, memory, battery, wifi,
and the companion link, plus an alert badge on the right ("! HUNGRY").
At 80 columns it does not all fit at full length, so each item has a
priority and a few shorter spellings:

    DECK-NET -53dBm  ->  -53dBm  ->  W-53
    CPU 17%          ->  C17%
    LINK UP · TCP 7777 -> LINK UP

fit() shortens the least important item first, then drops items
entirely, so the important ones (link, battery, badge) stay readable
instead of being chopped off mid-word.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    priority: int            # higher = keep longer
    forms: list[str]         # longest first; empty strings are skipped

    def __post_init__(self) -> None:
        self.forms = [f for f in self.forms if f] or [""]


def fit(items: list[Item], width: int, gap: str = "  ") -> str:
    """Join items in their original order, shortened/dropped to fit width."""
    choice = [0] * len(items)        # which form each item uses
    alive = [bool(item.forms[0]) for item in items]

    def text() -> str:
        return gap.join(items[i].forms[choice[i]] for i in range(len(items)) if alive[i])

    while len(text()) > width:
        # 1) shorten the least important item that still can be shortened
        shorter = [i for i in range(len(items)) if alive[i] and choice[i] + 1 < len(items[i].forms)]
        if shorter:
            i = min(shorter, key=lambda k: (items[k].priority, -k))
            choice[i] += 1
            continue
        # 2) otherwise drop the least important item
        living = [i for i in range(len(items)) if alive[i]]
        if len(living) <= 1:
            return text()[:width]
        alive[min(living, key=lambda k: (items[k].priority, -k))] = False
    return text()


def status_items(reading, link_label: str) -> list[Item]:
    """The standard status line, most important = link, battery, temperature."""
    cpu, mem = reading.cpu_percent, reading.mem_percent
    temp = reading.cpu_temp_c
    bat = reading.battery_percent
    plus = "+" if reading.charging else ""
    link_short = link_label.split(" · ")[0]
    wifi_full = reading.wifi_label()
    dbm = reading.wifi_dbm
    return [
        Item(6, [reading.temp_label(), f"{temp:.0f}C" if temp is not None else "T--"]),
        Item(3, [f"CPU {cpu:.0f}%" if cpu is not None else "CPU --", f"C{cpu:.0f}%" if cpu is not None else ""]),
        Item(2, [f"MEM {mem:.0f}%" if mem is not None else "MEM --", f"M{mem:.0f}%" if mem is not None else ""]),
        Item(8, [reading.battery_label(), f"B{bat:.0f}%{plus}" if bat is not None else "B--"]),
        Item(4, [wifi_full, f"{dbm}dBm" if dbm is not None else "", f"W{dbm}" if dbm is not None else ""]),
        Item(10, [link_label, link_short]),
    ]
