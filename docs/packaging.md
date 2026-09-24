# Packaging: Portable Windows and Linux Builds

## Strategy

| Choice | Decision | Why |
|---|---|---|
| Freezer | **PyInstaller 6, onedir** | Mature; bundles CPython, the stdlib, `sqlite3`, `tkinter`/Tcl-Tk and `ctypes` without any code changes. Onedir (not onefile) starts fast and leaves the bundled JRE, core and data as plain files beside it. The bootloader license (GPL with an exception) allows distributing the result under any license. |
| Nuitka | Keep as a later option | It compiles to C (faster startup, harder to decompile) but needs a C toolchain per platform. The app is I/O-bound and the emulator core is already native, so the gain is small for now. |
| Rewrite in another language | **No** | Not needed. The spike shows a frozen Python build runs the full pipeline with no Python installed. |
| Linux glibc | **Build in an old-glibc container** (Debian 12, glibc 2.36) | A frozen binary needs a glibc at least as new as the one it was built against. Building on this host (glibc 2.43) would only run on the newest distros. |
| Windows | Build **on Windows** (PyInstaller doesn't cross-compile), for example in a CI runner | Same assembler script, `--platform windows-x64` |

## Layout (Part L)

```
NFNF-IronMON-Linux/                 NFNF-IronMON-Windows/
├── NFNF-IronMON   (launcher)       ├── NFNF-IronMON.bat → app\nfnf-ironmon.exe
├── app/           frozen app       ├── app\
├── runtime/java/linux-x64/         ├── runtime\java\windows-x64\
├── emulator/cores/linux-x64/       ├── emulator\cores\windows-x64\
├── randomizer/upr-zx/              ├── randomizer\upr-zx\
├── rules/ randomizer-profiles/ input-mappings/ components.json
├── games/original/   ← user ROMs
├── runs/ data/ config/             (user data, created empty)
└── THIRD-PARTY-NOTICES.md
```

`paths.resolve_app_root()` handles this layout: if the executable lives in `app/`, the app root is its parent. The user data home defaults to the app root, so copying the whole folder moves the application **and** the career.

## Build (Linux), reproducible

```bash
python3 -m nfnf_ironmon components fetch            # JRE + UPR + core + profile, sha256-verified
docker run --rm -v "$PWD":/src -w /src python:3.12-slim-bookworm sh packaging/build_linux.sh
# → dist/NFNF-IronMON-Linux/   (the assembler refuses to package any .gb/.gbc/.gba/.sav/.state)
```

## Phase 2 verification (Linux)

The build was tested in a **clean `debian:bookworm-slim` container**: no Python, no Java, glibc 2.36. The folder was copied to `/opt/NFNF-IronMON` and the user's FireRed was mounted **read-only** and copied into `games/original/`.

| Step | Result |
|---|---|
| `./NFNF-IronMON doctor` | Python runtime **BUNDLED** (3.12.14, frozen), Java **BUNDLED**, UPR ZX FOUND, profile FOUND, FireRed FOUND, Linux **PORTABLE BUILD** |
| `run new --seed 18472931` | RUN-000001 **READY** with `rom/randomized.gba` (actual UPR seed `209617962950069` in the final v0.2.0 build test, `deterministic: false`) |
| `run verify` | rom/settings/settings_file/ruleset/event_log all VERIFIED. Overall UNKNOWN (no tracker yet). |
| `emulator smoke` | The randomized FireRed booted in the bundled core, 1320 frames at ~900 fps, and the screenshot shows Oak's intro |
| `career` | renders (0 attempts: the run is READY, not played) |

Size: **195 MB** in total. About 159 MB of that is the JRE; `jlink` can cut it to the modules UPR needs (still to be measured).

## Not yet done

* A Windows build, and any test on Windows (no Windows host was available).
* The frozen **UI** was not exercised in the container (no display). The UI smoke test passes from source, and PyInstaller bundled `libtk`/`libtcl`.
* A tested second Linux distribution. Newer distros should work thanks to glibc's forward compatibility, but only Debian 12 was actually run.
* Full license texts and source offers (see `standalone-dependency-audit.md`).
* Code signing on Windows (SmartScreen) and AppImage packaging are optional later steps.
