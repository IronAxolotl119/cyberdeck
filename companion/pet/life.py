"""The pet's inner life: bond, memory, moods, habits, and things it does alone.

``Life`` is attached to every Creature as ``creature.life`` and saved with it.
It never reads the clock or a global RNG on its own: every method takes
``now`` (epoch seconds, box-local dates) and an ``rng`` so tests can pin
time and randomness. ``update()`` is called from the link handler's tick and
appends ``(event, detail)`` pairs to ``outbox``; the handler turns those into
``pet.event`` pushes.

Wall-clock seconds drive the things a person notices (idle animations,
asks, tantrums, adventures, rivals). The simulated-hours clock in
Creature.tick still drives hunger, energy, age, and evolution.
"""

from __future__ import annotations

import random
import zlib
from typing import Any

from pet import calendar as cal
from pet import stories
from pet.speech import LinePicker, Voice, fill

# ---------------------------------------------------------------------------
# Tunables (seconds unless noted). Life.pace scales the "how often" knobs.
# ---------------------------------------------------------------------------
IDLE_AFTER = 60.0
IDLE_EVERY = (60.0, 180.0)
CHATTER_EVERY = (150.0, 360.0)
DREAM_EVERY = (120.0, 300.0)
QUIRK_CHECK = 60.0
ASK_CHECK = 30.0
ASK_COOLDOWN = 900.0
ASK_TIMEOUT = 240.0
IGNORED_FOR_TANTRUM = 3
SCOLDS_FOR_TANTRUM = 3
SCOLD_WINDOW = 600.0
TANTRUM_SECONDS = 60.0
BUBBLE_TTL = 12.0
GIFT_CHECK = 600.0
ADVENTURE_IDLE = 300.0
ADVENTURE_CHECK = 120.0
ADVENTURE_LEN = (180.0, 480.0)
RIVAL_FIRST = (3600.0, 7200.0)
RIVAL_EVERY = (10800.0, 21600.0)
RIVAL_WAIT = 300.0
JEALOUS_ACTIVITIES = 4
JEALOUS_AFTER = 600.0
LONELY_AFTER = 60.0
AWARE_CHECK = 60.0
BORED_WINDOW = 1800.0
OVERFEED_WINDOW = 1200.0
OVERFEED_FOR_SICK = 3
GLITCH_SELF_HEAL = 7200.0
NETWORK_COUNT_GAP = 60.0
KNOWN_AT = 3
TURF_AT = 30
RECOGNIZE_GAP = 1800.0

BOND_START = 20.0
OBEDIENCE_START = 50.0
BOND_TIERS = ((90, "soulmate"), (70, "bonded"), (45, "close"), (20, "friendly"), (0, "stranger"))

FLAVORS = ("sweet", "spicy", "crunchy", "salty", "sour", "fruity", "electric", "cheesy", "savory", "minty")
SIGNAL_KINDS = ("wifi", "bt", "lan", "rfid")
_FLAVOR_WORDS = {
    "sweet": ("candy", "cake", "cookie", "honey", "sugar", "donut", "pudding", "choco", "sweet", "gummy", "pie"),
    "spicy": ("chili", "pepper", "hot", "spicy", "curry", "salsa", "wasabi"),
    "crunchy": ("chip", "cracker", "crunch", "nut", "biscuit", "crumble", "toast"),
    "salty": ("salt", "pretzel", "jerky", "fries", "popcorn", "ration"),
    "sour": ("lemon", "lime", "sour", "pickle", "vinegar"),
    "fruity": ("apple", "berry", "fruit", "banana", "grape", "melon", "peach", "mango"),
    "electric": ("volt", "spark", "battery", "charge", "ion", "static", "zap", "watt"),
    "cheesy": ("cheese", "pizza", "nacho", "mac"),
    "savory": ("noodle", "ramen", "soup", "meat", "steak", "sushi", "rice", "bread"),
    "minty": ("mint", "menthol", "cool", "frost", "ice"),
}
DANCES = ("robot shuffle", "happy wiggle", "antenna twirl", "pixel shuffle", "moonwalk", "spin cycle", "bounce", "jazz hands")
SNEEZES = ("tiny", "explosive", "static", "double")
IDLE_ACTIONS = ("look_around", "yawn", "doze", "chase_bug", "stretch", "hum", "stare", "show_off", "dance", "count", "talk_router", "night")

PLACE_HINTS = (
    (("cafe", "coffee", "bean", "espresso", "starbucks", "latte", "brew"), "the café"),
    (("home", "house", "family", "casa", "apartment", "apt", "flat"), "home"),
    (("lab", "office", "corp", "work", "staff", "enterprise", "hq"), "work"),
    (("school", "edu", "campus", "student", "uni", "college"), "school"),
    (("library", "lib-", "books"), "the library"),
    (("hotel", "inn", "resort", "lodge"), "a hotel"),
    (("airport", "terminal", "gate"), "the airport"),
    (("guest", "visitor", "public", "free"), "a guest spot"),
    (("kiosk", "shop", "store", "mall", "market"), "the shops"),
    (("gym", "fitness"), "the gym"),
    (("park", "garden"), "the park"),
)
TREASURE_HINTS = ("hidden", "??", "beacon", "mystery", "secret", "unknown", "ghost")


def _uniform(rng: random.Random, span: tuple[float, float], pace: float = 1.0) -> float:
    return rng.uniform(span[0], span[1]) / max(0.05, pace)


def _stable_index(text: str, size: int) -> int:
    return zlib.crc32(text.lower().encode("utf-8")) % max(1, size)


def flavor_of(item: str) -> str:
    """Map a food name to a flavor (keywords first, then a stable hash)."""
    lowered = item.lower()
    for flavor, words in _FLAVOR_WORDS.items():
        if any(w in lowered for w in words):
            return flavor
    return FLAVORS[_stable_index(lowered, len(FLAVORS))]


def place_label(name: str, night_share: float = 0.0, count: int = 0) -> str:
    """Guess what kind of place a network belongs to from its name."""
    lowered = name.lower()
    for words, label in PLACE_HINTS:
        if any(w in lowered for w in words):
            return label
    if count >= KNOWN_AT and night_share >= 0.5:
        return "home"
    return f"the {name} place"


def is_treasure_name(kind: str, name: str, rssi: int | None = None) -> bool:
    """Rare signals: hidden SSIDs, odd beacons and names, very loud signals."""
    lowered = (name or "").strip().lower()
    if kind == "wifi" and not lowered:
        return True
    if any(h in lowered for h in TREASURE_HINTS):
        return True
    if rssi is not None and rssi >= -33:
        return True
    return False


def tier_for(bond: float) -> str:
    for floor, name in BOND_TIERS:
        if bond >= floor:
            return name
    return "stranger"


