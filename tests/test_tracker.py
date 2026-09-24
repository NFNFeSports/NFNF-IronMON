"""Native tracker: memory readers (synthetic memory) and the event engine (synthetic states)."""

import struct
import unittest

from nfnf_ironmon.events import EventType
from nfnf_ironmon.tracker import engine_for, reader_for
from nfnf_ironmon.tracker.engine import TrackerConfig, TrackerEngine, TrackerMemory
from nfnf_ironmon.tracker.gen3 import ORDERS, decode_pokemon, load_table
from nfnf_ironmon.tracker.memory import FakeMemory, GbWram
from nfnf_ironmon.tracker.state import BattleState, GameState, PokemonState
from nfnf_ironmon.tracker.text import GEN3, GEN12, decode_gen3, decode_gen12

G3 = {v: k for k, v in GEN3.items()}
G12 = {v: k for k, v in GEN12.items()}


def enc3(text: str, n: int) -> bytes:
    return (bytes(G3[c] for c in text) + b"\xff" * n)[:n]


def enc12(text: str, n: int) -> bytes:
    return (bytes(G12[c] for c in text) + b"\x50" * n)[:n]


def gen3_mon(species: int, level: int, hp: int, max_hp: int, personality=0x12345678, ot=0xCAFEBABE,
             status=0, corrupt=False) -> bytes:
    """Encode a struct Pokemon exactly like the game (encrypted substructures + checksum)."""
    growth = struct.pack("<HHIBBH", species, 0, 1000, 0, 70, 0)
    subs = {"G": growth, "A": bytes(12), "E": bytes(12), "M": bytes(12)}
    plain = b"".join(subs[c] for c in ORDERS[personality % 24])
    checksum = sum(struct.unpack("<24H", plain)) & 0xFFFF
    key = personality ^ ot
    secure = b"".join(struct.pack("<I", w ^ key) for w in struct.unpack("<12I", plain))
    head = struct.pack("<II", personality, ot) + enc3("NICK", 10) + bytes([2, 0x02]) + enc3("OT", 7) + b"\0"
    head += struct.pack("<HH", checksum ^ (1 if corrupt else 0), 0)
    tail = struct.pack("<IBBHH", status, level, 0, hp, max_hp) + bytes(10)
    return head + secure + tail


def fake_frlg_rom(table) -> bytes:
    a = table["addr"]
    rom = bytearray(0x400000)
    names = a["gSpeciesNames"] - 0x08000000
    for sid, name in ((0, "??????????"), (1, "BULBASAUR"), (25, "PIKACHU"), (94, "GENGAR")):
        rom[names + sid * 11:names + sid * 11 + 11] = enc3(name, 11)
    maps = a["sMapNames"] - 0x08000000
    text_at = 0x300000
    rom[text_at:text_at + 8] = enc3("ROUTE 3", 8)
    rom[maps + (0x60 - 0x58) * 4:maps + (0x60 - 0x58) * 4 + 4] = struct.pack("<I", 0x08000000 + text_at)
    return bytes(rom)


