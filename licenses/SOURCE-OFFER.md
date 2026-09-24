# Source code for bundled GPL / MPL components

NFNF IronMON distributes these components in binary form, unmodified. Their
complete corresponding source code is available from the upstream locations
below, at exactly the versions bundled. On request, the NFNF IronMON distributor
will provide the same source code on a physical medium or by download for at
least three years from distribution (GPL-3.0 §6, GPL-2.0 §3).

| Component | License | Exact source |
|---|---|---|
| Universal Pokémon Randomizer ZX 4.6.1 | GPL-3.0 | https://github.com/Ajarmar/universal-pokemon-randomizer-zx/tree/v4.6.1 |
| Eclipse Temurin 21.0.12.1+1 (OpenJDK) | GPL-2.0 + Classpath Exception | https://github.com/adoptium/jdk21u (tag jdk-21.0.12.1+1, source `1c417fbfc2f7`); build scripts https://github.com/adoptium/temurin-build (e6ba7dec) |
| mGBA libretro core (reports `0.11-212-7a12d6d`) | MPL-2.0 | https://github.com/libretro/mgba (commit 7a12d6d) and https://github.com/mgba-emu/mgba |
| PyInstaller bootloader 6.x | GPL-2.0 + bootloader exception | https://github.com/pyinstaller/pyinstaller |

The reduced Java runtime in portable builds is produced with `jlink` from the
official Temurin JDK of the same version (see `docs/building-linux.md`); `jlink`
copies the per-module legal notices into `runtime/java/<platform>/legal/`.
