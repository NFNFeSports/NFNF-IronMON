# Universal Pokémon Randomizer ZX Integration

Everything here was checked against the **v4.6.1 source** (`CliRandomizer.java`, `NewRandomizerGUI.main`, `Settings.java`, `Randomizer.java`, `RandomSource.java`) and by running the real jar with the bundled JRE. Nothing in this document is guessed.

## Version and distribution

| Item | Value |
|---|---|
| Supported version | **4.6.1** (latest release, 2024-11-23), settings version **322** |
| Artifact | `PokeRandoZX-v4_6_1.zip` → `PokeRandoZX.jar` (+ launchers, README), sha256 pinned in `components.json` |
| License | **GPL-3.0**. Redistribution is allowed with the license text and source availability. NFNF runs it as a separate process and never links to it. |
| Java | Bundled Temurin JRE 21. `Main-Class: com.dabomstew.pkrandom.newgui.NewRandomizerGUI` |
| FireRed | Supported (Gen 3 handler). The user's FireRed (USA, Europe) Rev 1 randomizes in about 2 s. |

## Command line (verified)

```
java -Djava.awt.headless=true -Xmx4096M -jar PokeRandoZX.jar cli \
     -s <settings.rnqs> -i <source ROM> -o <output ROM> -l
```

* `main()` checks whether the first argument is `cli`. If it is, it calls `CliRandomizer.invoke(rest)` and then `System.exit(code)`. No Swing window is created on this path, and the tool runs **headless**. `-Djava.awt.headless=true` is added as a safeguard; tested.
* The accepted flags are exactly `-i -o -s -d -u -l --help`. `-d` (save as a LayeredFS directory) and `-u` (3DS update file) only apply to 3DS games. **There is no seed flag.**
* **Exit codes**: `0` only after `Randomized successfully!` is printed on stdout. `1` for a missing argument, an unreadable settings file or source ROM, an unwritable output folder, or any randomization failure. Errors go to stderr as `ERROR: …`, warnings as `WARNING: …` (for example, "settings file was created by an older randomizer version").
* **Output**: the output path goes through `FileFunctions.fixFilename`, which may add the game's default extension. NFNF always passes `randomized.gba`, which is already correct for FireRed. UPR refuses to overwrite a 3DS source file in place; NFNF additionally refuses to use an existing output file or the source's own folder.
* **Log** (`-l`): written to `<output>.log`, UTF-8 **with BOM**. The header lines are `Randomizer Version: 4.6.1`, `Random Seed: <long>` and `Settings String: 322<base64>`; the log ends with `Randomization of <ROM name> completed.` NFNF moves it to `runs/<id>/logs/upr-zx.log`.
* **Side effects**: none were observed. The jar folder was unchanged and the working directory stayed empty after a run.

## Seeds and determinism

* `Randomizer.randomize(filename, log)` calls `RandomSource.pickSeed()`, which takes 6 bytes from `SecureRandom`: a random **48-bit seed** every time.
* A seeded overload `randomize(filename, log, seed)` exists in code, but **the CLI never exposes it**.
* So NFNF records **`deterministic = false`** for UPR runs and stores, separately:
  * `requested_seed`: NFNF's bookkeeping seed (a run label; fed to deterministic adapters only)
  * `actual_randomizer_seed`: parsed from `Random Seed:` in UPR's log (for example `106911130047473` for RUN-000001)
  * `source_rom_sha256`, `settings_sha256` (which includes the `.rnqs` file hash), and `generated_rom_sha256`
* **The generated ROM's SHA-256 is the authoritative identity of the game.** The generated ROM stays in the run folder, so the game can always be replayed, even though it can't be regenerated from the seed alone.
* Possible later option: a small separate helper (GPL-3.0, its own process) that calls the seeded overload. That would make `requested_seed` reproducible. It's not built yet, because running the same seed twice is not part of the IronMON workflow.

## Settings files (`.rnqs`), verified format

```
int32 BE  version            (322 = 4.6.1)
int32 BE  length N
N bytes   UTF-8 Base64 settings string
```

Decoded bytes: offset 51 holds the length of an ASCII ROM name that follows it. The last 8 bytes are a big-endian CRC32 of everything before them, followed by a custom-names checksum. UPR rejects files newer than itself, and files whose top version byte is 1–172 ("too old"). Older supported versions are upgraded on load with a `WARNING`.

`nfnf_ironmon/randomizers/rnqs.py` implements exactly this check (version, length, Base64, CRC, ROM name) in pure Python, so a broken or edited profile is rejected **before** Java starts.

## FireRed IronMON profile

| | |
|---|---|
| File | `randomizer-profiles/firered-ironmon.rnqs` (the location the brief expects) |
| Source | `besteon/Ironmon-Tracker` @ `41e67112…`, `ironmon_tracker/RandomizerSettings/FRLG Standard.rnqs`, copied **byte-for-byte**, MIT. The file was last changed on 2022-08-24. |
| sha256 | `f1fa42624c80e7fccc50acd3fc8594c360a5bb7b8842b00da35b273a88ba6210` |
| Contents | settings version **320**, ROM name `Fire Red (U) 1.1`, CRC valid |
| Behaviour | UPR 4.6.1 loads it with the "older version" warning (recorded in the run's `randomizer.json` manifest) |
| Profile JSON | `randomizer-profiles/firered-ironmon.json` (`randomizer: upr-zx`, `settings_file: …rnqs`) |

The profile is treated as **data**: the randomizer logic is untouched, and any other `.rnqs` works by adding a profile JSON. If the file is missing, `doctor` shows `Profile firered-ironmon  MISSING`, and `run new` fails with "Invalid randomizer settings …: Settings file not found: …" before a run folder is created.

> **Open question**: the official settings gist (UTDZac) targets UPR ≥ 4.6.0, while the tracker's bundled file is version 320 (the 4.5.x era). Both are published by the IronMON community. If the community's current FRLG Standard string differs, export it from UPR 4.6.1 as a new `.rnqs` and drop it in; NFNF records its hash in every run either way.

## Pipeline as implemented

```
games/original/<any name>.gba   (read-only, found by header)
  → identify (FireRed, Rev 1, No-Intro match) → SHA-256
  → create RUN-xxxxxx (CREATED → PREPARING)
  → validate .rnqs (pure Python)
  → java … cli -s … -i <original> -o runs/<id>/rom/randomized.gba -l   (timeout 300 s)
  → check exit code 0 + "Randomized successfully!" + output exists
  → re-hash the original (must be unchanged)
  → identify the output (must still be FireRed) and hash it (must differ from the source)
  → move the log to logs/, snapshot the .rnqs to settings.rnqs, write settings.json / randomizer.json
  → integrity baseline → READY
```

Any failure leaves the run **ABANDONED**, with the reason in `end_reason` and in `logs/setup-error.log`. UPR's stdout and stderr are kept in `logs/upr-zx-console.log`.