class Gen3ReaderTests(unittest.TestCase):
    def setUp(self):
        self.t = load_table("firered-v1.1")
        self.rom = fake_frlg_rom(self.t)
        self.reader = reader_for("firered", 1, self.rom, "BPRE")
        self.mem = FakeMemory()

    def write_world(self, party, in_battle=False, trainer=False, enemy=None, play=(1, 2, 3, 4), badges=0b11):
        a, m = self.t["addr"], self.mem
        sb1, sb2 = 0x02025000, 0x02024A00
        m.write(a["gSaveBlock1Ptr"], struct.pack("<I", sb1))
        m.write(a["gSaveBlock2Ptr"], struct.pack("<I", sb2))
        m.write(sb2, enc3("RED", 8))
        m.write(sb2 + 0x0E, struct.pack("<HBBB", *play))
        m.write(sb1, struct.pack("<hh", 7, 9))
        m.write(sb1 + 4, struct.pack("<bb", 3, 21))
        m.write(sb1 + 0xEE0 + (0x820 >> 3), bytes([badges]))
        m.write(a["gMapHeader"] + 0x12, struct.pack("<HB", 8, 0x60))
        m.write(a["gPlayerPartyCount"], bytes([len(party)]))
        for i, raw in enumerate(party):
            m.write(a["gPlayerParty"] + i * 100, raw)
        cb = (a["BattleMainCB2"] if in_battle else a["CB2_Overworld"]) | 1
        m.write(a["gMain"] + 4, struct.pack("<I", cb))
        if in_battle:
            m.write(a["gBattleTypeFlags"], struct.pack("<I", (1 << 3) if trainer else 0))
            m.write(a["gBattlerPartyIndexes"], struct.pack("<HH", 0, 0))
            if enemy:
                m.write(a["gEnemyParty"], enemy)
                m.write(a["gBattleMons"] + 0x58, struct.pack("<H", 25))   # active battler mirrors species
                m.write(a["gBattleMons"] + 0x58 + 0x28, struct.pack("<H", 11))
                m.write(a["gBattleMons"] + 0x58 + 0x2C, struct.pack("<H", 20))

    def test_decode_party_area_badges_time(self):
        self.write_world([gen3_mon(94, 18, 45, 60, status=0x40)])
        s = self.reader.read_state(self.mem, 1)
        self.assertTrue(s.session_valid)
        self.assertEqual(s.play_time_frames, (((1 * 60 + 2) * 60) + 3) * 60 + 4)
        self.assertEqual((s.area_id, s.area_name, s.player_xy, s.badges), ("3:21", "ROUTE 3", (7, 9), 0b11))
        self.assertEqual(s.badge_count, 2)
        p = s.party[0]
        self.assertEqual((p.species, p.level, p.hp, p.max_hp, p.status), ("GENGAR", 18, 45, 60, "PAR"))
        self.assertTrue(self.reader.is_pokemon_center(s))   # layout 8 = POKEMON_CENTER_1F

    def test_checksum_mismatch_is_never_trusted(self):
        self.assertIsNone(decode_pokemon(gen3_mon(94, 5, 1, 20, corrupt=True), self.reader.names))
        self.write_world([gen3_mon(94, 5, 0, 20, corrupt=True)])
        self.assertEqual(self.reader.read_state(self.mem, 1).party, ())

    def test_battle(self):
        self.write_world([gen3_mon(94, 5, 20, 20)], in_battle=True,
                         enemy=gen3_mon(25, 3, 20, 20, personality=0x0BADF00D, ot=1))
        b = self.reader.read_state(self.mem, 1).battle
        self.assertTrue(b.in_battle)
        self.assertEqual((b.kind, b.enemy.species, b.enemy.hp, b.enemy.max_hp), ("wild", "PIKACHU", 11, 20))

    def test_title_screen_is_not_a_session(self):
        self.assertFalse(self.reader.read_state(self.mem, 1).session_valid)
        self.write_world([], play=(0, 0, 0, 0))
        self.assertFalse(self.reader.read_state(self.mem, 1).session_valid)

    def test_text_decoders(self):
        self.assertEqual(decode_gen3(enc3("MR. MIME", 11)), "MR. MIME")
        self.assertEqual(decode_gen12(enc12("NIDORAN♂", 10)), "NIDORAN♂")


