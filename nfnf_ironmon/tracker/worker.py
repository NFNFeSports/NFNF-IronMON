"""Background tracker worker.

Threading model (the render thread must never block on disk):

    render/emulation thread ── GameState every N frames ──►  TrackerWorker thread
                            ── "record event" jobs     ──►   engine.update → events
                                                              → RunManager.record_event (fsync, SQLite)
                                                              → EventBus → Rules / Integrity / Projections
    render thread ◄── latest state, rules status, pending failure (plain attributes)

Only the render thread touches the emulator core. The worker only sees
immutable GameState snapshots.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from ..events import Event, EventType
from ..util import write_json
from .engine import TrackerEngine, TrackerMemory
from .state import GameState

log = logging.getLogger(__name__)

_STOP = object()


class TrackerWorker:
    def __init__(self, record: Callable[[str, dict[str, Any]], Any], engine: TrackerEngine,
                 memory_path: Path | None = None, max_queue: int = 64):
        self.record = record                 # records one event for the run (thread-safe)
        self.engine = engine
        self.memory_path = memory_path
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.latest: GameState | None = None
        self.rules_status = "VALID"          # VALID | WARNING | VIOLATION | RUN_FAILED
        self.last_violation: str | None = None
        self.errors: list[str] = []
        self.dropped = 0
        self.events_recorded = 0
        self._thread = threading.Thread(target=self._loop, name="nfnf-tracker", daemon=True)
        self._thread.start()

    # --- producer side (render thread) --------------------------------
    def submit_state(self, state: GameState) -> None:
        self._put(("state", state))

    def submit_event(self, etype: str, payload: dict[str, Any]) -> None:
        self._put(("event", (etype, payload)))

    def _put(self, item) -> None:
        try:
            self.q.put_nowait(item)
        except queue.Full:
            self.dropped += 1           # never block rendering; the next state supersedes this one

    def on_rule_violation(self, event: Event) -> None:
        outcome = event.payload.get("outcome", "VIOLATION")
        order = ("VALID", "WARNING", "VIOLATION", "RUN_FAILED")
        if order.index(outcome) >= order.index(self.rules_status):
            self.rules_status = outcome
            self.last_violation = event.payload.get("description")

    # --- worker thread -------------------------------------------------
    def _loop(self) -> None:
        while True:
            item = self.q.get()
            if item is _STOP:
                self.q.task_done()
                return
            kind, data = item
            try:
                if kind == "state":
                    self.latest = data
                    events = self.engine.update(data)
                    for ev in events:
                        self.record(ev.type, ev.payload)
                        self.events_recorded += 1
                    if events:
                        self._persist()
                else:
                    self.record(*data)
                    self.events_recorded += 1
            except Exception as exc:  # noqa: BLE001 — keep tracking; surface the error in the HUD/log
                log.exception("tracker worker error")
                self.errors.append(str(exc))
            finally:
                self.q.task_done()

    def _persist(self) -> None:
        if self.memory_path:
            write_json(self.memory_path, self.engine.m.to_dict())

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued work is processed (used at run end and in tests)."""
        done = threading.Event()

        def waiter():
            self.q.join()
            done.set()
        threading.Thread(target=waiter, daemon=True).start()
        return done.wait(timeout)

    def stop(self) -> None:
        self.flush()
        self._persist()
        self.q.put(_STOP)
        self._thread.join(timeout=5)


def load_memory(path: Path) -> TrackerMemory | None:
    try:
        return TrackerMemory.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


__all__ = ["TrackerWorker", "load_memory", "EventType"]
