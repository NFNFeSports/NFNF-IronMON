# Component Research

Researched 2026-09-24 from project repositories and docs. NFNF IronMON **does not copy code** from any of these projects. Each one is used as an **external program** through an adapter, which keeps the license question simple: the user installs each tool, and NFNF IronMON only launches it or reads its files.

## Summary

| Component | License | Platforms | Games | Automation | How NFNF IronMON uses it |
|---|---|---|---|---|---|
| Universal Pokémon Randomizer ZX | GPL-3.0 | Any OS with Java | Gen 1–7 (RBY, GSC, RSE, FRLG, DPPt, HGSS, BW/B2W2, XY, ORAS, SM/USUM) | **CLI mode** | `RandomizerAdapter` (`upr-zx`) runs it as a subprocess |
| Ironmon-Tracker (besteon) | MIT | Runs inside BizHawk (Win/Linux) or mGBA (Win/Mac/Linux) | Ruby, Sapphire, Emerald, FireRed, LeafGreen (English; FireRed also ES/FR/IT/DE) | Lua script inside the emulator. Has its own "New Run" (A+B+Start). | `TrackerAdapter` (`ironmon-tracker`). Phase 1 passes the script path; the event bridge is Phase 3. |
| BizHawk | MIT | Windows 10/11, Linux (Mono + Lua 5.4) | GB/GBC/GBA and many more | CLI `--lua=<script>` plus ROM path; Lua API; `comm` socket/HTTP functions (`--socket_ip/--socket_port`, `--url_get/--url_post`) | `EmulatorAdapter` (`bizhawk`) |
| mGBA | MPL-2.0 | Windows, macOS, Linux | GB/GBC/GBA | Lua scripting since 0.10: memory reads, callbacks (`frame`, `reset`, `keysRead`, `savedataUpdated`, …), TCP sockets; GDB stub | `EmulatorAdapter` (`mgba`) |
| Ironmon-gen-tracker (mollo010) | LICENSE file present, type not confirmed | BizHawk 2.8/2.9, mGBA | Red, Blue, Yellow (US/EU) | Lua, fork of the Gen 3 tracker, marked "WIP" | Candidate for Phase 6 |
| Ironmon-gen-2-tracker (seadogstingray) | LICENSE file present, type not confirmed | BizHawk | Crystal (US) only | Lua, marked "WIP" | Candidate for Phase 7. **Silver is not covered.** |
| KaizoCore | unverified | unverified | Gen 1–5 (per its release notes) | IronMON tracker features | Watch list |
| Official IronMON settings (UTDZac gist) | Community document | — | Settings strings for Gen 1–7 games | Settings strings for UPR ZX ≥ v4.6.0, in Standard, Ultimate, Kaizo, Survival and Doubles variants | Users paste a string into UPR ZX and export a `.rnqs`, which the randomizer profile then points at |

Latest versions seen: UPR ZX **v4.6.1**, Ironmon-Tracker **v9.3.1**, BizHawk **2.11.1** (1 May 2026), mGBA **0.10.5** (9 Mar 2025).

> **Phase 2 corrections (2026-09-24)**: (1) Ironmon-Tracker is now at **v9.4.0**, and it *does* have a `network/` module: a Streamer.bot bridge (Text / HTTP / WebSockets) built on BizHawk's `comm` library. It is not a general event feed. (2) The Phase 2 direction no longer runs BizHawk, mGBA or the tracker as external programs: see `standalone-dependency-audit.md`, `emulator-selection.md` and `tracker-integration.md`.

## Details that shape the architecture

### 1. UPR ZX CLI can't be given a seed

Usage, taken from the source (`CliRandomizer.java`):

```
java [-Xmx4096M] -jar PokeRandoZX.jar cli -s <settings.rnqs> -i <source ROM> -o <new ROM> [-d] [-u <3DS update>] [-l]
```

There is **no seed argument**. UPR picks its seed internally. So the requirement that the same ROM, settings and seed always give the same output can't be met with stock UPR through its CLI. Consequences:

* Each randomizer adapter declares `deterministic: true/false`. The mock is deterministic; `upr-zx` isn't.
* For every run we store the **generated ROM's SHA-256** plus the settings hash and source ROM hash. The generated ROM stays in the run folder, so a run can be reproduced from what was stored, even where it can't be regenerated.
* The NFNF seed is still recorded, as the run's label and the input for deterministic adapters.
* Phase 2 options: (a) read UPR's seed from its `-l` log, if the log contains it (not verified yet); (b) a separate helper program that calls UPR's randomization with a fixed seed. Because UPR is GPL-3.0, such a helper would itself be GPL-3.0 and would ship as a separate tool that NFNF IronMON calls as a subprocess.

### 2. Ironmon-Tracker already automates new runs, but only inside the emulator

Its "New Runs" feature (A+B+Start) either loads the next of a batch of pre-made ROMs, or runs UPR ZX itself (needs Java 64-bit, `PokeRandoZX.jar` and a `.rnqs`). Its output goes to files named "Auto Randomized" and "Previous Attempt". It exposes **no external API**: no network or file-based feed of events or state. So:

* NFNF IronMON can't observe gameplay through the stock tracker. The adapter reports **no capabilities**, and integrity verification returns **UNKNOWN** for gameplay rather than claiming VERIFIED.
* Phase 3 needs a bridge. Options: a small tracker extension (the tracker supports extensions) that forwards events over mGBA/BizHawk sockets or `comm.httpPost` to NFNF IronMON's local API (`POST /api/runs/<id>/events` already exists), or reading memory directly from our own Lua script.
* We must not let NFNF's pipeline and the tracker's own New Run feature both create runs. In Phase 5, NFNF IronMON should own seed and ROM generation, and the tracker's quickload should be disabled or pointed at NFNF's output.

### 3. Emulators

* **BizHawk**: on Linux you launch `EmuHawkMono.sh` and must pass absolute paths. `--lua=` loads the tracker at start, which is what the adapter builds. External communication goes through the `comm` Lua library (socket server, HTTP GET/POST).
* **mGBA**: its Lua API has a `reset` callback and `savedataUpdated`, but **no documented callback for loading a save state**. Detecting state loads will need an indirect signal (a jump in the frame counter or in-game play time), which the integrity system already treats as SUSPICIOUS "rollback". I didn't find documentation for loading a script from the command line, so it's opt-in through config (`emulators.mgba.script_flag`).

### 4. Gen 1 and Gen 2 coverage is incomplete

The community Gen 1 tracker covers RBY. The Gen 2 tracker covers **Crystal only**. Pokémon Silver (Phase 7) will need a tracker adapter written from scratch, or an extended fork, license permitting. Both forks are marked WIP.

## License compatibility

| Project | License | What we may do |
|---|---|---|
| UPR ZX | GPL-3.0 | Run it as a separate program (allowed). Don't copy or link its code into NFNF IronMON unless NFNF IronMON is also GPL-3.0. |
| Ironmon-Tracker | MIT | Integrating, or even copying with attribution, would be allowed. We still integrate it externally so users can update it on its own schedule. |
| BizHawk | MIT | Launch it; the user supplies the install |
| mGBA | MPL-2.0 | Launch it; the user supplies the install |
| Gen 1 / 2 forks | License type to be checked before any reuse | Integrate externally only |

## Sources

* https://github.com/Ajarmar/universal-pokemon-randomizer-zx (README, releases, wiki "CLI Randomizer", `src/.../cli/CliRandomizer.java`)
* https://github.com/besteon/Ironmon-Tracker (README, releases v9.3.1, wiki "New Runs Setup")
* https://github.com/TASEmulators/BizHawk and https://tasvideos.org/BizHawk/ReleaseHistory
* https://tasvideos.org/Bizhawk/LuaFunctions and BizHawk PR #1080 / #1505 (comm library, socket settings)
* https://github.com/mgba-emu/mgba (README, releases) and https://mgba.io/docs/scripting.html
* https://github.com/mollo010/Ironmon-gen-tracker
* https://github.com/seadogstingray/Ironmon-gen-2-tracker
* https://gist.github.com/UTDZac/a147c497424dfbd537d8c4b0c22b5621 (official IronMON randomizer settings)
