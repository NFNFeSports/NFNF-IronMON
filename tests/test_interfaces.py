"""CLI, local HTTP API, adapters, portability and UI smoke tests."""

import io
import json
import os
import shutil
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from helpers import AppTestCase

from nfnf_ironmon import cli
from nfnf_ironmon.api import create_server
from nfnf_ironmon.app import Application
from nfnf_ironmon.emulators import BizHawkAdapter, LaunchRequest, MgbaAdapter
from nfnf_ironmon.games import FireRedGameAdapter, GameAdapter
from nfnf_ironmon.integrity import IntegrityStatus
from nfnf_ironmon.trackers import IronmonTrackerAdapter


class CliTests(AppTestCase):
    def cli(self, *args):
        self.app.close()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--home", str(self.home), *args])
        self.app = Application(self.home, strict_events=True)
        return code, buf.getvalue()

    def test_cli_flow(self):
        self.assertEqual(self.cli("rom", "import", str(self.user_rom))[0], 0)
        code, out = self.cli("run", "new", "--seed", "18472931")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "ACTIVE")
        code, out = self.cli("run", "event", "RUN-000001", "POKEMON_FAINTED",
                             "--payload", '{"is_starter": true}')
        self.assertIn("Run is now FAILED", out)
        code, out = self.cli("run", "list")
        self.assertIn("RUN-000001  FAILED", out)
        code, out = self.cli("games")
        self.assertIn("firered", out)
        self.assertIn("planned", out)
        self.assertEqual(self.cli("doctor")[0], 0)

    def test_cli_errors_return_nonzero(self):
        self.assertEqual(self.cli("run", "show", "RUN-999999")[0], 1)
        self.assertEqual(self.cli("run", "new")[0], 1)  # no ROM imported yet


class ApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.import_firered()
        self.server = create_server(self.app, port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_api_flow(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        code, run = self.call("POST", "/api/runs", {"seed": 5})
        self.assertEqual((code, run["status"]), (201, "ACTIVE"))
        code, _ = self.call("POST", f"/api/runs/{run['id']}/events",
                            {"type": "BADGE_ACQUIRED", "payload": {"badge": "Boulder"}})
        self.assertEqual(code, 201)
        code, events = self.call("GET", f"/api/runs/{run['id']}/events")
        self.assertIn("BADGE_ACQUIRED", [e["type"] for e in events])
        code, report = self.call("POST", f"/api/runs/{run['id']}/verify")
        self.assertEqual(report["status"], "VERIFIED")
        code, new = self.call("POST", f"/api/runs/{run['id']}/restart", {"reason": "test"})
        self.assertEqual(new["previous_run_id"], run["id"])
        code, runs = self.call("GET", "/api/runs")
        self.assertEqual([r["status"] for r in runs], ["FAILED", "ACTIVE"])
        code, career = self.call("GET", "/api/career")
        self.assertEqual((code, career["attempts"], career["failed"]), (200, 2, 1))
        code, ctl = self.call("GET", "/api/controllers")
        self.assertEqual((code, ctl["mapping"]), (200, "xbox-gba-labels"))
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("POST", "/api/runs/RUN-000002/events", {"type": "BOGUS"})[0], 400)


class AdapterContractTests(unittest.TestCase):
    def test_firered_contract(self):
        fr = FireRedGameAdapter()
        self.assertIsInstance(fr, GameAdapter)
        self.assertEqual(fr.status, "supported")
        self.assertIn("core", fr.emulator_config("bizhawk"))
        self.assertEqual(fr.tracker_config("ironmon-tracker")["entry_script"], "Ironmon-Tracker.lua")

    def test_emulators_detect_configured_paths(self):
        tmp = Path(__import__("tempfile").mkdtemp())
        exe = tmp / BizHawkAdapter.executable_names()[0]
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        bh = BizHawkAdapter({"path": str(tmp)})
        self.assertTrue(bh.detect_installation().found)
        cmd = bh.build_launch_command(LaunchRequest(rom_path=tmp / "r.gba",
                                                    lua_scripts=[tmp / "Ironmon-Tracker.lua"]))
        self.assertTrue(cmd[1].startswith("--lua="))
        self.assertTrue(cmd[-1].endswith("r.gba"))
        self.assertFalse(BizHawkAdapter({"path": str(tmp / "missing")}).detect_installation().found)
        self.assertFalse(MgbaAdapter({"path": str(tmp / "missing")}).detect_installation().found)

    def test_ironmon_tracker_detection(self):
        tmp = Path(__import__("tempfile").mkdtemp())
        self.assertFalse(IronmonTrackerAdapter({"path": str(tmp)}).detect_installation().found)
        (tmp / "Ironmon-Tracker.lua").write_text("-- stub")
        self.assertTrue(IronmonTrackerAdapter({"path": str(tmp)}).detect_installation().found)


class PortabilityTests(AppTestCase):
    def test_copy_folder_to_new_location_and_continue(self):
        self.import_firered()
        first = self.app.new_run(seed=1)
        self.app.fail_run(first.id)
        active = self.app.new_run(seed=2)
        self.app.close()
        moved = self.tmp / "other-machine" / "NFNF-IronMON"
        shutil.copytree(self.home, moved)
        self.home.rename(self.tmp / "old-location-gone")  # nothing may point at the old path
        app = Application(moved, strict_events=True)
        try:
            self.assertEqual([r.id for r in app.runs.list()], [first.id, active.id])
            self.assertEqual(app.verify_run(first.id).status, IntegrityStatus.VERIFIED)
            self.assertEqual(app.verify_run(active.id).status, IntegrityStatus.VERIFIED)
            self.assertTrue(app.runs.get(active.id).path.is_relative_to(moved))
            nxt = app.restart_run(active.id)
            self.assertEqual(nxt.id, "RUN-000003")
        finally:
            app.close()
        self.app = Application(self.tmp / "unused", strict_events=True)


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "no display available")
class UiSmokeTest(AppTestCase):
    def test_window_builds(self):
        import tkinter as tk
        from nfnf_ironmon.ui import MainWindow
        self.import_firered()
        self.app.new_run(seed=18472931)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            win = MainWindow(root, self.app)
            root.update()
            self.assertEqual(win.games.size(), 3)
            self.assertIn("RUN-000001  ACTIVE", win.runs.get(0))
            text = win.current.get("1.0", "end")
            self.assertIn("Seed: 18472931", text)
            self.assertIn("Game: Pokémon FireRed", text)
            ctl = win.controller.get("1.0", "end")
            self.assertIn("Device:", ctl)
            self.assertIn("Mapping: xbox-gba-labels", ctl)
            self.assertIn("Attempts:         1", win.career.get("1.0", "end"))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
