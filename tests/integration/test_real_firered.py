"""Real FireRed randomization with bundled Java + UPR ZX (Part R).

Runs only when the user's FireRed dump is present in ``games/original/`` and
the components are bundled. The dump is located by header identification (no
filename is assumed) and is treated as immutable: its SHA-256, size, mtime and
permissions — and the folder listing — are compared before and after. All
output goes to a temporary home that is deleted afterwards.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import write_test_config

from nfnf_ironmon.app import Application
from nfnf_ironmon.games import GameRegistry
from nfnf_ironmon.paths import BUNDLE_ROOT
from nfnf_ironmon.runs import RunState

ORIGINALS = BUNDLE_ROOT / "games" / "original"


def fingerprint(directory: Path) -> dict:
    out = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(directory).as_posix()] = (
                hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def find_firered() -> Path | None:
    reg = GameRegistry()
    for p in sorted(ORIGINALS.rglob("*")) if ORIGINALS.is_dir() else []:
        if p.is_file() and p.suffix.lower() == ".gba":
            ident = reg.identify_file(p)
            if ident and ident.game_id == "firered":
                return p
    return None


class RealFireRedTest(unittest.TestCase):
    def setUp(self):
        self.firered = find_firered()
        if not self.firered:
            self.skipTest("No FireRed dump in games/original/")
        self.tmp = Path(tempfile.mkdtemp(prefix="nfnf-real-"))
        self.home = self.tmp / "home"
        write_test_config(self.home, {"default_randomizer_profile": "firered-ironmon",
                                      "emulator": "nfnf-libretro", "tracker": "none"})
        self.app = Application(self.home, strict_events=True)
        ok, why = self.app.randomizers.get("upr-zx").is_available()
        if not ok:
            self.app.close()
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.skipTest(f"UPR ZX not bundled: {why}")

    def tearDown(self):
        self.app.close()
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_randomization_leaves_original_untouched(self):
        before = fingerprint(ORIGINALS)
        project_runs_before = sorted(os.listdir(BUNDLE_ROOT / "runs"))

        scan = self.app.roms.scan(ORIGINALS)          # read-only, in place
        self.assertIn("firered", {r.game_id for r in scan.registered + scan.known})
        source = self.app.roms.find_original("firered")
        self.assertEqual(source.path, self.firered.resolve())

        run = self.app.new_run(seed=18472931)
        self.assertEqual(run.status, RunState.READY)   # integrated emulator: launch deferred
        rom = run.path / "rom" / "randomized.gba"
        self.assertTrue(rom.is_file())
        self.assertTrue(rom.resolve().is_relative_to(self.tmp.resolve()))
        self.assertNotEqual(run.row["generated_rom_sha256"], source.sha256)
        self.assertEqual(GameRegistry().identify_file(rom).game_id, "firered")
        self.assertIsInstance(run.row["actual_randomizer_seed"], int)
        self.assertEqual(run.row["deterministic"], 0)
        self.assertIn("Random Seed:", (run.path / "logs" / "upr-zx.log").read_text(encoding="utf-8"))

        report = self.app.verify_run(run.id)
        checks = {c.name: c.status.value for c in report.checks}
        for name in ("rom_hash", "settings_hash", "settings_file", "ruleset_hash", "event_log"):
            self.assertEqual(checks[name], "VERIFIED", name)
        self.assertEqual(report.status.value, "UNKNOWN")   # no tracker → gameplay not observed

        integ = self.app.emulators["nfnf-libretro"]
        if integ.detect_installation().found:
            res = integ.smoke_test(rom, self.tmp / "emu", frames=300, press_start=False)
            self.assertEqual(res.header_via_bus, "POKEMON FIREBPRE")
            self.assertGreater(res.distinct_colors, 4)   # something was drawn

        self.assertEqual(fingerprint(ORIGINALS), before)            # sha/size/mtime/mode
        self.assertEqual(sorted(os.listdir(BUNDLE_ROOT / "runs")), project_runs_before)


if __name__ == "__main__":
    unittest.main()
