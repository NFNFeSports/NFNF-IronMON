"""8. Database persistence and gameplay projections."""

from helpers import AppTestCase

from nfnf_ironmon.db import SCHEMA_VERSION
from nfnf_ironmon.events import EventType

TABLES = {"games", "roms", "randomizer_profiles", "rulesets", "runs", "events", "pokemon",
          "encounters", "deaths", "integrity_events"}


class DatabaseTests(AppTestCase):
    def test_schema(self):
        names = {r["name"] for r in self.app.db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(TABLES <= names, TABLES - names)
        self.assertEqual(self.app.db.schema_version, SCHEMA_VERSION)

    def test_catalog_synced(self):
        games = {r["id"]: r["status"] for r in self.app.db.query("SELECT id, status FROM games")}
        self.assertEqual(games, {"firered": "supported", "red": "supported", "silver": "supported"})
        self.assertEqual(len(self.app.db.query("SELECT * FROM rulesets")), 3)
        self.assertGreaterEqual(len(self.app.db.query("SELECT * FROM randomizer_profiles")), 1)

    def test_everything_persists_across_restart(self):
        self.import_firered()
        run = self.app.new_run(seed=99)
        self.app.record_event(run.id, EventType.BADGE_ACQUIRED, {"badge": "Boulder"})
        app = self.reopen()
        again = app.runs.get(run.id)
        self.assertEqual((again.status.value, again.row["seed"]), ("ACTIVE", 99))
        self.assertEqual(len(app.roms.list()), 1)
        n = app.db.query_one("SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run.id,))["n"]
        self.assertEqual(n, len(app.runs.events(run.id)))  # DB index == run log

    def test_projections(self):
        self.import_firered()
        rid = self.app.new_run(seed=1).id
        self.app.record_event(rid, EventType.PARTY_CHANGED,
                              {"party": [{"species": "Bulbasaur", "level": 5, "is_starter": True}]})
        self.app.record_event(rid, EventType.WILD_ENCOUNTER, {"species": "Pidgey", "level": 3, "area": "Route 1"})
        self.app.record_event(rid, EventType.TRAINER_BATTLE, {"trainer": "Youngster Ben", "area": "Route 3"})
        self.app.record_event(rid, EventType.PARTY_CHANGED,
                              {"party": [{"species": "Bulbasaur", "level": 9, "is_starter": True}]})
        self.app.record_event(rid, EventType.POKEMON_FAINTED,
                              {"species": "Bulbasaur", "level": 9, "is_starter": True, "cause": "Brock"})
        mons = self.app.db.query("SELECT species, level, status, is_starter FROM pokemon WHERE run_id=?", (rid,))
        self.assertEqual(mons, [{"species": "Bulbasaur", "level": 9, "status": "FAINTED", "is_starter": 1}])
        enc = self.app.db.query("SELECT species, encounter_type FROM encounters WHERE run_id=? ORDER BY id", (rid,))
        self.assertEqual(enc, [{"species": "Pidgey", "encounter_type": "wild"},
                               {"species": "Youngster Ben", "encounter_type": "trainer"}])
        deaths = self.app.db.query("SELECT species, cause FROM deaths WHERE run_id=?", (rid,))
        self.assertEqual(deaths, [{"species": "Bulbasaur", "cause": "Brock"}])

    def test_ironmon_encounter_table(self):
        self.import_firered()
        rid = self.app.new_run(seed=3).id
        rec = lambda t, **p: self.app.record_event(rid, t, p, source="tracker:nfnf")
        rec(EventType.WILD_ENCOUNTER, species="PIDGEY", level=3, area="ROUTE 1", area_id="3:19",
            first_in_area=True, duplicate=False)
        rec(EventType.POKEMON_CAPTURED, species="PIDGEY", level=3, method="caught")
        rec(EventType.WILD_ENCOUNTER, species="RATTATA", level=2, area="ROUTE 1", area_id="3:19",
            first_in_area=False, duplicate=False)
        rows = self.app.db.query("SELECT area, species, first_in_area, duplicate, captured FROM encounters"
                                 " WHERE run_id=? ORDER BY id", (rid,))
        self.assertEqual(rows, [
            {"area": "ROUTE 1", "species": "PIDGEY", "first_in_area": 1, "duplicate": 0, "captured": 1},
            {"area": "ROUTE 1", "species": "RATTATA", "first_in_area": 0, "duplicate": 0, "captured": 0}])
