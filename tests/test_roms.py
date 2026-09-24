"""5. ROM SHA-256 calculation, identification and ROM manager safety."""

import hashlib
import os
import unittest

from helpers import AppTestCase, synthetic_gb, synthetic_gba

from nfnf_ironmon.games import GameNotSupportedError, GameRegistry
from nfnf_ironmon.hashing import file_digests, sha256_file, sha256_json
from nfnf_ironmon.roms import RomError


class HashTests(AppTestCase):
    def test_sha256_matches_hashlib(self):
        data = os.urandom(3 * 1024 * 1024 + 17)  # spans several read chunks
        p = self.tmp / "blob.bin"
        p.write_bytes(data)
        self.assertEqual(sha256_file(p), hashlib.sha256(data).hexdigest())
        d = file_digests(p)
        self.assertEqual(d["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(d["sha1"], hashlib.sha1(data).hexdigest())
        self.assertEqual(d["size"], len(data))

    def test_known_vector(self):
        p = self.tmp / "abc"
        p.write_bytes(b"abc")
        self.assertEqual(sha256_file(p),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_json_hash_ignores_key_order(self):
        self.assertEqual(sha256_json({"a": 1, "b": 2}), sha256_json({"b": 2, "a": 1}))


class IdentificationTests(AppTestCase):
    def test_firered_identified(self):
        ident = GameRegistry().identify_file(self.user_rom)
        self.assertEqual(ident.game_id, "firered")
        self.assertEqual(ident.revision, 1)
        self.assertTrue(ident.header_checksum_ok)
        self.assertIn("USA/Europe", ident.version)

    def test_bad_header_checksum_noted(self):
        p = synthetic_gba(self.tmp / "x.gba")
        data = bytearray(p.read_bytes())
        data[0xBD] ^= 0xFF
        p.write_bytes(bytes(data))
        ident = GameRegistry().identify_file(p)
        self.assertFalse(ident.header_checksum_ok)
        self.assertTrue(any("checksum" in n for n in ident.notes))

    def test_other_gba_game_not_matched(self):
        p = synthetic_gba(self.tmp / "e.gba", title=b"POKEMON EMER", code=b"BPEE")
        self.assertIsNone(GameRegistry().identify_file(p))

    def test_gen1_gen2_identified(self):
        red = synthetic_gb(self.tmp / "r.gb", b"POKEMON RED")
        silver = synthetic_gb(self.tmp / "s.gbc", b"POKEMON_SLVAAXE", cgb_flag=0x80)
        reg = GameRegistry()
        self.assertEqual(reg.identify_file(red).game_id, "red")
        self.assertEqual(reg.identify_file(silver).game_id, "silver")


class RomManagerTests(AppTestCase):
    def test_import_copies_and_never_touches_original(self):
        before = (self.user_rom.read_bytes(), self.user_rom.stat().st_mtime_ns)
        rec = self.import_firered()
        self.assertEqual((self.user_rom.read_bytes(), self.user_rom.stat().st_mtime_ns), before)
        self.assertNotEqual(rec.path, self.user_rom)
        self.assertTrue(rec.path.is_relative_to(self.app.paths.originals_dir))
        self.assertEqual(sha256_file(rec.path), rec.sha256)
        self.assertFalse(os.access(rec.path, os.W_OK))   # stored read-only
        meta = rec.metadata
        self.assertEqual((meta["game"], meta["source"], meta["original"]), ("Pokémon FireRed", "user", True))
        self.assertTrue(rec.path.with_suffix(".json").exists())

    def test_import_is_idempotent(self):
        a, b = self.import_firered(), self.import_firered()
        self.assertEqual(a.sha256, b.sha256)
        self.assertEqual(len(self.app.roms.list()), 1)

    def test_rejects_unknown_and_empty(self):
        junk = self.tmp / "junk.gba"
        junk.write_bytes(b"\x00" * 1024)
        with self.assertRaises(RomError):
            self.app.import_rom(junk)
        empty = self.tmp / "empty.gba"
        empty.write_bytes(b"")
        with self.assertRaises(RomError):
            self.app.import_rom(empty)

    def test_missing_rom_gives_actionable_error(self):
        with self.assertRaisesRegex(RomError, "rom import"):
            self.app.roms.find_original("firered")

    def test_planned_games_identify_but_cannot_run(self):
        from nfnf_ironmon.games import FireRedGameAdapter
        class PlannedGame(FireRedGameAdapter):   # stand-in for a detection-only game
            game_id, display_name, status = "planned-demo", "Planned Demo", "planned"
        self.app.games.register(PlannedGame())
        with self.assertRaises(GameNotSupportedError):
            self.app.new_run(game_id="planned-demo")
        self.assertEqual(self.app.runs.list(), [])

    def test_red_run_uses_the_red_profile(self):
        # the configured default profile is FireRed's; a Red run picks Red's equivalent
        self.app.import_rom(synthetic_gb(self.tmp / "r.gb", b"POKEMON RED"))
        run = self.app.new_run(game_id="red")
        self.assertEqual((run.game_id, run.row["randomizer_profile_id"]), ("red", "red-mock-standard"))

if __name__ == "__main__":
    unittest.main()
