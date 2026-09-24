"""Phase 2 pipeline: real-randomizer path (process faked), isolation, source safety."""

import json
import os
import shutil
from pathlib import Path

from helpers import AppTestCase, FakeUprRunner, synthetic_gb, synthetic_gba, use_fake_upr

from nfnf_ironmon.app import Application
from nfnf_ironmon.integrity import IntegrityStatus
from nfnf_ironmon.orchestrator import RunSetupError
from nfnf_ironmon.roms import RomRecord
from nfnf_ironmon.runs import RunState


def stat_snapshot(path):
    st = os.stat(path)
    return (path.read_bytes(), st.st_size, st.st_mtime_ns, st.st_mode)


class UprPipelineTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.runner = use_fake_upr(self.app, self.tmp)
        self.original = self.import_firered()

    def new(self, **kw):
        return self.app.new_run(profile_id="firered-ironmon", **kw)

    def test_run_structure_and_metadata(self):
        run = self.new(seed=18472931)
        self.assertEqual(sorted(p.name for p in (run.path / "rom").iterdir()), ["randomized.gba"])
        for f in ("metadata.json", "settings.json", "settings.rnqs", "events.jsonl", "integrity.json",
                  "randomizer.json", "logs/upr-zx.log", "logs/upr-zx-console.log"):
            self.assertTrue((run.path / f).exists(), f)
        s = json.loads((run.path / "settings.json").read_text())
        self.assertEqual((s["requested_seed"], s["actual_randomizer_seed"], s["deterministic"]),
                         (18472931, 424242, False))
        self.assertEqual(s["randomizer_settings"]["settings_file_sha256"],
                         json.loads((run.path / "randomizer.json").read_text())["manifest"]["settings_file"]["sha256"])
        r = run.row
        self.assertEqual((r["seed"], r["actual_randomizer_seed"], r["deterministic"]), (18472931, 424242, 0))
        self.assertEqual(r["source_rom_sha256"], self.original.sha256)
        self.assertNotEqual(r["generated_rom_sha256"], self.original.sha256)
        meta = self.app.runs.metadata(run.id)
        self.assertEqual((meta["requestedSeed"], meta["actualRandomizerSeed"], meta["deterministic"]),
                         (18472931, 424242, False))

    def test_every_run_starts_from_the_clean_original(self):
        a, b = self.new(), self.new()
        inputs = [c[0][c[0].index("-i") + 1] for c in self.runner.calls]
        self.assertEqual(inputs, [str(self.original.path.resolve())] * 2)
        self.assertNotEqual(a.path, b.path)   # isolated folders
        self.assertEqual(self.app.verify_run(a.id).status, IntegrityStatus.VERIFIED)

    def test_original_immutable(self):
        before = stat_snapshot(self.original.path)
        user_before = stat_snapshot(self.user_rom)
        self.new()
        self.assertEqual(stat_snapshot(self.original.path), before)
        self.assertEqual(stat_snapshot(self.user_rom), user_before)
        self.assertEqual([p.suffix for p in self.app.paths.originals_dir.rglob("*.gba")], [".gba"])

    def test_output_validation(self):
        use_fake_upr(self.app, self.tmp, FakeUprRunner(change=False))
        with self.assertRaisesRegex(RunSetupError, "unchanged"):
            self.new()
        use_fake_upr(self.app, self.tmp, FakeUprRunner(rc=1, stderr="ERROR: Randomization failed"))
        with self.assertRaisesRegex(RunSetupError, "exit 1"):
            self.new()
        self.assertTrue(all(r.status == RunState.ABANDONED for r in self.app.runs.list()))

    def test_output_must_be_same_game(self):
        class WrongGame(FakeUprRunner):
            def __call__(self, cmd, **kw):
                result = super().__call__(cmd, **kw)
                out = Path(cmd[cmd.index("-o") + 1])
                data = bytearray(out.read_bytes())
                data[0xAC:0xB0] = b"BPEE"      # now claims to be Emerald
                out.write_bytes(bytes(data))
                return result
        use_fake_upr(self.app, self.tmp, WrongGame())
        with self.assertRaisesRegex(RunSetupError, "same game"):
            self.new()

    def test_settings_file_tamper_invalid(self):
        run = self.new()
        (run.path / "settings.rnqs").write_bytes(b"tampered")
        report = self.app.verify_run(run.id)
        self.assertEqual({c.name: c.status for c in report.checks}["settings_file"], IntegrityStatus.INVALID)

    def test_generated_rom_never_reused_as_source(self):
        run = self.new()
        generated = run.path / "rom" / "randomized.gba"
        # by content: a copy of a generated ROM imported as if it were an original
        copy = self.tmp / "copied-generated.gba"
        shutil.copyfile(generated, copy)
        rec = self.app.import_rom(copy)
        with self.assertRaisesRegex(RunSetupError, "refusing to reuse"):
            self.app.new_run(profile_id="firered-ironmon", source_rom_sha256=rec.sha256)
        # by location: anything under runs/ is rejected outright
        in_runs = RomRecord(**{**rec.__dict__, "path": generated})
        with self.assertRaisesRegex(RunSetupError, "inside runs/"):
            self.app.orchestrator._guard_source(in_runs)


