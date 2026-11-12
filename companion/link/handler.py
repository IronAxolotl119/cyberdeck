"""Map protocol requests onto our Creature.

The main deck speaks hunger as a *need* (0 = full, 100 = starving) and
happiness as a 0–100 mood score. Our simulation stores the opposite
hunger meter (0 = starving, 100 = stuffed) and a mood meter. This
module is the only place that conversion happens, so the rest of the
pet can stay in its own units.
"""

from __future__ import annotations

from typing import Any, Callable

from avatar.sprites import PALETTE, SPRITE_SIZE, sprite_grid
from discovery.effects import apply_signals
from discovery.simulated import SimulatedScanner
from link.protocol import (
    PROTO_VERSION,
    TYPE_HELLO,
    TYPE_HELLO_OK,
    TYPE_PET_AVATAR,
    TYPE_PET_AVATAR_OK,
    TYPE_PET_EVENT,
    TYPE_PET_FEED,
    TYPE_PET_FEED_OK,
    TYPE_PET_GIFT,
    TYPE_PET_GIFT_OK,
    TYPE_PET_PLAY,
    TYPE_PET_PLAY_OK,
    TYPE_PET_STATUS,
    TYPE_PET_STATUS_OK,
    TYPE_PING,
    TYPE_PONG,
    Message,
    new_id,
)
from pet.creature import TRAIN_ENERGY_COST, Creature
from pet.evolution import get_species
from pet.moves import MOVE_LIBRARY


# Name advertised in every protocol pet object and in hello.ok.
PET_NAME = "PULSE"

# Item kinds listed in PROTOCOL.md. Unknown kinds become trinket.
GIFT_KINDS = frozenset({"food", "toy", "battery", "evolve", "trinket"})

# Thresholds for the short mood word, aligned with the main deck's PetState.
HUNGRY_AT = 82
TIRED_AT = 18
GRUMPY_AT = 28
JOYFUL_HAPPINESS = 85
JOYFUL_HUNGER_MAX = 45
PLAYFUL_AT = 70

EventCallback = Callable[[Message], None]


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if value < lo else hi if value > hi else value


def need_hunger(creature: Creature) -> float:
    """Protocol hunger (need): 0 = full, 100 = starving."""
    return _clamp(100.0 - creature.stats.hunger)


def set_need_hunger(creature: Creature, need: float) -> None:
    """Write a protocol-space hunger value back into our fullness meter."""
    creature.stats.hunger = _clamp(100.0 - need)
    creature.stats.clamp()


def mood_word(creature: Creature) -> str:
    """Short word derived from protocol needs: content/playful/joyful/hungry/tired/grumpy."""
    hunger = need_hunger(creature)
    happiness = _clamp(creature.stats.mood)
    energy = _clamp(creature.stats.energy)
    if hunger >= HUNGRY_AT:
        return "hungry"
    if energy <= TIRED_AT:
        return "tired"
    if happiness <= GRUMPY_AT:
        return "grumpy"
    if happiness >= JOYFUL_HAPPINESS and hunger <= JOYFUL_HUNGER_MAX:
        return "joyful"
    if happiness >= PLAYFUL_AT:
        return "playful"
    return "content"


def pet_payload(creature: Creature, frame: int = 0) -> dict[str, Any]:
    """The pet object attached to status / feed / play / gift / event replies."""
    stats = creature.stats
    moves = [MOVE_LIBRARY[mid].name if mid in MOVE_LIBRARY else mid for mid in creature.known_move_ids]
    return {
        "name": PET_NAME,
        "species": creature.species.name,
        "stage": creature.species.stage,
        "hunger": int(round(need_hunger(creature))),
        "happiness": int(round(_clamp(stats.mood))),
        "energy": int(round(_clamp(stats.energy))),
        "mood": mood_word(creature),
        "age_hours": round(float(stats.age), 2),
        "gifts": len(creature.inventory),
        # Optional extras the main deck can ignore.
        "level": stats.level,
        "xp": stats.xp,
        "hp": stats.hp,
        "max_hp": stats.max_hp,
        "attack": stats.attack,
        "defense": stats.defense,
        "speed": stats.speed,
        "moves": moves,
        "care_mistakes": stats.care_mistakes,
        "avatar": {
            "form": creature.species_id,
            "mood": mood_word(creature),
            "frame": int(frame) % 2,
        },
    }


