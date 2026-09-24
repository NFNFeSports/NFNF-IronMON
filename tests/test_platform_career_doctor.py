"""App-root/packaging paths, config overrides, career statistics and the doctor report."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from helpers import AppTestCase

from nfnf_ironmon.career import GBA_FPS, compute_career, fmt_duration
from nfnf_ironmon.config import DEFAULTS, AppConfig
from nfnf_ironmon.doctor import render
from nfnf_ironmon.events import EventType
from nfnf_ironmon.paths import BUNDLE_ROOT, AppPaths, resolve_app_root
from nfnf_ironmon.runs import RunState


class AppRootTests(unittest.TestCase):
    def test_source_checkout(self):
        root = resolve_app_root(False, "/usr/bin/python3", "/x/NFNF-IronMON/nfnf_ironmon/paths.py")
        self.assertEqual(PurePosixPath(root).name, "NFNF-IronMON")

    def test_frozen_layouts(self):
        # portable layout: NFNF-IronMON/app/nfnf-ironmon(.exe)
        self.assertEqual(resolve_app_root(True, "/opt/NFNF-IronMON/app/nfnf-ironmon", "ignored"),
                         Path("/opt/NFNF-IronMON").resolve())
        # flat layout: NFNF-IronMON/nfnf-ironmon
        self.assertEqual(resolve_app_root(True, "/opt/NFNF-IronMON/nfnf-ironmon", "ignored"),
                         Path("/opt/NFNF-IronMON").resolve())

    def test_no_hardcoded_user_paths_in_code(self):
        for py in (BUNDLE_ROOT / "nfnf_ironmon").rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            with self.subTest(py.name):
                self.assertNotIn("/home/", text)
                self.assertNotIn("C:\\\\", text)


class SeparateHomeTests(AppTestCase):
    def test_bundled_defaults_copied_to_data_home(self):
        p = self.app.paths
        self.assertEqual(p.app_root, BUNDLE_ROOT)
        self.assertNotEqual(p.home, p.app_root)
        self.assertTrue((p.input_mappings_dir / "xbox-gba-labels.json").exists())
        if (BUNDLE_ROOT / "randomizer-profiles" / "firered-ironmon.rnqs").exists():
            self.assertTrue((p.profiles_dir / "firered-ironmon.rnqs").exists())
        # components resolve from the app root, never from user data
        self.assertEqual(self.app.components.app_root, BUNDLE_ROOT)

    def test_user_files_never_overwritten(self):
        custom = self.app.paths.rules_dir / "custom.json"
        custom.write_text(custom.read_text().replace('"Custom (example)"', '"Mine"'))
        AppPaths(self.home).ensure()
        self.assertIn('"Mine"', custom.read_text())

    def test_config_saves_overrides_only(self):
        fresh = self.tmp / "fresh"
        paths = AppPaths(fresh)
        paths.ensure()
        cfg = AppConfig.load(paths)
        cfg.save()
        self.assertEqual(json.loads(paths.config_file.read_text()), {"schema": 1})
        self.assertEqual(AppConfig.load(paths).get("default_randomizer_profile"),
                         DEFAULTS["default_randomizer_profile"])
        self.assertEqual(DEFAULTS["default_randomizer_profile"], "auto")
        self.assertEqual(DEFAULTS["tracker"], "none")


class ConfigOverrideTests(AppTestCase):
    def test_set_override_persists_only_that_key(self):
        self.app.config.set_override(("controller", "mapping"), "xbox-gba-positional")
        saved = json.loads(self.app.paths.config_file.read_text())
        self.assertEqual(saved["controller"], {"mapping": "xbox-gba-positional"})
        self.assertEqual(self.reopen().config.section("controller")["mapping"], "xbox-gba-positional")
        self.assertEqual(self.app.controllers.mapping_id, "xbox-gba-positional")


class CareerTests(AppTestCase):
    def test_empty(self):
        c = compute_career(self.app.db)
        self.assertEqual((c.attempts, c.best_progress, c.total_seconds), (0, "No badges yet", 0.0))
        self.assertIn("Attempts:         0", c.render())

    def test_career_statistics(self):
        self.import_firered()
        a = self.app.new_run(seed=1)
        self.app.record_event(a.id, EventType.BADGE_ACQUIRED, {"badge": "Boulder"})
        self.app.record_event(a.id, EventType.BADGE_ACQUIRED, {"badge": "Boulder"})   # duplicate
        self.app.record_event(a.id, EventType.BADGE_ACQUIRED, {"badge": "Cascade"})
        self.app.record_event(a.id, EventType.AREA_CHANGED, {"play_time_frames": int(GBA_FPS * 3600)})
        b = self.app.restart_run(a.id)           # a FAILED (attempt 1), b ACTIVE (attempt 2)
        self.app.new_run(launch=False)           # READY, never played: not an attempt
        now = datetime.now(timezone.utc) + timedelta(minutes=10)
        c = compute_career(self.app.db, now)
        self.assertEqual((c.attempts, c.failed, c.completed), (2, 1, 0))
        self.assertEqual((c.active_attempt, c.active_run_id), (2, b.id))
        self.assertEqual((c.best_badges, c.best_progress, c.best_run_id), (2, "Gym 2", a.id))
        self.assertEqual(fmt_duration(c.longest_seconds), "01:00:00")   # in-game time for run a
        self.assertEqual(c.time_sources, {"in-game", "wall-clock"})
        self.assertGreater(c.total_seconds, 3600)
        self.assertIn("Active Run:       #2", c.render())
        self.assertEqual(self.app.career().attempts, 2)

    def test_fmt(self):
        self.assertEqual(fmt_duration(19882), "05:31:22")
        self.assertEqual(fmt_duration(None), "--:--:--")


class DoctorTests(AppTestCase):
    def test_report_is_honest(self):
        report = self.app.doctor()
        flat = {(s["title"], i["label"]): i["status"] for s in report["sections"] for i in s["items"]}
        self.assertEqual(flat[("Tracker", "Integrated tracker engine")], "PLANNED")
        self.assertEqual(flat[("Emulator", "Interactive play")], "PLANNED")
        self.assertIn(flat[("Emulator", "Integrated backend")], ("PROTOTYPE", "MISSING"))
        self.assertEqual(flat[("Standalone build", "Windows")], "PLANNED")
        self.assertEqual(flat[("Standalone build", "Linux")], "DEVELOPMENT")
        self.assertEqual(flat[("Games", "Pokémon FireRed")], "MISSING")   # sandbox has no ROM
        self.assertEqual(flat[("Randomizer", "Seed reproducibility")], "NOT DETERMINISTIC")
        # nothing that is planned/prototype may be called READY
        for (section, label), status in flat.items():
            if section in ("Tracker", "Standalone build") or label == "Interactive play":
                self.assertNotEqual(status, "READY", (section, label))
        text = render(report)
        self.assertIn("NFNF IronMON Doctor", text)
        self.assertIn("Standalone build", text)

    def test_doctor_sees_rom_after_import_and_java_source(self):
        self.import_firered()
        flat = {i["label"]: i["status"] for s in self.app.doctor()["sections"] for i in s["items"]}
        self.assertEqual(flat["Pokémon FireRed"], "FOUND")
        self.assertIn(flat["Java runtime"], ("BUNDLED", "SYSTEM (DEV ONLY)", "MISSING", "OK"))


if __name__ == "__main__":
    unittest.main()
