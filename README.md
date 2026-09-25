# NFNF IronMON

A local, portable application for Pokémon **IronMON** challenge runs. The goal is **one standalone program** that randomizes your own game, runs it in a built-in emulator with an Xbox controller, tracks encounters and deaths, applies IronMON rules, and sets up the next attempt automatically, all offline.

It runs on **Windows and Linux** from a single folder you can copy between machines. There's no cloud, account or network access.

> **You supply your own games.** NFNF IronMON never downloads, bundles, uploads or distributes ROMs or saves. It only reads the dumps you place in `games/original/`, and it never modifies them.

## Status: end of Phase 2

**TESTED** = implemented and exercised by tests or a recorded run · **IMPLEMENTED** = code exists but couldn't be exercised here · **PARTIAL** = limited form · **PLANNED** = designed, not built

| Area | Status |
|---|---|
| **Real FireRed randomization** with the bundled Java runtime + Universal Pokémon Randomizer ZX 4.6.1 and the official IronMON FRLG Standard settings | **TESTED**: on this machine, in the integration test, and in a clean container |
| Original ROMs found **in place** in `games/original/` (any filename, by header). Never copied, renamed, chmod-ed or written. | **TESTED** (sha256/size/mtime/permissions checked before and after) |
| Separate requested vs. actual randomizer seed; `deterministic=false` for UPR; generated ROM hash as the game's identity | **TESTED** |
| Runs, integrity, rules, event log, archival, SQLite (Phase 1) | **TESTED** |
| Career / attempt counter (attempts, active run #, best progress, longest run, total play time) | **TESTED** |
| Integrated emulator engine: mGBA core **in-process** via libretro. Boots the randomized FireRed; video, input, memory, save RAM, save states. | **PARTIAL**: headless prototype, tested |
| Playing in a window with sound | **PLANNED** (Phase 3). `run new` therefore stops at **READY** with the ROM prepared. |
| Controllers: Linux kernel joystick API, Xbox layout, JSON mappings, UI test panel | **TESTED** (simulated events; no real pad was connected) |
| Controllers: Windows XInput | **IMPLEMENTED** (not tested on Windows) |
| Native tracker (encounters, deaths from game memory) | **PLANNED** (Phase 3) |
| Portable **Linux** build (no Python or Java needed) | **TESTED** in a clean Debian 12 container |
| Portable **Windows** build | **PLANNED** |

Details: [docs/phase-2-status.md](docs/phase-2-status.md).

## Quick start (from source, Linux)

Requires Python ≥ 3.10 for development. End users of the portable build don't need Python.

```bash
# 1. Bundle the third-party components (JRE, UPR ZX, mGBA core, IronMON settings), sha256-verified.
#    This downloads into this folder; nothing is installed system-wide. Run it once.
python3 -m nfnf_ironmon components fetch

# 2. Put your own ROM dumps in games/original/ (any file name), then check everything
python3 -m nfnf_ironmon doctor

# 3. Start a run: randomizes a fresh copy of your FireRed into runs/RUN-xxxxxx/rom/randomized.gba
python3 -m nfnf_ironmon run new
python3 -m nfnf_ironmon run list
python3 -m nfnf_ironmon run verify RUN-000001

# 4. Boot the run's ROM in the integrated emulator (headless) and save a screenshot
python3 -m nfnf_ironmon emulator smoke RUN-000001

# Career, controllers, UI, local API
python3 -m nfnf_ironmon career
python3 -m nfnf_ironmon controller list
python3 -m nfnf_ironmon controller test
python3 -m nfnf_ironmon ui
python3 -m nfnf_ironmon serve            # http://127.0.0.1:8765/api/runs
```

Other commands: `rom scan|import|inspect|list`, `rules list|show`, `run show|fail|restart|abandon|complete|archive|event`, `components list`, `emulator info`, `games`, `events`, `doctor --json`. Use `--home <folder>` (or set `NFNF_IRONMON_HOME`) to keep user data somewhere else.

To try the whole pipeline without the real randomizer, pass `--profile firered-mock-standard --emulator mock --tracker mock`: this uses the Phase 1 mock tools.

## Portable build (Linux)

```bash
docker run --rm -v "$PWD":/src -w /src python:3.12-slim-bookworm sh packaging/build_linux.sh
# → dist/NFNF-IronMON-Linux/   then:  ./NFNF-IronMON doctor
```

The build runs in a Debian 12 container, which makes the result compatible with glibc ≥ 2.36. See [docs/packaging.md](docs/packaging.md).

## Tests

```bash
python3 -m unittest discover -s tests -t tests
```

154 tests. They need no ROM: they use synthetic header-only files. Tests for real components skip themselves when those components are missing. `tests/integration/test_real_firered.py` runs the real randomizer against your FireRed if it's in `games/original/`; it checks that the file is untouched and cleans up after itself.

## Where things live

| Folder | Contents |
|---|---|
| `games/original/` | **your** ROM dumps (read only, never modified) |
| `runs/RUN-xxxxxx/` | one attempt: `rom/randomized.gba`, `settings.json` (+ `settings.rnqs`), `metadata.json`, `integrity.json`, `events.jsonl`, `saves/`, `logs/` (UPR log) |
| `runtime/`, `emulator/cores/`, `randomizer/upr-zx/` | bundled third-party components (see `components.json`) |
| `rules/`, `randomizer-profiles/`, `input-mappings/` | configurable rulesets, randomizer profiles, controller mappings |
| `config/settings.json` | your overrides only |
| `data/` | SQLite database (history and career) |

ROMs, saves, runs, the database and the bundled binaries are all git-ignored.

## Adding games

The core has no game-specific code. A game is a `GameAdapter` (header identification, compatible tools, run setup) plus a randomizer profile. Red and Silver are already identified; running them is Phase 6/7. See [docs/architecture.md](docs/architecture.md).

## Documentation

* [phase-2-status.md](docs/phase-2-status.md): what works, what's partial, what's planned
* [standalone-dependency-audit.md](docs/standalone-dependency-audit.md): every dependency, whether it can be bundled, and its license
* [upr-zx-integration.md](docs/upr-zx-integration.md): verified CLI, exit codes, seeds, `.rnqs` format, FireRed profile
* [emulator-selection.md](docs/emulator-selection.md): why the mGBA core via libretro, with evidence
* [controller-architecture.md](docs/controller-architecture.md): USB/XInput → mapping → emulator
* [tracker-integration.md](docs/tracker-integration.md): native tracker plan; what can be reused from the community tracker
* [packaging.md](docs/packaging.md): portable Windows/Linux builds
* [architecture.md](docs/architecture.md), [development-roadmap.md](docs/development-roadmap.md), [environment-audit.md](docs/environment-audit.md), [component-research.md](docs/component-research.md)

## Third-party components

Universal Pokémon Randomizer ZX (GPL-3.0, run as a separate process), Eclipse Temurin JRE (GPL-2.0 with Classpath Exception), the mGBA libretro core (MPL-2.0), and IronMON FRLG settings from Ironmon-Tracker (MIT). Each is used unmodified. Public release still needs the full license texts and source offers bundled: see the dependency audit.
# NFNF-IronMON
