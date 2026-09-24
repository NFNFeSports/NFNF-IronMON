"""11. New-run creation, failure -> new run, and the end-to-end lifecycle."""

import json

from helpers import AppTestCase

from nfnf_ironmon.events import EventType
from nfnf_ironmon.hashing import sha256_file
from nfnf_ironmon.orchestrator import RunSetupError
from nfnf_ironmon.runs import RunState


class NewRunTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.rom = self.import_firered()

    def test_new_run_pipeline(self):
        run = self.app.new_run(seed=18472931)
        self.assertEqual(run.status, RunState.ACTIVE)
        for f in ("metadata.json", "settings.json", "ruleset.json", "randomizer.json",
                  "integrity.json", "events.jsonl"):
            self.assertTrue((run.path / f).exists(), f)
        settings = json.loads((run.path / "settings.json").read_text())
        self.assertEqual(settings["seed"], 18472931)
        self.assertEqual(settings["ruleset"]["id"], "standard-ironmon")
        rnd = json.loads((run.path / "randomizer.json").read_text())
        self.assertEqual(rnd["sourceRomSha256"], self.rom.sha256)
        out = self.app.paths.abs(rnd["outputRom"])
        self.assertTrue(out.is_relative_to(run.path / "rom"))
        self.assertEqual(sha256_file(out), rnd["generatedRomSha256"])
        self.assertEqual(sha256_file(self.rom.path), self.rom.sha256)  # original untouched
        types = [e["type"] for e in self.app.runs.events(run.id)]
        for t in (EventType.ROM_RANDOMIZED, EventType.EMULATOR_STARTED, EventType.ROM_LOADED,
                  EventType.TRACKER_ATTACHED, EventType.RUN_STARTED):
            self.assertIn(t, types)

    def test_no_launch_stops_at_ready(self):
        self.assertEqual(self.app.new_run(launch=False).status, RunState.READY)

    def test_random_seed_generated(self):
        a, b = self.app.new_run(), self.app.new_run()
        self.assertNotEqual(a.row["seed"], b.row["seed"])

    def test_unavailable_emulator_abandons_run(self):
        with self.assertRaises(RunSetupError) as ctx:
            self.app.new_run(emulator_id="bizhawk")   # not installed in the test env
        run = self.app.runs.get(ctx.exception.run_id)
        self.assertEqual(run.status, RunState.ABANDONED)
        self.assertIn("setup_error", run.row["end_reason"])
        self.assertIn("RunSetupError", (run.path / "logs" / "setup-error.log").read_text())

    def test_invalid_selection_creates_nothing(self):
        for kwargs in ({"ruleset_id": "nope"}, {"profile_id": "nope"}, {"emulator_id": "nope"},
                       {"game_id": "nope"}):
            with self.subTest(**kwargs), self.assertRaises(Exception):
                self.app.new_run(**kwargs)
        self.assertEqual(self.app.runs.list(), [])

    def test_upr_without_java_abandons_run(self):
        # Phase 1 asserted UPR could not run at all; Phase 2 runs it, so the
        # contract is now: a missing runtime abandons the run with a reason.
        self.app.randomizers.get("upr-zx").config["java"] = str(self.tmp / "no-java-here")
        with self.assertRaises(RunSetupError) as ctx:
            self.app.new_run(profile_id="firered-ironmon")
        run = self.app.runs.get(ctx.exception.run_id)
        self.assertEqual(run.status, RunState.ABANDONED)
        self.assertIn("Java runtime not found", run.row["end_reason"])

    def test_tracker_events_drive_rules(self):
        run = self.app.new_run(seed=7)
        tracker = self.app.trackers["mock"]
        tracker.inject(EventType.WILD_ENCOUNTER, species="Rattata", level=2, area="Route 1")
        tracker.inject(EventType.POKEMON_FAINTED, species="Squirtle", is_starter=True, level=11)
        self.assertEqual(self.app.orchestrator.pump_tracker(run.id), 2)
        run = self.app.runs.get(run.id)
        self.assertEqual(run.status, RunState.FAILED)
        self.assertEqual(run.row["end_reason"], "rule:starter-faint-ends-run")
        self.assertTrue(run.archived)

    def test_auto_new_run_on_failure(self):
        self.app.config.data["auto_new_run_on_failure"] = True
        run = self.app.new_run(seed=7)
        self.app.record_event(run.id, EventType.POKECENTER_HEAL, {"location": "Viridian City"})
        self.assertEqual(self.app.runs.get(run.id).status, RunState.FAILED)
        nxt = self.app.runs.current()
        self.assertEqual(nxt.row["previous_run_id"], run.id)
        self.assertEqual(nxt.status, RunState.ACTIVE)

    def test_restart_after_failure(self):
        first = self.app.new_run(seed=111)
        second = self.app.restart_run(first.id, "wiped at Brock")
        first = self.app.runs.get(first.id)
        self.assertEqual((first.status, first.archived), (RunState.FAILED, True))
        self.assertEqual(second.row["previous_run_id"], first.id)
        self.assertNotEqual(second.row["seed"], 111)
        self.assertEqual(second.row["ruleset_id"], first.row["ruleset_id"])
        self.assertEqual(second.status, RunState.ACTIVE)
        # clean environment: fresh folder, only its own ROM, fresh event log
        self.assertNotEqual(second.path, first.path)
        self.assertEqual(len(list((second.path / "rom").iterdir())), 1)
        self.assertEqual(self.app.runs.events(second.id)[0]["type"], EventType.RUN_CREATED)


    def test_restart_of_already_failed_run(self):
        first = self.app.new_run(seed=3)
        self.app.record_event(first.id, EventType.POKEMON_FAINTED, {"is_starter": True})
        self.assertTrue(self.app.runs.get(first.id).archived)
        second = self.app.restart_run(first.id)
        self.assertEqual((second.status, second.row["previous_run_id"]), (RunState.ACTIVE, first.id))
        self.assertEqual(self.app.runs.get(first.id).status, RunState.FAILED)


