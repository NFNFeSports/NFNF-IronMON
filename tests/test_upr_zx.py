"""UPR ZX adapter: settings validation, runtime resolution, command, execution (faked)."""

import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import make_rnqs, synthetic_gba

from nfnf_ironmon.components import ComponentManager
from nfnf_ironmon.games import FireRedGameAdapter
from nfnf_ironmon.hashing import sha256_file
from nfnf_ironmon.paths import BUNDLE_ROOT
from nfnf_ironmon.randomizers import RandomizationRequest, RandomizerError, UprZxRandomizer
from nfnf_ironmon.randomizers.rnqs import RnqsError, load_rnqs, parse_rnqs
from nfnf_ironmon.randomizers.upr_zx import parse_upr_log


class RnqsTests(unittest.TestCase):
    def test_valid(self):
        info = parse_rnqs(make_rnqs())
        self.assertEqual((info.version, info.rom_name, info.crc_ok), (322, "Fire Red (U) 1.1", True))
        self.assertTrue(info.settings_string_with_version.startswith("322"))

    def test_rejections(self):
        cases = {
            "short": b"\x00\x00",
            "crc": make_rnqs(corrupt=True),
            "length": make_rnqs() + b"extra",
            "too old": make_rnqs(version=100 << 24),
            "not base64": struct.pack(">ii", 322, 4) + b"!!!!",
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(RnqsError):
                parse_rnqs(data)
        with self.assertRaisesRegex(RnqsError, "newer"):
            parse_rnqs(make_rnqs(version=400), max_version=322)

    def test_bundled_firered_profile(self):
        path = BUNDLE_ROOT / "randomizer-profiles" / "firered-ironmon.rnqs"
        if not path.exists():
            self.skipTest("FireRed IronMON profile not fetched")
        info = load_rnqs(path, max_version=322)
        self.assertEqual(info.rom_name, "Fire Red (U) 1.1")
        self.assertEqual(info.version, 320)   # older than 4.6.1 → UPR upgrades it with a warning

    def test_missing_file(self):
        with self.assertRaisesRegex(RnqsError, "not found"):
            load_rnqs("/nonexistent/x.rnqs")


class UprLogTests(unittest.TestCase):
    def test_parse(self):
        log = ("﻿Randomizer Version: 4.6.1\nRandom Seed: 99491030677263\n"
               "Settings String: 322WQIE==\n\n--x--\nRandomization of Fire Red (U) 1.1 completed.\n")
        p = parse_upr_log(log)
        self.assertEqual((p.version, p.seed, p.settings_string, p.rom_name),
                         ("4.6.1", 99491030677263, "322WQIE==", "Fire Red (U) 1.1"))
        self.assertIsNone(parse_upr_log("nothing").seed)


class FakeComponents(ComponentManager):
    def __init__(self, paths):
        self.paths = paths

    def resolve(self, cid):
        return self.paths.get(cid)

    def spec(self, cid):
        return {"version": "4.6.1"}


class UprAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.java = self.tmp / "runtime" / "java"
        self.java.parent.mkdir(parents=True)
        self.java.write_bytes(b"")
        self.jar = self.tmp / "PokeRandoZX.jar"
        self.jar.write_bytes(b"jar")
        (self.tmp / "profiles").mkdir()
        (self.tmp / "profiles" / "p.rnqs").write_bytes(make_rnqs())
        self.rom = synthetic_gba(self.tmp / "orig.gba")
        self.ident = FireRedGameAdapter().identify_file(self.rom)
        self.calls = []

    def adapter(self, runner=None, config=None):
        comps = FakeComponents({"java-runtime": self.java, "upr-zx": self.jar})
        return UprZxRandomizer(config or {}, self.tmp, comps, runner or self.fake_runner())

    def fake_runner(self, rc=0, stdout="Randomized successfully!\n", stderr="", write=True,
                    change=True):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            if write:
                out = Path(cmd[cmd.index("-o") + 1])
                data = bytearray(self.rom.read_bytes())
                if change:
                    data[0x800] ^= 0xFF
                out.write_bytes(bytes(data))
                Path(str(out) + ".log").write_text(
                    "﻿Randomizer Version: 4.6.1\nRandom Seed: 424242\nSettings String: 322X\n",
                    encoding="utf-8")
            return subprocess.CompletedProcess(cmd, rc, stdout, stderr)
        return run

    def request(self, out="run/rom"):
        return RandomizationRequest(source_rom=self.rom, source_rom_sha256=sha256_file(self.rom),
                                    identity=self.ident, settings={"settings_file": "profiles/p.rnqs"},
                                    seed=77, output_dir=self.tmp / out, log_dir=self.tmp / "run/logs")

    def test_resolution_order(self):
        a = self.adapter()
        self.assertEqual(a.java_path(), (self.java, "bundled"))
        self.assertEqual(a.jar_path(), (self.jar, "bundled"))
        self.assertTrue(a.is_available()[0])
        cfg = self.adapter(config={"java": str(self.tmp / "missing")})
        self.assertEqual(cfg.java_path(), (None, "config (not found)"))
        self.assertFalse(cfg.is_available()[0])
        none = UprZxRandomizer({}, self.tmp, FakeComponents({}))
        self.assertIn(none.jar_path()[1], ("missing",))
        self.assertIn(none.java_path()[1], ("system", "missing"))  # PATH is dev-only fallback

    def test_info(self):
        info = self.adapter().info()
        self.assertEqual((info.version, info.deterministic, info.modifies_rom), ("4.6.1", False, True))

    def test_successful_randomization(self):
        res = self.adapter().randomize(self.request())
        cmd, kw = self.calls[0]
        self.assertEqual(cmd[0], str(self.java))
        self.assertIn("-Djava.awt.headless=true", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], str(self.rom.resolve()))
        self.assertEqual(Path(cmd[cmd.index("-o") + 1]).name, "randomized.gba")
        self.assertEqual(kw["timeout"], 300)
        self.assertEqual(res.output_rom.name, "randomized.gba")
        self.assertEqual((res.seed, res.actual_seed), (77, 424242))
        self.assertNotEqual(res.generated_rom_sha256, res.source_rom_sha256)
        self.assertEqual(res.settings["settings_file_sha256"], sha256_file(self.tmp / "profiles/p.rnqs"))
        self.assertTrue((self.tmp / "run/logs/upr-zx.log").exists())
        self.assertTrue((self.tmp / "run/logs/upr-zx-console.log").exists())
        self.assertFalse(Path(str(res.output_rom) + ".log").exists())  # moved out of rom/
        self.assertEqual(sorted(os.listdir(self.tmp / "run/rom")), ["randomized.gba"])
        d = res.to_dict()
        self.assertEqual((d["requestedSeed"], d["actualRandomizerSeed"], d["deterministic"]),
                         (77, 424242, False))

    def test_failures(self):
        cases = {
            "exit code": self.fake_runner(rc=1, stderr="ERROR: Randomization failed"),
            "no success marker": self.fake_runner(stdout="something else"),
            "no output": self.fake_runner(write=False),
        }
        for i, (name, runner) in enumerate(cases.items()):
            with self.subTest(name), self.assertRaises(RandomizerError):
                self.adapter(runner).randomize(self.request(out=f"r{i}"))

    def test_timeout(self):
        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with self.assertRaisesRegex(RandomizerError, "timed out"):
            self.adapter(slow).randomize(self.request())

    def test_refuses_existing_output_and_bad_settings(self):
        (self.tmp / "run/rom").mkdir(parents=True)
        (self.tmp / "run/rom/randomized.gba").write_bytes(b"old")
        with self.assertRaisesRegex(RandomizerError, "overwrite"):
            self.adapter().randomize(self.request())
        (self.tmp / "profiles" / "p.rnqs").write_bytes(make_rnqs(corrupt=True))
        with self.assertRaisesRegex(RandomizerError, "Invalid randomizer settings"):
            self.adapter().randomize(self.request(out="other"))
        self.assertEqual(self.calls, [])  # never invoked Java with bad input

    def test_output_never_next_to_source(self):
        req = self.request()
        req.output_dir = self.rom.parent
        with self.assertRaises(RandomizerError):
            self.adapter().randomize(req)


if __name__ == "__main__":
    unittest.main()
