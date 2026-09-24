"""New-run automation.

Start New Run:
    select game -> select ruleset -> generate seed -> randomize clean ROM
    -> create run directory -> prepare emulator -> prepare tracker
    -> launch emulator -> attach tracker -> mark RUN ACTIVE

Run Failed:
    archive current run -> generate new seed -> new randomized ROM
    -> clean run environment -> launch new run

Every step is recorded in the run's event log. If preparation fails, the run
is marked ABANDONED with the reason, never left half-built and "READY".
"""

from __future__ import annotations

import logging
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .emulators import EmulatorAdapter, EmulatorSession, LaunchRequest
from .events import Event, EventBus, EventType
from .games import GameAdapter, GameNotSupportedError, GameRegistry, RunContext
from .integrity import IntegrityManager
from .randomizers import (ProfileRepository, RandomizationRequest, RandomizerProfile,
                          RandomizerRegistry)
from .roms import RomError, RomManager
from .rules import RulesEngine, RulesetRepository, parse_ruleset
from .runs import Run, RunManager, RunState
from .trackers import TrackerAdapter
from .util import read_json, write_json

log = logging.getLogger(__name__)


class RunSetupError(Exception):
    def __init__(self, message: str, run_id: str | None = None):
        super().__init__(message)
        self.run_id = run_id


@dataclass
class LiveSession:
    emulator: EmulatorAdapter
    session: EmulatorSession | None
    tracker: TrackerAdapter


