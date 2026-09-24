# Third-party components and licenses

NFNF IronMON bundles the components below **unmodified**. Full license texts are
in this folder; each component's own notices ship with it as well.

| Component | Version | License | Text | Where in the distribution |
|---|---|---|---|---|
| Universal Pokémon Randomizer ZX | 4.6.1 | GPL-3.0 | `GPL-3.0.txt` | `randomizer/upr-zx/` (run as a separate process) |
| Eclipse Temurin (OpenJDK) runtime | 21.0.12.1+1 (jlink-reduced in builds) | GPL-2.0 with Classpath Exception | `OpenJDK-GPL-2.0-with-Classpath-Exception.txt` + `runtime/java/*/legal/` | `runtime/java/<platform>/` |
| mGBA (libretro core) | 0.11-dev nightly, pinned by sha256 | MPL-2.0 | `MPL-2.0.txt` | `emulator/cores/<platform>/` |
| SDL2 | 2.32.10 | zlib | `SDL2-Zlib.txt` | `runtime/sdl2/<platform>/` |
| Python runtime + standard library | 3.12 (frozen) | PSF License | `Python-PSF.txt` | `app/_internal/` |
| Tcl/Tk (launcher UI) | 8.6 | Tcl/Tk license (BSD-style) | `Tk-license.terms.txt` | `app/_internal/` |
| PyInstaller bootloader | 6.x | GPL-2.0 with bootloader exception (output may be distributed under any license) | `PyInstaller-COPYING.txt`, `GPL-2.0.txt` | `app/nfnf-ironmon` |
| Ironmon-Tracker data (FRLG Standard `.rnqs`, FireRed address tables used to cross-check) | commit 41e67112 | MIT, © 2022-2023 Besteon | `Ironmon-Tracker-MIT.txt` | `randomizer-profiles/firered-ironmon.rnqs`, `nfnf_ironmon/tracker/data/*.json` |
| font8x8 (HUD font) | commit 8e279d2d | Public domain (Daniel Hepper; IBM VGA fonts via Marcel Sondaar) | — | `nfnf_ironmon/frontend/font8x8.py` |

## Data sources (attribution)

* **Memory addresses** in `nfnf_ironmon/tracker/data/` were extracted from the symbol files published by the
  [pret](https://github.com/pret) decompilation projects (pokefirered, pokered, pokegold). They are facts
  about the retail games; the FRLG tables were cross-checked against Ironmon-Tracker (MIT).
* **IronMON randomizer settings** for Gen 1/Gen 2 were converted from the community document
  "Ironmon Randomizer Settings" by UTDZac (gist a147c497…, revision ebe53603, 2026-07-05).
* **Map names** for Gen 1/2 come from pret `constants/map_constants.asm`; species and Gen 3 area names are
  read at runtime from the user's own ROM.

## Not included

No Pokémon ROMs, saves or game data are included or downloaded. The user supplies their own dumps.

## NFNF IronMON itself

The owner has not chosen a license for NFNF IronMON's own source code yet.
See `SOURCE-OFFER.md` for the GPL/MPL source availability of bundled components.
