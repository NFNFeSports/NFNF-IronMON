# Architecture

## Goals

* The core has **no game-specific knowledge**. FireRed, Red and Silver are plugins.
* **One standalone application** (Phase 2 direction). The randomizer, emulator engine, tracker engine, rules, controllers and career live *inside* NFNF IronMON or its portable folder. Third-party pieces are **bundled components** (`components.json`), never programs the user installs. Adapters for external tools (BizHawk, the mGBA app, the community tracker) remain only as development references.
* The **run folder is the source of truth**. SQLite is a fast index built from the same data.
* **Portable**: the application (app root) and user data (home) default to the same folder. Stored paths are relative.
* **Evidence, not accusations**: integrity checks report what they observed and never claim to know intent.
* The core is standard library only (Python ≥ 3.10: `sqlite3`, `tkinter`, `http.server`, `ctypes`). The end user doesn't need Python: the frozen build bundles it.

## Standalone engine view (Phase 2)

```text
                                   NFNF IronMON (one application)
 ┌───────────────┬──────────────┬───────────────┬────────────────┬────────────────┬──────────────┐
 │ Randomizer    │ Emulator     │ Tracker       │ Controller     │ Rules / Runs / │ UI / API     │
 │ Engine        │ Engine       │ Engine        │ Engine         │ Integrity /    │ CLI, Tk,     │
 │ UPR ZX 4.6.1  │ mGBA core in │ PLANNED (P3): │ Linux js API ✔ │ Career ✔       │ 127.0.0.1    │
 │ + bundled JRE │ process via  │ memory → events│ XInput (impl.) │ SQLite ✔      │ JSON API ✔   │
 │ ✔ real        │ libretro ✔   │ via read_bus  │ SDL2 (planned) │                │              │
 │               │ (headless)   │               │ InputMapping ✔ │                │              │
 └───────────────┴──────────────┴───────────────┴────────────────┴────────────────┴──────────────┘
     runtime/java/<plat>   emulator/cores/<plat>   (in code)    input-mappings/     rules/  data/
     randomizer/upr-zx/
```

## Components

```text
                ┌─────────── UI / API ────────────┐
                │  cli.py   ui.py (Tk)   api.py   │   (127.0.0.1 JSON API)
                └───────────────┬─────────────────┘
                                │
                        app.py  Application (facade)
                                │
      ┌──────────────┬──────────┴──────────┬─────────────────┐
      │              │                     │                 │
 orchestrator.py  runs.py            integrity.py        rules.py
 (new-run /       Run Manager        Integrity Manager   Rules Engine
  fail→restart)   state machine,     baseline, observe,  JSON rulesets,
                  events.jsonl,      verify              HARD/SOFT/MANUAL
                  archival
      │              │                     │                 │
      └──────────────┴─────── events.py EventBus ────────────┘
                                │                projections.py
                              db.py (SQLite)     (pokemon/encounters/deaths)
                                │
   ┌───────────────┬────────────┼──────────────┬─────────────────┐
 games/         roms.py      randomizers/    emulators/        trackers/      controllers/
 GameAdapter    ROM Manager  RandomizerAdapter EmulatorAdapter TrackerAdapter ControllerManager
 ├ FireRed ✔    (import +    ├ upr-zx ✔ real   ├ nfnf-libretro ├ none ✔ (dflt)├ linux-joystick ✔
 ├ Red   (id)    in-place    ├ mock ✔          │  (PROTOTYPE)  ├ mock ✔       ├ xinput (impl.)
 └ Silver(id)    discovery)  └ rnqs validator  ├ mock ✔        └ ironmon-     ├ sdl2 (planned)
                                               ├ bizhawk (ref)   tracker (ref)└ InputMapping (JSON)
                                               └ mgba (ref)
 components.py (bundled third-party, sha256-verified) · career.py · doctor.py
```

The spec's component names map to code like this:

