"""The built-in NFNF tracker (reads emulator memory in-process; see nfnf_ironmon/tracker/)."""

from __future__ import annotations

from pathlib import Path

from ..games import RomIdentity
from ..tracker import NATIVE_CAPABILITIES
from .base import TrackerAdapter, TrackerInstallation, TrackerPreparation

#: games with a verified memory reader (see nfnf_ironmon/tracker/__init__.py)
TRACKED = {("firered", 1): "firered-v1.1", ("firered", 0): "firered-v1.0", ("leafgreen", 1): "leafgreen-v1.1",
           ("red", 0): "red", ("silver", 0): "silver"}


class NativeTrackerAdapter(TrackerAdapter):
    tracker_id = "nfnf"
    display_name = "NFNF native tracker"
    supported_games = ("*",)
    capabilities = NATIVE_CAPABILITIES

    def detect_installation(self) -> TrackerInstallation:
        return TrackerInstallation(self.tracker_id, True, None, ["built in"])

    def detect_game(self, identity: RomIdentity) -> bool:
        return True   # untracked games still run; capabilities_for() reports nothing observable

    def has_reader(self, identity: RomIdentity) -> bool:
        if (identity.game_id, identity.revision) not in TRACKED:
            return False
        # English GBA releases only: other languages shift addresses (unverified)
        return identity.platform in ("gb", "gbc") or identity.game_code in ("BPRE", "BPGE")

    def capabilities_for(self, identity: RomIdentity) -> frozenset[str]:
        return NATIVE_CAPABILITIES if self.has_reader(identity) else frozenset()

    def prepare(self, run_dir: Path, identity: RomIdentity) -> TrackerPreparation:
        if self.has_reader(identity):
            return TrackerPreparation(notes=["Native memory tracker active"])
        return TrackerPreparation(notes=[f"No memory reader for {identity.game_name} {identity.version}: "
                                         "gameplay is not tracked"])