# Map a protocol mood word (or a sprite-face name) onto a sprite overlay.
# The main deck's pet.avatar.ok "mood" field is the protocol word; we still
# need a face to draw (happy / hungry / sad / neutral).
_SPRITE_FACE = {
    "happy": "happy",
    "joyful": "happy",
    "playful": "happy",
    "neutral": "neutral",
    "content": "neutral",
    "idle": "neutral",
    "hungry": "hungry",
    "tired": "hungry",
    "sad": "sad",
    "grumpy": "sad",
}


def sprite_face_for_mood(mood: str) -> str:
    """Pick a sprite face overlay for a protocol or sprite mood word."""
    key = mood.strip().lower().replace(" ", "")
    if key not in _SPRITE_FACE:
        raise ValueError(f"unknown mood {mood!r}")
    return _SPRITE_FACE[key]


def _rgb_to_hex(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def encode_avatar(creature: Creature, frame: int = 0, mood: str | None = None) -> dict[str, Any]:
    """Build a pet.avatar.ok payload from our 16x16 sprite.

    palette[0] is always null (transparent). Later entries are "#rrggbb".
    rows is a 16x16 grid of palette indexes. form is the species id.
    mood is the protocol (or override) word echoed back to the deck.
    """
    word = mood if mood is not None else mood_word(creature)
    face = sprite_face_for_mood(word)
    frame_n = int(frame) % 2
    form = creature.species_id
    grid = sprite_grid(form, frame=frame_n, mood=face)
    # Index 0 is reserved for transparency, matching the main deck's parser.
    palette: list[str | None] = [None]
    color_at: dict[tuple[int, int, int], int] = {}
    rows: list[list[int]] = []
    for line in grid:
        indexes: list[int] = []
        for ch in line:
            color = PALETTE[ch]
            if color is None:
                indexes.append(0)
                continue
            if color not in color_at:
                color_at[color] = len(palette)
                palette.append(_rgb_to_hex(color))
            indexes.append(color_at[color])
        rows.append(indexes)
    return {
        "form": form,
        "mood": word,
        "frame": frame_n,
        "width": SPRITE_SIZE,
        "height": SPRITE_SIZE,
        "palette": palette,
        "rows": rows,
    }


def _clean_power(raw: Any) -> int:
    """Default 10, clamp to 0–100. Non-numeric values also fall back to 10."""
    if raw is None:
        return 10
    try:
        return int(round(_clamp(float(raw), 0.0, 100.0)))
    except (TypeError, ValueError):
        return 10


class CompanionHandler:
    """Dispatch one already-decoded request and optionally queue pet.event pushes."""

    def __init__(
        self,
        creature: Creature,
        scanner: SimulatedScanner | None = None,
        source: str = "sim",
    ) -> None:
        self.creature = creature
        self.scanner = scanner
        self.source = source
        self.events_enabled = False
        self.frame = 0
        self._pending: list[Message] = []
        self._seen: dict[str, Any] = self._snapshot()

    def handle(self, message: Message) -> Message:
        """Return the correlated reply. Drain events afterwards if they are on."""
        typ = message.type
        payload = message.payload if isinstance(message.payload, dict) else {}

        if typ == TYPE_HELLO:
            reply = self._hello(message, payload)
        elif typ == TYPE_PING:
            reply = message.reply(TYPE_PONG, {"ok": True, "source": self.source})
        elif typ == TYPE_PET_STATUS:
            reply = message.reply(TYPE_PET_STATUS_OK, {"pet": self.pet_dict()})
        elif typ == TYPE_PET_FEED:
            reply = self._feed(message, payload)
        elif typ == TYPE_PET_PLAY:
            reply = self._play(message, payload)
        elif typ == TYPE_PET_GIFT:
            reply = self._gift(message, payload)
        elif typ == TYPE_PET_AVATAR:
            reply = self._avatar(message, payload)
        else:
            reply = message.error("unknown_type", f"unsupported message type: {typ}")

        self._collect_events()
        return reply

    def drain_events(self) -> list[Message]:
        """Pop and return any queued pet.event messages (empty if not opted in)."""
        events = self._pending
        self._pending = []
        return events

    def tick(self, dt: float) -> list[str]:
        """Advance the creature (and an optional fake scan). Queues events if enabled."""
        log: list[str] = []
        before_moves = list(self.creature.known_move_ids)
        before_level = self.creature.stats.level
        before_species = self.creature.species_id
        log.extend(self.creature.tick(dt))
        if self.scanner is not None:
            signals = self.scanner.scan()
            new_ids = [sig.id for sig in signals if sig.id not in self.creature.discovered_ids]
            applied = apply_signals(self.creature, signals)
            log.extend(applied)
            if self.events_enabled:
                for sig in signals:
                    if sig.id in new_ids:
                        self._queue_event(
                            "signal_found",
                            {
                                "ssid": sig.name,
                                "name": sig.name,
                                "kind": sig.kind,
                                "rssi": sig.rssi,
                                "id": sig.id,
                            },
                        )
        self.frame += 1
        # Level / move / evolution can also happen from a scan's XP.
        self._note_growth(before_species, before_level, before_moves)
        self._collect_events()
        return log

    def pet_dict(self) -> dict[str, Any]:
        """Current pet object for replies."""
        return pet_payload(self.creature, frame=self.frame)

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    def _hello(self, message: Message, payload: dict[str, Any]) -> Message:
        proto = payload.get("proto")
        try:
            proto_n = int(proto)
        except (TypeError, ValueError):
            return message.error(
                "proto_mismatch",
                f"protocol version {proto!r} is not compatible with {PROTO_VERSION}",
            )
        if proto_n != PROTO_VERSION:
            return message.error(
                "proto_mismatch",
                f"protocol version {proto_n} is not compatible with {PROTO_VERSION}",
            )
        want_events = bool(payload.get("events"))
        # Opting in turns events on. Opting out of *this* hello must not
        # silence other TCP clients that already asked for pushes; the
        # server sets events_enabled from the live sessions after handle().
        if want_events:
            # Only snapshot when events first start, so a second client
            # saying hello does not wipe the first client's pending needs.
            if not self.events_enabled:
                self._seen = self._snapshot()
            self.events_enabled = True
        body: dict[str, Any] = {
            "role": "companion",
            "proto": PROTO_VERSION,
            "source": self.source,
            "pet": PET_NAME,
        }
        if want_events:
            body["events"] = True
        return message.reply(TYPE_HELLO_OK, body)

    def _avatar(self, message: Message, payload: dict[str, Any]) -> Message:
        """Return the 16x16 sprite as palette indexes.

        Optional payload fields: frame (0 or 1) and mood (a protocol or
        sprite-face word). Missing fields use the live pet's frame and mood.
        """
        try:
            frame = _parse_frame(payload.get("frame"), default=self.frame % 2)
            mood = _parse_mood_override(payload.get("mood"))
        except ValueError as exc:
            return message.error("bad_payload", str(exc))
        return message.reply(TYPE_PET_AVATAR_OK, encode_avatar(self.creature, frame=frame, mood=mood))

    def _feed(self, message: Message, payload: dict[str, Any]) -> Message:
        item = payload.get("item")
        if not isinstance(item, str) or not item.strip():
            return message.error("bad_payload", "pet.feed needs payload.item as a non-empty string")
        item = item.strip()
        before = need_hunger(self.creature)
        before_level = self.creature.stats.level
        before_moves = list(self.creature.known_move_ids)
        before_species = self.creature.species_id
        self.creature.feed()
        after = need_hunger(self.creature)
        self._note_growth(before_species, before_level, before_moves)
        return message.reply(
            TYPE_PET_FEED_OK,
            {
                "item": item,
                "hunger_delta": int(round(after - before)),
                "pet": self.pet_dict(),
            },
        )

    def _play(self, message: Message, payload: dict[str, Any]) -> Message:
        game = payload.get("game")
        if not isinstance(game, str) or not game.strip():
            return message.error("bad_payload", "pet.play needs payload.game as a non-empty string")
        game = game.strip()
        if self.creature.stats.energy < TRAIN_ENERGY_COST:
            return message.reply(
                TYPE_PET_PLAY_OK,
                {
                    "game": game,
                    "played": False,
                    "reason": "too tired",
                    "pet": self.pet_dict(),
                },
            )
        before_level = self.creature.stats.level
        before_moves = list(self.creature.known_move_ids)
        before_species = self.creature.species_id
        stat = _stat_for_game(game)
        self.creature.train(stat)
        self._note_growth(before_species, before_level, before_moves)
        return message.reply(
            TYPE_PET_PLAY_OK,
            {
                "game": game,
                "played": True,
                "pet": self.pet_dict(),
            },
        )

    def _gift(self, message: Message, payload: dict[str, Any]) -> Message:
        item = payload.get("item")
        if not isinstance(item, dict):
            return message.error("bad_payload", "pet.gift needs payload.item as an object")
        name = str(item.get("name") or "").strip()
        if not name:
            return message.error("bad_item", "pet.gift needs payload.item with a name")
        kind = str(item.get("kind") or "trinket").lower().strip() or "trinket"
        if kind not in GIFT_KINDS:
            kind = "trinket"
        power = _clean_power(item.get("power", 10))
        cleaned = {"name": name, "kind": kind, "power": power}

        before = (
            need_hunger(self.creature),
            _clamp(self.creature.stats.mood),
            _clamp(self.creature.stats.energy),
            self.creature.species.stage,
            self.creature.species_id,
        )
        accepted = True
        reaction = ""
        pet_name = PET_NAME

        if kind == "food":
            set_need_hunger(self.creature, need_hunger(self.creature) - power)
            self.creature.stats.mood = _clamp(self.creature.stats.mood + 5.0)
            self.creature.stats.clamp()
            reaction = f"{pet_name} munches the {name}. nom."
        elif kind == "toy":
            self.creature.stats.mood = _clamp(self.creature.stats.mood + power)
            self.creature.stats.energy = _clamp(self.creature.stats.energy - 5.0)
            self.creature.stats.clamp()
            reaction = f"{pet_name} bats the {name} around happily!"
        elif kind == "battery":
            self.creature.stats.energy = _clamp(self.creature.stats.energy + power)
            self.creature.stats.clamp()
            reaction = f"{pet_name} plugs into the {name}. charged up!"
        elif kind == "evolve":
            if self.creature.next_form_id() is None:
                accepted = False
                stage = self.creature.species.stage
                if stage == "mega":
                    reaction = f"{pet_name} is already mega — it hands the {name} back."
                else:
                    reaction = (
                        f"{pet_name} is already a {self.creature.species.name} "
                        f"({stage}). it keeps the {name} as a trophy."
                    )
            else:
                lines = self.creature.advance_evolution()
                new = self.creature.species
                reaction = (
                    f"the {name} glows... {pet_name} evolved into {new.name} ({new.stage})!"
                    if lines
                    else f"{pet_name} cannot evolve further. it keeps the {name} as a trophy."
                )
                if not lines:
                    accepted = False
                else:
                    previous = get_species(before[4])
                    self._queue_event(
                        "evolved",
                        {
                            "from": previous.name,
                            "to": new.name,
                            "form": new.name,
                            "stage": new.stage,
                            "from_stage": previous.stage,
                        },
                    )
        else:  # trinket
            self.creature.stats.mood = _clamp(self.creature.stats.mood + 10.0)
            self.creature.stats.clamp()
            reaction = f"{pet_name} adds the {name} to its hoard."

        if accepted:
            self.creature.receive_gift(name)

        effects = {
            "hunger": int(round(need_hunger(self.creature) - before[0])),
            "happiness": int(round(_clamp(self.creature.stats.mood) - before[1])),
            "energy": int(round(_clamp(self.creature.stats.energy) - before[2])),
            "stage": self.creature.species.stage if self.creature.species.stage != before[3] else None,
        }
        return message.reply(
            TYPE_PET_GIFT_OK,
            {
                "accepted": accepted,
                "item": cleaned,
                "reaction": reaction,
                "effects": effects,
                "pet": self.pet_dict(),
            },
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        word = mood_word(self.creature)
        return {
            "hungry": need_hunger(self.creature) >= HUNGRY_AT,
            "tired": _clamp(self.creature.stats.energy) <= TIRED_AT,
            "sad": word in {"grumpy"} or _clamp(self.creature.stats.mood) <= GRUMPY_AT,
            "species": self.creature.species_id,
            "level": self.creature.stats.level,
            "moves": list(self.creature.known_move_ids),
        }

    def _note_growth(self, before_species: str, before_level: int, before_moves: list[str]) -> None:
        if not self.events_enabled:
            return
        if self.creature.species_id != before_species:
            previous = get_species(before_species)
            now = self.creature.species
            self._queue_event(
                "evolved",
                {
                    "from": previous.name,
                    "to": now.name,
                    "form": now.name,
                    "stage": now.stage,
                    "from_stage": previous.stage,
                },
            )
        if self.creature.stats.level > before_level:
            self._queue_event("level_up", {"level": self.creature.stats.level})
        for move_id in self.creature.known_move_ids:
            if move_id not in before_moves:
                label = MOVE_LIBRARY[move_id].name if move_id in MOVE_LIBRARY else move_id
                self._queue_event("learned_move", {"move": label})

    def _collect_events(self) -> None:
        if not self.events_enabled:
            self._seen = self._snapshot()
            return
        now = self._snapshot()
        prev = self._seen
        if now["hungry"] and not prev.get("hungry"):
            self._queue_event("hungry", {})
        if now["tired"] and not prev.get("tired"):
            self._queue_event("tired", {})
        if now["sad"] and not prev.get("sad"):
            self._queue_event("sad", {})
        # evolved / level_up / learned_move / signal_found are queued at the source
        self._seen = now

    def _queue_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        if not self.events_enabled:
            return
        body = dict(detail or {})
        # Avoid queuing the same event twice in one burst (e.g. gift + collect).
        for pending in self._pending:
            if pending.payload.get("event") == event and pending.payload.get("detail") == body:
                return
        self._pending.append(
            Message(
                type=TYPE_PET_EVENT,
                id=new_id(),
                payload={"event": event, "detail": body, "pet": self.pet_dict()},
            )
        )


def _stat_for_game(game: str) -> str:
    """Pick a train() stat from the game name; default to attack."""
    lowered = game.lower()
    for stat in ("defense", "speed", "attack"):
        if stat in lowered:
            return stat
    if "spar" in lowered or "fight" in lowered or "battle" in lowered:
        return "attack"
    if "race" in lowered or "dash" in lowered:
        return "speed"
    if "guard" in lowered or "block" in lowered:
        return "defense"
    return "attack"


def _parse_frame(raw: Any, default: int) -> int:
    """Accept 0 or 1 (or the strings '0'/'1'). Missing -> default."""
    if raw is None:
        return int(default) % 2
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("pet.avatar frame must be 0 or 1")
    if isinstance(raw, str):
        raw = raw.strip()
        if raw not in {"0", "1"}:
            raise ValueError("pet.avatar frame must be 0 or 1")
        return int(raw)
    if int(raw) != raw or int(raw) not in (0, 1):
        raise ValueError("pet.avatar frame must be 0 or 1")
    return int(raw)


def _parse_mood_override(raw: Any) -> str | None:
    """None if omitted; otherwise a recognized mood word, or ValueError."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("pet.avatar mood must be a non-empty string")
    word = raw.strip()
    sprite_face_for_mood(word)  # validates
    return word

