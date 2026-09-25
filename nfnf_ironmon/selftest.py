"""`nfnf-ironmon selftest`: the central workflow, headless, on the user's own ROM.

Runs in a temporary data folder (the user's career and runs are untouched; the
ROM is only read): new run → UPR randomization → integrated emulator → scripted
play to the starter → a faint written into real game memory → rules → RUN FAILED
→ the next run is prepared automatically → RUN ACTIVE. Used for clean-machine
validation of the portable builds.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .autoplay import INTRO_TO_STARTER, SessionScript
from .events import EventType


def run_selftest(app, game_id: str | None = None, keep: bool = False, window: bool = False,
                 capture: Path | None = None) -> dict[str, Any]:
    from .app import Application
    from .frontend.session import GameSession, SessionOptions

    app.roms.scan()
    candidates = [g.game_id for g in app.games.all()
                  if g.status == "supported" and g.game_id in INTRO_TO_STARTER and app.roms.list(g.game_id)]
    game_id = game_id or (candidates[0] if candidates else None)
    report: dict[str, Any] = {"game": game_id, "steps": {}, "result": "FAIL"}
    if not game_id:
        report["error"] = "No supported game ROM found in games/original/"
        return report
    tmp = Path(tempfile.mkdtemp(prefix="nfnf-selftest-"))
    home = tmp / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "settings.json").write_text(json.dumps(
        {"schema": 1, "default_randomizer_profile": "auto", "emulator": "nfnf-libretro", "tracker": "nfnf"}))
    t0 = time.time()
    test_app = Application(home)
    try:
        test_app.roms.scan(app.paths.originals_dir)               # read in place, never copied
        def on_frame(sess, frame):
            if capture and len(sess.result.runs_played) == 1 and frame == 2500:
                sess.request_window_capture(capture)

        script = SessionScript([INTRO_TO_STARTER[game_id] + ["WAIT60", "POKE_PARTY_HP:0", "WAIT200"], ["WAIT30"]],
                               runs=2, stop=lambda s: len(s.result.runs_played) >= 2 and s.run and s.frames >= 60,
                               on_frame=on_frame)
        sess = GameSession(test_app, SessionOptions(headless=not window, unthrottled=True, script=script,
                                                    max_frames=60000, muted=True))
        result = sess.play(new_run={"game_id": game_id})
        steps = report["steps"]
        steps["runs_played"] = result.runs_played
        steps["video_driver"] = sess.video_driver
        steps["audio_driver"] = sess.audio.driver if sess.audio else None
        if capture and capture.exists():
            steps["window_capture"] = str(capture)
        steps["frames_rendered"] = sess.rendered_frames
        if result.runs_played:
            first = test_app.runs.get(result.runs_played[0])
            types = [e["type"] for e in test_app.runs.events(first.id)]
            party = [e["payload"] for e in test_app.runs.events(first.id) if e["type"] == "STARTER_OBTAINED"]
            steps["randomized"] = EventType.ROM_RANDOMIZED in types
            steps["emulator_started"] = EventType.EMULATOR_STARTED in types
            steps["starter_detected"] = party[0]["species"] if party else None
            steps["faint_detected"] = EventType.POKEMON_FAINTED in types
            steps["run_failed"] = first.status.value
            steps["attempt_counter"] = test_app.career().attempts
        if len(result.runs_played) >= 2:
            second = test_app.runs.get(result.runs_played[1])
            steps["next_run"] = second.status.value
            steps["next_run_new_rom"] = second.row["generated_rom_sha256"] != first.row["generated_rom_sha256"]
        ok = (steps.get("randomized") and steps.get("emulator_started") and steps.get("starter_detected")
              and steps.get("faint_detected") and steps.get("run_failed") == "FAILED"
              and steps.get("next_run") == "ACTIVE" and steps.get("next_run_new_rom"))
        report["result"] = "PASS" if ok else "FAIL"
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        test_app.close()
        report["seconds"] = round(time.time() - t0, 1)
        if keep:
            report["data_folder"] = str(home)
        else:
            for p in tmp.rglob("*"):
                try:
                    os.chmod(p, 0o755 if p.is_dir() else 0o644)
                except OSError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)
    return report