| Spec | Code |
|---|---|
| Game Manager | `games/` (`GameRegistry`, `GameAdapter`) |
| ROM Manager | `roms.py` |
| Randomizer Adapter | `randomizers/` |
| Emulator Adapter | `emulators/` |
| Tracker Adapter | `trackers/` |
| Rules Engine | `rules.py` + `rules/*.json` |
| Run Manager | `runs.py` + `orchestrator.py` |
| Integrity Manager | `integrity.py` |
| Database | `db.py` |
| UI/API | `cli.py`, `ui.py`, `api.py`, facade `app.py` |
| Platform abstraction | `paths.py`, `platform_support.py`, `config.py` |
| Controller/Input Engine | `controllers/` + `input-mappings/*.json` |
| Emulator Engine (integrated) | `emulators/libretro.py`, `emulators/integrated.py` |
| Bundled components | `components.py` + `components.json` |
| Career / tries | `career.py` |

## Adapter contracts

### GameAdapter (`games/base.py`)

| Member | Purpose |
|---|---|
| `game_id`, `display_name`, `generation`, `platform`, `rom_extensions` | Static description |
| `status` | `"supported"` (runs possible) or `"planned"` (identification only) |
| `identify(header, size)` | Game and version identification from the cartridge header |
| `known_dumps` | SHA-1 → reference dump label (No-Intro) |
| `emulator_config(emulator_id)` / `tracker_config(tracker_id)` | Per-game integration hints |
| `initialize_run(RunContext)` | Game-specific run setup; returns metadata stored with the run |
| `supported_emulators` / `supported_trackers` / `supported_randomizers` | Compatibility, checked by the orchestrator |

`FireRedGameAdapter` parses the GBA header (title `POKEMON FIRE`, game codes `BPR[EJFDSI]`, revision byte, header checksum). `RedGameAdapter` and `SilverGameAdapter` parse the Game Boy header. They're registered, listed and identified exactly like FireRed; only `status` differs.

### RandomizerAdapter (`randomizers/base.py`)

`info()` returns name, version, license and `deterministic`. The other operations are `is_available()`, `detect_supported_game()`, `validate_rom()`, `generate_seed()` (a 31-bit int, which suits Java), `randomize(request)`, which calls the adapter's `_randomize` and then hashes the output, and `write_output()` (writes `randomizer.json`). The base class refuses output that would overwrite the source ROM.

A **randomizer profile** (`randomizer-profiles/*.json`) ties a game to a randomizer and its settings. For UPR, the settings point at a `.rnqs` file.

### EmulatorAdapter (`emulators/base.py`)

The operations are `detect_installation()`, `build_launch_command()`, `launch()`, `launch_game()`, `launch_with_rom()`, `is_running()`, `close()`, `connect_tracker()` and `capabilities()`. Each adapter reports its `status` (`PROTOTYPE`, `EXTERNAL` or `MOCK`) and whether it is `interactive`. The integrated adapter (`nfnf-libretro`) loads the bundled core in-process. It isn't interactive yet, so the orchestrator stops runs at READY instead of pretending to launch them. The external adapters (BizHawk, the mGBA app) find executables through `platform_support` and remain development references.

### TrackerAdapter (`trackers/base.py`)

The operations are `detect_installation()`, `detect_game()`, `prepare()` (returns the Lua scripts the emulator must load), `start()`, `stop()`, `poll_events()` and `get_game_state()`. Each tracker also declares `capabilities`: which things it can actually observe (`save_loads`, `savestate_loads`, `resets`, `play_time`, …).

## Data layout (app root + portable home)

```text
NFNF-IronMON/
├── app/ (frozen build) or nfnf_ironmon/ (source)      ─┐
├── runtime/java/<platform>/     bundled JRE              │ app root: application +
├── emulator/cores/<platform>/   mGBA libretro core       │ bundled components
├── randomizer/upr-zx/           PokeRandoZX.jar          │ (resolved from the executable)
├── components.json              component manifest      ─┘
├── rules/ randomizer-profiles/ input-mappings/   bundled defaults, user-editable
├── config/settings.json     user overrides only (defaults come from code)   ─┐
├── data/nfnf-ironmon.sqlite3 (+ .vN.bak before migrations)                    │ home: user data
├── games/original/          the user's own ROMs, read in place, never written │ (defaults to app root)
│                            (`rom import` from elsewhere copies to <game>/<sha256>.<ext>)
└── runs/RUN-000001/
    ├── metadata.json    summary + state history
    ├── settings.json    seed, profile, randomizer settings, ruleset ref
    ├── ruleset.json     snapshot of the ruleset in force
    ├── randomizer.json  seed, randomizer {name, version}, source/generated ROM SHA-256, timestamp
    ├── integrity.json   baseline + last verification
    ├── events.jsonl     hash-chained event log
    ├── archive.json     sealed file manifest (after archival)
    ├── rom/  saves/  logs/
```