class RunOrchestrator:
    def __init__(self, *, config: AppConfig, bus: EventBus, games: GameRegistry, roms: RomManager,
                 rulesets: RulesetRepository, profiles: ProfileRepository,
                 randomizers: RandomizerRegistry, emulators: dict[str, EmulatorAdapter],
                 trackers: dict[str, TrackerAdapter], runs: RunManager,
                 integrity: IntegrityManager, controllers=None):
        self.config, self.bus, self.games, self.roms = config, bus, games, roms
        self.rulesets, self.profiles, self.randomizers = rulesets, profiles, randomizers
        self.emulators, self.trackers, self.runs, self.integrity = emulators, trackers, runs, integrity
        self.controllers = controllers
        self.live: dict[str, LiveSession] = {}
        self._engines: dict[str, RulesEngine] = {}
        bus.subscribe(self._evaluate_rules)

    # ------------------------------------------------------------------ start
    def start_new_run(self, game_id: str | None = None, ruleset_id: str | None = None,
                      profile_id: str | None = None, seed: int | None = None,
                      emulator_id: str | None = None, tracker_id: str | None = None,
                      source_rom_sha256: str | None = None, launch: bool = True,
                      previous_run_id: str | None = None) -> Run:
        cfg = self.config
        game_id = game_id or cfg.get("default_game")
        ruleset_id = ruleset_id or cfg.get("default_ruleset")
        profile_id = profile_id or cfg.get("default_randomizer_profile")
        if profile_id == "auto":
            profile_id = self.auto_profile(game_id)
        emulator_id = emulator_id or cfg.get("emulator")
        tracker_id = tracker_id or cfg.get("tracker")

        # 1-2. select game + ruleset (validated before anything touches disk)
        game = self.games.get(game_id)
        if game.status != "supported":
            raise GameNotSupportedError(f"{game.display_name} runs are not implemented yet")
        ruleset = self.rulesets.load(ruleset_id)
        if not ruleset.applies_to(game_id):
            raise RunSetupError(f"Ruleset {ruleset_id} does not apply to {game_id}")
        profile = self.profiles.load(profile_id)
        if profile.game_id != game_id:
            raise RunSetupError(f"Profile {profile_id} is for {profile.game_id}, not {game_id}")
        randomizer = self.randomizers.get(profile.randomizer)
        emulator = self._get(self.emulators, emulator_id, "emulator")
        tracker = self._get(self.trackers, tracker_id, "tracker")
        if emulator_id not in game.supported_emulators:
            raise RunSetupError(f"{game.display_name} does not list emulator {emulator_id}")
        try:
            source = self.roms.find_original(game_id, source_rom_sha256)
        except RomError:
            self.roms.scan()   # user may have dropped a dump into games/original/
            source = self.roms.find_original(game_id, source_rom_sha256)
        self._guard_source(source)
        identity = self.games.identify_file(source.path, source.sha1)
        if identity is None or identity.game_id != game_id:
            raise RunSetupError("Imported original ROM no longer identifies as this game")

        # 3. seed
        if seed is None:
            seed = randomizer.generate_seed()

        run = self.runs.create_run(game_id, ruleset_id=ruleset.id, ruleset_sha256=ruleset.sha256,
                                   randomizer_profile_id=profile.id, emulator_id=emulator_id,
                                   tracker_id=tracker_id, previous_run_id=previous_run_id)
        try:
            self.runs.transition(run.id, RunState.PREPARING)
            self._prepare(run, game, identity, source, profile, randomizer, ruleset, seed,
                          emulator, tracker)
            self.runs.transition(run.id, RunState.READY)
            if launch and not emulator.interactive:
                # Honest boundary: the ROM is prepared, but nothing can be played yet.
                self.runs.update_fields(run.id, extra_metadata={"launch": {
                    "deferred": True, "emulator": emulator.emulator_id,
                    "reason": f"{emulator.display_name} is {emulator.status}: interactive play is not "
                              f"implemented yet. The randomized ROM is ready in rom/."}})
            elif launch:
                self._launch(run.id, game, emulator, tracker)
                self.runs.transition(run.id, RunState.ACTIVE)
        except Exception as exc:
            reason = f"setup_error: {exc}"
            log.warning("Run %s setup failed: %s", run.id, exc)
            (run.path / "logs" / "setup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            current = self.runs.get(run.id)
            if not current.is_terminal:
                self.runs.transition(run.id, RunState.ABANDONED, reason)
            self._stop_live(run.id)
            raise RunSetupError(reason, run.id) from exc
        return self.runs.get(run.id)

    def _prepare(self, run: Run, game: GameAdapter, identity, source, profile: RandomizerProfile,
                 randomizer, ruleset, seed: int, emulator: EmulatorAdapter,
                 tracker: TrackerAdapter) -> None:
        paths = self.runs.paths
        # 4. randomize a clean ROM (source is read-only; output goes to the run folder)
        result = randomizer.randomize(RandomizationRequest(
            source_rom=source.path, source_rom_sha256=source.sha256, identity=identity,
            settings=profile.settings, seed=seed, output_dir=run.path / "rom",
            log_dir=run.path / "logs"))
        if not self.roms.verify_original(source):
            raise RunSetupError("Original ROM changed during randomization — aborting")
        # validate the generated ROM before anything relies on it
        out_identity = self.games.identify_file(result.output_rom)
        if out_identity is None or out_identity.game_id != game.game_id:
            raise RunSetupError("Randomized ROM does not identify as the same game")
        if result.randomizer.modifies_rom and result.generated_rom_sha256 == source.sha256:
            raise RunSetupError("Randomizer reported success but the ROM is unchanged")
        randomizer.write_output(result, run.path / "randomizer.json", paths.rel)
        settings_file = result.manifest.get("settings_file")
        if settings_file:
            shutil.copyfile(settings_file["path"], run.path / "settings.rnqs")

        # 5. run environment: settings + ruleset snapshot
        write_json(run.path / "settings.json", {
            "seed": seed,
            "requested_seed": seed,
            "actual_randomizer_seed": result.actual_seed,
            "deterministic": result.randomizer.deterministic,
            "game_id": game.game_id,
            "randomizer_profile": profile.id,
            "randomizer": result.randomizer.to_dict(),
            "randomizer_settings": result.settings,
            "settings_file_snapshot": "settings.rnqs" if settings_file else None,
            "settings_sha256": result.settings_sha256,
            "ruleset": {"id": ruleset.id, "version": ruleset.version, "sha256": ruleset.sha256},
            "emulator": emulator.emulator_id,
            "tracker": tracker.tracker_id,
        })
        write_json(run.path / "ruleset.json", ruleset.raw)
        self.runs.update_fields(
            run.id, seed=seed, randomizer_name=result.randomizer.name,
            randomizer_version=result.randomizer.version, settings_sha256=result.settings_sha256,
            source_rom_sha256=result.source_rom_sha256,
            generated_rom_sha256=result.generated_rom_sha256,
            actual_randomizer_seed=result.actual_seed,
            deterministic=int(result.randomizer.deterministic))
        self.runs.record_event(run.id, EventType.ROM_RANDOMIZED, {
            "seed": seed, "requestedSeed": seed, "actualRandomizerSeed": result.actual_seed,
            "randomizer": result.randomizer.to_dict(),
            "sourceRomSha256": result.source_rom_sha256,
            "generatedRomSha256": result.generated_rom_sha256,
            "outputRom": paths.rel(result.output_rom),
            "deterministic": result.randomizer.deterministic})

        # game-specific initialisation
        ctx = RunContext(run_id=run.id, run_dir=run.path, rom_path=result.output_rom,
                         identity=identity, seed=seed, ruleset_id=ruleset.id,
                         emulator_id=emulator.emulator_id, tracker_id=tracker.tracker_id)
        game_meta = game.initialize_run(ctx)

        # 6-7. prepare emulator + tracker
        inst = emulator.detect_installation()
        if not inst.found:
            raise RunSetupError(f"Emulator {emulator.emulator_id} not installed: {'; '.join(inst.notes)}")
        if not tracker.detect_game(identity):
            raise RunSetupError(f"Tracker {tracker.tracker_id} does not support {game.display_name}")
        prep = tracker.prepare(run.path, identity)
        self._prepare_controller(run.id)

        self.runs.update_fields(run.id, extra_metadata={
            "gameAdapter": game_meta,
            "identity": identity.to_dict(),
            "rom": paths.rel(result.output_rom),
            "trackerScripts": [str(s) for s in prep.lua_scripts],
            "trackerNotes": prep.notes,
        })
        self.integrity.create_baseline(
            run.id, generated_rom=paths.rel(result.output_rom),
            generated_rom_sha256=result.generated_rom_sha256,
            source_rom_sha256=result.source_rom_sha256, seed=seed,
            settings_sha256=result.settings_sha256, ruleset_sha256=ruleset.sha256,
            tracker_capabilities=list(tracker.capabilities))

    def auto_profile(self, game_id: str) -> str:
        """First profile for the game whose (real) randomizer is available. Never the mock."""
        for p in self.profiles.list():
            if p.game_id != game_id or p.randomizer == "mock":
                continue
            if self.randomizers.get(p.randomizer).is_available()[0]:
                return p.id
        raise RunSetupError(
            f"No real randomizer is available for {game_id}. Bundle UPR ZX + Java with "
            f"`components fetch`, or choose a profile explicitly (e.g. --profile {game_id}-mock-standard).")

    def _guard_source(self, source) -> None:
        """Every run starts from a clean original — never from a generated ROM."""
        runs_dir = self.runs.paths.runs_dir.resolve()
        if source.path.resolve().is_relative_to(runs_dir):
            raise RunSetupError("Source ROM lies inside runs/: generated ROMs are never reused")
        reused = self.runs.db.query_one(
            "SELECT id FROM runs WHERE generated_rom_sha256 = ? AND generated_rom_sha256 != source_rom_sha256",
            (source.sha256,))
        if reused:
            raise RunSetupError(f"Source ROM is the generated ROM of {reused['id']}; refusing to reuse it")

    def _prepare_controller(self, run_id: str) -> None:
        if self.controllers is None:
            return
        try:
            device = self.controllers.select_device()
            mapping = self.controllers.mapping_id
            payload = {"device": device.to_dict() if device else None, "mapping": mapping,
                       "backends": self.controllers.backend_status()}
        except Exception as exc:  # noqa: BLE001 — a controller problem must not block a run
            payload = {"device": None, "error": str(exc)}
        self.runs.record_event(run_id, EventType.CONTROLLER_PREPARED, payload)

    def _launch(self, run_id: str, game: GameAdapter, emulator: EmulatorAdapter,
                tracker: TrackerAdapter) -> None:
        run = self.runs.get(run_id)
        meta = self.runs.metadata(run_id)
        rom = self.runs.paths.abs(meta["rom"])
        scripts = [Path(s) for s in meta.get("trackerScripts", [])]
        # 8. launch emulator (+ tracker script when the emulator accepts it)
        session = emulator.launch_with_rom(rom, scripts, log_file=run.path / "logs" / "emulator.log")
        self.live[run_id] = LiveSession(emulator, session, tracker)
        self.runs.record_event(run_id, EventType.EMULATOR_STARTED,
                               {"emulator": emulator.emulator_id, "pid": session.pid,
                                "command": session.command})
        self.runs.record_event(run_id, EventType.ROM_LOADED,
                               {"rom": meta["rom"], "rom_sha256": run.row["generated_rom_sha256"]})
        # 9. attach tracker
        tracker.start(session)
        attach = emulator.connect_tracker(session, scripts)
        self.runs.record_event(run_id, EventType.TRACKER_ATTACHED,
                               {"tracker": tracker.tracker_id, **attach})

    # ------------------------------------------------------------- tracker I/O
    def pump_tracker(self, run_id: str) -> int:
        """Move events observed by the tracker into the run's event log."""
        live = self.live.get(run_id)
        if not live:
            return 0
        events = live.tracker.poll_events()
        for te in events:
            if self.runs.get(run_id).archived:
                break
            self.runs.record_event(run_id, te.type, te.payload, source=f"tracker:{live.tracker.tracker_id}")
        return len(events)

    # ------------------------------------------------------------------ rules
    def engine_for(self, run_id: str) -> RulesEngine:
        if run_id not in self._engines:
            run = self.runs.get(run_id)
            # Evaluate against the run's own snapshot, not the live rules folder.
            rs = parse_ruleset(read_json(run.path / "ruleset.json"))
            self._engines[run_id] = RulesEngine(rs, run.game_id)
        return self._engines[run_id]

    def _evaluate_rules(self, event: Event) -> None:
        if not event.run_id or event.type in (EventType.RULE_VIOLATION, EventType.INTEGRITY_WARNING):
            return
        if event.source in ("system", "orchestrator", "integrity", "rules"):
            return  # only gameplay events (tracker/player) are judged
        run = self.runs.get(event.run_id)
        if run.status != RunState.ACTIVE:
            return
        for v in self.engine_for(event.run_id).evaluate(event):
            self.runs.record_event(event.run_id, EventType.RULE_VIOLATION, v.to_payload(), source="rules")
            if v.rule.failure_behavior == "FAIL_RUN":
                rid, reason = event.run_id, f"rule:{v.rule.id}"
                self.bus.call_soon(lambda rid=rid, reason=reason: self._auto_fail(rid, reason))

    def _auto_fail(self, run_id: str, reason: str) -> None:
        if self.runs.get(run_id).status != RunState.ACTIVE:
            return  # already handled (e.g. two violations from one event)
        if self.config.get("auto_new_run_on_failure"):
            self.restart_after_failure(run_id, reason)
        else:
            self.fail_run(run_id, reason)

    # ------------------------------------------------------------------ end
    def _stop_live(self, run_id: str) -> None:
        live = self.live.pop(run_id, None)
        if not live:
            return
        live.tracker.stop()
        if live.session:
            live.emulator.close(live.session)
            self.runs.record_event(run_id, EventType.EMULATOR_STOPPED, {"emulator": live.emulator.emulator_id})

    def _finish(self, run_id: str, state: RunState, reason: str | None, archive: bool) -> Run:
        self._stop_live(run_id)
        self.runs.transition(run_id, state, reason)
        self.integrity.mark_times(run_id)
        self.integrity.verify(run_id)
        self._engines.pop(run_id, None)
        if archive:
            self.runs.archive(run_id)
        return self.runs.get(run_id)

    def fail_run(self, run_id: str, reason: str = "player_failed", archive: bool = True) -> Run:
        return self._finish(run_id, RunState.FAILED, reason, archive)

    def complete_run(self, run_id: str, reason: str = "completed", archive: bool = True) -> Run:
        return self._finish(run_id, RunState.COMPLETED, reason, archive)

    def abandon_run(self, run_id: str, reason: str = "abandoned", archive: bool = True) -> Run:
        return self._finish(run_id, RunState.ABANDONED, reason, archive)

    def restart_after_failure(self, run_id: str, reason: str = "player_failed",
                              launch: bool = True) -> Run:
        """Run Failed -> archive -> new seed -> new ROM -> clean env -> launch."""
        old = self.runs.get(run_id)
        if old.status == RunState.ACTIVE:
            old = self.fail_run(run_id, reason)
        elif not old.is_terminal:   # prepared but never played: not a failure
            old = self.abandon_run(run_id, f"replaced before play: {reason}")
        elif not old.archived:
            self.runs.archive(run_id)
        return self.start_new_run(
            game_id=old.game_id, ruleset_id=old.row["ruleset_id"],
            profile_id=old.row["randomizer_profile_id"], emulator_id=old.row["emulator_id"],
            tracker_id=old.row["tracker_id"], source_rom_sha256=old.row["source_rom_sha256"],
            launch=launch, previous_run_id=run_id)

    @staticmethod
    def _get(table: dict[str, Any], key: str, kind: str) -> Any:
        if key not in table:
            raise RunSetupError(f"Unknown {kind} {key!r}. Available: {', '.join(table)}")
        return table[key]
