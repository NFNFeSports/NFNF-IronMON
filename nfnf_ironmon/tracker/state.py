"""Game-state models produced by the per-game memory readers.

Every field that could not be read reliably is ``None`` and is shown as
UNKNOWN — readers never invent values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

UNKNOWN = "UNKNOWN"

STATUS_GEN3 = ((0x7, "SLP"), (0x8, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR"), (0x80, "TOX"))
STATUS_GEN12 = ((0x7, "SLP"), (0x8, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR"))


def status_name(raw: int | None, table=STATUS_GEN3) -> str | None:
    if raw is None:
        return None
    for mask, name in table:
        if raw & mask:
            return name
    return "OK"


@dataclass(frozen=True)
class PokemonState:
    uid: str                     # stable identity within a run (personality/OT for Gen3, OT+DVs for Gen1/2)
    species_id: int
    species: str
    level: int | None
    hp: int | None
    max_hp: int | None
    status: str | None
    nickname: str | None = None
    slot: int | None = None

    @property
    def fainted(self) -> bool:
        return self.hp == 0 and (self.max_hp or 0) > 0

    def label(self) -> str:
        lvl = f" Lv{self.level}" if self.level is not None else ""
        hp = f" {self.hp}/{self.max_hp}" if self.hp is not None and self.max_hp else ""
        return f"{self.species}{lvl}{hp}"


@dataclass(frozen=True)
class BattleState:
    in_battle: bool
    kind: str | None = None          # "wild" | "trainer" | None
    tutorial: bool = False           # scripted battles (old man, Pokédude, first rival demo…)
    safari: bool = False
    enemy: PokemonState | None = None
    player_active_uid: str | None = None
    trainer_id: int | None = None
    outcome: int | None = None       # last battle outcome register (game specific codes)


@dataclass(frozen=True)
class GameState:
    frame: int
    game_id: str
    session_valid: bool              # a save is loaded / new game started (not title/intro)
    play_time_frames: int | None
    area_id: str | None
    area_name: str | None
    map_layout: int | None
    badges: int | None               # bitmask
    party: tuple[PokemonState, ...]
    battle: BattleState
    player_xy: tuple[int, int] | None = None
    notes: tuple[str, ...] = ()

    @property
    def badge_count(self) -> int | None:
        return None if self.badges is None else bin(self.badges).count("1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_state(frame: int, game_id: str, note: str = "") -> GameState:
    return GameState(frame=frame, game_id=game_id, session_valid=False, play_time_frames=None, area_id=None,
                     area_name=None, map_layout=None, badges=None, party=(), battle=BattleState(False),
                     notes=(note,) if note else ())


@dataclass
class EncounterRecord:
    area_id: str
    area_name: str | None
    species: str
    level: int | None
    first_in_area: bool
    duplicate: bool
    captured: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