class AutoProfileAndLaunchTests(AppTestCase):
    def test_auto_prefers_real_randomizer(self):
        self.import_firered()
        use_fake_upr(self.app, self.tmp)
        self.assertEqual(self.app.orchestrator.auto_profile("firered"), "firered-ironmon")

    def test_auto_never_falls_back_to_mock(self):
        self.app.randomizers.get("upr-zx").config.update({"java": str(self.tmp / "nope")})
        with self.assertRaisesRegex(RunSetupError, "No real randomizer"):
            self.app.orchestrator.auto_profile("firered")

    def test_integrated_emulator_defers_launch_to_ready(self):
        self.import_firered()
        run = self.app.new_run(emulator_id="nfnf-libretro")
        self.assertEqual(run.status, RunState.READY)
        self.assertIsNone(run.row["attempt_number"])        # not played → not an attempt
        launch = self.app.runs.metadata(run.id)["launch"]
        self.assertTrue(launch["deferred"])
        self.assertIn("not implemented", launch["reason"])
        types = [e["type"] for e in self.app.runs.events(run.id)]
        self.assertNotIn("EMULATOR_STARTED", types)             # nothing fake was launched
        self.assertIn("CONTROLLER_PREPARED", types)
        nxt = self.app.restart_run(run.id, "new seed please")   # replacing a READY run abandons it
        self.assertEqual(self.app.runs.get(run.id).status, RunState.ABANDONED)
        self.assertEqual(nxt.row["previous_run_id"], run.id)

    def test_attempt_numbers(self):
        self.import_firered()
        a = self.app.new_run()
        b = self.app.restart_run(a.id)
        self.assertEqual((self.app.runs.get(a.id).row["attempt_number"], b.row["attempt_number"]), (1, 2))


class InPlaceDiscoveryTests(AppTestCase):
    def test_scan_is_read_only(self):
        orig = self.app.paths.originals_dir
        fr = synthetic_gba(orig / "My FireRed.gba")
        red = synthetic_gb(orig / "red.gb", b"POKEMON RED")
        (orig / "notes.txt").write_text("hi")
        junk = orig / "junk.gba"
        junk.write_bytes(b"\x00" * 512)
        fr.chmod(0o664)
        before = {p.name: stat_snapshot(p) for p in (fr, red, junk)}
        listing = sorted(os.listdir(orig))
        res = self.app.roms.scan()
        self.assertEqual({r.game_id for r in res.registered}, {"firered", "red"})
        self.assertEqual([f.name for f in res.unrecognised], ["junk.gba"])
        self.assertEqual({p.name: stat_snapshot(p) for p in (fr, red, junk)}, before)  # incl. mode
        self.assertEqual(sorted(os.listdir(orig)), listing)   # no sidecars, no copies
        rec = self.app.roms.find_original("firered")
        self.assertEqual(rec.path, fr.resolve())
        self.assertEqual(rec.metadata["storage"], "in_place")
        again = self.app.roms.scan()
        self.assertEqual((len(again.registered), len(again.known)), (0, 2))

    def test_run_new_discovers_automatically(self):
        synthetic_gba(self.app.paths.originals_dir / "fr.gba")
        run = self.app.new_run()
        self.assertEqual(run.status, RunState.ACTIVE)

    def test_duplicates_and_changed_content_reported(self):
        orig = self.app.paths.originals_dir
        fr = synthetic_gba(orig / "a.gba")
        shutil.copyfile(fr, orig / "b.gba")
        res = self.app.roms.scan()
        self.assertTrue(any("duplicate" in p for p in res.problems))
        data = bytearray(fr.read_bytes())
        data[0x900] ^= 1
        fr.write_bytes(bytes(data))
        self.assertTrue(any("content changed" in p for p in self.app.roms.scan().problems))

    def test_phase1_database_migrates_with_backup(self):
        import sqlite3
        from nfnf_ironmon.db import SCHEMA_V1
        db = self.tmp / "old" / "data" / "nfnf-ironmon.sqlite3"
        db.parent.mkdir(parents=True)
        c = sqlite3.connect(db)
        c.executescript(SCHEMA_V1)
        c.execute("PRAGMA user_version = 1")
        c.commit()
        c.close()
        app = Application(self.tmp / "old")
        try:
            from nfnf_ironmon.db import SCHEMA_VERSION
            self.assertEqual(app.db.schema_version, SCHEMA_VERSION)
            cols = {r["name"] for r in app.db.query("PRAGMA table_info(runs)")}
            self.assertTrue({"actual_randomizer_seed", "deterministic", "attempt_number"} <= cols)
            self.assertTrue((db.parent / "nfnf-ironmon.sqlite3.v1.bak").exists())
        finally:
            app.close()
