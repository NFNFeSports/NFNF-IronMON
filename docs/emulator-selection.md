# Emulator Selection

**Question**: which emulator technology can be legally redistributed **inside** NFNF IronMON and give it the APIs automated IronMON tracking needs (memory, saves, input, frames) on Windows and Linux, for GB, GBC and GBA?

**Answer**: the **mGBA core, hosted in-process through the libretro API** by NFNF's own `ctypes` host (`nfnf_ironmon/emulators/libretro.py`). This was validated by a working prototype in Phase 2 (evidence below). BizHawk was *not* chosen just because Phase 1 had an adapter for it.

## Candidates

License entries marked ✔ were checked for Phase 2. The others are as published by each project and **must be re-checked before any bundling**.

| | **mGBA core (libretro)** | BizHawk | mGBA app (external) | SameBoy | Gambatte | VBA-M | NanoBoyAdvance | SkyEmu | PyBoy |
|---|---|---|---|---|---|---|---|---|---|
| License | **MPL-2.0 ✔** | MIT ✔ | MPL-2.0 ✔ | MIT | GPL-2.0 | GPL-2.0 | GPL-3.0 | MIT | LGPL-3.0 |
| Windows / Linux | ✔ / ✔ (buildbot binaries) | ✔ / ✔ via Mono | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ |
| GB / GBC / GBA | ✔ / ✔ / ✔ | ✔ / ✔ / ✔ | ✔ / ✔ / ✔ | ✔ / ✔ / ✗ | ✔ / ✔ / ✗ | ✔ / ✔ / ✔ | ✗ / ✗ / ✔ | ✔ / ✔ / ✔ | ✔ / ✔ / ✗ |
| Controller | **NFNF-owned** (libretro input callback) | own config | own config | host decides | host decides | own / host | own | own | host |
| Memory access | ✔ memory maps + RAM pointers (tested) | Lua | Lua (0.10+) | libretro | libretro | libretro | debugger | debugger | Python API |
| Save RAM / states | ✔ / ✔ (tested) | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ | ✔ / ✔ |
| Scripting | NFNF Python in-process | Lua in the emulator | Lua in the emulator | — | — | — | — | — | Python |
| Embedding | **in-process library** | ✗ separate .NET app | ✗ separate app (libmgba C API possible) | libretro | libretro | libretro | ✗ | ✗ | Python library |
| Standalone packaging | ~3 MB `.so`/`.dll`, glibc only (checked with `ldd`) | large; Mono on Linux | app + Qt | small | small | small | app | app | wheel + SDL |
| Covers all target games (Gen 1–3) | **yes** | yes | yes | no (no GBA) | no | yes | no | yes | no |

## Why not the others

* **BizHawk**: legally fine, but it's a whole .NET application that needs Mono on Linux. It can't be embedded, keeps its own controller configuration, and exposes game state only through Lua running inside it. Integrating it would keep NFNF as a "launcher of another program", which the Phase 2 brief rules out.
* **The standalone mGBA app**: same problem, a separate program with its own input configuration. Its Lua API (0.10+) has no documented save-state-load callback. Using its **core** directly avoids both problems.
* **SameBoy, Gambatte, PyBoy**: no GBA, so they can't run FireRed. They could be added later as extra GB/GBC libretro cores if accuracy requires it; the host doesn't change.
* **VBA-M, NanoBoyAdvance**: GPL, and they add nothing mGBA lacks for this use. NanoBoyAdvance is GBA-only.
* **SkyEmu**: an application, not an embeddable library.

## Why libretro rather than the raw libmgba C API

* A stable, documented C ABI that is easy to reach from `ctypes`, so there's no compiled glue code to build per platform.
* Prebuilt Windows and Linux binaries from the libretro buildbot (a tagged revision should be pinned for release).
* Standard functions for everything the tracker needs: `retro_get_memory_data`, `SET_MEMORY_MAPS` (full GBA bus map), `retro_serialize`, input callbacks and video callbacks.
* **Swappable cores**: if Gen 1/2 accuracy ever needs SameBoy or Gambatte, only a component entry changes.

## Evidence from the Phase 2 prototype

On this machine and in a **clean Debian 12 container using the frozen build**, with the bundled `mgba_libretro.so` (mGBA `0.11-212-7a12d6d`):

| Check | Result |
|---|---|
| Load the **UPR-randomized FireRed** in-process | ✔ |
| Frame rate (headless, Python host) | **~890–920 fps**, about 15× real time (59.7275 fps), so plenty of headroom for rendering and tracking |
| Video | 240×160 frames in RGB565, converted and saved as PNG. The screenshot shows the randomized FireRed at Professor Oak's intro. |
| Input | Scripted START/A presses moved through the title screen (proves `input_state` routing) |
| Memory | `read_bus(0x080000A0)` → `POKEMON FIREBPRE` through the core's memory map. `SYSTEM_RAM` is 32 KB. |
| Saves | `SAVE_RAM` is 128 KB (FireRed flash), with export/import round trip. Save state is 528 KB, with serialize/unserialize round trip. |
| Lifecycle | init → load → run → deinit → re-init in the same process ✔ (tested) |

## Architecture going forward

```
ControllerManager ──(logical buttons)──► LibretroCore.input ──► mGBA core ◄── randomized ROM (runs/<id>/rom)
                                              │ video/audio                    │ save RAM ↔ runs/<id>/saves/game.sav
                                              ▼                                ▼
                                   SDL2 window + audio (Phase 3)    memory map → NFNF Tracker Engine (Phase 3)
```

* **Owned by NFNF**: pacing (60 Hz using the core's `fps`), input mapping, saves in the run folder, and save states. Save states are *not* offered to the player during IronMON; they are reserved for crash recovery and flagged in integrity.
* **Phase 3 presentation**: SDL2 (zlib license) for the window, audio and hot-plug controllers. Tk stays for the management UI.
* Status in `doctor`: **Integrated backend: PROTOTYPE** (headless) and **Interactive play: PLANNED**. `run new` stops at READY with the reason recorded, rather than pretending a game was launched.
