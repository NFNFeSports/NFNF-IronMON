"""Local SQLite database (single portable file, no server, no account).

The run folders (``runs/<id>/``) remain the source of truth for each run; the
database is an index/projection that makes listing and querying fast. Paths
are stored relative to the app home so the folder can be moved.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2

SCHEMA_V1 = """
CREATE TABLE games (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    generation  INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    status      TEXT NOT NULL,
    adapter     TEXT NOT NULL
);

CREATE TABLE roms (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT NOT NULL REFERENCES games(id),
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL UNIQUE,
    sha1          TEXT NOT NULL,
    crc32         TEXT NOT NULL,
    size          INTEGER NOT NULL,
    source        TEXT NOT NULL,
    original      INTEGER NOT NULL DEFAULT 1,
    path          TEXT NOT NULL,
    imported_at   TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE randomizer_profiles (
    id            TEXT PRIMARY KEY,
    game_id       TEXT NOT NULL,
    randomizer    TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    path          TEXT NOT NULL,
    loaded_at     TEXT NOT NULL
);

CREATE TABLE rulesets (
    id         TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    path       TEXT NOT NULL,
    loaded_at  TEXT NOT NULL,
    PRIMARY KEY (id, sha256)
);

CREATE TABLE runs (
    id                    TEXT PRIMARY KEY,
    seq                   INTEGER NOT NULL UNIQUE,
    game_id               TEXT NOT NULL,
    status                TEXT NOT NULL,
    integrity_status      TEXT NOT NULL DEFAULT 'UNKNOWN',
    ruleset_id            TEXT,
    ruleset_sha256        TEXT,
    randomizer_profile_id TEXT,
    randomizer_name       TEXT,
    randomizer_version    TEXT,
    seed                  INTEGER,
    settings_sha256       TEXT,
    source_rom_sha256     TEXT,
    generated_rom_sha256  TEXT,
    emulator_id           TEXT,
    tracker_id            TEXT,
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    ended_at              TEXT,
    end_reason            TEXT,
    archived              INTEGER NOT NULL DEFAULT 0,
    archived_at           TEXT,
    previous_run_id       TEXT REFERENCES runs(id),
    path                  TEXT NOT NULL
);

CREATE TABLE events (
    id           TEXT PRIMARY KEY,
    run_id       TEXT REFERENCES runs(id),
    seq          INTEGER,
    type         TEXT NOT NULL,
    source       TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    hash         TEXT
);
CREATE INDEX idx_events_run ON events(run_id, seq);
CREATE INDEX idx_events_type ON events(type);

CREATE TABLE pokemon (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(id),
    species      TEXT NOT NULL,
    nickname     TEXT,
    level        INTEGER,
    is_starter   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'ALIVE',
    event_id     TEXT REFERENCES events(id),
    updated_at   TEXT NOT NULL
);

CREATE TABLE encounters (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(id),
    event_id       TEXT REFERENCES events(id),
    species        TEXT,
    level          INTEGER,
    area           TEXT,
    encounter_type TEXT NOT NULL,
    timestamp      TEXT NOT NULL
);

CREATE TABLE deaths (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(id),
    event_id   TEXT REFERENCES events(id),
    species    TEXT,
    level      INTEGER,
    area       TEXT,
    cause      TEXT,
    is_starter INTEGER NOT NULL DEFAULT 0,
    timestamp  TEXT NOT NULL
);

CREATE TABLE integrity_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(id),
    event_id   TEXT,
    check_name TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);
CREATE INDEX idx_integrity_run ON integrity_events(run_id);
"""

# v2 (Phase 2): the NFNF bookkeeping seed (``seed``) is kept separate from the
# seed the randomizer actually used; determinism is recorded per run; runs
# that reach ACTIVE get a career attempt number.
SCHEMA_V2 = """
ALTER TABLE runs ADD COLUMN actual_randomizer_seed INTEGER;
ALTER TABLE runs ADD COLUMN deterministic INTEGER;
ALTER TABLE runs ADD COLUMN attempt_number INTEGER;
CREATE UNIQUE INDEX idx_runs_attempt ON runs(attempt_number);
"""

MIGRATIONS = {1: SCHEMA_V1, 2: SCHEMA_V2}


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection guarded by a lock: the Tk UI and the local HTTP API
        # may touch it from another thread than the one that opened it.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            current = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if 0 < current < max(MIGRATIONS):
                # Keep a copy of the pre-migration database next to it.
                backup = self.path.with_name(f"{self.path.name}.v{current}.bak")
                if not backup.exists():
                    dst = sqlite3.connect(str(backup))
                    self.conn.backup(dst)
                    dst.close()
            for version in sorted(MIGRATIONS):
                if version > current:
                    self.conn.executescript(MIGRATIONS[version])
                    self.conn.execute(f"PRAGMA user_version = {version}")
            self.conn.commit()

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.transaction() as c:
            return c.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()
