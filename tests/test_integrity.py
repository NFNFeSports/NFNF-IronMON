"""9. Integrity events and verification."""

import json

from helpers import AppTestCase

from nfnf_ironmon.events import EventType
from nfnf_ironmon.integrity import IntegrityStatus as S, worst
from nfnf_ironmon.roms import make_writable


class IntegrityTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.import_firered()
        self.run = self.app.new_run(seed=1234)

    def verify(self):
        return self.app.verify_run(self.run.id)

    def checks(self, report):
        return {c.name: c.status for c in report.checks}

    def test_worst_ordering(self):
        self.assertEqual(worst(S.VERIFIED, S.UNKNOWN), S.UNKNOWN)
        self.assertEqual(worst(S.SUSPICIOUS, S.INVALID, S.VERIFIED), S.INVALID)

    def test_clean_run_verified(self):
        report = self.verify()
        self.assertEqual(report.status, S.VERIFIED, report.to_dict())
        base = json.loads((self.run.path / "integrity.json").read_text())
        for key in ("rom", "seed", "settingsSha256", "rulesetSha256", "sourceRomSha256"):
            self.assertIn(key, base)
        self.assertEqual(base["lastVerification"]["status"], "VERIFIED")
        self.assertEqual(self.app.runs.get(self.run.id).integrity_status, "VERIFIED")

    def test_rom_replacement_invalid(self):
        rom = next((self.run.path / "rom").iterdir())
        make_writable(rom)
        with open(rom, "r+b") as fh:
            fh.seek(0x200)
            fh.write(b"\xFF")
        report = self.verify()
        self.assertEqual(report.status, S.INVALID)
        self.assertEqual(self.checks(report)["rom_hash"], S.INVALID)

    def test_settings_change_invalid(self):
        p = self.run.path / "settings.json"
        data = json.loads(p.read_text())
        data["seed"] = 1
        p.write_text(json.dumps(data))
        self.assertEqual(self.checks(self.verify())["settings_hash"], S.INVALID)

    def test_ruleset_change_invalid(self):
        p = self.run.path / "ruleset.json"
        data = json.loads(p.read_text())
        data["rules"] = data["rules"][:1]
        p.write_text(json.dumps(data))
        self.assertEqual(self.checks(self.verify())["ruleset_hash"], S.INVALID)

    def test_event_log_tamper_invalid(self):
        p = self.run.path / "events.jsonl"
        lines = p.read_text().splitlines()
        p.write_text("\n".join(lines[:3] + lines[4:]) + "\n")
        self.assertEqual(self.checks(self.verify())["event_log"], S.INVALID)

    def test_savestate_load_suspicious(self):
        self.app.record_event(self.run.id, EventType.SAVE_LOADED, {"kind": "savestate"})
        report = self.verify()
        self.assertEqual(report.status, S.SUSPICIOUS)
        types = [e["type"] for e in self.app.runs.events(self.run.id)]
        self.assertIn(EventType.INTEGRITY_WARNING, types)
        self.assertIn(EventType.RULE_VIOLATION, types)  # also flagged by standard ruleset
        self.assertEqual(self.app.runs.get(self.run.id).status.value, "ACTIVE")  # SOFT: no fail

    def test_rollback_suspicious(self):
        for frames in (1000, 5000):
            self.app.record_event(self.run.id, EventType.AREA_CHANGED, {"area": "Route 1",
                                                                        "play_time_frames": frames})
        self.app.record_event(self.run.id, EventType.SAVE_LOADED, {"kind": "save", "play_time_frames": 2000})
        rows = self.app.db.query("SELECT check_name, status FROM integrity_events WHERE run_id=?"
                                 " AND status='SUSPICIOUS'", (self.run.id,))
        self.assertEqual(rows, [{"check_name": "rollback", "status": "SUSPICIOUS"}])
        self.assertEqual(self.verify().status, S.SUSPICIOUS)

    def test_rollback_detected_after_restart(self):
        self.app.record_event(self.run.id, EventType.AREA_CHANGED, {"play_time_frames": 9000})
        app = self.reopen()  # in-memory cache gone; falls back to the DB
        app.record_event(self.run.id, EventType.AREA_CHANGED, {"play_time_frames": 10})
        self.assertEqual(app.verify_run(self.run.id).status, S.SUSPICIOUS)

    def test_resets_are_recorded_but_not_suspicious(self):
        self.app.record_event(self.run.id, EventType.GAME_RESET, {"kind": "soft"})
        rows = self.app.db.query("SELECT status FROM integrity_events WHERE check_name='game_reset'")
        self.assertEqual(rows, [{"status": "INFO"}])
        self.assertEqual(self.verify().status, S.VERIFIED)

    def test_wrong_rom_loaded_invalid(self):
        self.app.record_event(self.run.id, EventType.ROM_LOADED, {"rom_sha256": "0" * 64}, source="tracker:mock")
        self.assertEqual(self.verify().status, S.INVALID)

    def test_unobservable_tracker_is_unknown(self):
        # The community tracker exposes no telemetry yet -> gameplay cannot be verified.
        run = self.app.new_run(seed=5, tracker_id="ironmon-tracker")
        report = self.app.verify_run(run.id)
        self.assertEqual(report.status, S.UNKNOWN)
        self.assertEqual(self.checks(report)["telemetry"], S.UNKNOWN)

    def test_archived_run_still_verifiable(self):
        self.app.fail_run(self.run.id, "test")
        self.assertEqual(self.verify().status, S.VERIFIED)
        (self.run.path / "logs" / "sneaky.txt").write_text("x")
        self.assertEqual(self.checks(self.verify())["archive"], S.INVALID)