The **app root** is the executable's parent folder when frozen (or its parent's parent if the executable lives in `app/`), otherwise the source checkout. The **home** is chosen from the `--home` option, then `NFNF_IRONMON_HOME`, then the app root. A separate home receives copies of the bundled rules, profiles and mappings, and existing files are never overwritten. Bundled components always resolve from the app root. All stored paths are relative to home. There is a test that copies a home folder elsewhere, removes the original, and carries on the run history.

## Run lifecycle

```text
CREATED → PREPARING → READY → ACTIVE → FAILED | COMPLETED | ABANDONED
   │          │          │                     │
   └──────────┴──────────┴→ ABANDONED / INVALID └→ INVALID (later integrity finding)
```

Any other transition raises `InvalidTransition`. Run IDs (`RUN-000001`) come from the larger of the highest ID in the DB and the highest folder name on disk, so a lost database never causes an ID to be reused.

### Start New Run (`RunOrchestrator.start_new_run`)

1. Resolve and validate game, ruleset, profile, randomizer, emulator and tracker. Profile `auto` picks the game's **real** randomizer and errors out rather than silently falling back to the mock.
2. Find the original. If none is registered, `games/original/` is scanned in place (read-only). Reject any source that lies under `runs/` or whose hash is a generated ROM of an earlier run.
3. Generate the bookkeeping seed, unless one was given.
4. Create the run (`CREATED`), then move it to `PREPARING`.
5. Randomize from the original into `runs/<id>/rom/randomized.<ext>` (UPR: validate the `.rnqs`, run the bundled Java, check the exit code, success marker and output, move the log to `logs/`, parse the actual seed). Re-hash the original to prove it didn't change.
6. **Validate the output**: it must still identify as the same game, and a real randomizer must actually have changed it.
7. Write `settings.json` (requested and actual seeds, `deterministic`, effective settings including the `.rnqs` hash), `settings.rnqs`, `ruleset.json` and `randomizer.json`, then call `GameAdapter.initialize_run`.
8. Prepare the emulator, the tracker and the **controller** (`CONTROLLER_PREPARED`). Record the integrity baseline. Move to `READY`.
9. If the emulator is interactive: launch, attach the tracker, move to `ACTIVE` (assigning the career **attempt number**). Otherwise stay `READY` and record `metadata.launch.deferred` with the reason.

If any step fails, the run moves to `ABANDONED` with `end_reason = setup_error: …`, and the traceback goes to `logs/setup-error.log`.

### Run Failed → new run (`restart_after_failure`)

The orchestrator stops the tracker and emulator, moves the run to `FAILED`, records start and end times, runs integrity verification, and archives the run (sealed manifest, ROM made read-only). It then starts a new run with the same game, ruleset, profile and tools, a new seed, and `previous_run_id` set to the old run. If `auto_new_run_on_failure` is set in config, a `FAIL_RUN` rule violation triggers this automatically.

## Event system

Event types are UPPER_SNAKE strings in a registry. `register_event_type()` lets plugins add their own, and publishing an unregistered type raises an error. `RunManager.record_event()` first **persists** the event (appended to `events.jsonl`, inserted into the DB) and then **publishes** it on the `EventBus`.

The bus is synchronous and **breadth-first**. An event published by a handler is queued until every handler has seen the current event. `call_soon()` defers work until the whole chain has finished. For example: `POKEMON_FAINTED` → the rules engine emits `RULE_VIOLATION` → a deferred call fails the run. So a run's state never changes while an event is still being handled. An exception in one handler is logged and doesn't stop the others; tests use `strict=True` to make such errors fail loudly.

