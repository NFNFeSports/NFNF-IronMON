"""Game adapter contract.

The core never contains game-specific knowledge: everything it needs to know
about a particular Pokémon game is asked of a :class:`GameAdapter`. Adding a
game = adding an adapter module and registering it; nothing in the core
changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HEADER_BYTES = 0x200


class GameNotSupportedError(Exception):
    """The game is known (can be identified) but runs are not implemented yet."""


@dataclass(frozen=True)
class RomIdentity:
    game_id: str
    game_name: str
    platform: str            # "gba", "gb", "gbc"
    generation: int
    version: str             # human readable, e.g. "USA/Europe Rev 1"
    region: str
    revision: int
    header_title: str
    game_code: str
    header_checksum_ok: bool
    known_dump: str | None = None   # label of a matching reference dump, if any
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunContext:
    """What a game adapter gets when a run is initialised."""
    run_id: str
    run_dir: Path
    rom_path: Path
    identity: RomIdentity
    seed: int
    ruleset_id: str
    emulator_id: str
    tracker_id: str


class GameAdapter(ABC):
    game_id: str = ""
    display_name: str = ""
    generation: int = 0
    platform: str = ""
    rom_extensions: tuple[str, ...] = ()
    #: "supported" = runs can be created; "detected"/"planned" = identification only.
    status: str = "planned"
    supported_emulators: tuple[str, ...] = ()
    supported_trackers: tuple[str, ...] = ()
    supported_randomizers: tuple[str, ...] = ()
    #: SHA-1 of reference dumps -> label. SHA-1 is what No-Intro DATs publish.
    known_dumps: dict[str, str] = {}

    # --- identification -------------------------------------------------
    @abstractmethod
    def identify(self, header: bytes, size: int) -> RomIdentity | None:
        """Return an identity if the header belongs to this game, else None."""

    def identify_file(self, path: Path | str, sha1: str | None = None) -> RomIdentity | None:
        path = Path(path)
        if path.suffix.lower() not in self.rom_extensions:
            return None
        with open(path, "rb") as fh:
            header = fh.read(HEADER_BYTES)
        ident = self.identify(header, path.stat().st_size)
        if ident and sha1 and sha1 in self.known_dumps:
            ident = RomIdentity(**{**ident.to_dict(), "known_dump": self.known_dumps[sha1]})
        return ident

    def version_label(self, identity: RomIdentity) -> str:
        return identity.version

    # --- integration hints ------------------------------------------------
    def emulator_config(self, emulator_id: str) -> dict[str, Any]:
        """Emulator-specific hints (core, settings) for this game."""
        return {}

    def tracker_config(self, tracker_id: str) -> dict[str, Any]:
        """Tracker-specific hints for this game."""
        return {}

    def initialize_run(self, ctx: RunContext) -> dict[str, Any]:
        """Game-specific run initialisation. Returns metadata stored with the run."""
        if self.status != "supported":
            raise GameNotSupportedError(f"{self.display_name} runs are not implemented yet")
        return {}

    def describe(self) -> dict[str, Any]:
        return {"id": self.game_id, "name": self.display_name, "generation": self.generation,
                "platform": self.platform, "status": self.status,
                "emulators": list(self.supported_emulators),
                "trackers": list(self.supported_trackers),
                "randomizers": list(self.supported_randomizers)}
