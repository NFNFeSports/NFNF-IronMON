"""Generation 3 (FireRed / LeafGreen) memory reader.

Addresses: ``tracker/data/<game>.json`` (pret pokefirered symbols, cross-checked
with Ironmon-Tracker's MIT address tables). Structure layouts follow the
pokefirered decompilation (struct Pokemon / BoxPokemon / BattlePokemon /
SaveBlock1 / SaveBlock2 / MapHeader).
"""

from __future__ import annotations

import json
import struct
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from .memory import MemoryView, u8, u16, u32
from .state import BattleState, GameState, PokemonState, empty_state, status_name
from .text import decode_gen3

DATA = Path(__file__).resolve().parent / "data"

POKEMON_SIZE = 100
BATTLE_MON_SIZE = 0x58
# Substructure order for personality % 24 (G=growth, A=attacks, E=EVs, M=misc)
ORDERS = ("GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA", "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
          "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG", "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG")
KANTO_BADGES = ("Boulder", "Cascade", "Thunder", "Rainbow", "Soul", "Marsh", "Volcano", "Earth")
OUTCOMES = {0: None, 1: "WON", 2: "LOST", 3: "DREW", 4: "RAN", 5: "PLAYER_TELEPORTED", 6: "MON_FLED",
            7: "CAUGHT", 8: "NO_SAFARI_BALLS", 9: "FORFEITED", 10: "MON_TELEPORTED"}
EWRAM = range(0x02000000, 0x02040000)


@lru_cache(maxsize=None)
def load_table(name: str) -> dict:
    d = json.loads((DATA / f"{name}.json").read_text())
    d["addr"] = {k: int(v, 16) for k, v in d["addresses"].items()}
    return d


def decode_pokemon(raw: bytes, names: "RomNames", slot: int | None = None) -> PokemonState | None:
    """Decode a 100-byte struct Pokemon. Returns None for empty/invalid data."""
    if len(raw) < POKEMON_SIZE:
        return None
    personality, ot_id = struct.unpack_from("<II", raw, 0)
    flags = raw[19]
    if personality == 0 and ot_id == 0:
        return None
    if not (flags & 0x02) or (flags & 0x01):     # hasSpecies unset, or bad egg
        return None
    key = personality ^ ot_id
    secure = bytearray(raw[32:80])
    for i in range(0, 48, 4):
        struct.pack_into("<I", secure, i, struct.unpack_from("<I", secure, i)[0] ^ key)
    checksum = sum(struct.unpack_from("<24H", secure)) & 0xFFFF
    if checksum != struct.unpack_from("<H", raw, 28)[0]:
        return None                            # mid-write or corrupted: never trust it
    g = ORDERS[personality % 24].index("G") * 12
    species_id = struct.unpack_from("<H", secure, g)[0]
    if species_id == 0 or (flags & 0x04):      # empty, or an egg
        return None
    status, level = struct.unpack_from("<IB", raw, 80)
    hp, max_hp = struct.unpack_from("<HH", raw, 86)
    return PokemonState(uid=f"{personality:08x}{ot_id:08x}", species_id=species_id,
                        species=names.species(species_id), level=level, hp=hp, max_hp=max_hp,
                        status=status_name(status), nickname=decode_gen3(raw[8:18]) or None, slot=slot)


class RomNames:
    """Species and area names read from the game's own ROM tables."""

    def __init__(self, rom: bytes, table: dict):
        self.rom = rom
        self.t = table["addr"]
        self.mapsec_base = table["constants"].get("mapsec_base", 0x58)
        self._species: dict[int, str] = {}
        self._areas: dict[int, str | None] = {}

    def species(self, sid: int) -> str:
        if sid not in self._species:
            off = self.t["gSpeciesNames"] - 0x08000000 + sid * 11
            name = decode_gen3(self.rom[off:off + 11]) if 0 <= off < len(self.rom) - 11 else ""
            self._species[sid] = name or f"#{sid}"
        return self._species[sid]

    def area(self, mapsec: int) -> str | None:
        if mapsec not in self._areas:
            idx = mapsec - self.mapsec_base
            name = None
            if 0 <= idx < 109:
                ptr = struct.unpack_from("<I", self.rom, self.t["sMapNames"] - 0x08000000 + idx * 4)[0]
                if 0x08000000 <= ptr < 0x08000000 + len(self.rom):
                    name = decode_gen3(self.rom[ptr - 0x08000000:ptr - 0x08000000 + 24]) or None
            self._areas[mapsec] = name
        return self._areas[mapsec]


