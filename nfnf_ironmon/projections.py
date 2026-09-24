"""Projects gameplay events into the pokemon / encounters / deaths tables."""

from __future__ import annotations

from .db import Database
from .events import Event, EventBus, EventType
from .util import utc_now


class GameplayProjection:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        bus.subscribe(self._encounter, EventType.WILD_ENCOUNTER)
        bus.subscribe(self._encounter, EventType.TRAINER_BATTLE)
        bus.subscribe(self._captured, EventType.POKEMON_CAPTURED)
        bus.subscribe(self._party, EventType.PARTY_CHANGED)
        bus.subscribe(self._fainted, EventType.POKEMON_FAINTED)

    def _encounter(self, e: Event) -> None:
        if not e.run_id:
            return
        p = e.payload
        kind = "wild" if e.type == EventType.WILD_ENCOUNTER else "trainer"
        self.db.execute(
            "INSERT INTO encounters (run_id, event_id, species, level, area, encounter_type, timestamp)"
            " VALUES (?,?,?,?,?,?,?)",
            (e.run_id, e.event_id, p.get("species") or p.get("trainer"), p.get("level"),
             p.get("area"), kind, e.timestamp))

    def _upsert(self, e: Event, mon: dict, status: str = "ALIVE") -> None:
        species = mon.get("species")
        if not species:
            return
        existing = self.db.query_one(
            "SELECT id FROM pokemon WHERE run_id = ? AND species = ? AND COALESCE(nickname,'') = ?",
            (e.run_id, species, mon.get("nickname") or ""))
        if existing:
            self.db.execute("UPDATE pokemon SET level = COALESCE(?, level), updated_at = ? WHERE id = ?",
                            (mon.get("level"), utc_now(), existing["id"]))
        else:
            self.db.execute(
                "INSERT INTO pokemon (run_id, species, nickname, level, is_starter, status, event_id,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (e.run_id, species, mon.get("nickname"), mon.get("level"),
                 int(bool(mon.get("is_starter"))), status, e.event_id, utc_now()))

    def _captured(self, e: Event) -> None:
        if e.run_id:
            self._upsert(e, e.payload)

    def _party(self, e: Event) -> None:
        if e.run_id:
            for mon in e.payload.get("party", []):
                self._upsert(e, mon)

    def _fainted(self, e: Event) -> None:
        if not e.run_id:
            return
        p = e.payload
        self.db.execute(
            "INSERT INTO deaths (run_id, event_id, species, level, area, cause, is_starter, timestamp)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (e.run_id, e.event_id, p.get("species"), p.get("level"), p.get("area"), p.get("cause"),
             int(bool(p.get("is_starter"))), e.timestamp))
        if p.get("species"):
            self.db.execute("UPDATE pokemon SET status = 'FAINTED', updated_at = ?"
                            " WHERE run_id = ? AND species = ?", (utc_now(), e.run_id, p["species"]))
