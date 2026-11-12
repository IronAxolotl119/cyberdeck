"""Dashboard first-sightings: XP, kind-themed moves, and environment mood.

The Rich dashboard reports each new Wi-Fi / BT / LAN / RFID find with
`pet.discovery`. This module is the pet-side rules: how much XP a find is
worth, whether it teaches or strengthens a move, and how a rolling window
of recent finds becomes a busy/bored environment buff.

These numbers follow the dashboard's suggested [xp] table in
config/dash.ini (docs/DASHBOARD.md). The companion is authoritative.
"""

from __future__ import annotations

import random
import time
from typing import Any

from pet.creature import Creature
from pet.moves import MOVE_LIBRARY, get_move


DISCOVERY_KINDS = frozenset({"wifi", "bt", "lan", "rfid"})

# Base XP for a first sighting, plus INTEREST_BONUS for each interest step
# above 1 (so rfid + interest 3 = 8 + 3*2 = 14, matching the docs example).
XP_BASE = {"wifi": 4, "bt": 3, "lan": 3, "rfid": 8}
INTEREST_BONUS = 3

# Chance to teach or strengthen a move is LEARN_CHANCE * interest (capped at 1).
LEARN_CHANCE = 0.12

# Rolling window (wall-clock seconds) used for the environment busy meter.
ACTIVITY_WINDOW = 300.0
BUSY_AT = 8  # this many first-finds in the window => busy 1.0

# Each strengthen rank adds this much power. Rank is capped so a move
# cannot grow without bound.
POWER_PER_RANK = 2
MAX_RANK = 5

# Asleep pets still record the find, but XP is halved and they will not
# learn or strengthen a move until they are awake.
SLEEP_XP_FACTOR = 0.5


# Kind-themed, discovery-only move ids (see pet/moves.py).
KIND_MOVES: dict[str, tuple[str, ...]] = {
    "wifi": ("signal_sniff", "beacon_burst", "channel_hop"),
    "bt": ("blue_pulse", "tooth_echo"),
    "lan": ("ping_echo", "arp_whisper"),
    "rfid": ("tag_bite", "near_field"),
}


def clamp_interest(raw: Any) -> int:
    """Clamp interest to 1–3. Missing or unreadable values become 1."""
    if raw is None or raw is False:
        return 1
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return 1
    if value < 1:
        return 1
    if value > 3:
        return 3
    return value