class EndToEndTest(AppTestCase):
    """create run -> metadata -> seed -> run dir -> event -> ACTIVE -> fail -> archive -> second run.

    Uses the orchestrator's individual building blocks explicitly, with no ROM
    needed beyond a synthetic header file."""

    def test_full_lifecycle(self):
        app = self.app
        self.import_firered()
        orch = app.orchestrator

        # create run (CREATED) + generate metadata + generate seed + run directory
        run = orch.start_new_run(game_id="firered", ruleset_id="standard-ironmon", launch=False)
        meta = app.runs.metadata(run.id)
        self.assertEqual((run.id, run.status), ("RUN-000001", RunState.READY))
        self.assertIsInstance(run.row["seed"], int)
        self.assertEqual(meta["seed"], run.row["seed"])
        self.assertTrue(run.path.is_dir())

        # record event
        app.record_event(run.id, EventType.PLAYER_NOTE, {"text": "ready to go"})

        # mark run active
        orch._launch(run.id, app.games.get("firered"), app.emulators["mock"], app.trackers["mock"])
        run = app.runs.transition(run.id, RunState.ACTIVE)
        self.assertEqual(run.status, RunState.ACTIVE)
        app.record_event(run.id, EventType.WILD_ENCOUNTER, {"species": "Caterpie", "level": 3})

        # fail run + archive run
        run = orch.fail_run(run.id, "starter fainted to Brock")
        self.assertEqual(run.status, RunState.FAILED)
        self.assertTrue(run.archived)
        self.assertEqual(app.runs.verify_archive(run.id), [])
        self.assertEqual(run.integrity_status, "VERIFIED")

        # create second run
        second = orch.restart_after_failure(run.id)
        self.assertEqual(second.id, "RUN-000002")
        self.assertEqual(second.status, RunState.ACTIVE)
        self.assertEqual(second.row["previous_run_id"], "RUN-000001")
        self.assertNotEqual(second.row["seed"], run.row["seed"])
        self.assertEqual([r.id for r in app.runs.list()], ["RUN-000001", "RUN-000002"])
        self.assertEqual(app.runs.current().id, "RUN-000002")
        self.assertEqual(app.bus.errors, [])
