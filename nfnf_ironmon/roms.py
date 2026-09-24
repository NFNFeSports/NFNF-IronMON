"""ROM manager.

    user ROM -> validate -> identify game/version -> SHA-256 -> store metadata

The user's file is only ever *read*. Import copies it into
``games/original/<game>/<sha256><ext>`` (marked read-only) with a sidecar
metadata JSON. Runs work on further copies; nothing writes to an original.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .games import GameRegistry, RomIdentity
from .hashing import file_digests
from .paths import AppPaths
from .util import utc_now, write_json

MAX_ROM_SIZE = 64 * 1024 * 1024  # anything bigger is not a GB/GBC/GBA ROM
ROM_EXTENSIONS = {".gba", ".gb", ".gbc"}


@dataclass
class ScanResult:
    registered: list["RomRecord"]
    known: list["RomRecord"]
    unrecognised: list[Path]
    problems: list[str]


class RomError(Exception):
    pass


@dataclass
class RomRecord:
    game_id: str
    version: str
    sha256: str
    sha1: str
    crc32: str
    size: int
    path: Path
    source: str
    original: bool
    metadata: dict[str, Any]


def make_read_only(path: Path) -> None:
    mode = os.stat(path).st_mode
    os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(path: Path) -> None:
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)


class RomManager:
    def __init__(self, paths: AppPaths, db: Database, games: GameRegistry):
        self.paths, self.db, self.games = paths, db, games

    def inspect(self, path: Path | str) -> tuple[RomIdentity, dict[str, Any]]:
        """Validate + identify a ROM without importing it (read-only)."""
        path = Path(path)
        if not path.is_file():
            raise RomError(f"Not a file: {path}")
        size = path.stat().st_size
        if size == 0 or size > MAX_ROM_SIZE:
            raise RomError(f"Implausible ROM size: {size} bytes")
        digests = file_digests(path)
        identity = self.games.identify_file(path, digests["sha1"])
        if identity is None:
            raise RomError(f"Unrecognised ROM (no game adapter matched): {path.name}")
        return identity, digests

    def import_rom(self, source_path: Path | str) -> RomRecord:
        source_path = Path(source_path).resolve()
        identity, digests = self.inspect(source_path)

        existing = self.get(digests["sha256"])
        if existing:
            return existing

        dest_dir = self.paths.originals_dir / identity.game_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{digests['sha256']}{source_path.suffix.lower()}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(source_path, tmp)          # read source, write only our copy
        if file_digests(tmp)["sha256"] != digests["sha256"]:
            tmp.unlink()
            raise RomError("Copy verification failed (hash mismatch)")
        os.replace(tmp, dest)
        make_read_only(dest)

        metadata = {
            "game": identity.game_name,
            "game_id": identity.game_id,
            "version": identity.version,
            "sha256": digests["sha256"],
            "sha1": digests["sha1"],
            "crc32": digests["crc32"],
            "size": digests["size"],
            "source": "user",
            "original": True,
            "original_filename": source_path.name,
            "identity": identity.to_dict(),
            "imported_at": utc_now(),
        }
        write_json(dest.with_suffix(".json"), metadata)
        self.db.execute(
            "INSERT INTO roms (game_id, version, sha256, sha1, crc32, size, source, original, path,"
            " imported_at, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (identity.game_id, identity.version, digests["sha256"], digests["sha1"],
             digests["crc32"], digests["size"], "user", 1, self.paths.rel(dest),
             metadata["imported_at"], json.dumps(metadata)))
        return self._record(metadata, dest)

    def register_in_place(self, path: Path | str) -> RomRecord:
        """Register a ROM where it lies (e.g. dropped into games/original/).

        Strictly read-only: the file is not copied, renamed, chmod-ed, and no
        sidecar is written next to it. Metadata lives only in the database."""
        path = Path(path).resolve()
        identity, digests = self.inspect(path)
        existing = self.get(digests["sha256"])
        if existing:
            return existing
        st = path.stat()
        metadata = {
            "game": identity.game_name, "game_id": identity.game_id, "version": identity.version,
            "sha256": digests["sha256"], "sha1": digests["sha1"], "crc32": digests["crc32"],
            "size": digests["size"], "source": "user", "original": True, "storage": "in_place",
            "original_filename": path.name, "mtime_ns": st.st_mtime_ns,
            "identity": identity.to_dict(), "imported_at": utc_now(),
        }
        self.db.execute(
            "INSERT INTO roms (game_id, version, sha256, sha1, crc32, size, source, original, path,"
            " imported_at, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (identity.game_id, identity.version, digests["sha256"], digests["sha1"],
             digests["crc32"], digests["size"], "user", 1, self.paths.rel(path),
             metadata["imported_at"], json.dumps(metadata)))
        return self._record(metadata, path)

    def scan(self, directory: Path | None = None) -> ScanResult:
        """Discover ROMs under ``games/original/`` (default) without modifying anything."""
        directory = directory or self.paths.originals_dir
        result = ScanResult([], [], [], [])
        if not directory.is_dir():
            return result
        for f in sorted(directory.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in ROM_EXTENSIONS:
                continue
            row = self.db.query_one("SELECT * FROM roms WHERE path = ?", (self.paths.rel(f),))
            if row:
                meta = json.loads(row["metadata_json"])
                rec = self._record(meta, f)
                st = f.stat()
                if st.st_size == meta["size"] and meta.get("mtime_ns") in (None, st.st_mtime_ns):
                    result.known.append(rec)
                elif self.verify_original(rec):
                    result.known.append(rec)
                else:
                    result.problems.append(f"{f.name}: content changed since it was registered "
                                           f"(expected sha256 {meta['sha256'][:12]}…)")
                continue
            try:
                rec = self.register_in_place(f)
                if rec.path.resolve() == f.resolve():
                    result.registered.append(rec)
                else:  # same dump already registered from another location
                    result.known.append(rec)
                    result.problems.append(f"{f.name}: duplicate of {rec.path.name} (ignored)")
            except RomError:
                result.unrecognised.append(f)
            except OSError as exc:
                result.problems.append(f"{f.name}: {exc}")
        return result

    def get(self, sha256: str) -> RomRecord | None:
        row = self.db.query_one("SELECT * FROM roms WHERE sha256 = ?", (sha256,))
        if not row:
            return None
        return self._record(json.loads(row["metadata_json"]), self.paths.abs(row["path"]))

    def list(self, game_id: str | None = None) -> list[RomRecord]:
        sql, args = "SELECT * FROM roms", ()
        if game_id:
            sql, args = sql + " WHERE game_id = ?", (game_id,)
        rows = self.db.query(sql + " ORDER BY imported_at", args)
        return [self._record(json.loads(r["metadata_json"]), self.paths.abs(r["path"])) for r in rows]

    def find_original(self, game_id: str, sha256: str | None = None) -> RomRecord:
        if sha256:
            rec = self.get(sha256)
            if not rec or rec.game_id != game_id:
                raise RomError(f"No imported {game_id} ROM with sha256 {sha256}")
            return rec
        recs = self.list(game_id)
        if not recs:
            raise RomError(
                f"No original ROM found for {game_id!r}. Put your own dump in "
                f"{self.paths.originals_dir} (then run `rom scan`) or use `rom import <path>`")
        return recs[0]

    def verify_original(self, rec: RomRecord) -> bool:
        return rec.path.is_file() and file_digests(rec.path)["sha256"] == rec.sha256

    @staticmethod
    def _record(meta: dict[str, Any], path: Path) -> RomRecord:
        return RomRecord(game_id=meta["game_id"], version=meta["version"], sha256=meta["sha256"],
                         sha1=meta["sha1"], crc32=meta["crc32"], size=meta["size"], path=path,
                         source=meta["source"], original=meta["original"], metadata=meta)
