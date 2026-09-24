"""Generation 1 (Red/Blue) and Generation 2 (Gold/Silver) memory readers.

Addresses from pret symbol files (``tracker/data/red.json``, ``silver.json``);
struct layouts from pret ``party_struct`` / ``battle_struct`` macros. Gen 1/2
Pokémon have no personality value, so identity is OT ID + DVs (stable across
evolution and level-ups).
"""

from __future__ import annotations

import struct

from .gen3 import load_table
from .memory import MemoryView, u8
from .state import STATUS_GEN12, BattleState, GameState, PokemonState, empty_state, status_name
from .text import decode_gen12

KANTO_BADGES = ("Boulder", "Cascade", "Thunder", "Rainbow", "Soul", "Marsh", "Volcano", "Earth")
JOHTO_BADGES = ("Zephyr", "Hive", "Plain", "Fog", "Storm", "Mineral", "Glacier", "Rising")
OUTCOMES = {0: "WON", 1: "LOST", 2: "DREW"}


def gb_rom_offset(sym: int) -> int:
    bank, addr = sym >> 16, sym & 0xFFFF
    return addr if bank == 0 else bank * 0x4000 + (addr - 0x4000)


class _GbReader:
    generation = 0
    party_size = 0
    layout: dict[str, int] = {}
    enemy_layout: dict[str, int] = {}
    badge_names: tuple[str, ...] = ()

    def __init__(self, game_id: str, table_name: str, rom: bytes):
        self.game_id = game_id
        self.table = load_table(table_name)
        self.a = self.table["addr"]
        self.c = self.table["constants"]
        self.maps = self.table.get("map_names", {})
        self.rom = rom
        self._names: dict[int, str] = {}

    # ---- helpers ---------------------------------------------------------
    def species(self, sid: int) -> str:
        raise NotImplementedError

    def _name_at(self, sym: str, index: int) -> str:
        off = gb_rom_offset(self.a[sym]) + index * 10
        return decode_gen12(self.rom[off:off + 10]) if 0 <= off < len(self.rom) - 10 else ""

    def _mon(self, raw: bytes, slot: int | None, lay: dict[str, int]) -> PokemonState | None:
        sid = raw[0]
        if sid in (0, 0xFF):
            return None
        hp = struct.unpack_from(">H", raw, lay["hp"])[0]
        max_hp = struct.unpack_from(">H", raw, lay["max_hp"])[0]
        level = raw[lay["level"]]
        if max_hp == 0 or hp > max_hp or not 1 <= level <= 100:
            return None     # implausible → treat as unreadable, never guess
        ident = (struct.unpack_from(">H", raw, lay["ot"])[0] if "ot" in lay else 0,
                 struct.unpack_from(">H", raw, lay["dvs"])[0])
        return PokemonState(uid=f"{ident[0]:04x}{ident[1]:04x}", species_id=sid, species=self.species(sid),
                            level=level, hp=hp, max_hp=max_hp,
                            status=status_name(raw[lay["status"]], STATUS_GEN12), slot=slot)

    def is_pokemon_center(self, state: GameState) -> bool | None:
        if state.area_name is None:
            return None
        n = state.area_name.lower()
        return "pokecenter" in n or "pokemon center" in n

    @staticmethod
    def outcome_name(code: int | None) -> str | None:
        return OUTCOMES.get(code & 0x0F) if code is not None else None

    # ---- per generation --------------------------------------------------
    def _play_time(self, mem: MemoryView) -> int | None:
        raise NotImplementedError

    def _area(self, mem: MemoryView) -> tuple[str, str | None]:
        raise NotImplementedError

    def _badges(self, mem: MemoryView) -> int:
        raise NotImplementedError

    def _battle(self, mem: MemoryView) -> tuple[bool, str | None, bool, bool]:
        raise NotImplementedError

    def read_state(self, mem: MemoryView, frame: int) -> GameState:
        a = self.a
        play = self._play_time(mem)
        count = u8(mem, a["wPartyCount"])
        if count > 6:
            return empty_state(frame, self.game_id, "party count implausible (title/intro)")
        species_list = mem.read(a["wPartySpecies"], 7)
        if species_list[count] != 0xFF:
            return empty_state(frame, self.game_id, "party list not terminated (title/intro)")
        if not play:
            return empty_state(frame, self.game_id, "new game not started (clock not running)")
        base = a["wPartyMons" if "wPartyMons" in a else "wPartyMon1"]
        raw = mem.read(base, self.party_size * 6)
        party = tuple(m for i in range(count)
                      if (m := self._mon(raw[i * self.party_size:(i + 1) * self.party_size], i, self.layout)))
        area_id, area_name = self._area(mem)
        in_battle, kind, tutorial, safari = self._battle(mem)
        battle = BattleState(False, outcome=u8(mem, a["wBattleResult"]))
        if in_battle:
            enemy = self._mon(mem.read(a["wEnemyMon"], 0x20), None, self.enemy_layout)
            battle = BattleState(True, kind=kind, tutorial=tutorial, safari=safari, enemy=enemy,
                                 player_active_uid=party[0].uid if party else None,
                                 outcome=u8(mem, a["wBattleResult"]))
        xy = (u8(mem, a["wXCoord"]), u8(mem, a["wYCoord"]))
        return GameState(frame=frame, game_id=self.game_id, session_valid=True, play_time_frames=play,
                         area_id=area_id, area_name=area_name, map_layout=None, badges=self._badges(mem),
                         party=party, battle=battle, player_xy=xy,
                         notes=("player_active_uid approximated by first party slot",) if in_battle else ())