Subscribers:

* **Rules engine** (in the orchestrator): judges gameplay events from tracker, player or API sources against the run's **snapshot** ruleset, and only while the run is `ACTIVE`.
* **Integrity manager**: watches ROM, save, reset and emulator events, and play time on every event.
* **Gameplay projection**: fills the `pokemon`, `encounters` and `deaths` tables.

## Rules engine

Rulesets are JSON files (`rules/*.json`). Each rule has these fields:

| Field | Values |
|---|---|
| `id`, `description` | text |
| `severity` | `HARD` (provable automatically), `SOFT` (suspicious, can't be proven), `MANUAL` (the player confirms) |
| `detection` | `automatic`, `heuristic`, `manual`, `unavailable` |
| `event_type` | a registered event type, or `null` for a checklist-only rule |
| `condition` | `{"field","op","value"}` with `all`, `any`, `not`. Operators: `eq ne gt gte lt lte in not_in exists missing contains` |
| `failure_behavior` | `FAIL_RUN`, `WARN`, `FLAG_FOR_REVIEW`, `CONFIRM_WITH_PLAYER`, `IGNORE` |
| `games` | optional list of game IDs the rule is limited to |

Each triggered rule also carries an **outcome**: `RUN_FAILED` (FAIL_RUN), `VIOLATION` (FLAG_FOR_REVIEW, CONFIRM_WITH_PLAYER), `WARNING` (WARN), or `VALID` when nothing fires. `RulesEngine.outcome(event)` returns the worst one.

Validation rejects incoherent rules: a HARD rule that isn't automatic, a MANUAL rule set to `FAIL_RUN`, unknown event types, bad operators. A ruleset's hash is taken over canonical JSON, so reformatting the file doesn't change it.

## Integrity

A **baseline** is written at preparation: generated ROM path and SHA-256, source ROM SHA-256, seed, settings hash, ruleset hash, and tracker capabilities. `verify()` then checks:

| Check | What makes it fail |
|---|---|
| `rom_hash` | Run ROM missing or modified → INVALID |
| `settings_hash` | Seed or randomizer settings in `settings.json` changed → INVALID |
| `settings_file` | The `settings.rnqs` snapshot differs from the hash recorded in `settings.json` → INVALID |
| `ruleset_hash` | `ruleset.json` snapshot changed → INVALID |
| `event_log` | Hash chain broken (a line edited, removed or reordered) → INVALID |
| `archive` | For archived runs, any file added, removed or modified after sealing → INVALID |
| `telemetry` | Tracker can't observe resets, save loads, state loads or play time → UNKNOWN |
| observed | A save state was loaded, or play time went backwards (rollback / save restored) → SUSPICIOUS. A ROM loaded with the wrong hash → INVALID. Resets and emulator start/stop are logged as INFO only. |

The overall result is the worst individual result, in the order VERIFIED < UNKNOWN < SUSPICIOUS < INVALID. These checks give evidence of tampering, not prevention: someone with full control of the files can rebuild a consistent history. Phase 11 covers stronger options.

## Adding a game (for example, Pokémon Emerald)

1. `games/emerald.py`: subclass `GameAdapter`, implement `identify()` (GBA code `BPEE`), and list the supported tools.
2. Register it in `games/__init__.py:default_game_adapters()`.
3. Add `randomizer-profiles/emerald-*.json`.
4. Optionally add game-specific rules with `"games": ["emerald"]`, or a new ruleset.

The core, run manager, integrity system, DB schema and UI don't change. A tracker that only supports some games declares this through `supported_games` and `detect_game()`. Game-specific events such as `SAFARI_BALL_THROWN` are added with `register_event_type()`.

## Security and copyright

The app never downloads, uploads or distributes ROMs or saves, and makes no network calls. The API binds to `127.0.0.1` only. `.gitignore` blocks `*.gb *.gbc *.gba *.sav *.state` and more, plus the `games/original/`, `runs/` and `data/` folders.
