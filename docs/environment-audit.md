# Environment Audit

Audit date: 2026-09-24. Machine: `nfnf-server-ubuntu`. Everything outside `~/avacalho/IronMON/` was only read (listings, headers, hashes). Nothing there was modified.

## Host

| Item | Found |
|---|---|
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic, x86_64 |
| CPU | AMD Ryzen 7 2700, 8 cores / 16 threads |
| RAM | 15 GiB, plus 4 GiB swap (swap was 3.6 GiB used during the audit) |
| GPU | NVIDIA GeForce RTX 3060 12 GB, driver 595.91.07 |
| Disk | `/`: 439 GB total, 271 GB free |
| Display | X server on `:0` (used by the Tk UI smoke test) |

## Runtimes and tools

| Tool | Status | Notes |
|---|---|---|
| Python | **3.14.4** (`/usr/bin/python3`) | Includes sqlite3 (SQLite 3.46.1) and tkinter 8.6. **NFNF IronMON uses only these.** |
| pip | 25.1.1 | Not needed. The project has no third-party dependencies. |
| pytest | not installed | The tests use stdlib `unittest`, so pytest can also run them. |
| Node.js / npm | v22.22.1 / 9.2.0 | Not used |
| Java | **not installed** | Needed in Phase 2 for Universal Pokémon Randomizer ZX |
| .NET | not on PATH (`~/.dotnet` exists) | Not needed. BizHawk on Linux uses Mono. |
| Mono | **not installed** | Needed for BizHawk on Linux |
| Rust / Go | not installed | — |
| Lua / LuaJIT | runtime libs only (`liblua5.4-0`, `libluajit-5.1-2`), no interpreter | BizHawk on Linux needs Lua 5.4 libs, which are present |
| git | 2.53.0 | The IronMON folder is **not** a git repo yet |
| sqlite3 CLI | not installed | Not needed (Python's module is used) |
| Docker | 29.1.3 | Not used |
| Flatpak / Snap | 1.16.6 / 2.76.3 | Flatpak has OBS Studio 32.2.2 (not used, OBS is out of scope) |

## Emulators, randomizers, trackers

| Component | Status |
|---|---|
| BizHawk (EmuHawk) | **Not installed** (no binary on PATH, no Flatpak, no package) |
| mGBA | **Not installed** |
| RetroArch | Not installed |
| Universal Pokémon Randomizer ZX (`PokeRandoZX.jar`) | **Not found** |
| Ironmon-Tracker (`Ironmon-Tracker.lua`) | **Not found** |
| Gen 1 / Gen 2 IronMON trackers | Not found |
| PyBoy | Used inside the existing RL projects (`pyboy==2.4.0` / `2.7.0`). NFNF IronMON doesn't use it. |

Result: Phase 1 runs entirely on **mock** emulator, tracker and randomizer adapters. The real adapters detect these tools and report "not found" (run `python3 -m nfnf_ironmon doctor` to see this).

## Existing projects (inspected read-only, not modified)

| Path | What it is |
|---|---|
| `~/avacalho/IronMON/` | **Empty** at start of Phase 1. No existing NFNF IronMON project, so no backup was needed. |
| `~/avacalho/pokemon-ai/` | Pokémon RL work: a clone of `PWhiddy/PokemonRedExperiments` with NFNF additions (`v2/nfnf_*`, `manual_player/`, `nfnf-lab/`), plus `external/pokegym-badge3` and `pufferlib-badge3`. Uses PyBoy, gymnasium, stable-baselines3 and torch. Holds ROM copies and many `.state` files. |
| `~/avacalho/ai-streamer/` | AI streaming project. Holds FireRed/Red/Silver ROM copies (+ `_rom_backup/`), saves and states. |
| `~/avacalho/NFNF-Vision/` | Sprite and animation desktop tool, not Pokémon tooling |
| `~/avacalho/nfnf-server-sharenetwork/` | Shared assets, including a Pokémon Red ROM folder and zip |
| Others (`The-Dojo-Code-Zero`, `footsnipe`, `1`, `2`, `3`, `~/projects*`) | Not Pokémon related |

## User-owned ROM dumps found

Identified from the cartridge header and hashed. No file was modified.

| Game | Header | SHA-1 (matches No-Intro) | SHA-256 | Locations |
|---|---|---|---|---|
| Pokémon FireRed (USA, Europe) (Rev 1) | `POKEMON FIRE` / `BPRE` / v1, header checksum OK, 16 MiB | `dd5945db9b930750cb39d00c84da8571feebf417` | `729041b9…ca4d059` | `~/Downloads/Pokemon_ FireRed Version/`, `ai-streamer/`, `pokemon-ai/PokemonRedExperiments/` |
| Pokémon Silver (USA, Europe) | `POKEMON_SLV` + `AAXE`, CGB 0x80 | `49b163f7e57702bc939d642a18f591de55d92dae` | `72b19085…fc19a8c` | `~/Downloads/Pokemon_ Silver Version/`, `ai-streamer/`, `pokemon-ai/PokemonRedExperiments/` |
| Pokémon Red (USA, Europe) | `POKEMON RED` | `ea9bcae617fdf159b045185467ae58b2e4a48b9a` | `5ca7ba01…af96b7b` | `nfnf-server-sharenetwork/…`, `ai-streamer/`, `pokemon-ai/…`, `external/pokegym-badge3/pokemon_red.gb` |

The copies in each location are identical (same SHA-256). These SHA-1 values are now in the game adapters' `known_dumps` tables, so an imported ROM is labelled with its No-Intro name.

Phase 1 verification used the real FireRed ROM once, in a throwaway scratchpad home. The source file's SHA-256, mtime, size and mode were checked before and after and did not change. The scratchpad copies were then deleted. The project's `games/original/` is still empty.

## What to install for Phase 2+

1. Java 64-bit runtime (Universal Pokémon Randomizer ZX needs Java 8 or newer, check its wiki for the current requirement)
2. `PokeRandoZX.jar` (UPR ZX v4.6.x) plus an IronMON `.rnqs` settings file
3. BizHawk 2.11.x with Mono (Linux), or mGBA 0.10.x
4. Ironmon-Tracker (latest release, v9.3.x at time of research)
