"""NFNF native tracker: emulator memory → GameState → events.

``reader_for(game_id, identity, rom_bytes)`` returns the memory reader for a
game, or None when no tracker exists for it (the game then runs untracked and
integrity reports gameplay as UNKNOWN).
"""

from __future__ import annotations

from typing import Any

from .engine import TrackerConfig, TrackerEngine, TrackerEvent, TrackerMemory
from .state import BattleState, GameState, PokemonState

# game id -> (reader class path, table name chooser)
_GEN3 = {("firered", 1): "firered-v1.1", ("firered", 0): "firered-v1.0", ("leafgreen", 1): "leafgreen-v1.1"}
_GB = {"red": ("gen1", "red"), "blue": ("gen1", "blue"), "silver": ("gen2", "silver"), "gold": ("gen2", "gold")}


def reader_for(game_id: str, revision: int | None, rom: bytes, game_code: str | None = None) -> Any | None:
    if (game_id, revision) in _GEN3:
        if game_code not in (None, "BPRE", "BPGE"):
            return None          # non-English FRLG: address tables not verified
        from .gen3 import FRLGReader
        return FRLGReader(game_id, _GEN3[(game_id, revision)], rom)
    if game_id in _GB:
        gen, table = _GB[game_id]
        from .gen3 import DATA
        if not (DATA / f"{table}.json").exists():
            return None
        from .gen12 import Gen1Reader, Gen2Reader
        return (Gen1Reader if gen == "gen1" else Gen2Reader)(game_id, table, rom)
    return None


def engine_for(reader: Any, config: TrackerConfig | None = None,
               memory: TrackerMemory | None = None) -> TrackerEngine:
    return TrackerEngine(reader.game_id, badge_names=reader.badge_names, outcome_name=reader.outcome_name,
                         is_pokemon_center=reader.is_pokemon_center, config=config, memory=memory)


#: Capabilities an in-process memory tracker provides (checked by integrity).
NATIVE_CAPABILITIES = frozenset({"battles", "encounters", "faints", "party", "badges", "areas", "resets",
                                 "save_loads", "savestate_loads", "play_time"})

__all__ = ["reader_for", "engine_for", "NATIVE_CAPABILITIES", "TrackerConfig", "TrackerEngine",
           "TrackerEvent", "TrackerMemory", "GameState", "PokemonState", "BattleState"]