class GbReaderTests(unittest.TestCase):
    def gb_rom(self, table_name, sym, names):
        from nfnf_ironmon.tracker.gen12 import gb_rom_offset
        t = load_table(table_name)
        rom = bytearray(0x200000)
        off = gb_rom_offset(t["addr"][sym])
        for i, n in enumerate(names):
            rom[off + i * 10:off + i * 10 + 10] = enc12(n, 10)
        return bytes(rom), t

    def wram(self, t, writes):
        ram = bytearray(0x8000)
        view = GbWram(bytes(ram))
        for sym, data in writes:
            off = view.offset(t["addr"][sym] if isinstance(sym, str) else sym)
            ram[off:off + len(data)] = data
        return GbWram(bytes(ram))

    def test_gen1(self):
        rom, t = self.gb_rom("red", "MonsterNames", ["RHYDON", "KANGASKHAN"])
        r = reader_for("red", 0, rom)
        mon = bytearray(44)
        mon[0] = 1
        mon[1:3] = struct.pack(">H", 30)
        mon[12:14] = struct.pack(">H", 0x1234)
        mon[27:29] = struct.pack(">H", 0xABCD)
        mon[33] = 12
        mon[34:36] = struct.pack(">H", 40)
        mem = self.wram(t, [("wPartyCount", b"\x01"), ("wPartySpecies", b"\x01\xff"), ("wPartyMons", bytes(mon)),
                            ("wCurMap", b"\x0c"), ("wObtainedBadges", b"\x01"), ("wPlayTimeHours", b"\x01"),
                            ("wPlayTimeMinutes", b"\x02"), ("wIsInBattle", b"\x01"), ("wBattleType", b"\x00"),
                            ("wEnemyMon", bytes([2, 0, 9]) + bytes(11) + bytes([4, 0, 10]))])
        s = r.read_state(mem, 0)
        self.assertTrue(s.session_valid)
        self.assertEqual((s.area_name, s.badges), ("Route 1", 1))
        self.assertEqual(s.party[0].label(), "RHYDON Lv12 30/40")
        self.assertEqual(s.party[0].uid, "1234abcd")
        self.assertEqual((s.battle.kind, s.battle.enemy.species, s.battle.enemy.hp), ("wild", "KANGASKHAN", 9))

    def test_gen2_and_implausible_data(self):
        rom, t = self.gb_rom("silver", "PokemonNames", ["BULBASAUR", "IVYSAUR"])
        r = reader_for("silver", 0, rom)
        mon = bytearray(48)
        mon[0] = 2
        mon[6:8] = struct.pack(">H", 0x0042)
        mon[21:23] = struct.pack(">H", 0x7777)
        mon[31] = 5
        mon[34:36] = struct.pack(">H", 19)
        mon[36:38] = struct.pack(">H", 21)
        writes = [("wPartyCount", b"\x01"), ("wPartySpecies", b"\x02\xff"), ("wPartyMon1", bytes(mon)),
                  ("wMapGroup", b"\x18"), ("wMapNumber", b"\x03"), ("wGameTimeMinutes", b"\x05"),
                  ("wJohtoBadges", b"\x03"), ("wKantoBadges", b"\x01")]
        s = r.read_state(self.wram(t, writes), 0)
        self.assertEqual((s.area_name, s.badges, s.badge_count), ("Route 29", 0x103, 3))
        self.assertEqual(s.party[0].label(), "IVYSAUR Lv5 19/21")
        bad = bytearray(mon)
        bad[34:36] = struct.pack(">H", 99)     # hp > max hp → unreadable, not guessed
        s = r.read_state(self.wram(t, writes[:2] + [("wPartyMon1", bytes(bad))] + writes[3:]), 0)
        self.assertEqual(s.party, ())


def mon(uid="s1", species="GENGAR", hp=20, max_hp=20, level=5, slot=0, status="OK"):
    return PokemonState(uid, 94, species, level, hp, max_hp, status, slot=slot)


def st(frame=0, party=(), battle=None, area="3:21", name="ROUTE 3", badges=0, play=1000, valid=True, layout=0):
    return GameState(frame, "firered", valid, play if valid else None, area if valid else None,
                     name if valid else None, layout, badges if valid else None, tuple(party),
                     battle or BattleState(False))


