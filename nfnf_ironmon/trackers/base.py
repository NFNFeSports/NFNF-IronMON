"""Tracker adapter contract.

A tracker observes the running game and reports events/state. Each adapter
declares *capabilities* — what it can actually observe — so the integrity
system can say UNKNOWN instead of pretending it saw everything.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..emulators import EmulatorSession
from ..games import RomIdentity

CAPABILITIES = ("battles", "encounters", "faints", "party", "items", "badges", "areas",
                "resets", "save_loads", "savestate_loads", "play_time")


@dataclass
class TrackerInstallation:
    tracker_id: str
    found: bool
    path: Path | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"tracker": self.tracker_id, "found": self.found,
                "path": str(self.path) if self.path else None, "notes": self.notes}


@dataclass
class TrackerPreparation:
    lua_scripts: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TrackerEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


class TrackerAdapter(ABC):
    tracker_id: str = ""
    display_name: str = ""
    supported_games: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()

    def __init__(self, config: dict[str, Any] | None = None, base: Path | None = None):
        self.config = config or {}
        self.base = base
        self.attached = False

    @abstractmethod
    def detect_installation(self) -> TrackerInstallation: ...

    def detect_game(self, identity: RomIdentity) -> bool:
        return identity.game_id in self.supported_games

    @abstractmethod
    def prepare(self, run_dir: Path, identity: RomIdentity) -> TrackerPreparation:
        """Prepare files; return scripts the emulator must load."""

    def start(self, session: EmulatorSession | None) -> None:
        self.attached = True

    def stop(self) -> None:
        self.attached = False

    def poll_events(self) -> list[TrackerEvent]:
        """Events observed since the last poll (Phase 3 for real trackers)."""
        return []

    def get_game_state(self) -> dict[str, Any] | None:
        return None
