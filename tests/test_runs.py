"""1. Run creation  2. Run state transitions  10. Run archival."""

import json

from helpers import AppTestCase

from nfnf_ironmon.events import EventType
from nfnf_ironmon.runs import (TRANSITIONS, EventLog, InvalidTransition, RunArchivedError, RunError,
                               RunState)


class RunCreationTests(AppTestCase):
    def test_ids_are_sequential_and_zero_padded(self):
        a = self.app.runs.create_run("firered")
        b = self.app.runs.create_run("firered")
        self.assertEqual((a.id, b.id), ("RUN-000001", "RUN-000002"))

    def test_run_folder_layout(self):
        run = self.app.runs.create_run("firered")
        for name in ("metadata.json", "events.jsonl", "rom", "saves", "logs"):
            self.assertTrue((run.path / name).exists(), name)
        meta = json.loads((run.path / "metadata.json").read_text())
        self.assertEqual(meta["id"], run.id)
        self.assertEqual(meta["status"], "CREATED")
        self.assertEqual(run.row["path"], "runs/RUN-000001")  # stored relative -> portable

    def test_creation_is_logged(self):
        run = self.app.runs.create_run("firered")
        events = self.app.runs.events(run.id)
        self.assertEqual(events[0]["type"], EventType.RUN_CREATED)

    def test_ids_never_reused_even_if_db_is_lost(self):
        self.app.runs.create_run("firered")
        self.app.close()
        self.app.paths.db_path.unlink()
        app = self.reopen()
        self.assertEqual(app.runs.create_run("firered").id, "RUN-000002")


class RunStateTests(AppTestCase):
    def test_happy_path_and_timestamps(self):
        rid = self.app.runs.create_run("firered").id
        for s in (RunState.PREPARING, RunState.READY, RunState.ACTIVE):
            self.app.runs.transition(rid, s)
        run = self.app.runs.get(rid)
        self.assertEqual(run.status, RunState.ACTIVE)
        self.assertIsNotNone(run.row["started_at"])
        run = self.app.runs.transition(rid, RunState.FAILED, "starter fainted")
        self.assertTrue(run.is_terminal)
        self.assertEqual(run.row["end_reason"], "starter fainted")
        history = self.app.runs.metadata(rid)["history"]
        self.assertEqual([h["to"] for h in history], ["PREPARING", "READY", "ACTIVE", "FAILED"])
        types = [e["type"] for e in self.app.runs.events(rid)]
        self.assertIn(EventType.RUN_STARTED, types)
        self.assertIn(EventType.RUN_ENDED, types)

    def test_illegal_transitions_rejected(self):
        rid = self.app.runs.create_run("firered").id
        with self.assertRaises(InvalidTransition):
            self.app.runs.transition(rid, RunState.ACTIVE)  # must prepare first
        with self.assertRaises(InvalidTransition):
            self.app.runs.transition(rid, RunState.COMPLETED)

    def test_every_state_is_covered(self):
        self.assertEqual(set(TRANSITIONS), set(RunState))
        self.assertEqual(TRANSITIONS[RunState.INVALID], set())

    def test_finished_run_can_be_invalidated(self):
        rid = self.app.runs.create_run("firered").id
        self.app.runs.transition(rid, RunState.ABANDONED)
        self.app.runs.transition(rid, RunState.INVALID, "integrity")
        with self.assertRaises(InvalidTransition):
            self.app.runs.transition(rid, RunState.ACTIVE)


class RunArchivalTests(AppTestCase):
    def _finished_run(self):
        self.import_firered()
        run = self.app.new_run(seed=42)
        return self.app.orchestrator.fail_run(run.id, "test", archive=False)

    def test_cannot_archive_unfinished_run(self):
        rid = self.app.runs.create_run("firered").id
        with self.assertRaises(RunError):
            self.app.runs.archive(rid)

    def test_archive_seals_run(self):
        run = self._finished_run()
        manifest = self.app.runs.archive(run.id)
        self.assertIn("metadata.json", manifest["files"])
        self.assertTrue((run.path / "archive.json").exists())
        self.assertTrue(self.app.runs.get(run.id).archived)
        self.assertEqual(self.app.runs.verify_archive(run.id), [])
        rom = next((run.path / "rom").iterdir())
        with self.assertRaises(PermissionError):
            open(rom, "ab")
        with self.assertRaises(RunArchivedError):
            self.app.runs.record_event(run.id, EventType.PLAYER_NOTE, {"text": "late"})
        with self.assertRaises(RunArchivedError):
            self.app.runs.archive(run.id)

    def test_archive_detects_later_modification(self):
        run = self._finished_run()
        self.app.runs.archive(run.id)
        (run.path / "saves" / "extra.txt").write_text("x")
        self.assertEqual(self.app.runs.verify_archive(run.id), ["added after archival: saves/extra.txt"])


class EventLogTests(AppTestCase):
    def test_hash_chain_detects_edits(self):
        rid = self.app.runs.create_run("firered").id
        self.app.runs.record_event(rid, EventType.PLAYER_NOTE, {"text": "a"})
        path = self.app.runs.get(rid).path / "events.jsonl"
        self.assertEqual(EventLog.verify(path), [])
        lines = path.read_text().splitlines()
        lines[1] = lines[1].replace('"a"', '"b"')
        path.write_text("\n".join(lines) + "\n")
        self.assertTrue(EventLog.verify(path))

    def test_hash_chain_detects_deleted_line(self):
        rid = self.app.runs.create_run("firered").id
        for t in "abc":
            self.app.runs.record_event(rid, EventType.PLAYER_NOTE, {"text": t})
        path = self.app.runs.get(rid).path / "events.jsonl"
        lines = path.read_text().splitlines()
        del lines[2]
        path.write_text("\n".join(lines) + "\n")
        self.assertTrue(EventLog.verify(path))