class Life:
    """All aliveness state for one pet (JSON round-trippable)."""

    def __init__(self, data: dict[str, Any] | None = None, now: float = 0.0) -> None:
        d = dict(data or {})
        self.seed: int = int(d.get("seed", 0))
        self.hatched_at: float = float(d.get("hatched_at", now))
        self.nickname: str | None = d.get("nickname") or None
        self.renamed_at: float = float(d.get("renamed_at", 0.0))
        self.bond: float = float(d.get("bond", BOND_START))
        self.obedience: float = float(d.get("obedience", OBEDIENCE_START))
        self.modifiers: list[dict[str, Any]] = [dict(m) for m in d.get("modifiers") or []]
        self.prefs: dict[str, str] = dict(d.get("prefs") or {})
        self.known_prefs: list[str] = list(d.get("known_prefs") or [])
        self.quirks: dict[str, str] = dict(d.get("quirks") or {})
        self.sick: dict[str, Any] | None = dict(d["sick"]) if d.get("sick") else None
        self.overfeeds: list[float] = [float(x) for x in d.get("overfeeds") or []]
        self.recent_actions: list[list[Any]] = [list(x) for x in d.get("recent_actions") or []]
        self.scolds: list[float] = [float(x) for x in d.get("scolds") or []]
        self.praises: list[float] = [float(x) for x in d.get("praises") or []]
        self.ignored_asks: int = int(d.get("ignored_asks", 0))
        self.tantrum_until: float = float(d.get("tantrum_until", 0.0))
        self.ask: dict[str, Any] | None = dict(d["ask"]) if d.get("ask") else None
        self.last_interaction_at: float = float(d.get("last_interaction_at", now))
        self.last_seen: float = float(d.get("last_seen", 0.0))
        self.last_misbehave_at: float = float(d.get("last_misbehave_at", 0.0))
        self.last_good_at: float = float(d.get("last_good_at", 0.0))
        self.schedule: list[int] = [int(x) for x in (d.get("schedule") or [0] * 24)][:24]
        self.schedule += [0] * (24 - len(self.schedule))
        self.sessions: int = int(d.get("sessions", 0))
        self.last_session_day: str = str(d.get("last_session_day", ""))
        self.networks: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (d.get("networks") or {}).items()}
        self.treasures: list[str] = list(d.get("treasures") or [])
        self.shelf: list[dict[str, Any]] = [dict(x) for x in d.get("shelf") or []]
        self.diary: list[dict[str, str]] = [dict(x) for x in d.get("diary") or []]
        self.day: dict[str, Any] = dict(d.get("day") or {})
        self.recent: list[list[Any]] = [list(x) for x in d.get("recent") or []]
        self.adventure: dict[str, Any] | None = dict(d["adventure"]) if d.get("adventure") else None
        self.adventures: int = int(d.get("adventures", 0))
        self.rival: dict[str, Any] = dict(d.get("rival") or {})
        self.celebrated: list[str] = list(d.get("celebrated") or [])
        self.generation: int = int(d.get("generation", 1))
        self.lineage: list[dict[str, Any]] = [dict(x) for x in d.get("lineage") or []]
        self.legacy: dict[str, Any] | None = dict(d["legacy"]) if d.get("legacy") else None
        self.egg: dict[str, Any] | None = dict(d["egg"]) if d.get("egg") else None
        self.gifts_given: int = int(d.get("gifts_given", 0))
        # Lifetime battle record (pet.combat.end): wins, losses, flees, bosses.
        self.record: dict[str, int] = {k: int(v) for k, v in (d.get("record") or {}).items()}
        self.activity_since_pet: int = int(d.get("activity_since_pet", 0))
        self.last_activity: str = str(d.get("last_activity", ""))
        self.weather: dict[str, Any] | None = dict(d["weather"]) if d.get("weather") else None
        self.cooldowns: dict[str, float] = {k: float(v) for k, v in (d.get("cooldowns") or {}).items()}
        self.last_care_mistakes: int = int(d.get("last_care_mistakes", 0))
        self.picker = LinePicker(d.get("picker") or {})
        # Runtime-only state (not saved).
        self.pace: float = 1.0
        self.outbox: list[tuple[str, dict[str, Any]]] = []
        self.bubble: dict[str, Any] | None = None
        self.anim: dict[str, Any] | None = None
        self.last_now: float = now
        self.link_up: bool = False
        self.separated_at: float | None = None
        self.pending_greeting: dict[str, Any] | None = None
        self.last_scan: dict[str, Any] = {"count": None, "empty_streak": 0, "place": None, "investigate": None}
        self.temp: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Construction / persistence
    # ------------------------------------------------------------------

    @classmethod
    def new(cls, rng: random.Random, now: float, legacy: dict[str, Any] | None = None) -> "Life":
        """A freshly hatched life with seeded preferences, quirks, and rival."""
        life = cls(now=now)
        life.seed = rng.randrange(1 << 30)
        life.hatched_at = float(now)
        life.last_interaction_at = float(now)
        fav, hated = rng.sample(FLAVORS, 2)
        fav_sig, hated_sig = rng.sample(SIGNAL_KINDS, 2)
        life.prefs = {"fav_food": fav, "hated_food": hated, "fav_signal": fav_sig, "hated_signal": hated_sig}
        life.quirks = {
            "catchphrase": stories.catchphrase(rng),
            "dance": rng.choice(DANCES),
            "sneeze": rng.choice(SNEEZES),
            "tic": rng.choice(stories.pool("tic")),
            "fav_idle": rng.choice(IDLE_ACTIONS[:6]),
        }
        life.rival = {
            "name": stories.rival_name(rng),
            "wins": 0,
            "losses": 0,
            "next_at": now + rng.uniform(*RIVAL_FIRST),
            "active": None,
        }
        life.day = {"date": cal.local_dt(now).date().isoformat(), "c": {}, "h": {}}
        if legacy:
            life.apply_legacy(legacy)
        return life

    def apply_legacy(self, legacy: dict[str, Any]) -> None:
        """Inherit a trait from an egg; legacy pets start with a warmer bond."""
        self.legacy = dict(legacy)
        self.generation = int(legacy.get("generation", 1))
        trait = legacy.get("trait")
        value = legacy.get("trait_value")
        if trait == "catchphrase" and value:
            self.quirks["catchphrase"] = str(value)
        elif trait == "fav_food" and value:
            self.prefs["fav_food"] = str(value)
            if self.prefs.get("hated_food") == value:
                self.prefs["hated_food"] = next(f for f in FLAVORS if f != value)
        elif trait == "dance" and value:
            self.quirks["dance"] = str(value)
        self.bond = BOND_START + 10.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, now: float, rng: random.Random | None = None) -> "Life":
        """Load saved life; an old save without one gets a fresh life."""
        if not data:
            return cls.new(rng or random.Random(), now)
        life = cls(data, now=now)
        if not life.prefs or not life.quirks or not life.rival:
            fresh = cls.new(rng or random.Random(life.seed or None), now)
            life.prefs = life.prefs or fresh.prefs
            life.quirks = life.quirks or fresh.quirks
            life.rival = life.rival or fresh.rival
        if not life.day:
            life.day = {"date": cal.local_dt(now).date().isoformat(), "c": {}, "h": {}}
        return life

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "hatched_at": self.hatched_at,
            "nickname": self.nickname,
            "renamed_at": self.renamed_at,
            "bond": round(self.bond, 3),
            "obedience": round(self.obedience, 3),
            "modifiers": [dict(m) for m in self.modifiers],
            "prefs": dict(self.prefs),
            "known_prefs": list(self.known_prefs),
            "quirks": dict(self.quirks),
            "sick": dict(self.sick) if self.sick else None,
            "overfeeds": list(self.overfeeds),
            "recent_actions": [list(x) for x in self.recent_actions],
            "scolds": list(self.scolds),
            "praises": list(self.praises),
            "ignored_asks": self.ignored_asks,
            "tantrum_until": self.tantrum_until,
            "ask": dict(self.ask) if self.ask else None,
            "last_interaction_at": self.last_interaction_at,
            "last_seen": self.last_seen,
            "last_misbehave_at": self.last_misbehave_at,
            "last_good_at": self.last_good_at,
            "schedule": list(self.schedule),
            "sessions": self.sessions,
            "last_session_day": self.last_session_day,
            "networks": {k: dict(v) for k, v in sorted(self.networks.items())},
            "treasures": list(self.treasures),
            "shelf": [dict(x) for x in self.shelf],
            "diary": [dict(x) for x in self.diary],
            "day": {"date": self.day.get("date"), "c": dict(self.day.get("c") or {}), "h": dict(self.day.get("h") or {})},
            "recent": [list(x) for x in self.recent],
            "adventure": dict(self.adventure) if self.adventure else None,
            "adventures": self.adventures,
            "rival": dict(self.rival),
            "celebrated": list(self.celebrated),
            "generation": self.generation,
            "lineage": [dict(x) for x in self.lineage],
            "legacy": dict(self.legacy) if self.legacy else None,
            "egg": dict(self.egg) if self.egg else None,
            "gifts_given": self.gifts_given,
            "record": dict(self.record),
            "activity_since_pet": self.activity_since_pet,
            "last_activity": self.last_activity,
            "weather": dict(self.weather) if self.weather else None,
            "cooldowns": dict(sorted(self.cooldowns.items())),
            "last_care_mistakes": self.last_care_mistakes,
            "picker": self.picker.to_dict(),
        }

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def display_name(self, default: str = "PULSE") -> str:
        return self.nickname or default

    def emit(self, event: str, detail: dict[str, Any] | None = None) -> None:
        self.outbox.append((event, dict(detail or {})))

    def take_outbox(self) -> list[tuple[str, dict[str, Any]]]:
        out, self.outbox = self.outbox, []
        return out

    def ready(self, key: str, now: float) -> bool:
        return now >= self.cooldowns.get(key, 0.0)

    def cool(self, key: str, now: float, seconds: float) -> None:
        self.cooldowns[key] = now + seconds

    def count(self, counter: str, n: int = 1) -> None:
        c = self.day.setdefault("c", {})
        c[counter] = int(c.get(counter, 0)) + n

    def highlight(self, key: str, value: Any) -> None:
        h = self.day.setdefault("h", {})
        if value is not None and not h.get(key):
            h[key] = value

    def remember(self, kind: str, label: str, now: float) -> None:
        self.recent.append([kind, str(label), now])
        self.recent = self.recent[-24:]

    def time_of_day(self, now: float) -> str:
        return cal.time_of_day(cal.local_dt(now))

    # ------------------------------------------------------------------
    # Bond, obedience, moods
    # ------------------------------------------------------------------

    def add_bond(self, delta: float) -> None:
        self.bond = max(0.0, min(100.0, self.bond + delta))

    def add_obedience(self, delta: float) -> None:
        self.obedience = max(0.0, min(100.0, self.obedience + delta))

    def bond_tier(self) -> str:
        return tier_for(self.bond)

    def add_modifier(self, kind: str, amount: float, seconds: float, now: float) -> None:
        """Add or refresh a lingering mood modifier that fades linearly to 0."""
        for mod in self.modifiers:
            if mod.get("kind") == kind:
                mod.update({"amount": float(amount), "start": now, "until": now + seconds})
                return
        self.modifiers.append({"kind": kind, "amount": float(amount), "start": now, "until": now + seconds})
        self.modifiers = self.modifiers[-12:]

    @staticmethod
    def _mod_value(mod: dict[str, Any], now: float) -> float:
        start, until = float(mod.get("start", now)), float(mod.get("until", now))
        if now >= until:
            return 0.0
        span = max(1.0, until - start)
        return float(mod.get("amount", 0.0)) * min(1.0, (until - now) / span)

    def active_modifiers(self, now: float | None = None) -> list[dict[str, Any]]:
        now = self.last_now if now is None else now
        out = []
        for mod in self.modifiers:
            value = self._mod_value(mod, now)
            if abs(value) >= 0.5:
                out.append({"kind": mod["kind"], "amount": round(value, 1), "seconds_left": int(float(mod["until"]) - now)})
        return out

    def mood_offset(self, now: float | None = None) -> float:
        now = self.last_now if now is None else now
        return sum(self._mod_value(m, now) for m in self.modifiers)

    def prune(self, now: float) -> None:
        self.modifiers = [m for m in self.modifiers if float(m.get("until", 0)) > now]
        self.scolds = [t for t in self.scolds if now - t <= SCOLD_WINDOW]
        self.praises = [t for t in self.praises if now - t <= 120.0]
        self.overfeeds = [t for t in self.overfeeds if now - t <= OVERFEED_WINDOW]
        self.recent_actions = [a for a in self.recent_actions if now - float(a[2]) <= BORED_WINDOW]

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    def speech_mood(self, creature: Any, now: float) -> str:
        if self.sick:
            return "sick"
        stats = creature.stats
        mood = stats.mood + self.mood_offset(now)
        if stats.hunger <= 25:
            return "hungry"
        if stats.energy <= 22:
            return "tired"
        if mood <= 22:
            return "grumpy"
        if mood <= 38:
            return "sad"
        status = creature.evolution_status() if hasattr(creature, "evolution_status") else {}
        if status.get("ready"):
            return "excited"
        if mood >= 72:
            return "happy"
        if now - self.last_interaction_at > 900:
            return "bored"
        return "content"

    def slots(self, creature: Any, now: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Template slots shared by every line."""
        moves = creature.known_moves() if hasattr(creature, "known_moves") else []
        rival = self.rival.get("name") if self.rival else None
        s: dict[str, Any] = {
            "name": self.display_name(),
            "nickname": self.display_name(),
            "species": creature.species.name,
            "level": creature.stats.level,
            "move": moves[-1].name if moves else "Chirp",
            "network": self.favorite_network() or "that network",
            "rival": rival,
            "rival_like": stories.rival_name(random.Random(self.seed + 7)),
            "flavor": self.prefs.get("fav_food", "tasty"),
            "food": (self.day.get("h") or {}).get("food") or "snack",
            "hours": max(1, int((now - self.hatched_at) // 3600)),
            "count": max(1, len(self.shelf)),
            "trinket": self.favorite_trinket() or "pebble",
            "dance": self.quirks.get("dance", "happy wiggle"),
            "weather": (self.weather or {}).get("condition") or "nice",
            "catchphrase": self.quirks.get("catchphrase"),
            "place": self.current_place() or "somewhere new",
            "time": self.usual_time_label() or "the usual time",
        }
        if extra:
            s.update({k: v for k, v in extra.items() if v is not None})
        return s

    def say(
        self,
        creature: Any,
        key: str,
        rng: random.Random,
        now: float,
        extra: dict[str, Any] | None = None,
        *,
        mood: str | None = None,
        decorate: bool = True,
        ttl: float = BUBBLE_TTL,
    ) -> str:
        """Speak a line from a pool in this pet's voice and show it as a bubble."""
        voice = Voice(self.picker, rng)
        dt = cal.local_dt(now)
        text = voice.speak(
            key,
            self.slots(creature, now, extra),
            mood=mood or self.speech_mood(creature, now),
            stage=creature.species.stage,
            time_of_day="night" if cal.is_late_night(dt) else cal.time_of_day(dt),
            tic=self.quirks.get("tic"),
            catchphrase=self.quirks.get("catchphrase"),
            decorate=decorate,
        )
        if text:
            self.bubble = {"text": text, "category": key, "at": now, "until": now + ttl}
        return text

    def current_bubble(self, now: float | None = None) -> dict[str, Any] | None:
        now = self.last_now if now is None else now
        if self.bubble and now < float(self.bubble.get("until", 0)):
            return {"text": self.bubble["text"], "category": self.bubble.get("category")}
        return None

    def set_anim(self, name: str, now: float, seconds: float = 6.0, face: str | None = None) -> None:
        self.anim = {"name": name, "until": now + seconds, "face": face}

    def current_anim(self, now: float | None = None) -> dict[str, Any] | None:
        now = self.last_now if now is None else now
        if self.anim and now < float(self.anim.get("until", 0)):
            return dict(self.anim)
        return None

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def in_tantrum(self, now: float | None = None) -> bool:
        now = self.last_now if now is None else now
        return now < self.tantrum_until

    def away(self) -> bool:
        return bool(self.adventure)

    def costume(self, now: float | None = None) -> str | None:
        """party on a birthday / hatchday, else today's holiday key (or None)."""
        now = self.last_now if now is None else now
        today = cal.local_dt(now).date()
        hatched = cal.local_dt(self.hatched_at).date()
        if cal.is_birthday(hatched, today) or cal.is_hatchday(hatched, today):
            return "party"
        return cal.holiday_for(today)

    def favorite_network(self) -> str | None:
        if not self.networks:
            return None
        return max(self.networks.items(), key=lambda kv: (kv[1].get("count", 0), kv[0]))[0]

    def favorite_trinket(self) -> str | None:
        if not self.shelf:
            return None
        ranked = sorted(self.shelf, key=lambda t: (-int(t.get("rarity", 1)), float(t.get("at", 0))))
        return ranked[0]["name"]

    def current_place(self) -> str | None:
        return self.last_scan.get("place")

    def usual_hour(self) -> int | None:
        """The hour you usually show up, once there is a clear habit."""
        if self.sessions < 4:
            return None
        hour = max(range(24), key=lambda h: self.schedule[h])
        count = self.schedule[hour]
        if count >= 3 and count >= 0.3 * self.sessions:
            return hour
        return None

    def usual_time_label(self) -> str | None:
        hour = self.usual_hour()
        if hour is None:
            return None
        return f"{(hour % 12) or 12}{'am' if hour < 12 else 'pm'}"

    def known_preferences(self) -> dict[str, Any]:
        return {key: self.prefs[key] for key in self.known_prefs if key in self.prefs}

    def reveal(self, key: str) -> bool:
        if key not in self.known_prefs:
            self.known_prefs.append(key)
            return True
        return False

    # ------------------------------------------------------------------
    # Hooks from the link handler
    # ------------------------------------------------------------------

    def touch(self, now: float) -> None:
        """Any caretaker action: resets idle and jealousy counters."""
        self.last_interaction_at = now
        self.activity_since_pet = 0

    def note_misbehave(self, now: float) -> None:
        self.last_misbehave_at = now

    def note_good(self, now: float) -> None:
        self.last_good_at = now

    def repeats(self, kind: str, item: str, now: float) -> int:
        """How many times in a row (inside the window) this same item was used."""
        n = 0
        for k, it, at in reversed(self.recent_actions):
            if k != kind:
                continue
            if str(it).lower() == item.lower() and now - float(at) <= BORED_WINDOW:
                n += 1
            else:
                break
        return n

    def note_action(self, kind: str, item: str, now: float) -> None:
        self.recent_actions.append([kind, item, now])
        self.recent_actions = self.recent_actions[-16:]

    @staticmethod
    def boredom_factor(repeats_before: int) -> float:
        """1st/2nd full effect, then 0.75, 0.5, 0.25, and the 6th in a row is refused."""
        table = (1.0, 1.0, 0.75, 0.5, 0.25)
        return table[repeats_before] if repeats_before < len(table) else 0.0

    def fulfill(self, kinds: tuple[str, ...], creature: Any, rng: random.Random, now: float) -> str | None:
        """If the open ask is one of kinds, thank the user and grow the bond."""
        if not self.ask or self.ask.get("kind") not in kinds:
            return None
        kind = self.ask.get("kind")
        self.ask = None
        self.ignored_asks = 0
        self.add_bond(3.0)
        self.add_modifier("answered", 6.0, 600.0, now)
        self.count("asks_answered")
        self.note_good(now)
        text = self.say(creature, "ask.thanks", rng, now)
        self.emit("ask_answered", {"kind": kind, "say": text})
        return text

    def on_feed(self, creature: Any, item: str, rng: random.Random, now: float) -> dict[str, Any]:
        """Decide how a meal goes: {allowed, factor, flavor, reaction, say}."""
        self.prune(now)
        flavor = flavor_of(item)
        reps = self.repeats("feed", item, now)
        factor = self.boredom_factor(reps)
        extra = {"food": item, "flavor": flavor, "count": reps + 1}
        if factor <= 0:
            self.note_misbehave(now)
            say = self.say(creature, "feed.refuse", rng, now, extra)
            return {"allowed": False, "factor": 0.0, "flavor": flavor, "reaction": "refuse", "say": say}
        stuffed = creature.stats.hunger >= 90
        self.note_action("feed", item, now)
        self.touch(now)
        self.count("fed")
        self.highlight("food", item)
        self.remember("food", item, now)
        reaction = "normal"
        if flavor == self.prefs.get("fav_food"):
            reaction = "like"
            self.add_modifier("favorite", 8.0, 900.0, now)
            self.add_bond(1.5)
            if self.reveal("fav_food"):
                self.emit("preference_learned", {"likes": flavor, "food": item})
        elif flavor == self.prefs.get("hated_food"):
            reaction = "hate"
            self.add_modifier("yucky", -6.0, 600.0, now)
            if self.reveal("hated_food"):
                self.emit("preference_learned", {"dislikes": flavor, "food": item})
        elif factor < 1.0:
            reaction = "bored"
        if stuffed:
            reaction = "stuffed"
            self.overfeeds.append(now)
            recent = [t for t in self.overfeeds if now - t <= OVERFEED_WINDOW]
            if len(recent) >= OVERFEED_FOR_SICK and not self.sick:
                self.make_sick(creature, "glitch", rng, now)
        elif creature.stats.hunger <= 35:
            self.add_bond(1.0)
        if creature.asleep:
            reaction = "asleep"
        say = self.say(creature, f"feed.{reaction}", rng, now, extra)
        self.fulfill(("food",), creature, rng, now)
        return {"allowed": True, "factor": factor, "flavor": flavor, "reaction": reaction, "say": say}

    def on_play(self, creature: Any, game: str, stat: str, rng: random.Random, now: float) -> dict[str, Any]:
        self.prune(now)
        extra: dict[str, Any] = {"game": game, "stat": stat}
        if self.sick:
            return {"allowed": False, "factor": 0.0, "reason": "sick", "say": self.say(creature, "play.sick", rng, now, extra)}
        reps = self.repeats("play", game, now)
        factor = self.boredom_factor(reps)
        extra["count"] = reps + 1
        if factor <= 0:
            self.note_misbehave(now)
            return {"allowed": False, "factor": 0.0, "reason": "bored", "say": self.say(creature, "play.refuse", rng, now, extra)}
        self.note_action("play", game, now)
        self.touch(now)
        self.count("played")
        self.highlight("game", game)
        self.add_bond(0.8 * factor)
        self.note_good(now)
        say = self.say(creature, "play.fun" if factor >= 1.0 else "play.bored", rng, now, extra)
        self.fulfill(("play", "attention"), creature, rng, now)
        return {"allowed": True, "factor": factor, "reason": None, "say": say}

    def on_rest(self, creature: Any, rng: random.Random, now: float, sleep: bool | None = None) -> str:
        self.touch(now)
        key = "rest.ok" if sleep is None else ("sleep.on" if sleep else "sleep.off")
        if sleep:
            self.count("slept")
        text = self.say(creature, key, rng, now)
        self.fulfill(("rest",), creature, rng, now)
        return text

    def on_gift(self, creature: Any, kind: str, name: str, rng: random.Random, now: float) -> str | None:
        self.touch(now)
        self.count("gifts_got")
        self.add_bond(1.0)
        if kind == "trinket":
            self.add_to_shelf(name, "gift", 1, now)
        if kind == "toy" and self.in_tantrum(now):
            self.tantrum_until = now  # a toy bribe ends the tantrum
            self.cooldowns["tantrum_over"] = now
            self.emit("tantrum_over", {"say": self.say(creature, "tantrum.over", rng, now)})
        if kind == "medicine":
            return self.heal(creature, rng, now)
        wanted = {"food": "food", "battery": "rest"}.get(kind, "attention")
        self.fulfill((wanted, "attention"), creature, rng, now)
        return None

    def on_praise(self, creature: Any, rng: random.Random, now: float) -> dict[str, Any]:
        self.prune(now)
        recent = len(self.praises)
        self.praises.append(now)
        self.touch(now)
        self.count("praised")
        if recent >= 3:
            return {"obedience": round(self.obedience), "say": self.say(creature, "praise.spam", rng, now), "effect": "spam"}
        deserved = self.last_good_at > 0 and now - self.last_good_at <= 180
        self.add_obedience(4.0 if deserved else 2.0)
        self.add_bond(1.5 if deserved else 0.8)
        self.add_modifier("praised", 6.0, 600.0, now)
        key = "praise.low_bond" if self.bond < 20 else "praise.normal"
        text = self.say(creature, key, rng, now)
        self.fulfill(("attention",), creature, rng, now)
        return {"obedience": round(self.obedience), "say": text, "effect": "deserved" if deserved else "kind"}

    def on_scold(self, creature: Any, rng: random.Random, now: float) -> dict[str, Any]:
        self.prune(now)
        self.scolds.append(now)
        self.touch(now)
        self.count("scolded")
        deserved = self.last_misbehave_at > 0 and now - self.last_misbehave_at <= 180
        if deserved:
            self.add_obedience(5.0)
            self.add_bond(-0.5)
            self.add_modifier("scolded", -4.0, 300.0, now)
            key = "scold.deserved"
        else:
            self.add_obedience(-2.0)
            self.add_bond(-2.0)
            self.add_modifier("scolded", -10.0, 900.0, now)
            key = "scold.sulky" if len(self.scolds) >= 2 else "scold.undeserved"
        text = self.say(creature, key, rng, now)
        started = False
        if len(self.scolds) >= SCOLDS_FOR_TANTRUM and not self.in_tantrum(now):
            started = True
            self.start_tantrum(creature, rng, now, "scolded")
            self.scolds = []
        return {"obedience": round(self.obedience), "say": text, "deserved": deserved, "tantrum": started}

    def on_rename(self, creature: Any, new_name: str, rng: random.Random, now: float) -> dict[str, Any]:
        old = self.display_name()
        again = bool(self.renamed_at) and now - self.renamed_at <= 600
        self.nickname = new_name
        self.renamed_at = now
        self.touch(now)
        self.count("renamed")
        if again:
            key = "rename.again"
        elif self.bond >= 45:
            key = "rename.happy"
            self.add_modifier("new_name", 5.0, 600.0, now)
        else:
            key = "rename.confused"
        text = self.say(creature, key, rng, now, {"nickname": new_name, "old": old})
        return {"reaction": key.split(".")[1], "say": text, "old": old}

    def on_combat_end(self, creature: Any, result: str, rng: random.Random, now: float) -> str:
        self.touch(now)
        self.remember("fight", "the dungeon", now)
        if result == "win":
            self.count("wins")
            self.add_modifier("won_fight", 8.0, 900.0, now)
            self.add_bond(1.0)
            self.note_good(now)
            return self.say(creature, "activity.dungeon_win", rng, now)
        if result == "lose":
            self.count("losses")
            self.add_modifier("lost_fight", -12.0, 1500.0, now)
            return self.say(creature, "activity.dungeon_lose", rng, now)
        return self.say(creature, "activity.dungeon_flee", rng, now)

    def on_activity(self, creature: Any, kind: str, name: str | None, result: str | None, rng: random.Random, now: float) -> str:
        """Deck activity (pet.activity). Not a caretaker touch: it feeds jealousy."""
        self.activity_since_pet += 1
        self.last_activity = name or kind
        extra = {"app": name or kind}
        if kind in {"dungeon", "dungeon_start"} and not result:
            return self.say(creature, "activity.dungeon_start", rng, now, extra)
        if kind in {"dungeon", "dungeon_end"}:
            if result in {"win", "clear", "cleared"}:
                self.count("wins")
                self.add_modifier("won_fight", 8.0, 900.0, now)
                return self.say(creature, "activity.dungeon_win", rng, now, extra)
            if result in {"lose", "loss", "died"}:
                self.add_modifier("lost_fight", -8.0, 900.0, now)
                return self.say(creature, "activity.dungeon_lose", rng, now, extra)
            return self.say(creature, "activity.dungeon_flee", rng, now, extra)
        if kind in {"app", "open", "launch"}:
            return self.say(creature, "activity.app", rng, now, extra)
        if kind in {"scan", "typing"}:
            return self.say(creature, f"activity.{kind}", rng, now, extra)
        return self.say(creature, "activity.generic", rng, now, extra)

    # ------------------------------------------------------------------
    # Sickness, tantrums
    # ------------------------------------------------------------------

    def make_sick(self, creature: Any, kind: str, rng: random.Random, now: float) -> None:
        self.sick = {"kind": kind, "since": now}
        self.count("sick")
        self.add_modifier("sick", -10.0, 3600.0, now)
        text = self.say(creature, f"sick.{kind}", rng, now)
        self.set_anim("sick", now, 8.0, face="sick")
        self.emit("sick", {
            "kind": kind,
            "label": "glitch" if kind == "glitch" else "corrupted data",
            "say": text,
            "cure": "pet.heal, or pet.gift with kind medicine",
        })

    def heal(self, creature: Any, rng: random.Random, now: float) -> str:
        self.touch(now)
        if not self.sick:
            return self.say(creature, "heal.not_sick", rng, now)
        kind = self.sick.get("kind")
        self.sick = None
        self.overfeeds = []
        self.modifiers = [m for m in self.modifiers if m.get("kind") != "sick"]
        self.add_bond(2.0)
        self.count("healed")
        text = self.say(creature, "healed", rng, now)
        self.set_anim("healed", now, 5.0, face="happy")
        self.emit("healed", {"kind": kind, "say": text})
        self.fulfill(("heal",), creature, rng, now)
        return text

    def start_tantrum(self, creature: Any, rng: random.Random, now: float, reason: str) -> None:
        self.tantrum_until = now + TANTRUM_SECONDS
        self.count("tantrums")
        self.note_misbehave(now)
        text = self.say(creature, "tantrum.start", rng, now, ttl=TANTRUM_SECONDS)
        self.emit("tantrum", {"reason": reason, "seconds": int(TANTRUM_SECONDS), "say": text})

    def tantrum_reply(self, creature: Any, rng: random.Random, now: float) -> str:
        return self.say(creature, "tantrum.during", rng, now)

    # ------------------------------------------------------------------
    # Signals: memory, territory, treasure
    # ------------------------------------------------------------------

    def remember_network(self, name: str, now: float) -> tuple[dict[str, Any], bool, bool]:
        """Update one network. Returns (record, is_new, came_back_after_gap)."""
        rec = self.networks.get(name)
        is_new = rec is None
        came_back = False
        hour = cal.local_dt(now).hour
        night = 1 if (hour >= 20 or hour <= 6) else 0
        if rec is None:
            rec = {"count": 1, "first": now, "last": now, "night": night}
            self.networks[name] = rec
            if len(self.networks) > 200:
                oldest = min(self.networks.items(), key=lambda kv: kv[1].get("last", 0))[0]
                self.networks.pop(oldest, None)
        else:
            gap = now - float(rec.get("last", now))
            came_back = gap >= RECOGNIZE_GAP
            if gap >= NETWORK_COUNT_GAP:
                rec["count"] = int(rec.get("count", 0)) + 1
                rec["night"] = int(rec.get("night", 0)) + night
            rec["last"] = now
        count = int(rec.get("count", 1))
        rec["label"] = place_label(name, int(rec.get("night", 0)) / max(1, count), count)
        return rec, is_new, came_back

    def turf(self) -> list[str]:
        return sorted(n for n, r in self.networks.items() if int(r.get("count", 0)) >= TURF_AT)

    def add_to_shelf(self, name: str, source: str, rarity: int, now: float) -> dict[str, Any]:
        item = {"name": name, "source": source, "rarity": int(rarity), "at": now}
        self.shelf.append(item)
        if len(self.shelf) > 30:
            self.shelf.sort(key=lambda t: (int(t.get("rarity", 1)), float(t.get("at", 0))))
            self.shelf.pop(0)
        return item

    def find_treasure(
        self,
        creature: Any,
        kind: str,
        ident: str,
        name: str,
        rng: random.Random,
        now: float,
        rssi: int | None = None,
        force: bool = False,
    ) -> bool:
        """A rare signal goes on the shelf once, with XP and a happy burst."""
        key = f"{kind}:{ident}"
        if key in self.treasures:
            return False
        if not force and not is_treasure_name(kind, name, rssi):
            return False
        self.treasures.append(key)
        self.treasures = self.treasures[-200:]
        label = name or "a hidden network"
        trinket = f"{label} echo"
        self.add_to_shelf(trinket, "treasure", 3, now)
        self.add_modifier("treasure", 8.0, 900.0, now)
        self.count("treasures")
        self.highlight("treasure", label)
        self.remember("discovery", label, now)
        log = creature.add_xp(6) if hasattr(creature, "add_xp") else []
        text = self.say(creature, "treasure", rng, now, {"network": label}, mood="excited")
        self.set_anim("treasure", now, 6.0, face="excited")
        if now - self.last_interaction_at >= 60:
            self.last_scan["investigate"] = now
        self.emit("treasure", {"kind": kind, "id": ident, "name": label, "xp": 6, "trinket": trinket, "say": text, "log": log})
        return True

    def on_signals(
        self,
        creature: Any,
        signals: list[tuple[str, str, str, int | None]],
        new_ids: set[str],
        rng: random.Random,
        now: float,
    ) -> None:
        """A batch of scanned or reported signals: (kind, id, name, rssi)."""
        count = len(signals)
        self.last_scan["count"] = count
        self.last_scan["empty_streak"] = self.last_scan.get("empty_streak", 0) + 1 if count == 0 else 0
        known_here = 0
        turf_here = False
        place = None
        best = -1
        newcomers: list[str] = []
        for kind, ident, name, rssi in signals:
            if ident in new_ids:
                self.count("discoveries")
                self.highlight("network", name)
                self.remember("discovery", name, now)
                if kind == self.prefs.get("fav_signal"):
                    self.add_modifier("favorite_signal", 5.0, 600.0, now)
                    if self.reveal("fav_signal"):
                        self.emit("preference_learned", {"likes_signal": kind, "name": name})
                elif kind == self.prefs.get("hated_signal"):
                    self.add_modifier("hated_signal", -3.0, 300.0, now)
                    if self.reveal("hated_signal"):
                        self.emit("preference_learned", {"dislikes_signal": kind, "name": name})
            self.find_treasure(creature, kind, ident, name, rng, now, rssi)
            if kind != "wifi" or not name:
                continue
            before = self.networks.get(name)
            prior = int(before.get("count", 0)) if before else 0
            rec, is_new, came_back = self.remember_network(name, now)
            if is_new:
                newcomers.append(name)
            if prior >= 2:
                known_here += 1
            if prior >= TURF_AT:
                turf_here = True
            if rec["count"] > best:
                best = rec["count"]
                place = rec["label"]
            if came_back and prior >= KNOWN_AT and self.ready("recognize", now):
                self.cool("recognize", now, 600.0)
                text = self.say(creature, "territory.recognize", rng, now, {"network": name, "place": rec["label"], "count": rec["count"]})
                self.emit("recognize", {"network": name, "place": rec["label"], "visits": rec["count"], "say": text})
        if place:
            self.last_scan["place"] = place
        if turf_here and newcomers and self.ready("suspicious", now):
            self.cool("suspicious", now, 300.0)
            text = self.say(creature, "territory.suspicious", rng, now, {"network": newcomers[0]})
            self.set_anim("suspicious", now, 6.0, face="suspicious")
            self.emit("suspicious", {"network": newcomers[0], "say": text})
        wifi_count = len([s for s in signals if s[0] == "wifi" and s[2]])
        if wifi_count >= 2 and known_here == 0 and len(self.networks) > wifi_count and self.ready("nervous", now):
            self.cool("nervous", now, 1800.0)
            self.add_modifier("nervous", -5.0, 600.0, now)
            text = self.say(creature, "territory.nervous", rng, now)
            self.set_anim("nervous", now, 6.0, face="nervous")
            self.emit("new_place", {"say": text, "signals": count})
        if count >= 5 and self.ready("busy", now):
            self.cool("busy", now, 3600.0)
            self.add_modifier("busy", 4.0, 600.0, now)
            text = self.say(creature, "aware.busy", rng, now, {"count": count}, mood="excited")
            self.emit("busy_place", {"signals": count, "say": text})
        if self.last_scan["empty_streak"] >= 3 and self.ready("empty", now):
            self.cool("empty", now, 900.0)
            self.add_modifier("anxious", -5.0, 600.0, now)
            self.emit("empty_place", {"say": self.say(creature, "aware.empty", rng, now)})

    # ------------------------------------------------------------------
    # Link: greetings, schedule, separation
    # ------------------------------------------------------------------

    def seen(self, now: float) -> None:
        self.last_seen = now

    def on_hello(self, creature: Any, rng: random.Random, now: float) -> None:
        """A client said hello. Queue a greeting for the next tick."""
        if self.link_up and self.separated_at is None:
            return  # a second client joining the same session
        reunion = self.separated_at is not None and now - self.separated_at >= 20
        absence = now - self.last_seen if self.last_seen > 0 else None
        if reunion and self.separated_at is not None:
            absence = now - self.separated_at
        self.link_up = True
        self.separated_at = None
        dt = cal.local_dt(now)
        today = dt.date().isoformat()
        new_session = absence is None or absence >= 300 or reunion
        on_time = late = False
        usual = self.usual_hour()
        if new_session:
            if usual is not None:
                on_time = dt.hour == usual
                late = self.last_session_day != today and dt.hour >= usual + 2
            self.schedule[dt.hour] += 1
            self.sessions += 1
            self.count("visits")
        first_today = self.last_session_day != today
        self.last_session_day = today
        if reunion:
            key = "greet.reunion"
        elif absence is None:
            key = "greet.first"
        elif on_time:
            key = "greet.on_time"
        elif late:
            key = "greet.late"
        elif first_today and 5 <= dt.hour <= 10 and absence >= 3600:
            key = "greet.morning"
        elif absence < 300:
            key = "greet.quick"
        elif absence < 3600:
            key = "greet.short"
        elif absence < 8 * 3600:
            key = "greet.medium"
        elif absence < 2 * 86400:
            key = "greet.long"
        else:
            key = "greet.very_long"
        if key in {"greet.long", "greet.very_long", "greet.medium"} and (self.bond < 35 or self.ignored_asks > 0) and rng.random() < 0.5:
            key = "greet.sulk"
        if key in {"greet.short", "greet.medium"} and cal.is_late_night(dt) and rng.random() < 0.5:
            key = "greet.night"
        self.pending_greeting = {"key": key, "absence": absence, "reunion": reunion, "on_time": on_time}

    def on_link(self, active: bool, now: float) -> None:
        """Transport callback: active=False when the last client went away."""
        if active:
            return
        if self.link_up:
            self.link_up = False
            self.separated_at = now

    def _flush_greeting(self, creature: Any, rng: random.Random, now: float) -> None:
        g = self.pending_greeting
        if not g:
            return
        self.pending_greeting = None
        absence = g.get("absence") or 0
        extra = {
            "minutes": max(1, int(absence // 60)),
            "hours": max(1, int(absence // 3600)),
            "days": max(1, int(absence // 86400)),
        }
        key = g["key"]
        if key == "greet.reunion":
            self.add_bond(1.0)
            self.add_modifier("reunion", 10.0, 900.0, now)
            self.modifiers = [m for m in self.modifiers if m.get("kind") != "lonely"]
            self.set_anim("reunion", now, 6.0, face="excited")
        elif key == "greet.on_time":
            self.add_bond(2.0)
            self.add_modifier("on_time", 6.0, 900.0, now)
            self.set_anim("reunion", now, 4.0, face="excited")
        elif key == "greet.morning":
            self.add_modifier("morning_grump", -8.0, 300.0, now)
        elif key == "greet.sulk":
            self.add_modifier("sulking", -6.0, 600.0, now)
            self.set_anim("pout", now, 6.0, face="pouting")
        elif key in {"greet.long", "greet.very_long"}:
            self.set_anim("stare", now, 5.0)
        text = self.say(creature, key, rng, now, extra)
        event = "reunion" if key == "greet.reunion" else "greeting"
        self.emit(event, {"kind": key.split(".", 1)[1], "absent_s": int(absence), "say": text})

    # ------------------------------------------------------------------
    # The heartbeat
    # ------------------------------------------------------------------

    def update(self, creature: Any, now: float, rng: random.Random, world: Any = None) -> None:
        """Run every timer-driven behavior once. Safe to call every tick."""
        self.last_now = now
        self.prune(now)
        self._flush_greeting(creature, rng, now)
        self._calendar(creature, rng, now)
        self._sickness(creature, rng, now)
        if self.tantrum_until and now >= self.tantrum_until and self.cooldowns.get("tantrum_over", 0) < self.tantrum_until:
            self.cooldowns["tantrum_over"] = self.tantrum_until
            self.emit("tantrum_over", {"say": self.say(creature, "tantrum.over", rng, now)})
        if self.adventure:
            if now >= float(self.adventure.get("until", now)):
                self.finish_adventure(creature, rng, now)
            return
        self._rival_tick(creature, rng, now)
        self._separation(creature, rng, now)
        self._ask_tick(creature, rng, now)
        self._awareness(creature, rng, now, world)
        if creature.asleep:
            self._dream(creature, rng, now)
            return
        self._quirks(creature, rng, now)
        self._idle(creature, rng, now)
        self._chatter(creature, rng, now)
        self._jealousy(creature, rng, now)
        self._gift_back(creature, rng, now)
        self._maybe_adventure(creature, rng, now)

    # -- calendar: diary rollover, birthday, holidays ------------------------

    def _calendar(self, creature: Any, rng: random.Random, now: float) -> None:
        today = cal.local_dt(now).date()
        iso = today.isoformat()
        if self.day.get("date") and self.day.get("date") != iso:
            self.write_diary(creature, rng, now)
            self.day = {"date": iso, "c": {}, "h": {}}
        elif not self.day.get("date"):
            self.day = {"date": iso, "c": {}, "h": {}}
        hatched = cal.local_dt(self.hatched_at).date()
        if cal.is_birthday(hatched, today) and f"birthday:{today.year}" not in self.celebrated:
            years = today.year - hatched.year
            self.celebrated.append(f"birthday:{today.year}")
            self.add_modifier("birthday", 15.0, 86400.0, now)
            self.add_bond(3.0)
            stats = creature.stats
            stats.max_hp += 5
            stats.hp += 5
            stats.attack += 1
            stats.defense += 1
            stats.speed += 1
            stats.clamp()
            self.highlight("birthday", True)
            self.add_to_shelf(f"birthday candle #{years}", "birthday", 2, now)
            text = self.say(creature, "birthday", rng, now, {"years": years}, mood="happy")
            self.set_anim("party", now, 10.0, face="party")
            self.emit("birthday", {"years": years, "boost": {"max_hp": 5, "attack": 1, "defense": 1, "speed": 1}, "costume": "party", "say": text})
        elif cal.is_hatchday(hatched, today) and f"hatchday:{iso[:7]}" not in self.celebrated:
            months = cal.months_between(hatched, today)
            self.celebrated.append(f"hatchday:{iso[:7]}")
            self.add_modifier("hatchday", 6.0, 43200.0, now)
            creature.stats.mood = min(100.0, creature.stats.mood + 5.0)
            text = self.say(creature, "hatchday", rng, now, {"months": months}, mood="happy")
            self.set_anim("party", now, 8.0, face="party")
            self.emit("hatchday", {"months": months, "costume": "party", "say": text})
        holiday = cal.holiday_for(today)
        if holiday and f"{holiday}:{today.year}" not in self.celebrated:
            self.celebrated.append(f"{holiday}:{today.year}")
            label = cal.holiday_label(holiday)
            self.add_modifier("holiday", 6.0, 43200.0, now)
            self.highlight("holiday", label)
            text = self.say(creature, f"holiday.{holiday}", rng, now, {"holiday": label}, mood="happy")
            self.emit("holiday", {"holiday": holiday, "label": label, "costume": holiday, "say": text})
        self.celebrated = self.celebrated[-60:]

    def write_diary(self, creature: Any, rng: random.Random, now: float, draft: bool = False) -> dict[str, str]:
        """Assemble the diary entry for self.day (draft=True: preview, not saved)."""
        slots = self.slots(creature, now)
        mood_score = creature.stats.mood + self.mood_offset(now)
        sign = self.display_name()
        if rng.random() < 0.4 and self.quirks.get("catchphrase"):
            sign = f"{sign}, {self.quirks['catchphrase']}"
        text = stories.diary_entry(self.picker, rng, self.day, slots, mood_score, sign)
        entry = {"date": str(self.day.get("date") or cal.local_dt(now).date().isoformat()), "text": text}
        if not draft:
            self.diary.append(entry)
            self.diary = self.diary[-30:]
            self.emit("diary", {"date": entry["date"], "text": text})
        return entry

    # -- sickness -----------------------------------------------------------

    def _sickness(self, creature: Any, rng: random.Random, now: float) -> None:
        mistakes = int(creature.stats.care_mistakes)
        if mistakes > self.last_care_mistakes:
            if not self.sick and rng.random() < 0.35:
                self.make_sick(creature, "corrupted", rng, now)
            self.add_bond(-1.5 * (mistakes - self.last_care_mistakes))
        self.last_care_mistakes = mistakes
        if not self.sick:
            return
        self.add_modifier("sick", -10.0, 600.0, now)
        if self.sick.get("kind") == "glitch" and now - float(self.sick.get("since", now)) >= GLITCH_SELF_HEAL:
            self.heal(creature, rng, now)

    # -- asks ----------------------------------------------------------------

    def _ask_tick(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.ask:
            if not self.link_up:
                self.ask = None  # the deck went away; nobody ignored us
                return
            if now >= float(self.ask.get("until", now)):
                kind = self.ask.get("kind")
                self.ask = None
                self.ignored_asks += 1
                self.add_bond(-1.0)
                self.add_modifier("ignored", -8.0, 900.0, now)
                self.count("asks_ignored")
                text = self.say(creature, "ask.ignored", rng, now)
                self.emit("ask_ignored", {"kind": kind, "say": text, "ignored_in_a_row": self.ignored_asks})
                # Back off a little more each time it is ignored.
                self.cool("ask", now, ASK_COOLDOWN * (1 + self.ignored_asks))
                if self.ignored_asks >= IGNORED_FOR_TANTRUM and not self.in_tantrum(now):
                    self.ignored_asks = 0
                    self.start_tantrum(creature, rng, now, "ignored")
            return
        # Only ask when someone is connected to answer.
        if not self.link_up or creature.asleep or self.in_tantrum(now) or not self.ready("ask", now):
            return
        stats = creature.stats
        kind = None
        investigate = self.last_scan.get("investigate")
        if self.sick:
            kind = "heal"
        elif stats.hunger <= 35:
            kind = "food"
        elif stats.energy <= 30:
            kind = "rest"
        elif investigate and now - float(investigate) <= 300:
            kind = "investigate"
            self.last_scan["investigate"] = None
        elif stats.mood + self.mood_offset(now) <= 45 or now - self.last_interaction_at >= 1200:
            kind = "play" if stats.energy >= 40 else "attention"
        elif now - self.last_interaction_at >= 900:
            kind = "attention"
        elif self.recent and now - float(self.recent[-1][2]) >= 1800:
            kind = "scan"
        if not kind:
            self.cool("ask", now, ASK_CHECK)
            return
        self.ask = {"kind": kind, "at": now, "until": now + ASK_TIMEOUT}
        self.cool("ask", now, ASK_COOLDOWN + ASK_TIMEOUT)
        text = self.say(creature, f"ask.{kind}", rng, now, ttl=30.0)
        self.emit("ask", {"kind": kind, "say": text, "expires_in": int(ASK_TIMEOUT)})

    # -- awareness ------------------------------------------------------------

    def _awareness(self, creature: Any, rng: random.Random, now: float, world: Any) -> None:
        if not self.ready("aware", now):
            return
        self.cool("aware", now, AWARE_CHECK)
        try:
            busy = float(creature.environment().get("busy", 0.0))
        except Exception:
            busy = 0.0
        if world is not None:
            from pet.world import COLD_C, HOT_C

            temp, source = world.cpu_temp(now, busy)
            self.temp = {"c": temp, "source": source}
            if temp >= HOT_C and self.ready("hot", now):
                self.cool("hot", now, 900.0)
                self.add_modifier("hot", -5.0, 900.0, now)
                self.set_anim("hot", now, 6.0, face="hot")
                self.emit("hot", {"temp_c": temp, "say": self.say(creature, "aware.hot", rng, now, {"temp": int(temp)})})
            elif temp <= COLD_C and self.ready("cold", now):
                self.cool("cold", now, 1800.0)
                self.emit("cold", {"temp_c": temp, "say": self.say(creature, "aware.cold", rng, now, {"temp": int(temp)})})
            weather = world.refresh_weather(now) if hasattr(world, "refresh_weather") else None
            self._weather(creature, rng, now, weather)
        if creature.asleep:
            return
        dt = cal.local_dt(now)
        if cal.is_late_night(dt) and self.ready("night", now):
            self.cool("night", now, 1800.0)
            self.add_modifier("sleepy_night", -3.0, 1800.0, now)
            self.emit("sleepy", {"hour": dt.hour, "say": self.say(creature, "aware.night", rng, now, mood="tired")})
        if cal.time_of_day(dt) == "afternoon" and self.ready("afternoon", now):
            self.cool("afternoon", now, 3 * 3600.0)
            self.add_modifier("hyper", 5.0, 1800.0, now)
            self.emit("hyper", {"say": self.say(creature, "tod.afternoon", rng, now, mood="excited")})

    def _weather(self, creature: Any, rng: random.Random, now: float, weather: dict | None) -> None:
        if not weather:
            return
        cond = weather.get("condition")
        previous = (self.weather or {}).get("condition")
        self.weather = {"condition": cond, "temp_c": weather.get("temp_c"), "at": weather.get("at", now)}
        effects = {"sunny": 5.0, "rain": -6.0, "storm": -8.0, "snow": 3.0, "fog": -2.0, "cloudy": 0.0}
        amount = effects.get(cond, 0.0)
        if amount:
            self.add_modifier(cond, amount, 1800.0, now)
        if cond != previous:
            self.highlight("weather", cond)
            if cond == "sunny":
                creature.stats.energy = min(100.0, creature.stats.energy + 5.0)
            self.emit("weather", {"condition": cond, "temp_c": weather.get("temp_c"), "say": self.say(creature, f"weather.{cond}", rng, now)})

    # -- dreams, quirks, idle, chatter ------------------------------------------

    def _dream(self, creature: Any, rng: random.Random, now: float) -> None:
        if not self.ready("dream", now):
            return
        self.cool("dream", now, _uniform(rng, DREAM_EVERY))
        about = "generic"
        extra: dict[str, Any] = {}
        if self.recent and rng.random() < 0.75:
            kind, label, _at = rng.choice(self.recent[-8:])
            about = {"discovery": "discovery", "fight": "fight", "food": "food", "adventure": "adventure", "rival": "fight"}.get(kind, "generic")
            extra = {"network": label, "foe": label, "food": label, "place": label}
        elif rng.random() < 0.3:
            about = "friend"
        text = self.say(creature, f"dream.{about}", rng, now, extra, decorate=False, ttl=20.0)
        self.set_anim("dream", now, 15.0, face="dreaming")
        self.emit("dream", {"about": about, "say": text})

    def _quirks(self, creature: Any, rng: random.Random, now: float) -> None:
        if not self.ready("quirk", now):
            return
        self.cool("quirk", now, QUIRK_CHECK)
        stats = creature.stats
        off = sum((stats.hunger < 30, stats.energy < 30, stats.mood + self.mood_offset(now) < 35))
        off += 2 if self.sick else 0
        if rng.random() >= 0.006 + 0.02 * off:
            return
        kind = "sneeze" if rng.random() < 0.5 else "hiccup"
        style = self.quirks.get("sneeze", "tiny") if kind == "sneeze" else "hiccup"
        text = self.say(creature, kind, rng, now, decorate=False, ttl=5.0)
        self.set_anim(f"{kind}:{style}", now, 3.0)
        self.emit(kind, {"style": style, "say": text})

    def _idle(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.in_tantrum(now) or now - self.last_interaction_at < IDLE_AFTER or not self.ready("idle", now):
            return
        pace = self.pace * (1.6 if self.time_of_day(now) == "afternoon" else 1.0)
        self.cool("idle", now, _uniform(rng, IDLE_EVERY, pace))
        stats = creature.stats
        mood = stats.mood + self.mood_offset(now)
        late = cal.is_late_night(cal.local_dt(now))
        weights: dict[str, float] = {"look_around": 3, "stretch": 2, "hum": 2, "chase_bug": 2, "yawn": 1, "count": 1, "talk_router": 0.5}
        fav = self.quirks.get("fav_idle", "hum")
        weights[fav] = weights.get(fav, 1) + 2
        if late:
            weights.update({"yawn": 4, "doze": 3, "night": 3})
        if stats.energy < 35:
            weights.update({"yawn": 4, "doze": 3})
        if mood >= 70:
            weights.update({"dance": 3, "hum": 3})
            if self.shelf:
                weights["show_off"] = 3
        if now - self.last_interaction_at >= 600:
            weights["stare"] = 3
        if mood < 50:
            weights["chase_bug"] = weights.get("chase_bug", 1) + 2
        usual = self.usual_hour()
        if not self.link_up and usual is not None and (usual - cal.local_dt(now).hour) % 24 in (0, 1):
            weights["waiting"] = 6
        names = list(weights)
        action = rng.choices(names, weights=[weights[n] for n in names])[0]
        extra = {"count": len(self.shelf) if action == "show_off" else len(creature.known_move_ids)}
        text = self.say(creature, f"idle.{action}", rng, now, extra, decorate=False, ttl=8.0)
        anim = f"dance:{self.quirks.get('dance')}" if action == "dance" else action
        self.set_anim(anim, now, 6.0)
        detail: dict[str, Any] = {"action": action, "say": text, "anim": anim}
        if action == "doze" and stats.energy < 50 and late and rng.random() < 0.3:
            creature.asleep = True
            detail["fell_asleep"] = True
        self.emit("idle", detail)

    def _chatter(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.in_tantrum(now):
            return
        pace = self.pace * (1.6 if self.time_of_day(now) == "afternoon" else 1.0)
        if "chatter" not in self.cooldowns:
            self.cool("chatter", now, _uniform(rng, CHATTER_EVERY, pace))
            return
        if not self.ready("chatter", now):
            return
        self.cool("chatter", now, _uniform(rng, CHATTER_EVERY, pace))
        key = "chatter"
        dt = cal.local_dt(now)
        if any(m["kind"] == "morning_grump" for m in self.active_modifiers(now)):
            key = "tod.morning"
        elif cal.is_late_night(dt) and rng.random() < 0.4:
            key = "tod.night"
        text = self.say(creature, key, rng, now)
        self.emit("speech", {"say": text, "topic": key})

    def _jealousy(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.activity_since_pet < JEALOUS_ACTIVITIES or now - self.last_interaction_at < JEALOUS_AFTER:
            return
        if not self.ready("jealous", now):
            return
        self.cool("jealous", now, 900.0)
        self.add_modifier("jealous", -6.0, 900.0, now)
        extra = {"app": self.last_activity or "that app", "minutes": int((now - self.last_interaction_at) // 60)}
        text = self.say(creature, "jealous", rng, now, extra)
        self.set_anim("pout", now, 6.0, face="pouting")
        self.emit("jealous", {"app": extra["app"], "say": text})

    def _separation(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.separated_at is None or now - self.separated_at < LONELY_AFTER:
            return
        self.add_modifier("lonely", -6.0, 600.0, now)
        if self.ready("lonely", now):
            self.cool("lonely", now, 300.0)
            self.set_anim("lonely", now, 6.0, face="lonely")
            self.emit("lonely", {"since_s": int(now - self.separated_at), "say": self.say(creature, "lonely", rng, now, mood="sad")})

    # -- gifts back -------------------------------------------------------------

    def _gift_back(self, creature: Any, rng: random.Random, now: float) -> None:
        if self.bond < 70:
            return
        if "gift" not in self.cooldowns:
            self.cool("gift", now, GIFT_CHECK / max(0.05, self.pace))
            return
        if not self.ready("gift", now):
            return
        self.cool("gift", now, GIFT_CHECK / max(0.05, self.pace))
        if rng.random() >= (0.12 if self.bond < 90 else 0.22):
            return
        self.make_gift(creature, rng, now)

    def make_gift(self, creature: Any, rng: random.Random, now: float) -> dict[str, Any]:
        """Give the caretaker something: an item, a rare evo chip, or a fun fact."""
        roll = rng.random()
        can_evo = creature.next_form_id() is not None if hasattr(creature, "next_form_id") else False
        item: dict[str, Any] | None
        if roll < 0.03 and can_evo:
            item = {"name": "evo chip", "kind": "evolve", "power": 0}
            text = self.say(creature, "gift_back.evo", rng, now)
            kind = "evo"
        elif roll < 0.38 and self.networks:
            text = self.say(creature, "gift_back.fact", rng, now, {"fact": self.fun_fact(rng, now)}, decorate=False)
            item = None
            kind = "fact"
        else:
            sort = rng.choice(("food", "toy", "battery", "trinket"))
            if sort == "food":
                name = rng.choice(stories.FOOD_NAMES)
            elif sort == "toy":
                name = rng.choice(stories.TOY_NAMES)
            elif sort == "battery":
                name = rng.choice(stories.BATTERY_NAMES)
            else:
                name = stories.trinket_name(self.picker, rng)
            item = {"name": name, "kind": sort, "power": rng.randint(8, 20)}
            text = self.say(creature, "gift_back.item", rng, now, {"item": name})
            kind = "item"
        self.gifts_given += 1
        self.count("gifts_given")
        self.set_anim("gift", now, 6.0, face="love")
        detail = {"kind": kind, "item": item, "say": text}
        self.emit("gift_back", detail)
        return detail

    def fun_fact(self, rng: random.Random, now: float) -> str:
        name = rng.choice(sorted(self.networks))
        rec = self.networks[name]
        first = cal.local_dt(float(rec.get("first", now))).strftime("%b %d")
        places = {r.get("label") for r in self.networks.values()}
        slots = {
            "network": name,
            "count": rec.get("count", 1),
            "first": first,
            "known": len(self.networks),
            "place": rec.get("label", "somewhere"),
            "places": len(places),
            "turf": ", ".join(self.turf()[:3]) or name,
        }
        return fill(self.picker.pick("funfact", rng), slots)

    # -- adventures ---------------------------------------------------------------

    def _maybe_adventure(self, creature: Any, rng: random.Random, now: float) -> None:
        if now - self.last_interaction_at < ADVENTURE_IDLE or not self.ready("adventure", now):
            return
        self.cool("adventure", now, ADVENTURE_CHECK / max(0.05, self.pace))
        if creature.stats.energy < 40 or self.sick or self.in_tantrum(now) or (self.rival or {}).get("active"):
            return
        if rng.random() < 0.3:
            self.start_adventure(creature, rng, now)

    def start_adventure(self, creature: Any, rng: random.Random, now: float) -> dict[str, Any] | None:
        if self.adventure or creature.asleep or creature.fainted:
            return None
        place = stories.adventure_place(self.picker, rng)
        length = _uniform(rng, ADVENTURE_LEN, self.pace)
        self.adventure = {"place": place, "started": now, "until": now + length}
        text = self.say(creature, "adv.start", rng, now, {"place": place})
        self.set_anim("away", now, 4.0)
        self.emit("adventure_start", {"place": place, "return_in": int(length), "say": text})
        return dict(self.adventure)

    def finish_adventure(self, creature: Any, rng: random.Random, now: float, cut_short: bool = False) -> dict[str, Any] | None:
        """Come home: roll the outcome from stats, write the story, hand out loot."""
        adv = self.adventure
        if not adv:
            return None
        self.adventure = None
        stats = creature.stats
        score = 25 + stats.mood * 0.2 + self.bond * 0.2 + stats.energy * 0.1 + min(20, stats.level * 2)
        score += (stats.attack + stats.defense + stats.speed) * 0.3 + rng.uniform(-25, 25)
        if cut_short:
            score = min(score, 55)
        outcome = "great" if score >= 85 else "good" if score >= 60 else "meh" if score >= 35 else "bad"
        loot_chance = {"great": 1.0, "good": 0.6, "meh": 0.2, "bad": 0.05}[outcome]
        loot = stories.trinket_name(self.picker, rng) if rng.random() < loot_chance else None
        xp = {"great": 10 + stats.level, "good": 6, "meh": 2, "bad": 1}[outcome]
        stats.energy = max(0.0, stats.energy - 10.0)
        if outcome == "bad":
            stats.hp = max(1, stats.hp - 3)
            self.add_modifier("adventure", -5.0, 900.0, now)
        else:
            self.add_modifier("adventure", {"great": 10.0, "good": 6.0, "meh": 2.0}[outcome], 900.0, now)
        stats.clamp()
        log = creature.add_xp(xp) if hasattr(creature, "add_xp") else []
        if loot:
            self.add_to_shelf(loot, "adventure", 2 if outcome == "great" else 1, now)
        story = stories.adventure_story(self.picker, rng, outcome, self.slots(creature, now, {"place": adv["place"]}), loot)
        self.adventures += 1
        self.count("adventures")
        self.cool("adventure", now, 1800.0 / max(0.05, self.pace))
        self.highlight("place", adv["place"])
        self.remember("adventure", adv["place"], now)
        self.bubble = {"text": story, "category": "adventure", "at": now, "until": now + 30.0}
        self.set_anim("return", now, 6.0, face="happy" if outcome != "bad" else "hurt")
        detail = {
            "place": adv["place"],
            "outcome": outcome,
            "story": story,
            "say": story,
            "reward": {"xp": xp, "trinket": loot},
            "cut_short": cut_short,
            "log": log,
        }
        self.emit("adventure_return", detail)
        return detail

    # -- rival --------------------------------------------------------------------

    def _rival_tick(self, creature: Any, rng: random.Random, now: float) -> None:
        rival = self.rival
        if not rival:
            return
        active = rival.get("active")
        if active:
            if now >= float(active.get("until", now)):
                rival["active"] = None
                rival["next_at"] = now + _uniform(rng, RIVAL_EVERY, self.pace)
                text = self.say(creature, "rival.leave", rng, now, {"rival": rival.get("name")})
                self.add_modifier("rival_taunt", -3.0, 600.0, now)
                self.emit("rival_leaves", {"rival": rival.get("name"), "say": text})
            return
        if creature.asleep or creature.fainted or self.in_tantrum(now):
            return
        if now >= float(rival.get("next_at", now + 1)):
            self.rival_appear(creature, rng, now)

    def rival_appear(self, creature: Any, rng: random.Random, now: float) -> dict[str, Any]:
        from pet.rival import make_rival

        rival = self.rival
        foe = make_rival(rival.get("name") or "Glitchfang", creature.stats.level, rng, record=rival)
        foe["until"] = now + RIVAL_WAIT
        foe["appeared_at"] = now
        rival["active"] = foe
        self.count("rival_seen")
        self.highlight("rival", rival.get("name"))
        self.remember("rival", rival.get("name"), now)
        extra = {"rival": rival.get("name"), "level": foe["level"]}
        text = self.say(creature, "rival.appear", rng, now, extra, decorate=False, ttl=20.0)
        taunt = fill(self.picker.pick("rival.taunt", rng), self.slots(creature, now, extra))
        self.set_anim("rival", now, 8.0, face="angry")
        self.emit("rival_appears", {
            "rival": rival.get("name"),
            "level": foe["level"],
            "stats": {k: foe[k] for k in ("hp", "attack", "defense", "speed")},
            "taunt": taunt,
            "record": {"wins": rival.get("wins", 0), "losses": rival.get("losses", 0)},
            "expires_in": int(RIVAL_WAIT),
            "say": text,
            "respond": "pet.rival {action: fight|ignore}",
        })
        return foe

    # -- eggs -----------------------------------------------------------------------

    def lay_egg(self, creature: Any, rng: random.Random, now: float, move_id: str | None = None, trait: str | None = None) -> dict[str, Any]:
        """Store an egg carrying one move and one trait for the next generation."""
        moves = [m for m in creature.known_move_ids if m != "chirp"]
        if move_id not in creature.known_move_ids:
            move_id = max(moves, key=lambda m: (creature.move_rank(m), m)) if moves else "chirp"
        if trait not in {"catchphrase", "fav_food", "dance"}:
            trait = rng.choice(("catchphrase", "fav_food", "dance"))
        value = {"catchphrase": self.quirks.get("catchphrase"), "fav_food": self.prefs.get("fav_food"), "dance": self.quirks.get("dance")}[trait]
        self.egg = {
            "move": move_id,
            "trait": trait,
            "trait_value": value,
            "parent": creature.species.name,
            "parent_name": self.display_name(),
            "generation": self.generation + 1,
            "laid_at": now,
        }
        return dict(self.egg)

    def inherit_from(self, old: "Life", egg: dict[str, Any], parent_species: str) -> None:
        """Carry memory that belongs to the household into the next generation."""
        self.networks = old.networks
        self.schedule = old.schedule
        self.sessions = old.sessions
        self.last_session_day = old.last_session_day
        self.last_seen = old.last_seen
        self.diary = old.diary
        self.picker = old.picker
        self.rival = dict(old.rival, active=None)
        heirloom = old.favorite_trinket()
        if heirloom:
            self.shelf = [dict(t) for t in old.shelf if t.get("name") == heirloom][:1]
        self.lineage = old.lineage + [{
            "generation": old.generation,
            "species": parent_species,
            "name": old.display_name(),
        }]
        self.lineage = self.lineage[-20:]
        self.treasures = old.treasures
