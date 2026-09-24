"""Mock tracker: events are injected by tests (or the CLI) instead of a game."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..games import RomIdentity
from .base import CAPABILITIES, TrackerAdapter, TrackerEvent, TrackerInstallation, TrackerPreparation


class MockTracker(TrackerAdapter):
    tracker_id = "mock"
    display_name = "Mock Tracker"
    supported_games = ("*",)
    capabilities = frozenset(CAPABILITIES)

    def __init__(self, config: dict[str, Any] | None = None, base: Path | None = None):
        super().__init__(config, base)
        self._pending: list[TrackerEvent] = []
        self.state: dict[str, Any] = {}

    def detect_installation(self) -> TrackerInstallation:
        return TrackerInstallation(self.tracker_id, True, Path("<mock>"))

    def detect_game(self, identity: RomIdentity) -> bool:
        return True

    def prepare(self, run_dir: Path, identity: RomIdentity) -> TrackerPreparation:
        return TrackerPreparation()

    def inject(self, event_type: str, **payload: Any) -> None:
        self._pending.append(TrackerEvent(event_type, payload))

    def poll_events(self) -> list[TrackerEvent]:
        events, self._pending = self._pending, []
        return events

    def get_game_state(self) -> dict[str, Any] | None:
        return dict(self.state)