def sighting_key(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def xp_for(kind: str, interest: int, *, asleep: bool = False) -> int:
    """XP for a *first* sighting of this kind and interest."""
    base = XP_BASE.get(kind, 0)
    xp = base + INTEREST_BONUS * (int(interest) - 1)
    if asleep:
        xp = int(round(xp * SLEEP_XP_FACTOR))
    return max(0, xp)


def effective_power(base_power: int, rank: int) -> int:
    rank = max(0, min(MAX_RANK, int(rank)))
    return max(0, int(base_power) + rank * POWER_PER_RANK)


def roll_move(
    kind: str,
    known_ids: list[str],
    rng: random.Random,
    interest: int,
) -> tuple[str, str] | None:
    """Maybe learn a new kind-themed move, or strengthen a known one.

    Returns ("learned", move_id), ("strengthened", move_id), or None.
    """
    if kind not in KIND_MOVES:
        return None
    chance = min(1.0, LEARN_CHANCE * max(1, int(interest)))
    if rng.random() >= chance:
        return None
    pool = list(KIND_MOVES[kind])
    unknown = [move_id for move_id in pool if move_id not in known_ids]
    if unknown:
        return ("learned", rng.choice(unknown))
    # Every themed move is already known: strengthen one of them, or any
    # known library move if the pool is somehow empty.
    choices = [move_id for move_id in pool if move_id in known_ids] or list(known_ids)
    if not choices:
        return None
    return ("strengthened", rng.choice(choices))


def environment_state(
    recent: list[tuple[float, int]],
    *,
    now: float | None = None,
    window: float = ACTIVITY_WINDOW,
    busy_at: int = BUSY_AT,
    last_find_at: float | None = None,
) -> dict[str, Any]:
    """Build the pet.status.environment object from a rolling find window.

    `recent` is a list of (timestamp, interest) for first sightings.
    busy is 0–1 (finds in the window / busy_at). buff is a short label;
    mood_bonus and speed_bonus are the temporary effects to apply.
    """
    stamp = time.monotonic() if now is None else now
    live = [(ts, interest) for ts, interest in recent if stamp - ts <= window]
    count = len(live)
    high = sum(1 for _ts, interest in live if interest >= 3)
    ratio = 0.0 if busy_at <= 0 else min(1.0, count / busy_at)

    # A long quiet stretch (no first-finds in the window, and we have been
    # quiet for at least one full window after having seen something)
    # makes the pet bored. A brand-new pet with no history is just calm.
    quiet = count == 0 and last_find_at is not None and (stamp - last_find_at) >= window
    if quiet:
        buff, mood_bonus, speed_bonus = "bored: it's been quiet", -2, 0
    elif high >= 2 or ratio >= 1.0:
        buff, mood_bonus, speed_bonus = "busy airwaves", 4, 1
    elif ratio >= 0.3 or count >= max(1, busy_at // 3):
        buff, mood_bonus, speed_bonus = "curious: sniffing around", 2, 0
    else:
        buff, mood_bonus, speed_bonus = "calm", 0, 0

    return {
        "busy": round(ratio, 3),
        "buff": buff,
        "mood_bonus": mood_bonus,
        "speed_bonus": speed_bonus,
        "recent": count,
    }


def apply_sighting(
    creature: Creature,
    *,
    kind: str,
    ident: str,
    interest: int,
    event: str | None = None,
    label: str | None = None,
    rng: random.Random | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Record a dashboard sighting and return the pet.discovery.ok body (no pet)."""
    del event  # optional flavour; the reaction uses label (or id) instead.
    ident = ident.strip()
    interest = clamp_interest(interest)
    display = (label or ident).strip() or ident
    key = sighting_key(kind, ident)
    rng = rng or random.Random()
    stamp = time.monotonic() if now is None else now

    if key in creature.sighting_keys:
        return {
            "xp": 0,
            "learned_move": None,
            "strengthened_move": None,
            "reaction": f"{creature.name} has already logged this {kind} signal.",
            "level": creature.stats.level,
            "level_up": False,
            "repeat": True,
        }

    creature.sighting_keys.add(key)
    creature.note_sighting(stamp, interest)

    asleep = bool(creature.asleep)
    xp = xp_for(kind, interest, asleep=asleep)
    before_level = creature.stats.level
    events: list[str] = []
    if xp > 0:
        events.extend(creature.add_xp(xp))
    leveled = creature.stats.level > before_level

    learned: str | None = None
    strengthened: str | None = None
    if asleep:
        reaction = (
            f"{creature.name} stirs in its sleep and files {display} away "
            f"(+{xp} XP)."
        )
    else:
        roll = roll_move(kind, list(creature.known_move_ids), rng, interest)
        if roll and roll[0] == "learned":
            move_id = roll[1]
            events.extend(creature.learn_move(move_id))
            learned = get_move(move_id).name
            reaction = f"{creature.name} learned {learned} from {display}!"
        elif roll and roll[0] == "strengthened":
            move_id = roll[1]
            rank = creature.strengthen_move(move_id)
            strengthened = get_move(move_id).name if move_id in MOVE_LIBRARY else move_id
            reaction = (
                f"{creature.name}'s {strengthened} grew stronger "
                f"(rank {rank})."
            )
        else:
            reaction = f"{creature.name} logged {display} (+{xp} XP)."

    if leveled:
        reaction += f"  LEVEL {creature.stats.level}!"

    return {
        "xp": xp,
        "learned_move": learned,
        "strengthened_move": strengthened,
        "reaction": reaction,
        "level": creature.stats.level,
        "level_up": leveled,
        "repeat": False,
        "events": events,
        "asleep": asleep,
    }
