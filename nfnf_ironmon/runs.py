"""Run manager: run IDs, directories, lifecycle state machine, event log, archival.

Each run lives in its own folder, which is the source of truth::

    runs/RUN-000001/
        metadata.json    run summary + state history
        settings.json    seed, randomizer profile/settings, ruleset reference
        ruleset.json     snapshot of the ruleset used (hashed for integrity)
        randomizer.json  randomization record (hashes, versions, timestamp)
        integrity.json   integrity baseline + last verification
        events.jsonl     append-only, hash-chained event log
        archive.json     sealed file manifest (written on archival)
        rom/  saves/  logs/
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .db import Database
from .events import Event, EventBus, EventType
from .hashing import canonical_json, sha256_bytes, sha256_file
from .paths import AppPaths
from .roms import make_read_only
from .util import read_json, utc_now, write_json

RUN_ID_RE = re.compile(r"^RUN-(\d{6,})$")
GENESIS_HASH = "0" * 64


class RunState(str, Enum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    INVALID = "INVALID"


TERMINAL = {RunState.FAILED, RunState.COMPLETED, RunState.ABANDONED, RunState.INVALID}

TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.PREPARING, RunState.ABANDONED, RunState.INVALID},
    RunState.PREPARING: {RunState.READY, RunState.ABANDONED, RunState.INVALID},
    RunState.READY: {RunState.ACTIVE, RunState.ABANDONED, RunState.INVALID},
    RunState.ACTIVE: {RunState.FAILED, RunState.COMPLETED, RunState.ABANDONED, RunState.INVALID},
    # A finished run can still be invalidated by a later integrity check.
    RunState.FAILED: {RunState.INVALID},
    RunState.COMPLETED: {RunState.INVALID},
    RunState.ABANDONED: {RunState.INVALID},
    RunState.INVALID: set(),
}


class RunError(Exception):
    pass


class InvalidTransition(RunError):
    pass


class RunArchivedError(RunError):
    pass


def format_run_id(seq: int) -> str:
    return f"RUN-{seq:06d}"


@dataclass
class Run:
    id: str
    seq: int
    game_id: str
    status: RunState
    integrity_status: str
    path: Path
    row: dict[str, Any]

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def archived(self) -> bool:
        return bool(self.row.get("archived"))

    def __getattr__(self, item: str) -> Any:
        row = self.__dict__.get("row", {})
        if item in row:
            return row[item]
        raise AttributeError(item)


# --- hash-chained event log --------------------------------------------------

class EventLog:
    """``events.jsonl`` where each line carries the hash of the previous line.

    Editing, deleting or reordering any line breaks the chain, so tampering
    with a run's history is detectable (it is evidence, not prevention).
    """

    @staticmethod
    def _hash(record: dict[str, Any]) -> str:
        return sha256_bytes(canonical_json(record).encode("utf-8"))

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    @classmethod
    def head(cls, path: Path) -> tuple[int, str]:
        records = cls.read(path)
        if not records:
            return 0, GENESIS_HASH
        return records[-1]["seq"], records[-1]["hash"]

    @classmethod
    def append(cls, path: Path, event: Event) -> tuple[int, str]:
        seq, prev = cls.head(path)
        record = {"seq": seq + 1, "event_id": event.event_id, "run_id": event.run_id,
                  "type": event.type, "source": event.source, "timestamp": event.timestamp,
                  "payload": event.payload, "prev_hash": prev}
        record["hash"] = cls._hash(record)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record["seq"], record["hash"]

    @classmethod
    def verify(cls, path: Path) -> list[str]:
        problems, prev, expected_seq = [], GENESIS_HASH, 1
        try:
            records = cls.read(path)
        except json.JSONDecodeError as exc:
            return [f"events.jsonl is not valid JSON lines: {exc}"]
        for rec in records:
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec.get("seq") != expected_seq:
                problems.append(f"sequence gap at line {expected_seq} (found {rec.get('seq')})")
            if rec.get("prev_hash") != prev:
                problems.append(f"chain broken at seq {rec.get('seq')}")
            if cls._hash(body) != rec.get("hash"):
                problems.append(f"record {rec.get('seq')} was modified")
            prev, expected_seq = rec.get("hash"), expected_seq + 1
        return problems


# --- run manager -------------------------------------------------------------

class RunManager:
    def __init__(self, paths: AppPaths, db: Database, bus: EventBus):
        self.paths, self.db, self.bus = paths, db, bus

    # ids / lookup
    def allocate_seq(self) -> int:
        row = self.db.query_one("SELECT MAX(seq) AS m FROM runs")
        seq = (row["m"] or 0) if row else 0
        # Also respect folders on disk, so a lost/rebuilt DB never reuses an id.
        if self.paths.runs_dir.exists():
            for d in self.paths.runs_dir.iterdir():
                m = RUN_ID_RE.match(d.name)
                if m:
                    seq = max(seq, int(m.group(1)))
        return seq + 1

    def get(self, run_id: str) -> Run:
        row = self.db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not row:
            raise RunError(f"Unknown run {run_id}")
        return Run(id=row["id"], seq=row["seq"], game_id=row["game_id"],
                   status=RunState(row["status"]), integrity_status=row["integrity_status"],
                   path=self.paths.abs(row["path"]), row=row)

    def list(self) -> list[Run]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM runs ORDER BY seq")]

    def current(self) -> Run | None:
        row = self.db.query_one(
            "SELECT id FROM runs WHERE status NOT IN ('FAILED','COMPLETED','ABANDONED','INVALID')"
            " ORDER BY seq DESC LIMIT 1")
        return self.get(row["id"]) if row else None

    def latest(self) -> Run | None:
        row = self.db.query_one("SELECT id FROM runs ORDER BY seq DESC LIMIT 1")
        return self.get(row["id"]) if row else None

    # creation
    def create_run(self, game_id: str, *, ruleset_id: str | None = None,
                   ruleset_sha256: str | None = None, randomizer_profile_id: str | None = None,
                   emulator_id: str | None = None, tracker_id: str | None = None,
                   previous_run_id: str | None = None) -> Run:
        with self.db.transaction() as c:
            seq = self.allocate_seq()
            run_id = format_run_id(seq)
            run_dir = self.paths.run_dir(run_id)
            if run_dir.exists():
                raise RunError(f"Run folder already exists: {run_dir}")
            for sub in ("rom", "saves", "logs"):
                (run_dir / sub).mkdir(parents=True)
            (run_dir / "events.jsonl").touch()
            now = utc_now()
            c.execute(
                "INSERT INTO runs (id, seq, game_id, status, ruleset_id, ruleset_sha256,"
                " randomizer_profile_id, emulator_id, tracker_id, created_at, previous_run_id, path)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, seq, game_id, RunState.CREATED.value, ruleset_id, ruleset_sha256,
                 randomizer_profile_id, emulator_id, tracker_id, now, previous_run_id,
                 self.paths.rel(run_dir)))
        self._sync_metadata(run_id)
        self.record_event(run_id, EventType.RUN_CREATED, {"game_id": game_id,
                                                          "previous_run_id": previous_run_id})
        return self.get(run_id)

    # field updates / metadata
    UPDATABLE = {"integrity_status", "ruleset_id", "ruleset_sha256", "randomizer_profile_id",
                 "randomizer_name", "randomizer_version", "seed", "settings_sha256",
                 "source_rom_sha256", "generated_rom_sha256", "emulator_id", "tracker_id",
                 "actual_randomizer_seed", "deterministic"}

    def update_fields(self, run_id: str, extra_metadata: dict[str, Any] | None = None,
                      **fields: Any) -> Run:
        run = self.get(run_id)
        bad = set(fields) - self.UPDATABLE
        if bad:
            raise RunError(f"Fields not updatable: {sorted(bad)}")
        if run.archived and set(fields) != {"integrity_status"}:
            raise RunArchivedError(f"{run_id} is archived")
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))
        if not run.archived:
            self._sync_metadata(run_id, extra_metadata)
        return self.get(run_id)

    def metadata(self, run_id: str) -> dict[str, Any]:
        p = self.get(run_id).path / "metadata.json"
        return read_json(p) if p.exists() else {}

    def _sync_metadata(self, run_id: str, extra: dict[str, Any] | None = None,
                       history_entry: dict[str, Any] | None = None) -> None:
        run = self.get(run_id)
        path = run.path / "metadata.json"
        meta = read_json(path) if path.exists() else {"history": []}
        r = run.row
        meta.update({
            "id": run.id,
            "game": run.game_id,
            "status": r["status"],
            "integrityStatus": r["integrity_status"],
            "attemptNumber": r["attempt_number"],
            "seed": r["seed"],
            "requestedSeed": r["seed"],
            "actualRandomizerSeed": r["actual_randomizer_seed"],
            "deterministic": None if r["deterministic"] is None else bool(r["deterministic"]),
            "randomizer": {"profile": r["randomizer_profile_id"], "name": r["randomizer_name"],
                           "version": r["randomizer_version"]},
            "ruleset": {"id": r["ruleset_id"], "sha256": r["ruleset_sha256"]},
            "settingsSha256": r["settings_sha256"],
            "sourceRomSha256": r["source_rom_sha256"],
            "generatedRomSha256": r["generated_rom_sha256"],
            "emulator": r["emulator_id"],
            "tracker": r["tracker_id"],
            "createdAt": r["created_at"],
            "startedAt": r["started_at"],
            "endedAt": r["ended_at"],
            "endReason": r["end_reason"],
            "archived": bool(r["archived"]),
            "archivedAt": r["archived_at"],
            "previousRunId": r["previous_run_id"],
        })
        if extra:
            meta.update(extra)
        if history_entry:
            meta.setdefault("history", []).append(history_entry)
        write_json(path, meta)

    # lifecycle
    def transition(self, run_id: str, new_state: RunState | str, reason: str | None = None) -> Run:
        new_state = RunState(new_state)
        run = self.get(run_id)
        if new_state not in TRANSITIONS[run.status]:
            raise InvalidTransition(f"{run_id}: {run.status.value} -> {new_state.value} not allowed")
        if run.archived:
            raise RunArchivedError(f"{run_id} is archived")
        now = utc_now()
        sets, args = ["status = ?"], [new_state.value]
        if new_state == RunState.ACTIVE:
            # Career attempt number: only runs that were actually played count.
            sets += ["started_at = ?",
                     "attempt_number = (SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM runs)"]
            args.append(now)
        if new_state in TERMINAL:
            sets += ["ended_at = COALESCE(ended_at, ?)", "end_reason = COALESCE(?, end_reason)"]
            args += [now, reason]
        self.db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", (*args, run_id))
        self._sync_metadata(run_id, history_entry={"from": run.status.value, "to": new_state.value,
                                                   "at": now, "reason": reason})
        self.record_event(run_id, EventType.RUN_STATE_CHANGED,
                          {"from": run.status.value, "to": new_state.value, "reason": reason})
        if new_state == RunState.ACTIVE:
            self.record_event(run_id, EventType.RUN_STARTED, {})
        if new_state in TERMINAL and run.status not in TERMINAL:
            self.record_event(run_id, EventType.RUN_ENDED, {"status": new_state.value,
                                                            "reason": reason})
        return self.get(run_id)

    # events
    def record_event(self, run_id: str | None, event_type: str,
                     payload: dict[str, Any] | None = None, source: str = "system") -> Event:
        """Persist (run log + DB) and then publish an event."""
        event = Event(type=event_type, payload=dict(payload or {}), run_id=run_id, source=source)
        seq, digest = None, None
        if run_id:
            run = self.get(run_id)
            if run.archived:
                raise RunArchivedError(f"{run_id} is archived; its event log is sealed")
            seq, digest = EventLog.append(run.path / "events.jsonl", event)
            event.seq = seq
        self.db.execute(
            "INSERT INTO events (id, run_id, seq, type, source, timestamp, payload_json, hash)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event.event_id, run_id, seq, event.type, event.source, event.timestamp,
             json.dumps(event.payload, ensure_ascii=False), digest))
        self.bus.publish(event)
        return event

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return EventLog.read(self.get(run_id).path / "events.jsonl")

    # archival
    def archive(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if not run.is_terminal:
            raise RunError(f"Only finished runs can be archived ({run_id} is {run.status.value})")
        if run.archived:
            raise RunArchivedError(f"{run_id} is already archived")
        now = utc_now()
        self.record_event(run_id, EventType.RUN_ARCHIVED, {"archived_at": now})
        self.db.execute("UPDATE runs SET archived = 1, archived_at = ? WHERE id = ?", (now, run_id))
        self._sync_metadata(run_id)
        manifest = {
            "run_id": run_id,
            "archived_at": now,
            "status": run.status.value,
            "events_head_hash": EventLog.head(run.path / "events.jsonl")[1],
            "files": self._file_manifest(run.path),
        }
        write_json(run.path / "archive.json", manifest)
        for rom in (run.path / "rom").glob("*"):
            if rom.is_file():
                make_read_only(rom)
        return manifest

    @staticmethod
    def _file_manifest(run_dir: Path) -> dict[str, str]:
        files = {}
        for p in sorted(run_dir.rglob("*")):
            if p.is_file() and p.name != "archive.json":
                files[p.relative_to(run_dir).as_posix()] = sha256_file(p)
        return files

    def verify_archive(self, run_id: str) -> list[str]:
        run = self.get(run_id)
        path = run.path / "archive.json"
        if not path.exists():
            return ["archive.json missing"]
        expected = read_json(path)["files"]
        actual = self._file_manifest(run.path)
        problems = [f"missing: {f}" for f in expected if f not in actual]
        problems += [f"added after archival: {f}" for f in actual if f not in expected]
        problems += [f"modified: {f}" for f in expected if f in actual and actual[f] != expected[f]]
        return problems
