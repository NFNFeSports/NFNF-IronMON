# Phase 2 Status

Legend: **TESTED**: implemented and exercised by automated tests or a recorded run. **IMPLEMENTED**: code exists but couldn't be exercised here. **PARTIAL**: works in a limited form. **PLANNED**: designed, not built. **BLOCKED**: can't proceed without something external.

## Objectives

| Objective | Status | Evidence |
|---|---|---|
| A. Standalone dependency audit | **DONE** | `standalone-dependency-audit.md` |
| B. Real FireRed randomization | **TESTED** | RUN-000001 in this folder, the integration test `tests/integration/test_real_firered.py`, and a clean-container run |
| C. Integrated emulator strategy | **DONE** + prototype **PARTIAL** (headless) | `emulator-selection.md`, `emulators/libretro.py`, 7 tests |
| D. Controller/input architecture | **TESTED** on Linux (simulated events). Windows XInput **IMPLEMENTED** (fake-DLL tests only). | `controller-architecture.md`, 13 tests |
| E. Standalone packaging architecture | **Linux: TESTED** (clean container). **Windows: PLANNED** | `packaging.md` |

## Feature matrix

| Feature | Status | Notes |
|---|---|---|
| In-place discovery of ROMs in `games/original/` (no copy, no chmod, no sidecar) | TESTED | Found FireRed, Red and Silver by header. The folder listing and every file's sha256/size/mtime/mode are unchanged. |
| `.rnqs` validation in pure Python | TESTED | Version, length, Base64, CRC, ROM name |
| FireRed IronMON profile (`firered-ironmon.rnqs`) | TESTED | Official FRLG Standard file from Ironmon-Tracker (MIT), byte-for-byte |
| UPR ZX 4.6.1 run with the bundled JRE | TESTED | exit code, success marker, log, 300 s timeout, stderr capture |
| Requested vs. actual seed, `deterministic=false` | TESTED | The actual seed is parsed from UPR's log |
| Output validation (same game, changed, hashed, only in `runs/<id>/rom/`) | TESTED | |
| Generated ROM never reused as a source | TESTED | Checked by location and by content hash |
| `.rnqs` snapshot + integrity check `settings_file` | TESTED | |
| `auto` profile (a real randomizer only, never a silent mock fallback) | TESTED | |
| Integrated emulator: load, run, video, input, memory, save RAM, save states | TESTED (headless) | ~900 fps |
| Interactive play (window, audio, pacing, live controller) | PLANNED (Phase 3) | `run new` stops at **READY** with the reason recorded |
| Controller detection: Linux | TESTED | A non-gamepad joystick node is correctly ignored. No real pad was available. |
| Controller detection: Windows XInput | IMPLEMENTED | untested on Windows |
| SDL2 controller backend | PLANNED | |
| Controller UI panel (Device/Status/Mapping/TEST/CONFIGURE) | TESTED (builds; values shown) | |
| Career / attempts (attempts, active #, completed, best progress, longest run, total time) | TESTED | CLI `career`, UI panel, `GET /api/career` |
| DB schema v2 migration with automatic backup | TESTED | `data/nfnf-ironmon.sqlite3.v1.bak` was created here |
| Config stores user overrides only | TESTED | Old config kept as `config/settings.json.phase1.bak` |
| `doctor` with honest statuses | TESTED | Never shows READY for PLANNED or PROTOTYPE items |
| Component manifest + sha256-verified fetch | TESTED | JRE sha from Adoptium; others pinned on the first fetch |
| Portable Linux build (PyInstaller, bookworm) | TESTED | Clean container, no Python or Java |
| Windows build | PLANNED | Needs a Windows build host |
| Native tracker engine (FireRed memory → events) | PLANNED (Phase 3) | Not started, per the brief |
| Automatic new run *with* emulator restart and a fresh save | PARTIAL | Archive → new seed → new ROM → fresh `saves/` works. The emulator restart waits for interactive play. |
| Rule outcome classes VALID / WARNING / VIOLATION / RUN_FAILED | TESTED | Added to rule-violation payloads |

## Tests

* Baseline: 86 (Phase 1). Three were updated because Phase 2 deliberately changed their contract: UPR is now runnable; `RandomizerInfo` gained `modifies_rom`; the UPR command gained `-Djava.awt.headless=true`.
* Total: **154**. All pass; 0 skipped on this machine (the real-component tests skip themselves where components or ROMs are missing).
* Command: `python3 -m unittest discover -s tests -t tests`

## Blockers and risks

| Item | Kind |
|---|---|
| No Windows machine: the Windows build and XInput are untested | BLOCKED on hardware/CI |
| No Xbox controller connected: real-pad behaviour unverified | BLOCKED on hardware |
| The official FRLG Standard settings may have been updated since the tracker's 2022 file | Needs confirmation by the IronMON community / the user |
| mGBA core comes from a nightly (pinned by hash) | Pin a tagged revision before release |
| Full license texts and source offers aren't bundled yet | Required before public distribution |
| JRE size (159 MB) | Improve with `jlink` (Phase 3 packaging task) |
