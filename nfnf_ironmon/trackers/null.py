""""none" tracker: observes nothing and says so (no capabilities).

Used until the integrated NFNF tracker engine exists (Phase 3), so integrity
reports gameplay as UNKNOWN instead of implying anything was watched.
"""

from __future__ import annotations

from pathlib import Path

from ..games import RomIdentity
from .base import TrackerAdapter, TrackerInstallation, TrackerPreparation


class NullTracker(TrackerAdapter):
    tracker_id = "none"
    display_name = "No tracker"
    supported_games = ("*",)
    capabilities = frozenset()

    def detect_installation(self) -> TrackerInstallation:
        return TrackerInstallation(self.tracker_id, True, None, ["No gameplay observation"])

    def detect_game(self, identity: RomIdentity) -> bool:
        return True

    def prepare(self, run_dir: Path, identity: RomIdentity) -> TrackerPreparation:
        return TrackerPreparation(notes=["No tracker: gameplay events are not observed"])