class FRLGReader:
    generation = 3

    def __init__(self, game_id: str, table_name: str, rom: bytes):
        self.game_id = game_id
        self.table = load_table(table_name)
        self.a = self.table["addr"]
        self.c = self.table["constants"]
        self.names = RomNames(rom, self.table)

    def _party(self, mem: MemoryView, base: int, count: int) -> tuple[PokemonState, ...]:
        raw = mem.read(base, POKEMON_SIZE * 6)
        out = []
        for i in range(min(max(count, 0), 6)):
            mon = decode_pokemon(raw[i * POKEMON_SIZE:(i + 1) * POKEMON_SIZE], self.names, slot=i)
            if mon:
                out.append(mon)
        return tuple(out)

    def read_state(self, mem: MemoryView, frame: int) -> GameState:
        a, c = self.a, self.c
        sb1, sb2 = u32(mem, a["gSaveBlock1Ptr"]), u32(mem, a["gSaveBlock2Ptr"])
        if sb1 not in EWRAM or sb2 not in EWRAM:
            return empty_state(frame, self.game_id, "save blocks not initialised")
        name0 = u8(mem, sb2)
        if name0 in (0x00, 0xFF):
            return empty_state(frame, self.game_id, "no player yet (title/intro)")
        hours, minutes, seconds, vblanks = struct.unpack("<HBBB", mem.read(sb2 + 0x0E, 5))
        play = (((hours * 60 + minutes) * 60) + seconds) * 60 + vblanks if minutes < 60 and seconds < 60 else None
        if not play:   # naming screen / intro: the clock starts when the overworld does
            return empty_state(frame, self.game_id, "new game not started (clock not running)")

        callback2 = u32(mem, a["gMain"] + 4) & ~1
        in_battle = callback2 == (a["BattleMainCB2"] & ~1)
        count = u8(mem, a["gPlayerPartyCount"])
        party = self._party(mem, a["gPlayerParty"], count if count <= 6 else 0)

        group, num = struct.unpack("<bb", mem.read(sb1 + 4, 2))
        header = mem.read(a["gMapHeader"], 0x1C)
        layout = struct.unpack_from("<H", header, 0x12)[0]
        mapsec = header[0x14]
        flags_byte = u8(mem, sb1 + c["saveblock1_flags_offset"] + (c["badge_flag_base"] >> 3))
        x, y = struct.unpack("<hh", mem.read(sb1, 4))

        battle = BattleState(False)
        if in_battle:
            bt = c["battle_type"]
            flags = u32(mem, a["gBattleTypeFlags"])
            enemy_idx = u16(mem, a["gBattlerPartyIndexes"] + 2)
            enemy = None
            if enemy_idx < 6:
                enemy = decode_pokemon(mem.read(a["gEnemyParty"] + enemy_idx * POKEMON_SIZE, POKEMON_SIZE),
                                       self.names, slot=enemy_idx)
                if enemy:   # live HP from the active opposing battler (gBattleMons[1])
                    bm = mem.read(a["gBattleMons"] + BATTLE_MON_SIZE, BATTLE_MON_SIZE)
                    if struct.unpack_from("<H", bm, 0)[0] == enemy.species_id:
                        enemy = replace(enemy, hp=struct.unpack_from("<H", bm, 0x28)[0],
                                        max_hp=struct.unpack_from("<H", bm, 0x2C)[0])
            player_idx = u16(mem, a["gBattlerPartyIndexes"])
            active = next((p.uid for p in party if p.slot == player_idx), None)
            battle = BattleState(
                in_battle=True, kind="trainer" if flags & bt["TRAINER"] else "wild",
                tutorial=bool(flags & (bt["OLD_MAN_TUTORIAL"] | bt["POKEDUDE"])),
                safari=bool(flags & bt["SAFARI"]), enemy=enemy, player_active_uid=active,
                trainer_id=u16(mem, a["gTrainerBattleOpponent_A"]) if flags & bt["TRAINER"] else None,
                outcome=u8(mem, a["gBattleOutcome"]))
        else:
            battle = BattleState(False, outcome=u8(mem, a["gBattleOutcome"]))

        return GameState(frame=frame, game_id=self.game_id, session_valid=True, play_time_frames=play,
                         area_id=f"{group}:{num}", area_name=self.names.area(mapsec), map_layout=layout,
                         badges=flags_byte, party=party, battle=battle, player_xy=(x, y))

    def is_pokemon_center(self, state: GameState) -> bool | None:
        if state.map_layout is None:
            return None
        return state.map_layout in self.c.get("pokemon_center_1f_layouts", [])

    @staticmethod
    def outcome_name(code: int | None) -> str | None:
        return OUTCOMES.get(code) if code is not None else None

    badge_names = KANTO_BADGES
