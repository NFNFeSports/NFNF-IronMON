"""THE central NFNF IronMON workflow, on the user's real games (skipped when absent).

Application → game + rules → new run → UPR randomization → fresh save → integrated
emulator (headless SDL) → rendering → scripted input → native tracker reads the game
→ starter + encounter + party detected → Pokémon faints → rules → RUN FAILED →
attempt counter → next run prepared automatically → new ROM, new save → RUN ACTIVE.

Scripted input comes from nfnf_ironmon.autoplay (recorded paths, seed independent).
The faint is produced by writing HP=0 into real game memory (POKE_PARTY_HP), which
keeps the test deterministic; real faints were validated manually (see docs/tracker.md).
The user's originals are fingerprinted before and after: nothing may change.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import write_test_config

from nfnf_ironmon.app import Application
from nfnf_ironmon.autoplay import FIRERED_TO_ROUTE1_ENCOUNTER, INTRO_TO_STARTER, SessionScript
from nfnf_ironmon.events import EventType
from nfnf_ironmon.games import GameRegistry
from nfnf_ironmon.paths import BUNDLE_ROOT
from nfnf_ironmon.runs import RunState

ORIGINALS = BUNDLE_ROOT / "games" / "original"


def fingerprint(directory: Path) -> dict:
    out = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(directory).as_posix()] = (
                hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def has_game(game_id: str) -> bool:
    reg = GameRegistry()
    return ORIGINALS.is_dir() and any(
        (i := reg.identify_file(p)) and i.game_id == game_id for p in ORIGINALS.iterdir() if p.is_file())


class RealGameplayBase(unittest.TestCase):
    game = ""

    def setUp(self):
        if not has_game(self.game):
            self.skipTest(f"no {self.game} dump in games/original/")
        try:
            from nfnf_ironmon.frontend.session import GameSession, SessionOptions  # noqa: F401
        except Exception as exc:   # noqa: BLE001
            self.skipTest(f"SDL2 unavailable: {exc}")
        self.before = fingerprint(ORIGINALS)
        self.tmp = Path(tempfile.mkdtemp(prefix="nfnf-e2e-"))
        self.home = self.tmp / "home"
        write_test_config(self.home, {"default_randomizer_profile": "auto", "emulator": "nfnf-libretro",
                                      "tracker": "nfnf"})
        self.app = Application(self.home)
        ok, why = self.app.randomizers.get("upr-zx").is_available()
        if not ok or not self.app.emulators["nfnf-libretro"].core_path():
            self.app.close()
            self.skipTest(f"bundled components missing: {why}")
        self.app.roms.scan(ORIGINALS)

    def tearDown(self):
        self.app.close()
        self.assertEqual(fingerprint(ORIGINALS), self.before, "an original ROM changed!")
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def play(self, first_script, second_frames=120):
        from nfnf_ironmon.frontend.session import GameSession, SessionOptions
        captures = {}

        def stop(sess):
            return len(sess.result.runs_played) >= 2 and sess.run is not None and sess.frames >= second_frames

        def on_frame(sess, frame):
            if len(sess.result.runs_played) == 1 and frame == 3000:
                sess.request_window_capture()
            if sess.last_capture is not None and "run1" not in captures:
                captures["run1"] = sess.last_capture

        script = SessionScript([first_script, ["WAIT30"]], runs=2, stop=stop, on_frame=on_frame)
        sess = GameSession(self.app, SessionOptions(headless=True, unthrottled=True, script=script,
                                                    max_frames=60000))
        result = sess.play(new_run={"game_id": self.game, "ruleset_id": "standard-ironmon"})
        return sess, result, captures

    def assert_central_loop(self, sess, result, captures, expect_encounter: bool):
        self.assertEqual(len(result.runs_played), 2, result)
        first, second = (self.app.runs.get(r) for r in result.runs_played)
        types = [e["type"] for e in self.app.runs.events(first.id)]
        payloads = {t: [e["payload"] for e in self.app.runs.events(first.id) if e["type"] == t] for t in set(types)}
        # randomization + emulator + tracker
        self.assertIn(EventType.ROM_RANDOMIZED, types)
        self.assertIn(EventType.EMULATOR_STARTED, types)
        self.assertIn("STARTER_OBTAINED", types)
        self.assertIn(EventType.PARTY_CHANGED, types)
        if expect_encounter:
            enc = payloads[EventType.WILD_ENCOUNTER][0]
            self.assertTrue(enc["first_in_area"])
            self.assertTrue(enc["species"])
            rows = self.app.db.query("SELECT area, first_in_area FROM encounters WHERE run_id=? "
                                     "AND encounter_type='wild'", (first.id,))
            self.assertEqual(rows[0]["first_in_area"], 1)
        faint = payloads[EventType.POKEMON_FAINTED][0]
        self.assertTrue(faint["is_starter"])
        self.assertEqual(payloads[EventType.RULE_VIOLATION][0]["outcome"], "RUN_FAILED")
        # failure → archive → next run
        self.assertEqual((first.status, first.archived), (RunState.FAILED, True))
        self.assertEqual(first.row["end_reason"], "rule:starter-faint-ends-run")
        self.assertEqual(second.status, RunState.ACTIVE)
        self.assertEqual((first.row["attempt_number"], second.row["attempt_number"]), (1, 2))
        self.assertEqual(second.row["previous_run_id"], first.id)
        self.assertNotEqual(first.row["generated_rom_sha256"], second.row["generated_rom_sha256"])
        self.assertEqual(first.row["source_rom_sha256"], second.row["source_rom_sha256"])
        # fresh save: run 2 never imports another run's save or state (GB/GBC games may
        # write their own SRAM at boot, so the file itself can exist)
        second_types = [e["type"] for e in self.app.runs.events(second.id)]
        self.assertNotIn(EventType.SESSION_RESUMED, second_types)
        self.assertNotIn(EventType.STATE_RESTORED, second_types)
        self.assertIn(EventType.EMULATOR_STARTED, [e["type"] for e in self.app.runs.events(second.id)])
        # rendering happened and the window was not blank
        self.assertGreater(sess.rendered_frames, 1000)
        self.assertTrue(captures.get("run1") and len(set(captures["run1"])) > 4)
        # generated ROMs only inside run folders
        for run in (first, second):
            roms = list(run.path.rglob("*.gb*"))
            self.assertTrue(roms and all(p.is_relative_to(self.home / "runs") for p in roms))
        career = self.app.career()
        self.assertEqual((career.attempts, career.failed, career.active_attempt), (2, 1, 2))


class FireRedCentralWorkflow(RealGameplayBase):
    game = "firered"

    def test_central_workflow(self):
        script = INTRO_TO_STARTER["firered"] + FIRERED_TO_ROUTE1_ENCOUNTER + [
            "WAIT120", "POKE_PARTY_HP:0", "A*10 WAIT200"]
        self.assert_central_loop(*self.play(script), expect_encounter=True)


class RedCentralWorkflow(RealGameplayBase):
    game = "red"

    def test_central_workflow(self):
        script = INTRO_TO_STARTER["red"] + ["WAIT60", "POKE_PARTY_HP:0", "WAIT200"]
        self.assert_central_loop(*self.play(script), expect_encounter=False)


class SilverCentralWorkflow(RealGameplayBase):
    game = "silver"

    def test_central_workflow(self):
        script = INTRO_TO_STARTER["silver"] + ["WAIT60", "POKE_PARTY_HP:0", "WAIT200"]
        self.assert_central_loop(*self.play(script), expect_encounter=False)


if __name__ == "__main__":
    unittest.main()
