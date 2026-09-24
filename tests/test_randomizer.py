"""3. Seed generation  4. Deterministic randomizer metadata."""

import random
import tempfile
import unittest
from pathlib import Path

from helpers import synthetic_gba

from nfnf_ironmon.games import FireRedGameAdapter
from nfnf_ironmon.hashing import sha256_file
from nfnf_ironmon.randomizers import (MockRandomizer, RandomizationRequest, RandomizerError,
                                      UprZxRandomizer)
from nfnf_ironmon.randomizers.base import SEED_MAX, SEED_MIN


class SeedTests(unittest.TestCase):
    def test_seed_range(self):
        r = MockRandomizer()
        for _ in range(500):
            self.assertTrue(SEED_MIN <= r.generate_seed() <= SEED_MAX)

    def test_seed_with_rng_is_reproducible(self):
        r = MockRandomizer()
        a = [r.generate_seed(random.Random(7)) for _ in range(3)]
        b = [r.generate_seed(random.Random(7)) for _ in range(3)]
        self.assertEqual(a, b)

    def test_seeds_vary(self):
        r = MockRandomizer()
        self.assertGreater(len({r.generate_seed() for _ in range(50)}), 45)


class MockRandomizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rom = synthetic_gba(self.tmp / "src.gba")
        self.sha = sha256_file(self.rom)
        self.ident = FireRedGameAdapter().identify_file(self.rom)
        self.settings = {"preset": "standard-ironmon", "species_max": 386}

    def _run(self, seed, settings=None, out="out"):
        return MockRandomizer().randomize(RandomizationRequest(
            source_rom=self.rom, source_rom_sha256=self.sha, identity=self.ident,
            settings=settings or self.settings, seed=seed, output_dir=self.tmp / out))

    def test_same_inputs_same_metadata(self):
        a, b = self._run(18472931, out="a"), self._run(18472931, out="b")
        self.assertEqual(a.manifest, b.manifest)
        self.assertEqual(a.settings_sha256, b.settings_sha256)
        self.assertEqual(a.generated_rom_sha256, b.generated_rom_sha256)

    def test_seed_and_settings_change_output(self):
        base = self._run(1).manifest["derivation"]
        self.assertNotEqual(base, self._run(2, out="o2").manifest["derivation"])
        other = dict(self.settings, preset="kaizo")
        self.assertNotEqual(base, self._run(1, other, out="o3").manifest["derivation"])

    def test_metadata_fields(self):
        d = self._run(18472931).to_dict()
        for key in ("seed", "randomizer", "settingsSha256", "sourceRomSha256",
                    "generatedRomSha256", "timestamp"):
            self.assertIn(key, d)
        self.assertEqual(d["seed"], 18472931)
        self.assertEqual(d["sourceRomSha256"], self.sha)
        self.assertTrue(d["randomizer"]["deterministic"])
        self.assertEqual(set(d["randomizer"]), {"name", "version", "license", "deterministic",
                                                "modifies_rom"})
        self.assertEqual((d["requestedSeed"], d["actualRandomizerSeed"]), (18472931, 18472931))

    def test_source_rom_untouched(self):
        before = self.rom.read_bytes()
        self._run(5)
        self.assertEqual(self.rom.read_bytes(), before)

    def test_refuses_to_overwrite_source(self):
        class Evil(MockRandomizer):
            def _randomize(s, req):
                return req.source_rom, {}, None
        with self.assertRaises(RandomizerError):
            Evil().randomize(RandomizationRequest(
                source_rom=self.rom, source_rom_sha256=self.sha, identity=self.ident,
                settings=self.settings, seed=1, output_dir=self.tmp / "e"))

    def test_rejects_bad_seed(self):
        with self.assertRaises(RandomizerError):
            self._run(0)


class UprZxTests(unittest.TestCase):
    def test_not_deterministic_and_unavailable_without_config(self):
        upr = UprZxRandomizer({})
        self.assertFalse(upr.info().deterministic)
        ok, why = upr.is_available()
        self.assertFalse(ok)

    def test_cli_command_shape(self):
        cmd = UprZxRandomizer({"max_heap": "2048M"}).build_command(
            Path("s.rnqs"), Path("in.gba"), Path("out.gba"))
        self.assertEqual(cmd[1:], ["-Djava.awt.headless=true", "-Xmx2048M", "-jar", "PokeRandoZX.jar",
                                   "cli", "-s", "s.rnqs", "-i", "in.gba", "-o", "out.gba", "-l"])

    def test_version_from_jar_name(self):
        tmp = Path(tempfile.mkdtemp())
        jar = tmp / "PokeRandoZX-4.6.1.jar"
        jar.write_bytes(b"")
        self.assertEqual(UprZxRandomizer({"jar": str(jar)}).info().version, "4.6.1")
