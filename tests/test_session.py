"""Game window session (headless SDL: dummy video/audio) with a synthetic ROM + mock randomizer.

Covers the central automatic loop without any real ROM: ACTIVE → tracker event →
rules → RUN FAILED → archive → next run prepared automatically → ACTIVE (#2).
"""

import json
import os
import unittest

from helpers import AppTestCase, write_test_config

from nfnf_ironmon.app import Application
from nfnf_ironmon.controllers import InputMapping, PadState
from nfnf_ironmon.emulators.libretro import MEMORY_SAVE_RAM
from nfnf_ironmon.events import EventType
from nfnf_ironmon.integrity import IntegrityStatus
from nfnf_ironmon.recovery import find_interrupted, is_interrupted
from nfnf_ironmon.runs import RunState

try:
    from nfnf_ironmon.frontend.session import GameSession, SessionOptions
    from nfnf_ironmon.frontend import sdl2 as S
    S.load_sdl(None)
    HAVE_SDL = True
except Exception:  # noqa: BLE001
    HAVE_SDL = False


def core_available(app) -> bool:
    return bool(app.emulators["nfnf-libretro"].core_path())


@unittest.skipUnless(HAVE_SDL, "SDL2 not available")
class SessionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.close()
        write_test_config(self.home, {"emulator": "nfnf-libretro", "tracker": "nfnf"})
        self.app = Application(self.home, strict_events=False)
        if not core_available(self.app):
            self.skipTest("mGBA core not fetched")
        self.import_firered()

    def session(self, **kw):
        kw.setdefault("headless", True)
        kw.setdefault("unthrottled", True)
        return GameSession(self.app, SessionOptions(**kw))

    def test_automatic_fail_and_next_run(self):
        seen = {"failed_screen": 0}

        def script(sess, frame):
            if frame == -1:
                seen["failed_screen"] += 1
                return {"NEW_RUN"}
            if frame == 40 and len(sess.result.runs_played) == 1:
                sess.worker.submit_event(EventType.POKEMON_FAINTED,
                                         {"species": "GENGAR", "is_starter": True, "level": 9})
            if frame == 50:
                sess.request_window_capture()
            return None

        sess = self.session(max_frames=260, script=script)
        result = sess.play(new_run={"game_id": "firered"})
        self.assertEqual(len(result.runs_played), 2, result)
        first, second = (self.app.runs.get(r) for r in result.runs_played)
        self.assertEqual((first.status, first.archived), (RunState.FAILED, True))
        self.assertEqual(first.row["end_reason"], "rule:starter-faint-ends-run")
        self.assertEqual(second.status, RunState.ACTIVE)
        self.assertEqual((first.row["attempt_number"], second.row["attempt_number"]), (1, 2))
        self.assertEqual(second.row["previous_run_id"], first.id)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(seen["failed_screen"], 1)
        types = [e["type"] for e in self.app.runs.events(first.id)]
        for t in (EventType.EMULATOR_STARTED, EventType.ROM_LOADED, EventType.TRACKER_ATTACHED,
                  EventType.POKEMON_FAINTED, EventType.RULE_VIOLATION, EventType.RUN_ENDED, EventType.RUN_ARCHIVED):
            self.assertIn(t, types)
        loaded = [e for e in self.app.runs.events(first.id) if e["type"] == EventType.ROM_LOADED][0]
        self.assertEqual(loaded["payload"]["rom_sha256"], first.row["generated_rom_sha256"])
        career = self.app.career()
        self.assertEqual((career.attempts, career.failed, career.active_attempt), (2, 1, 2))
        # rendering: frames were presented and the window shows the run panel (non-black pixels)
        self.assertGreater(sess.rendered_frames, 100)
        self.assertTrue(sess.last_capture and any(sess.last_capture))

    def test_audio_video_and_panel(self):
        seen = {}

        def script(sess, frame):
            if frame == 20:
                seen.update(available=sess.audio.available, volume=sess.audio.volume,
                            queued=sess.audio.queued_seconds() >= 0, driver=sess.audio.driver)
            return None

        sess = self.session(max_frames=30, volume=40, script=script)
        sess.play(new_run={"game_id": "firered"})
        self.assertTrue(seen["available"])        # dummy audio driver headless; a real device on desktops
        self.assertEqual(seen["volume"], 40)
        self.assertEqual(sess.video_driver, "dummy")
        self.assertEqual(sess.core, None)                                         # shut down cleanly

    def test_saves_recovery_state_and_clean_close(self):
        def script(sess, frame):
            if frame == 10:
                sess.core.write_memory(MEMORY_SAVE_RAM, 0, b"NFNF-SAVE")
            return None

        sess = self.session(max_frames=200, script=script, recovery_seconds=0)
        result = sess.play(new_run={"game_id": "firered"})
        run = self.app.runs.get(result.runs_played[0])
        sav = run.path / "saves" / "game.sav"
        self.assertTrue(sav.read_bytes().startswith(b"NFNF-SAVE"))
        self.assertTrue((run.path / "states" / "recovery.state").exists())
        session = json.loads((run.path / "session.json").read_text())
        self.assertEqual(session["state"], "closed")
        types = [e["type"] for e in self.app.runs.events(run.id)]
        self.assertIn(EventType.SAVE_CREATED, types)
        self.assertIn(EventType.SESSION_CLOSED, types)
        self.assertEqual(run.status, RunState.ACTIVE)   # quitting keeps the run; nothing is discarded
        self.assertEqual(find_interrupted(self.app.runs), [])

    def test_crash_detection_and_resume(self):
        sess = self.session(max_frames=60)
        result = sess.play(new_run={"game_id": "firered"})
        run = self.app.runs.get(result.runs_played[0])
        # simulate a crash: the session file still says "running" for a process that is gone
        (run.path / "session.json").write_text(json.dumps(
            {"pid": 2 ** 22 + 12345, "state": "running", "heartbeat": "2026-01-01T00:00:00+00:00"}))
        found = find_interrupted(self.app.runs)
        self.assertEqual([f.run.id for f in found], [run.id])
        self.assertTrue(found[0].has_recovery_state)
        self.app.runs.record_event(run.id, EventType.SESSION_INTERRUPTED, {"detected": "test"}, source="session")
        sess = self.session(max_frames=30)
        sess.play(run_id=run.id, resume=True, restore_state=True, after_crash=True)
        types = [e["type"] for e in self.app.runs.events(run.id)]
        self.assertIn(EventType.SESSION_RESUMED, types)
        self.assertIn(EventType.STATE_RESTORED, types)
        report = self.app.verify_run(run.id)
        self.assertEqual(report.status, IntegrityStatus.SUSPICIOUS)   # interruption recorded, not hidden
        self.assertFalse(is_interrupted(json.loads((run.path / "session.json").read_text())))

    def test_abandon_from_game(self):
        def script(sess, frame):
            if frame == -1:
                return {"QUIT"}                             # answer the ABANDONED screen
            if frame == 20 and sess.run:
                sess._abandon()
                sess.overlays[-1].buttons[0].action()     # confirm ABANDON
            return None

        sess = self.session(max_frames=40, script=script)
        result = sess.play(new_run={"game_id": "firered"})
        run = self.app.runs.get(result.runs_played[0])
        self.assertEqual((run.status, run.row["end_reason"]), (RunState.ABANDONED, "abandoned by player"))
        self.assertEqual(self.app.career().failed, 0)     # abandon is not a death


