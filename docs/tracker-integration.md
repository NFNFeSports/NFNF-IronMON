# Tracker Integration

## Target architecture

```
Integrated emulator (libretro mGBA core)
      │  read_bus / memory maps (tested in Phase 2)
      ▼
Game state readers (per game adapter: FireRed addresses, party/battle structures)
      ▼
NFNF Tracker Engine  — turns state *changes* into events
      ▼
Event bus → Rules Engine → VALID / WARNING / VIOLATION / RUN_FAILED
      ▼             ↘ Integrity (play time, resets, saves)   ↘ Projections (pokemon / encounters / deaths) → Career
```

This replaces the Phase 1 path (emulator → external tracker → NFNF). The user never installs a tracker.

## Can the community Ironmon-Tracker be used?

| Question | Finding |
|---|---|
| License | **MIT** (`LICENSE.txt`). Bundling, modifying and reusing code or data are allowed, with the copyright notice kept. |
| Where it runs | Only inside **BizHawk** (≥ 2.8) or **mGBA** (≥ 0.10) as a Lua script, using their `memory.*`, `emu.*`, `gui.*`, `event.*` and `comm.*` APIs |
| Can NFNF embed it? | **Not directly.** NFNF hosts the mGBA *libretro core*, which has no Lua host. Running the tracker would mean re-implementing the BizHawk/mGBA Lua API surface (drawing, forms, events, comm) inside NFNF: large, fragile, and tied to another program's UI. |
| External API | *Correction to Phase 1*: v9 has a `network/` module, but it is a **Streamer.bot** bridge (Text-file, HTTP, or WebSockets connection types) that depends on BizHawk's `comm` library; WebSockets need an unreleased BizHawk build. It's not a general event feed, and it's useless without BizHawk. |
| Memory layer | `Memory.lua` is a thin wrapper: `read8/16/32` → `memory.read_u*_le`. NFNF's `LibretroCore.read_bus()` provides the same primitive. |
| Address data | `ironmon_tracker/GameAddresses/*.json`, one file per game and version (for example `Pokemon FireRed v1.1.json`, about 10 KB). Its comments say the addresses come from the **pret/pokefirered** symbol files (`pokefirered_rev1.sym` for v1.1). |
| Game data | `data/PokemonData.lua`, `data/MoveData.lua` (base stats, moves, …) |

## Decision

**Reimplement the tracker natively in NFNF (Phase 3), reusing the community tracker's MIT data with attribution:**

1. **Adapt, don't embed.** Port the FireRed v1.1 address table (`GameAddresses/Pokemon FireRed v1.1.json`) into the FireRed game adapter, and cross-check it against pret's `pokefirered_rev1.sym`. Keep the MIT notice in `THIRD-PARTY-NOTICES.md`.
2. **State readers** in the game adapter (`FireRedGameAdapter.read_state(core)`): party (species, level, HP, status), battle flags, opponent, map ID, badges, in-game play time, items. Every one is a pure function of bytes read through `read_bus`, so each is **unit-testable from recorded memory snapshots** without a ROM.
3. **Tracker Engine** (`trackers/engine.py`): runs once per N frames, diffs successive states, and emits `WILD_ENCOUNTER`, `TRAINER_BATTLE`, `BATTLE_STARTED/ENDED`, `POKEMON_FAINTED {is_starter}`, `PARTY_CHANGED`, `BADGE_ACQUIRED`, `AREA_CHANGED`, `POKECENTER_HEAL`, `ITEM_USED {in_battle, category}`, with `play_time_frames` on each event.
4. **Capabilities** are declared honestly per game. Integrity becomes VERIFIED only when resets, save loads and play time are really observed. Save-state loads can't be hidden from NFNF, because NFNF owns the save-state API, so they are recorded directly.
5. The rules engine stays unchanged. Rules are still configured in `rules/*.json`.

The existing `IronmonTrackerAdapter` (external tracker, via BizHawk/mGBA) stays as a **development reference**: it's useful for cross-checking NFNF's detected events against the community tracker while developing Phase 3. It is never required.

## Phase 2 status

| Item | Status |
|---|---|
| Memory access from the integrated core | **TESTED** (ROM header via the bus map, 32 KB system RAM, 128 KB save RAM) |
| `none` tracker (honest: observes nothing, so integrity is UNKNOWN) | **IMPLEMENTED + TESTED**, the default |
| Mock tracker (event injection for tests) | TESTED |
| External Ironmon-Tracker adapter | IMPLEMENTED (reference only), not installed |
| Native NFNF tracker engine, FireRed state readers | **PLANNED** (Phase 3). Not started, per the brief. |
