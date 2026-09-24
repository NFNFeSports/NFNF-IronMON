"""Application facade — the single API used by the CLI, the Tk UI and the HTTP API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .career import Career, compute_career
from .components import ComponentManager
from .config import AppConfig
from .controllers import ControllerManager, MappingRepository
from .db import Database
from .emulators import build_emulators
from .events import EventBus, is_registered
from .games import GameRegistry
from .integrity import IntegrityManager, IntegrityReport
from .orchestrator import RunOrchestrator
from .paths import AppPaths
from .projections import GameplayProjection
from .randomizers import MockRandomizer, ProfileRepository, RandomizerRegistry, UprZxRandomizer
from .roms import RomManager, RomRecord
from .rules import RulesetRepository
from .runs import Run, RunManager
from .trackers import build_trackers
from .util import utc_now


class Application:
    def __init__(self, home: Path | str | None = None, strict_events: bool = False):
        self.paths = AppPaths.resolve(home)
        self.paths.ensure()
        self.config = AppConfig.load(self.paths)
        if not self.paths.config_file.exists():
            self.config.save()
        self.db = Database(self.paths.db_path)
        self.bus = EventBus(strict=strict_events)
        self.games = GameRegistry()
        self.roms = RomManager(self.paths, self.db, self.games)
        self.rulesets = RulesetRepository(self.paths.rules_dir)
        self.profiles = ProfileRepository(self.paths.profiles_dir)
        self.components = ComponentManager(self.paths.app_root)
        self.randomizers = RandomizerRegistry([
            MockRandomizer(),
            UprZxRandomizer(self.config.section("randomizers", "upr-zx"), self.paths.home,
                            self.components),
        ])
        self.emulators = build_emulators(self.config.section("emulators"), self.paths.home,
                                         self.components)
        ctl = self.config.section("controller")
        self.controllers = ControllerManager(MappingRepository(self.paths.input_mappings_dir),
                                             mapping_id=ctl.get("mapping") or "xbox-gba-labels",
                                             preferred_device=ctl.get("device"))
        self.trackers = build_trackers(self.config.section("trackers"), self.paths.home)
        self.runs = RunManager(self.paths, self.db, self.bus)
        self.integrity = IntegrityManager(self.db, self.bus, self.runs)
        self.projection = GameplayProjection(self.db, self.bus)
        self.orchestrator = RunOrchestrator(
            config=self.config, bus=self.bus, games=self.games, roms=self.roms,
            rulesets=self.rulesets, profiles=self.profiles, randomizers=self.randomizers,
            emulators=self.emulators, trackers=self.trackers, runs=self.runs,
            integrity=self.integrity, controllers=self.controllers)
        self._sync_catalog()

    def close(self) -> None:
        self.controllers.close()
        self.db.close()

    def __enter__(self) -> "Application":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _sync_catalog(self) -> None:
        """Mirror adapters, rulesets and profiles into the DB (for queries/history)."""
        now = utc_now()
        with self.db.transaction() as c:
            for g in self.games.all():
                c.execute("INSERT OR REPLACE INTO games (id, name, generation, platform, status, adapter)"
                          " VALUES (?,?,?,?,?,?)", (g.game_id, g.display_name, g.generation,
                                                    g.platform, g.status, type(g).__name__))
        for rs in self._safe(self.rulesets.list):
            self.db.execute("INSERT OR IGNORE INTO rulesets (id, sha256, name, version, path, loaded_at)"
                            " VALUES (?,?,?,?,?,?)",
                            (rs.id, rs.sha256, rs.name, rs.version, self.paths.rel(rs.path), now))
        for p in self._safe(self.profiles.list):
            self.db.execute("INSERT OR REPLACE INTO randomizer_profiles (id, game_id, randomizer,"
                            " settings_hash, path, loaded_at) VALUES (?,?,?,?,?,?)",
                            (p.id, p.game_id, p.randomizer, p.settings_sha256,
                             self.paths.rel(p.path), now))

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return []  # a broken user file must not stop the app; `doctor` reports it

    # --- queries ----------------------------------------------------------
    def list_games(self) -> list[dict[str, Any]]:
        out = []
        for g in self.games.all():
            d = g.describe()
            d["roms_imported"] = len(self.roms.list(g.game_id))
            out.append(d)
        return out

    def list_runs(self) -> list[Run]:
        return self.runs.list()

    def career(self) -> Career:
        return compute_career(self.db)

    def current_run(self) -> Run | None:
        return self.runs.current() or self.runs.latest()

    def run_summary(self, run: Run) -> dict[str, Any]:
        r = run.row
        return {"id": run.id, "game": run.game_id, "status": run.status.value,
                "integrity": run.integrity_status, "seed": r["seed"],
                "attempt": r["attempt_number"],
                "actual_randomizer_seed": r["actual_randomizer_seed"],
                "deterministic": None if r["deterministic"] is None else bool(r["deterministic"]),
                "generated_rom_sha256": r["generated_rom_sha256"],
                "ruleset": r["ruleset_id"], "randomizer": r["randomizer_name"],
                "created_at": r["created_at"], "started_at": r["started_at"],
                "ended_at": r["ended_at"], "end_reason": r["end_reason"],
                "archived": bool(r["archived"]), "previous_run_id": r["previous_run_id"],
                "path": r["path"]}

    # --- commands ---------------------------------------------------------
    def import_rom(self, path: Path | str) -> RomRecord:
        return self.roms.import_rom(path)

    def new_run(self, **kwargs: Any) -> Run:
        return self.orchestrator.start_new_run(**kwargs)

    def fail_run(self, run_id: str, reason: str = "player_failed") -> Run:
        return self.orchestrator.fail_run(run_id, reason)

    def restart_run(self, run_id: str, reason: str = "player_failed", launch: bool = True) -> Run:
        return self.orchestrator.restart_after_failure(run_id, reason, launch=launch)

    def complete_run(self, run_id: str) -> Run:
        return self.orchestrator.complete_run(run_id)

    def abandon_run(self, run_id: str, reason: str = "abandoned") -> Run:
        return self.orchestrator.abandon_run(run_id, reason)

    def verify_run(self, run_id: str) -> IntegrityReport:
        return self.integrity.verify(run_id)

    def record_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None,
                     source: str = "player") -> dict[str, Any]:
        if not is_registered(event_type):
            raise ValueError(f"Unknown event type {event_type!r}")
        return self.runs.record_event(run_id, event_type, payload, source).to_dict()

    # --- diagnostics ------------------------------------------------------
    def doctor(self) -> dict[str, Any]:
        from .doctor import build_report
        return build_report(self)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