class FakeSdl:
    """Keyboard/controller stand-in for InputRouter tests."""

    def __init__(self):
        import ctypes
        self.keys = (ctypes.c_uint8 * 512)()
        self.buttons, self.axes = set(), {}

    def GetKeyboardState(self, _):
        return self.keys

    def NumJoysticks(self):
        return 1

    def IsGameController(self, i):
        return True

    def GameControllerOpen(self, i):
        return 1

    def GameControllerGetJoystick(self, h):
        return 1

    def JoystickInstanceID(self, j):
        return 7

    def GameControllerName(self, h):
        return b"Xbox Wireless Controller"

    def GameControllerGetButton(self, h, idx):
        return int(S.CONTROLLER_BUTTONS[idx] in self.buttons)

    def GameControllerGetAxis(self, h, idx):
        return self.axes.get(S.CONTROLLER_AXES[idx], 0)

    def GameControllerClose(self, h):
        pass


@unittest.skipUnless(HAVE_SDL, "SDL2 module import failed")
class InputRouterTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from nfnf_ironmon.controllers import MappingRepository
        from nfnf_ironmon.frontend.input import InputRouter
        from nfnf_ironmon.paths import BUNDLE_ROOT
        self.sdl = FakeSdl()
        self.router = InputRouter(self.sdl, MappingRepository(BUNDLE_ROOT / "input-mappings").load("xbox-gba-labels"))
        self.router.scan()

    def test_controller_through_nfnf_mapping(self):
        self.assertEqual(self.router.active_name, "Xbox Wireless Controller")
        self.assertEqual(self.router.changes, [("connected", "Xbox Wireless Controller")])
        self.sdl.buttons = {"A", "DPAD_UP", "BACK"}
        self.sdl.axes = {"RT": 32000}
        snap = self.router.poll()
        self.assertEqual(snap.logical, {"A", "UP", "SELECT", "R"})
        self.sdl.buttons = {"GUIDE"}
        self.assertTrue(self.router.poll().menu_combo)

    def test_keyboard(self):
        self.sdl.keys[S.SC["x"]] = 1
        self.sdl.keys[S.SC["LEFT"]] = 1
        self.sdl.keys[S.SC["RIGHT"]] = 1          # opposite directions cancel
        self.assertEqual(self.router.poll().logical, {"A"})


if __name__ == "__main__":
    unittest.main()