class EngineTests(unittest.TestCase):
    def eng(self, **cfg):
        return TrackerEngine("firered", badge_names=("Boulder", "Cascade"),
                             outcome_name=lambda c: {1: "WON", 2: "LOST", 7: "CAUGHT"}.get(c),
                             is_pokemon_center=lambda s: s.map_layout == 8, config=TrackerConfig(**cfg))

    def types(self, events):
        return [e.type for e in events]

    def test_starter_area_badges(self):
        e = self.eng()
        evs = e.update(st(party=[mon()]))
        self.assertEqual(self.types(evs), [EventType.GAME_LOADED, EventType.AREA_CHANGED, "STARTER_OBTAINED",
                                           EventType.PARTY_CHANGED])
        self.assertEqual(evs[0].payload["play_time_frames"], 1000)
        evs = e.update(st(party=[mon()], badges=0b01))
        self.assertEqual([(x.type, x.payload["badge"]) for x in evs], [(EventType.BADGE_ACQUIRED, "Boulder")])

    def test_faint_is_debounced_and_transient_zero_ignored(self):
        e = self.eng(faint_confirm_polls=3)
        e.update(st(party=[mon()]))
        self.assertEqual(e.update(st(party=[mon(hp=0)])), [])
        e.update(st(party=[mon(hp=5)]))                         # recovered: not a faint
        for _ in range(2):
            self.assertNotIn(EventType.POKEMON_FAINTED, self.types(e.update(st(party=[mon(hp=0)]))))
        evs = e.update(st(party=[mon(hp=0)]))
        faint = [x for x in evs if x.type == EventType.POKEMON_FAINTED]
        self.assertEqual(len(faint), 1)
        self.assertTrue(faint[0].payload["is_starter"])
        self.assertEqual(e.update(st(party=[mon(hp=0)])), [])  # reported once

    def test_unstable_party_read_is_not_judged(self):
        e = self.eng()
        e.update(st(party=[mon()]))
        self.assertEqual(e.update(st(party=[])), [])           # one odd read (battle start rewrite)
        self.assertEqual(e.update(st(party=[mon()])), [])
        evs = e.update(st(party=[]))
        evs += e.update(st(party=[]))                          # stable for 2 polls → real change
        self.assertIn(EventType.PARTY_CHANGED, self.types(evs))

    def test_wild_encounter_first_duplicate_capture(self):
        e = self.eng()
        e.update(st(party=[mon()]))
        wild = BattleState(True, kind="wild", enemy=mon("w1", "PIDGEY", slot=None), player_active_uid="s1")
        evs = e.update(st(party=[mon()], battle=wild))
        enc = [x for x in evs if x.type == EventType.WILD_ENCOUNTER][0].payload
        self.assertEqual((enc["species"], enc["first_in_area"], enc["duplicate"]), ("PIDGEY", True, False))
        started = [x for x in evs if x.type == EventType.BATTLE_STARTED][0].payload
        self.assertTrue(started["lead_is_starter"])
        evs = e.update(st(party=[mon()], battle=BattleState(False, outcome=7)))
        self.assertEqual([x.payload["outcome"] for x in evs if x.type == EventType.BATTLE_ENDED], ["CAUGHT"])
        self.assertTrue(e.m.encounters["3:21"]["captured"])
        two = [mon(), mon("c1", "PIDGEY", slot=1)]
        e.update(st(party=two))
        evs = e.update(st(party=two))
        cap = [x for x in evs if x.type == EventType.POKEMON_CAPTURED]
        self.assertEqual((cap[0].payload["species"], cap[0].payload["method"]), ("PIDGEY", "caught"))
        evs = e.update(st(party=two, battle=wild))                  # same area again
        enc = [x for x in evs if x.type == EventType.WILD_ENCOUNTER][0].payload
        self.assertEqual((enc["first_in_area"], enc["duplicate"]), (False, True))

    def test_tutorial_battles_are_not_encounters(self):
        e = self.eng()
        e.update(st(party=[mon()]))
        evs = e.update(st(party=[mon()], battle=BattleState(True, kind="wild", tutorial=True,
                                                             enemy=mon("t", "WEEDLE"))))
        self.assertNotIn(EventType.WILD_ENCOUNTER, self.types(evs))

    def test_pokecenter_heal_vs_other_heal(self):
        e = self.eng()
        e.update(st(party=[mon(hp=5)]))
        evs = e.update(st(party=[mon(hp=20)], layout=8))
        self.assertEqual([x.payload["forced"] for x in evs if x.type == EventType.POKECENTER_HEAL], [False])
        e.update(st(party=[mon(hp=5)]))
        evs = e.update(st(party=[mon(hp=20)], layout=1))
        self.assertIn("PARTY_HEALED", self.types(evs))
        self.assertNotIn(EventType.POKECENTER_HEAL, self.types(evs))

    def test_reset_and_reload(self):
        e = self.eng()
        e.update(st(party=[mon()], play=5000))
        self.assertEqual(self.types(e.update(st(valid=False))), [EventType.GAME_RESET])
        evs = e.update(st(party=[mon()], play=3000))
        load = [x for x in evs if x.type == EventType.SAVE_LOADED][0].payload
        self.assertEqual((load["play_time_frames"], load["previous_play_time_frames"]), (3000, 5000))

    def test_memory_roundtrip(self):
        e = self.eng()
        e.update(st(party=[mon()]))
        restored = TrackerMemory.from_dict(e.m.to_dict())
        self.assertEqual(restored.starter_uid, "s1")
        e2 = TrackerEngine("firered", memory=restored)
        self.assertNotIn("STARTER_OBTAINED", self.types(e2.update(st(party=[mon()]))))

    def test_engine_for_reader(self):
        t = load_table("firered-v1.1")
        r = reader_for("firered", 1, fake_frlg_rom(t), "BPRE")
        self.assertEqual(engine_for(r).badge_names[0], "Boulder")
        self.assertIsNone(reader_for("firered", 1, b"", "BPRF"))    # non-English: not verified → no reader
        self.assertIsNone(reader_for("emerald", 0, b""))


if __name__ == "__main__":
    unittest.main()
