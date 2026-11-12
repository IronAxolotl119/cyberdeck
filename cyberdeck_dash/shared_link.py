"""One companion connection for the whole interface.

In plain English: the main deck (cyberdeck/link.py) already knows how to
reach PULSE: the built-in sim pet, TCP, or a UART between two Pis. The
dashboard used to open its OWN extra TCP connection. In the combined
interface there is just one link, the deck's, and this file lets both
halves share it:

  * TeeCompanion wraps the deck's link. Every pushed pet.event is copied
    into two mailboxes, one for the deck's features (pet alerts, pop-ups)
    and one for the dashboard's PULSE panel, so neither steals the
    other's events. Everything else (request, is_linked, link_label…) is
    passed straight through, so deck commands never notice the wrapper.

  * SharedLink gives the dashboard's PetAdapter (pet.py) the small API it
    expects (online, pet, events, results, request(type, payload, cb))
    but sends everything over the deck's link. Requests run on a helper
    thread so a slow companion never freezes the screen; replies come
    back through `results` and are handled on the UI thread by
    PetAdapter.drain(), exactly like before.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from typing import Any, Callable

from cyberdeck.companion import CompanionError, SimCompanionLink
from cyberdeck.protocol import Message


# ---------------------------------------------------------------------------
# The event splitter around the deck's link
# ---------------------------------------------------------------------------
class TeeCompanion:
    """The deck's CompanionLink, with pushed events copied to two readers."""

    def __init__(self, link) -> None:
        self._link = link
        self._for_deck: collections.deque = collections.deque(maxlen=200)
        self._for_dash: collections.deque = collections.deque(maxlen=200)
        self._lock = threading.Lock()

    @property
    def inner(self):
        """The real link underneath (sim, tcp, or serial)."""
        return self._link

    @property
    def is_sim(self) -> bool:
        return isinstance(self._link, SimCompanionLink)

    def pump(self) -> None:
        """Move newly pushed events from the link into both mailboxes."""
        try:
            fresh = self._link.drain_events()
        except Exception:  # noqa: BLE001 — a link hiccup must not stop the UI
            fresh = []
        with self._lock:
            for message in fresh:
                self._for_deck.append(message)
                self._for_dash.append(message)

    def drain_events(self) -> list[Message]:
        """What deck features see (same shape as CompanionLink.drain_events)."""
        self.pump()
        with self._lock:
            out = list(self._for_deck)
            self._for_deck.clear()
        return out

    def drain_dash_events(self) -> list[Message]:
        """What the dashboard's PULSE panel sees."""
        self.pump()
        with self._lock:
            out = list(self._for_dash)
            self._for_dash.clear()
        return out

    # Everything else (request, is_linked, link_label, close, stats…) is
    # simply the real link's.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._link, name)


def tee(link) -> TeeCompanion:
    """Wrap a link once (wrapping twice would split events twice)."""
    return link if isinstance(link, TeeCompanion) else TeeCompanion(link)


# ---------------------------------------------------------------------------
# The dashboard-side face of that same link
# ---------------------------------------------------------------------------
class SharedLink:
    """Drop-in for pet.CompanionLink that rides on the deck's link.

    threaded=False (tests, --snapshot) runs each request immediately on
    the caller's thread, which is fine for the in-process sim pet.
    """

    def __init__(self, link: TeeCompanion, poll: float = 3.0, threaded: bool = True,
                 timeout: float | None = None) -> None:
        self.link = link
        self.poll = poll
        self.threaded = threaded
        self.timeout = timeout
        self.pet: dict[str, Any] = {}
        self.events: "queue.Queue[dict]" = queue.Queue()     # pet.event payloads
        self.results: "queue.Queue[tuple]" = queue.Queue()   # (callback, Message)
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_poll = 0.0

    # --- what pet.PetAdapter and the header read ----------------------------
    @property
    def online(self) -> bool:
        try:
            return bool(self.link.is_linked())
        except Exception:  # noqa: BLE001
            return False

    @property
    def status(self) -> str:
        try:
            return self.link.link_label()
        except Exception:  # noqa: BLE001
            return "link ?"

    # --- called from the UI thread ----------------------------------------------
    def request(self, typ: str, payload: dict | None = None, callback: Callable | None = None) -> None:
        if self.threaded:
            self._jobs.put((typ, payload or {}, callback))
        else:
            self._do(typ, payload or {}, callback)

    def start(self) -> None:
        if self.threaded and (self._thread is None or not self._thread.is_alive()):
            self._halt.clear()
            self._thread = threading.Thread(target=self._work, name="dash-shared-link", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._halt.set()

    def pump(self, now: float | None = None) -> None:
        """Pick up pushed events and the latest pet snapshot (UI thread, every frame)."""
        for message in self.link.drain_dash_events():
            payload = dict(message.payload or {})
            if isinstance(payload.get("pet"), dict):
                self.pet = payload["pet"]
            self.events.put(payload)
        snap = None
        try:
            snap = self.link.pet_snapshot()
        except Exception:  # noqa: BLE001
            snap = None
        if isinstance(snap, dict):
            self.pet = snap
        elif not self.threaded:
            # The sim link has no background poll: ask now and then.
            now = time.monotonic() if now is None else now
            if self.online and now - self._last_poll >= self.poll:
                self._last_poll = now
                self._do("pet.status", {}, None)

    # --- helper thread ------------------------------------------------------------
    def _work(self) -> None:
        while not self._halt.is_set():
            try:
                typ, payload, cb = self._jobs.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                if self.online and now - self._last_poll >= self.poll:
                    self._last_poll = now
                    try:
                        have_snapshot = isinstance(self.link.pet_snapshot(), dict)
                    except Exception:  # noqa: BLE001
                        have_snapshot = False
                    if not have_snapshot:
                        self._do("pet.status", {}, None)
                continue
            self._do(typ, payload, cb)

    def _do(self, typ: str, payload: dict, cb: Callable | None) -> None:
        try:
            if self.timeout is None:
                msg = self.link.request(typ, payload)
            else:
                msg = self.link.request(typ, payload, timeout=self.timeout)
        except CompanionError as exc:
            msg = Message("error", "local", {"code": "link_error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            msg = Message("error", "local", {"code": "link_error", "message": str(exc)})
        if msg.type == "pet.status.ok" and isinstance(msg.payload.get("pet"), dict):
            self.pet = msg.payload["pet"]
        if cb:
            self.results.put((cb, msg))
