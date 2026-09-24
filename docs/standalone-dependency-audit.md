# Standalone Dependency Audit

Date: 2026-09-24 (Phase 2). Scope: everything the final NFNF IronMON application needs at runtime, and whether it can ship **inside** the portable folder so the user installs nothing.

> Note: the Phase 2 brief mentions `nfnf_ironmon/adapters/`. That folder never existed. The adapters live in `nfnf_ironmon/{games,randomizers,emulators,trackers,controllers}/`, and all of them were audited.

## Summary table

| Component | Current dependency (Phase 2) | Can bundle? | License | Windows | Linux | Replacement / plan |
|---|---|---|---|---|---|---|
| **Python runtime** | Needed to run from source. **Not needed** by the frozen build (verified in a clean Debian 12 container). | Yes (PyInstaller bundles libpython + stdlib) | PSF-2.0 | PyInstaller onedir (planned, untested) | PyInstaller onedir, **tested** | None needed. Keep Python and freeze it. |
| **Java runtime** | Bundled **Eclipse Temurin JRE 21.0.12.1+1** at `runtime/java/<platform>/`. No system Java installed. | **Yes**. GPL-2.0 with Classpath Exception explicitly allows redistribution. | GPL-2.0-only WITH Classpath-exception-2.0 | zip pinned in `components.json` (sha256 from Adoptium), not fetched yet | **Bundled and tested** | Shrink with `jlink` (only the modules UPR needs), measured in Phase 3. The long-term option is a native randomizer (see UPR row). |
| **UPR ZX 4.6.1** | Bundled at `randomizer/upr-zx/PokeRandoZX.jar`, run as a **separate process** | **Yes**, with GPL-3.0 obligations: ship the license text and a source offer (link to the v4.6.1 tag). Running it as a separate program keeps NFNF's own code free of GPL linking. | GPL-3.0 | Same jar + Windows JRE | **Tested** (real FireRed randomization) | Short term: keep it. Long term (optional): native randomization for the settings IronMON actually uses, which removes Java (~160 MB). High effort; not planned before Phase 8. |
| **IronMON FRLG settings** (`firered-ironmon.rnqs`) | Bundled, 112 bytes, byte-for-byte from Ironmon-Tracker `FRLG Standard.rnqs` | **Yes** | MIT (Ironmon-Tracker repo) | same file | same file | None needed. More presets (Kaizo, Survival, …) come from the same folder. |
| **BizHawk** | **Not used** by the final design. Adapter kept for development and comparison. | Legally yes (MIT). **Technically no**: needs .NET/Mono, is Windows-centric, can't be embedded, and is a full application. | MIT | — | — | Replaced by the integrated libretro engine (see `emulator-selection.md`). |
| **mGBA (standalone app)** | **Not used** as an external program | Yes (MPL-2.0), but not needed | MPL-2.0 | — | — | Its **core** is used directly (next row). |
| **mGBA libretro core** | Bundled at `emulator/cores/<platform>/`, loaded **in-process** through NFNF's `ctypes` libretro host | **Yes**. MPL-2.0 allows binary redistribution, provided the source of the MPL files stays available (link to the source repo). | MPL-2.0 | `.dll` listed, not pinned or fetched yet | **Bundled and tested** (headless boot of the randomized FireRed) | For release builds, pin a tagged libretro/mgba commit instead of the nightly. |
| **Community Ironmon-Tracker** | **Not used** at runtime. Adapter kept as reference. | Legally yes (MIT). Technically it only runs inside BizHawk or mGBA's own Lua host, which NFNF doesn't embed. | MIT | — | — | Native NFNF tracker engine (Phase 3) that reads memory through the libretro host. Its MIT address tables and data can be reused with attribution (see `tracker-integration.md`). |
| **SQLite** | Python's built-in `sqlite3` | Yes (inside the frozen Python) | Public domain | yes | yes, **tested** | — |
| **UI framework** | Tkinter (Tcl/Tk 8.6), part of the stdlib | Yes. PyInstaller bundles `libtcl`/`libtk` and their data (seen in the Linux build). | Tcl/Tk license (BSD-style) | bundled by PyInstaller (untested) | Bundled. The UI smoke test passes from source; the frozen UI isn't tested in the container (no display). | Phase 3 needs a game window with scaling, audio and vsync. Tk can't do that well, so SDL2 is the likely choice for the game view (see below). |
| **Controller input: Linux** | Kernel joystick API (`/dev/input/js*`), stdlib `fcntl`/`os` only | Nothing to bundle | — | — | **Implemented and tested** (simulated events; real device enumeration on this host) | SDL2 GameController later, for hot-plug and wider HID mapping. |
| **Controller input: Windows** | XInput via `ctypes` (`XInput1_4.dll`, which ships with Windows 8+) | Nothing to bundle (OS DLL) | OS component | **Implemented, untested** (tested only with a fake DLL) | — | Same SDL2 plan |
| **SDL2** (planned: window, audio, controllers) | Not bundled yet | Yes. zlib license, very permissive. PySDL2 is public domain. | zlib / CC0 | planned | planned | Chosen for Phase 3 presentation and hot-plug controllers |
| **Game ROMs** | User-supplied in `games/original/` | **Never bundled** | Copyrighted (user's own dumps) | — | — | Only the user's own files are used |

## What a user needs today vs. at the end

| | Needed now (running from source) | Needed with the portable Linux build (tested) | Final goal |
|---|---|---|---|
| Python | yes (development) | **no** | no |
| Java | no. The bundled JRE is used (it lives in the app folder, not installed system-wide). | **no** | no |
| UPR ZX | no (bundled) | **no** | no |
| Emulator program | no (core bundled, in-process) | **no** | no |
| Tracker program | no | **no** | no |
| ROMs | own dumps in `games/original/` | own dumps in `games/original/` | own dumps only |

## Remaining licensing tasks before a public release

1. Ship **full license texts** (GPL-3.0 for UPR ZX, MPL-2.0 for mGBA, MIT for Ironmon-Tracker; the Temurin JRE already carries its own `legal/` folder), plus a written **source offer** or links to the exact source revisions. The Phase 2 assembler writes `THIRD-PARTY-NOTICES.md` with licenses and URLs. That is enough for a local build, **not enough for public distribution**.
2. Pin the mGBA core to a tagged revision (it's currently a nightly, pinned by hash).
3. Decide NFNF IronMON's own license. Nothing forces GPL, because UPR runs as a separate process and the MPL core is loaded unmodified.