class Gen1Reader(_GbReader):
    generation = 1
    party_size = 44
    layout = {"hp": 1, "status": 4, "ot": 12, "dvs": 27, "level": 33, "max_hp": 34}
    enemy_layout = {"hp": 1, "status": 4, "dvs": 12, "level": 14, "max_hp": 15}
    badge_names = KANTO_BADGES

    def species(self, sid: int) -> str:
        if sid not in self._names:   # MonsterNames is indexed by internal id - 1
            self._names[sid] = self._name_at("MonsterNames", sid - 1) or f"#{sid}"
        return self._names[sid]

    def _play_time(self, mem):
        a = self.a
        h, m, s, f = (u8(mem, a[k]) for k in ("wPlayTimeHours", "wPlayTimeMinutes", "wPlayTimeSeconds",
                                              "wPlayTimeFrames"))
        return (((h * 60 + m) * 60) + s) * 60 + f if m < 60 and s < 60 and f < 60 else None

    def _area(self, mem):
        mid = u8(mem, self.a["wCurMap"])
        return str(mid), self.maps.get(str(mid))

    def _badges(self, mem):
        return u8(mem, self.a["wObtainedBadges"])

    def _battle(self, mem):
        mode = u8(mem, self.a["wIsInBattle"])
        btype = u8(mem, self.a["wBattleType"])
        bt = self.c["battle_type"]
        in_battle = mode in (1, 2)
        return (in_battle, {1: "wild", 2: "trainer"}.get(mode), btype == bt["OLD_MAN"],
                btype == bt["SAFARI"])


class Gen2Reader(_GbReader):
    generation = 2
    party_size = 48
    layout = {"ot": 6, "dvs": 21, "level": 31, "status": 32, "hp": 34, "max_hp": 36}
    enemy_layout = {"dvs": 6, "level": 13, "status": 14, "hp": 16, "max_hp": 18}
    badge_names = JOHTO_BADGES + KANTO_BADGES

    def species(self, sid: int) -> str:
        if sid not in self._names:   # PokemonNames is in National Dex order
            self._names[sid] = self._name_at("PokemonNames", sid - 1) or f"#{sid}"
        return self._names[sid]

    def _play_time(self, mem):
        a = self.a
        h = struct.unpack("<H", mem.read(a["wGameTimeHours"], 2))[0]
        m, s, f = (u8(mem, a[k]) for k in ("wGameTimeMinutes", "wGameTimeSeconds", "wGameTimeFrames"))
        return (((h * 60 + m) * 60) + s) * 60 + f if m < 60 and s < 60 and f < 60 else None

    def _area(self, mem):
        g, n = u8(mem, self.a["wMapGroup"]), u8(mem, self.a["wMapNumber"])
        return f"{g}:{n}", self.maps.get(f"{g}:{n}")

    def _badges(self, mem):
        return u8(mem, self.a["wJohtoBadges"]) | (u8(mem, self.a["wKantoBadges"]) << 8)

    def _battle(self, mem):
        mode = u8(mem, self.a["wBattleMode"])
        btype = u8(mem, self.a["wBattleType"])
        bt = self.c["battle_type"]
        return (mode in (1, 2), {1: "wild", 2: "trainer"}.get(mode), btype == bt["TUTORIAL"],
                btype == bt["CONTEST"])
