"""Event system.

* Event types are strings kept in an extensible registry. Game adapters,
  trackers or plugins add new types with :func:`register_event_type`;
  publishing an unregistered type is an error (catches typos early).
* :class:`EventBus` dispatches synchronously but *breadth-first*: an event
  published from inside a handler is queued until every handler has seen the
  current event. ``call_soon`` defers work (e.g. failing a run) until the
  whole cascade has settled, so run state never changes mid-dispatch.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .util import utc_now

log = logging.getLogger(__name__)

WILDCARD = "*"

_REGISTRY: dict[str, str] = {}


def register_event_type(name: str, description: str = "") -> str:
    if not name or not name.replace("_", "").isalnum() or name.upper() != name:
        raise ValueError(f"Event type names must be UPPER_SNAKE_CASE: {name!r}")
    _REGISTRY.setdefault(name, description)
    return name


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def registered_event_types() -> dict[str, str]:
    return dict(_REGISTRY)


class EventType:
    """Built-in event types (constants are just registered strings)."""

    RUN_CREATED = register_event_type("RUN_CREATED", "Run directory and metadata created")
    RUN_STATE_CHANGED = register_event_type("RUN_STATE_CHANGED", "Run lifecycle transition")
    RUN_STARTED = register_event_type("RUN_STARTED", "Run became ACTIVE")
    RUN_ENDED = register_event_type("RUN_ENDED", "Run reached a terminal state")
    RUN_ARCHIVED = register_event_type("RUN_ARCHIVED", "Run sealed and archived")
    ROM_RANDOMIZED = register_event_type("ROM_RANDOMIZED", "Randomizer produced the run ROM")
    GAME_LOADED = register_event_type("GAME_LOADED", "Game booted in the emulator")
    GAME_RESET = register_event_type("GAME_RESET", "Soft/hard reset observed")
    SAVE_LOADED = register_event_type("SAVE_LOADED", "In-game save or save state loaded")
    SAVE_CREATED = register_event_type("SAVE_CREATED", "In-game save or save state written")
    AREA_CHANGED = register_event_type("AREA_CHANGED", "Player moved to another map/area")
    BATTLE_STARTED = register_event_type("BATTLE_STARTED", "Battle started")
    BATTLE_ENDED = register_event_type("BATTLE_ENDED", "Battle ended")
    WILD_ENCOUNTER = register_event_type("WILD_ENCOUNTER", "Wild Pokémon encountered")
    TRAINER_BATTLE = register_event_type("TRAINER_BATTLE", "Trainer battle started")
    POKEMON_CAPTURED = register_event_type("POKEMON_CAPTURED", "Pokémon caught")
    POKEMON_FAINTED = register_event_type("POKEMON_FAINTED", "Player Pokémon fainted")
    PARTY_CHANGED = register_event_type("PARTY_CHANGED", "Party composition changed")
    ITEM_ACQUIRED = register_event_type("ITEM_ACQUIRED", "Item obtained")
    ITEM_USED = register_event_type("ITEM_USED", "Item used")
    BADGE_ACQUIRED = register_event_type("BADGE_ACQUIRED", "Gym badge obtained")
    POKECENTER_HEAL = register_event_type("POKECENTER_HEAL", "Party healed at a Pokémon Center")
    ROM_LOADED = register_event_type("ROM_LOADED", "Emulator loaded a ROM")
    EMULATOR_STARTED = register_event_type("EMULATOR_STARTED", "Emulator process started")
    EMULATOR_STOPPED = register_event_type("EMULATOR_STOPPED", "Emulator process stopped")
    TRACKER_ATTACHED = register_event_type("TRACKER_ATTACHED", "Tracker attached to the emulator")
    CONTROLLER_PREPARED = register_event_type("CONTROLLER_PREPARED", "Controller detected/selected for the run")
    CONTROLLER_CONNECTED = register_event_type("CONTROLLER_CONNECTED", "Controller plugged in during play")
    CONTROLLER_DISCONNECTED = register_event_type("CONTROLLER_DISCONNECTED", "Controller unplugged during play")
    SCREENSHOT_TAKEN = register_event_type("SCREENSHOT_TAKEN", "Player took a screenshot")
    SESSION_INTERRUPTED = register_event_type("SESSION_INTERRUPTED", "Game session ended without clean shutdown")
    SESSION_RESUMED = register_event_type("SESSION_RESUMED", "Game session resumed for an active run")
    SESSION_CLOSED = register_event_type("SESSION_CLOSED", "Game session closed cleanly (run stays active)")
    STATE_RESTORED = register_event_type("STATE_RESTORED", "Emulator state restored by NFNF (recovery)")
    RULE_VIOLATION = register_event_type("RULE_VIOLATION", "A ruleset rule was triggered")
    INTEGRITY_WARNING = register_event_type("INTEGRITY_WARNING", "Integrity anomaly observed")
    PLAYER_NOTE = register_event_type("PLAYER_NOTE", "Free-form note or manual confirmation")


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    source: str = "system"
    timestamp: str = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, strict: bool = False):
        self._subs: dict[str, list[Handler]] = {}
        self._queue: deque[Event] = deque()
        self._deferred: deque[Callable[[], None]] = deque()
        self._dispatching = False
        self.strict = strict  # re-raise handler errors (tests)
        self.errors: list[tuple[Event, BaseException]] = []

    def subscribe(self, handler: Handler, event_type: str = WILDCARD) -> Callable[[], None]:
        if event_type != WILDCARD and not is_registered(event_type):
            raise ValueError(f"Unknown event type {event_type!r}; register it first")
        self._subs.setdefault(event_type, []).append(handler)
        return lambda: self._subs.get(event_type, []).remove(handler)

    def publish(self, event: Event) -> None:
        if not is_registered(event.type):
            raise ValueError(f"Unknown event type {event.type!r}; register it first")
        self._queue.append(event)
        self._drain()

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` once the current dispatch cascade has finished."""
        self._deferred.append(fn)
        self._drain()

    def _drain(self) -> None:
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._queue or self._deferred:
                while self._queue:
                    self._dispatch(self._queue.popleft())
                if self._deferred:
                    self._deferred.popleft()()
        finally:
            self._dispatching = False
            self._queue.clear()
            self._deferred.clear()

    def _dispatch(self, event: Event) -> None:
        handlers = list(self._subs.get(event.type, [])) + list(self._subs.get(WILDCARD, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # one bad handler must not break the others
                self.errors.append((event, exc))
                log.exception("Event handler failed for %s", event.type)
                if self.strict:
                    raise
