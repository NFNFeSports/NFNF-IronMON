"""Run integrity subsystem.

Records a baseline when a run is prepared (ROM hash, seed, settings hash,
ruleset hash) and observes emulator/save events while it is played. It
detects *evidence* — ROM replacement, settings/ruleset changes, event-log
tampering, rollbacks, save-state loads, resets — and never claims to know
intent.

Statuses (worst wins):
    VERIFIED    every check passed and gameplay was observable
    UNKNOWN     nothing wrong found, but something could not be checked
                (no baseline, or the tracker cannot observe save loads/resets)
    SUSPICIOUS  signals that may or may not be legitimate (state load, rollback)
    INVALID     proven inconsistency (hash mismatch, broken event chain)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .db import Database
from .events import Event, EventBus, EventType
from .hashing import sha256_file, sha256_json
from .runs import EventLog, RunManager
from .util import read_json, utc_now, write_json


class IntegrityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    SUSPICIOUS = "SUSPICIOUS"
    INVALID = "INVALID"


_RANK = {IntegrityStatus.VERIFIED: 0, IntegrityStatus.UNKNOWN: 1,
         IntegrityStatus.SUSPICIOUS: 2, IntegrityStatus.INVALID: 3}

INFO = "INFO"  # observation that does not affect status (e.g. a soft reset)

#: Tracker capabilities needed before gameplay can be called VERIFIED.
REQUIRED_TELEMETRY = ("resets", "save_loads", "savestate_loads", "play_time")


def worst(*statuses: IntegrityStatus) -> IntegrityStatus:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else IntegrityStatus.VERIFIED


@dataclass
class IntegrityCheck:
    name: str
    status: IntegrityStatus
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "detail": self.detail}


@dataclass
class IntegrityReport:
    run_id: str
    status: IntegrityStatus
    checks: list[IntegrityCheck] = field(default_factory=list)
    checked_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "status": self.status.value, "checked_at": self.checked_at,
                "checks": [c.to_dict() for c in self.checks]}


class IntegrityManager:
    OBSERVED = (EventType.ROM_LOADED, EventType.SAVE_LOADED, EventType.GAME_RESET,
                EventType.EMULATOR_STARTED, EventType.EMULATOR_STOPPED, EventType.SAVE_CREATED,
                EventType.SESSION_INTERRUPTED, EventType.STATE_RESTORED, EventType.CONTROLLER_DISCONNECTED,
                EventType.SESSION_RESUMED, EventType.SESSION_CLOSED)

    def __init__(self, db: Database, bus: EventBus, runs: RunManager):
        self.db, self.bus, self.runs = db, bus, runs
        self._last_play_time: dict[str, int] = {}
        for et in self.OBSERVED:
            bus.subscribe(self.on_event, et)
        # Any event may carry play time; watch all of them for rollbacks.
        bus.subscribe(self._check_play_time)

    # --- baseline ---------------------------------------------------------
    def create_baseline(self, run_id: str, *, generated_rom: str, generated_rom_sha256: str,
                        source_rom_sha256: str, seed: int, settings_sha256: str,
                        ruleset_sha256: str, tracker_capabilities: list[str]) -> dict[str, Any]:
        run = self.runs.get(run_id)
        baseline = {
            "run_id": run_id,
            "created_at": utc_now(),
            "rom": {"path": generated_rom, "sha256": generated_rom_sha256},
            "sourceRomSha256": source_rom_sha256,
            "seed": seed,
            "settingsSha256": settings_sha256,
            "rulesetSha256": ruleset_sha256,
            "trackerCapabilities": sorted(tracker_capabilities),
            "runStart": None,
            "runEnd": None,
            "lastVerification": None,
        }
        write_json(run.path / "integrity.json", baseline)
        self.record(run_id, "baseline", INFO, "Integrity baseline recorded")
        return baseline

    def mark_times(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        path = run.path / "integrity.json"
        if run.archived or not path.exists():
            return
        data = read_json(path)
        data["runStart"], data["runEnd"] = run.row["started_at"], run.row["ended_at"]
        write_json(path, data)

    # --- observations -----------------------------------------------------
    def record(self, run_id: str, check: str, status: str, detail: str,
               event_id: str | None = None) -> None:
        value = status.value if isinstance(status, IntegrityStatus) else status
        self.db.execute(
            "INSERT INTO integrity_events (run_id, event_id, check_name, status, detail, timestamp)"
            " VALUES (?,?,?,?,?,?)", (run_id, event_id, check, value, detail, utc_now()))
        if value in (IntegrityStatus.SUSPICIOUS.value, IntegrityStatus.INVALID.value):
            run = self.runs.get(run_id)
            if not run.archived:
                self.runs.record_event(run_id, EventType.INTEGRITY_WARNING,
                                       {"check": check, "status": value,
                                        "detail": detail, "trigger_event_id": event_id},
                                       source="integrity")

    def _observable(self, event: Event) -> bool:
        if not event.run_id or event.source == "integrity":
            return False
        run = self.runs.get(event.run_id)
        return not run.archived

    def on_event(self, event: Event) -> None:
        if not self._observable(event):
            return
        rid, p = event.run_id, event.payload
        if event.type == EventType.ROM_LOADED:
            expected = self.runs.get(rid).row["generated_rom_sha256"]
            actual = p.get("rom_sha256")
            if actual and expected and actual != expected:
                self.record(rid, "rom_loaded", IntegrityStatus.INVALID,
                            f"Emulator loaded a ROM with sha256 {actual}, expected {expected}",
                            event.event_id)
            else:
                self.record(rid, "rom_loaded", INFO, "ROM load observed", event.event_id)
        elif event.type == EventType.SAVE_LOADED:
            if p.get("kind") == "savestate":
                self.record(rid, "savestate_loaded", IntegrityStatus.SUSPICIOUS,
                            "A save state was loaded", event.event_id)
            else:
                self.record(rid, "save_loaded", INFO, "In-game save loaded", event.event_id)
        elif event.type == EventType.GAME_RESET:
            self.record(rid, "game_reset", INFO, f"Reset observed ({p.get('kind', 'unknown')})",
                        event.event_id)
        elif event.type == EventType.SESSION_INTERRUPTED:
            self.record(rid, "session_interrupted", IntegrityStatus.SUSPICIOUS,
                        "Game session ended without a clean shutdown (crash, kill or power loss)",
                        event.event_id)
        elif event.type == EventType.STATE_RESTORED:
            after_crash = bool(p.get("after_crash"))
            self.record(rid, "state_restored", IntegrityStatus.SUSPICIOUS if after_crash else INFO,
                        f"Recovery state restored ({'after an interrupted session' if after_crash else 'clean resume'})",
                        event.event_id)
        elif event.type in (EventType.EMULATOR_STARTED, EventType.EMULATOR_STOPPED,
                            EventType.SAVE_CREATED, EventType.CONTROLLER_DISCONNECTED,
                            EventType.SESSION_RESUMED, EventType.SESSION_CLOSED):
            self.record(rid, event.type.lower(), INFO, f"{event.type} observed", event.event_id)

    def _check_play_time(self, event: Event) -> None:
        pt = event.payload.get("play_time_frames")
        if not isinstance(pt, int) or not self._observable(event):
            return
        last = self._last_play_time.get(event.run_id)
        if last is None:
            row = self.db.query_one(
                "SELECT MAX(CAST(json_extract(payload_json, '$.play_time_frames') AS INTEGER)) AS m"
                " FROM events WHERE run_id = ? AND id != ?", (event.run_id, event.event_id))
            last = row["m"] if row and row["m"] is not None else None
        if last is not None and pt < last:
            self.record(event.run_id, "rollback", IntegrityStatus.SUSPICIOUS,
                        f"In-game play time went backwards ({last} -> {pt} frames): "
                        "a save was restored or state rolled back", event.event_id)
        self._last_play_time[event.run_id] = max(pt, last or 0)

    # --- verification -----------------------------------------------------
    def verify(self, run_id: str) -> IntegrityReport:
        run = self.runs.get(run_id)
        checks: list[IntegrityCheck] = []
        S = IntegrityStatus
        base_path = run.path / "integrity.json"

        if not base_path.exists():
            checks.append(IntegrityCheck("baseline", S.UNKNOWN, "No integrity baseline recorded"))
        else:
            base = read_json(base_path)
            rom = self.runs.paths.abs(base["rom"]["path"])
            if not rom.exists():
                checks.append(IntegrityCheck("rom_hash", S.INVALID, "Run ROM is missing"))
            elif sha256_file(rom) != base["rom"]["sha256"]:
                checks.append(IntegrityCheck("rom_hash", S.INVALID, "Run ROM was replaced or modified"))
            else:
                checks.append(IntegrityCheck("rom_hash", S.VERIFIED, "Run ROM matches baseline"))

            settings = read_json(run.path / "settings.json") if (run.path / "settings.json").exists() else None
            if settings is None:
                checks.append(IntegrityCheck("settings_hash", S.INVALID, "settings.json missing"))
            elif (sha256_json(settings.get("randomizer_settings")) != base["settingsSha256"]
                  or settings.get("seed") != base["seed"]):
                checks.append(IntegrityCheck("settings_hash", S.INVALID, "Seed or randomizer settings changed"))
            else:
                checks.append(IntegrityCheck("settings_hash", S.VERIFIED, "Seed and settings match baseline"))
            rset = (settings or {}).get("randomizer_settings", {})
            expected = rset.get("settings_files_sha256") or (
                [rset["settings_file_sha256"]] if rset.get("settings_file_sha256") else [])
            snaps = (settings or {}).get("settings_file_snapshots") or (["settings.rnqs"] if expected else [])
            if expected:
                bad = []
                for name, digest in zip(snaps, expected):
                    snap = run.path / name
                    if not snap.exists():
                        bad.append(f"{name} missing")
                    elif sha256_file(snap) != digest:
                        bad.append(f"{name} changed")
                if len(snaps) != len(expected):
                    bad.append("settings snapshot count mismatch")
                checks.append(IntegrityCheck("settings_file", S.INVALID if bad else S.VERIFIED,
                                             "; ".join(bad) if bad else "Randomizer settings file(s) match"))

            rs_path = run.path / "ruleset.json"
            if not rs_path.exists():
                checks.append(IntegrityCheck("ruleset_hash", S.INVALID, "ruleset.json snapshot missing"))
            elif sha256_json(read_json(rs_path)) != base["rulesetSha256"]:
                checks.append(IntegrityCheck("ruleset_hash", S.INVALID, "Ruleset changed after run start"))
            else:
                checks.append(IntegrityCheck("ruleset_hash", S.VERIFIED, "Ruleset matches baseline"))

            missing = [c for c in REQUIRED_TELEMETRY if c not in base.get("trackerCapabilities", [])]
            if missing:
                checks.append(IntegrityCheck("telemetry", S.UNKNOWN,
                                             "Tracker cannot observe: " + ", ".join(missing)))
            else:
                checks.append(IntegrityCheck("telemetry", S.VERIFIED, "Gameplay telemetry available"))

        problems = EventLog.verify(run.path / "events.jsonl")
        checks.append(IntegrityCheck("event_log", S.INVALID if problems else S.VERIFIED,
                                     "; ".join(problems) if problems else "Hash chain intact"))

        if run.archived:
            problems = self.runs.verify_archive(run_id)
            checks.append(IntegrityCheck("archive", S.INVALID if problems else S.VERIFIED,
                                         "; ".join(problems) if problems else "Archive seal intact"))

        observed = self.db.query(
            "SELECT check_name, status, detail FROM integrity_events WHERE run_id = ?"
            " AND status IN ('SUSPICIOUS','INVALID')", (run_id,))
        for o in observed:
            checks.append(IntegrityCheck(f"observed:{o['check_name']}", S(o["status"]), o["detail"]))

        report = IntegrityReport(run_id, worst(*(c.status for c in checks)), checks)
        self.runs.update_fields(run_id, integrity_status=report.status.value)
        if not run.archived and base_path.exists():
            data = read_json(base_path)
            data["lastVerification"] = report.to_dict()
            write_json(base_path, data)
        return report
