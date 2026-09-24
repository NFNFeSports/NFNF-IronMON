"""Adapter for the community Ironmon-Tracker (besteon/Ironmon-Tracker, MIT).

The tracker is an external Lua script the user installs; it runs *inside*
BizHawk/mGBA. Phase 1 detects it and hands its entry script to the emulator.
It exposes no external event API, so this adapter reports no capabilities
until the Phase 3 bridge (a tracker extension that forwards events) exists.
"""

from __future__ import annotations

from pathlib import Path

from ..games import RomIdentity
from .base import TrackerAdapter, TrackerInstallation, TrackerPreparation

ENTRY_SCRIPT = "Ironmon-Tracker.lua"


class IronmonTrackerAdapter(TrackerAdapter):
    tracker_id = "ironmon-tracker"
    display_name = "Ironmon-Tracker (community)"
    supported_games = ("firered", "leafgreen", "ruby", "sapphire", "emerald")
    capabilities = frozenset()  # nothing observable from outside the emulator yet

    def tracker_dir(self) -> Path | None:
        p = self.config.get("path")
        if not p:
            return None
        path = Path(p).expanduser()
        if not path.is_absolute() and self.base:
            path = self.base / path
        return path

    def detect_installation(self) -> TrackerInstallation:
        d = self.tracker_dir()
        if d and (d / ENTRY_SCRIPT).is_file():
            return TrackerInstallation(self.tracker_id, True, d / ENTRY_SCRIPT)
        return TrackerInstallation(self.tracker_id, False, notes=[
            "Set trackers.ironmon-tracker.path to the folder containing Ironmon-Tracker.lua"])

    def prepare(self, run_dir: Path, identity: RomIdentity) -> TrackerPreparation:
        inst = self.detect_installation()
        if not inst.found:
            return TrackerPreparation(notes=inst.notes)
        return TrackerPreparation(lua_scripts=[inst.path],
                                  notes=["Tracker events are not bridged to NFNF IronMON until Phase 3"])
