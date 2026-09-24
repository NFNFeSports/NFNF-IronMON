# Development Roadmap

Revised after Phase 2 for the **standalone** goal: one application containing the randomizer, emulator, tracker, rules, run manager and career, with controller input, offline, and portable on Windows and Linux. Each phase ends with tests and a report, and none starts automatically.

## Phase 1: foundation ✅
Architecture, adapters, run manager, rules, integrity, SQLite, CLI/UI/API, 86 tests.

## Phase 2: standalone architecture + real FireRed randomization ✅
Bundled JRE + UPR ZX 4.6.1 (real randomization, tested), official FRLG `.rnqs`, in-place ROM discovery, emulator selection (mGBA core via libretro, headless prototype), controller architecture (Linux tested, XInput implemented), career, doctor, a portable Linux build tested in a clean container. See `phase-2-status.md`.

## Phase 3: playable FireRed inside NFNF
* **Game window**: SDL2 (bundled; zlib license) video with integer scaling, audio queue at the core's sample rate, 59.7275 Hz pacing, pause/quit.
* **Live input**: `ControllerManager.poll_logical()` feeds `LibretroCore.input` every frame. Add the SDL2 GameController backend (hot-plug, generic HID) and a keyboard mapping.
* **Saves**: load `runs/<id>/saves/game.sav` at start, flush save RAM on in-game save and on exit, fresh saves for new runs. Save states are recovery-only and recorded in integrity.
* `IntegratedEmulatorAdapter.interactive = True`, so `run new` goes READY → ACTIVE for real and the attempt counter starts counting.
* **Native tracker engine, first slice**: FireRed v1.1 address table (from Ironmon-Tracker `GameAddresses`, MIT, cross-checked with pret symbols) and readers for party, battle state, map, badges and play time, turned into events.
* Packaging: `jlink` a minimal JRE, first Windows build on a Windows host or CI.

## Phase 4: automatic encounter, death and rule detection
* Complete the event set: `WILD_ENCOUNTER`, `TRAINER_BATTLE`, `POKEMON_FAINTED {is_starter}`, `POKECENTER_HEAL`, `ITEM_USED {in_battle, category}`, `BADGE_ACQUIRED`, `AREA_CHANGED`, with `play_time_frames` on each.
* Declare real tracker capabilities, so integrity can reach VERIFIED.
* Check the rulesets against the official IronMON rules. Add a player confirmation flow for MANUAL and FLAG_FOR_REVIEW items.

## Phase 5: automatic failed-run → new-run loop
* On `RUN_FAILED`: stop the core, archive, randomize a new ROM, fresh save, reload the core with no program restart and no user action. Optionally pre-generate the next ROM in the background. Show the career on the failure screen.

## Phase 6: Pokémon Red
* Promote `RedGameAdapter`. Add a UPR profile (a Gen 1 `.rnqs`), Gen 1 address tables and readers (the mGBA core already runs GB), and Gen 1 rules.

## Phase 7: Pokémon Silver
* Promote `SilverGameAdapter`. Community Gen 2 tracker data covers Crystal only, so Silver needs its own addresses (pret/pokegold symbols).

## Phase 8: more Gen 1/2/3 games
Blue, Yellow, Gold, Crystal, Ruby, Sapphire, Emerald, LeafGreen. Each is an adapter, a profile, an address table and tests.

## Phase 9: Windows portable build
PyInstaller onedir on Windows, the Windows JRE and core from `components.json`, XInput tested on real hardware, and a copy-to-another-PC test.

## Phase 10: Linux portable build (release quality)
Already TESTED as a prototype in Phase 2. Still to do: pin a tagged core, full license texts and source offers, a test on a second distribution, optional AppImage.

## Phase 11: advanced integrity verification
Signed run manifests, a save-file hash chain, save-state usage records (NFNF owns the API), and a run export/verify tool.

## Phase 12: polished unified dashboard
Live run view, history and statistics from `encounters`/`deaths`/`pokemon`, the career, and a rule review screen.

## Recommended next step
**Phase 3, starting with the game window**: an SDL2 presentation loop around `LibretroCore` with live `ControllerManager` input and save RAM in `runs/<id>/saves/`. This turns the tested headless prototype into the first actually playable run.
