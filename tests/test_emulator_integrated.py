"""Emulator capability detection and the in-process libretro host.

Core tests use the bundled mGBA libretro core (skipped if not fetched) with a
synthetic header-only ROM — no Pokémon ROM is needed."""

import tempfile
import unittest
from pathlib import Path

from helpers import synthetic_gba

from nfnf_ironmon.components import ComponentManager
from nfnf_ironmon.emulators import (EmulatorError, IntegratedEmulatorAdapter, LaunchRequest,
                                    build_emulators)
from nfnf_ironmon.emulators.libretro import (MEMORY_SAVE_RAM, Frame, LibretroCore, LibretroError,
                                             write_png)
from nfnf_ironmon.paths import BUNDLE_ROOT

COMPONENTS = ComponentManager(BUNDLE_ROOT)
CORE = COMPONENTS.resolve("mgba-libretro")


class CapabilityTests(unittest.TestCase):
    def test_capabilities_distinguish_integrated_and_external(self):
        emus = build_emulators({}, BUNDLE_ROOT, COMPONENTS)
        integ = emus["nfnf-libretro"].capabilities()
        self.assertTrue(integ["embedded"] and integ["memory_access"] and integ["save_states"])
        self.assertFalse(integ["window"])                   # honest: headless prototype
        self.assertEqual(integ["status"], "PROTOTYPE")
        for ext in ("bizhawk", "mgba"):
            caps = emus[ext].capabilities()
            self.assertFalse(caps["embedded"])
            self.assertEqual(caps["status"], "EXTERNAL")
        self.assertEqual(emus["mock"].status, "MOCK")
        self.assertFalse(emus["nfnf-libretro"].interactive)

    def test_integrated_launch_refuses(self):
        with self.assertRaisesRegex(EmulatorError, "Phase 3"):
            IntegratedEmulatorAdapter(components=COMPONENTS).launch(LaunchRequest(Path("x.gba")))

    def test_missing_core_detected(self):
        a = IntegratedEmulatorAdapter({"core": "/nonexistent/core.so"})
        self.assertFalse(a.detect_installation().found)

    def test_png_writer(self):
        tmp = Path(tempfile.mkdtemp())
        write_png(tmp / "x.png", Frame(2, 1, bytes([255, 0, 0, 0, 0, 255])), scale=2)
        data = (tmp / "x.png").read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR\x00\x00\x00\x04\x00\x00\x00\x02", data)   # 4x2 after scaling


@unittest.skipUnless(CORE, "mGBA libretro core not fetched (components fetch)")
class LibretroHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rom = synthetic_gba(self.tmp / "synthetic.gba", size=64 * 1024)

    def test_core_boots_rom_and_exposes_state(self):
        with LibretroCore(CORE, self.tmp / "sys", self.tmp / "saves") as core:
            info = core.info()
            self.assertEqual(info.library_name, "mGBA")
            self.assertIn("gba", info.valid_extensions)
            core.load_game(self.rom)
            core.run_frames(30, {"START", "A"})
            self.assertEqual(core.frames_run, 30)
            av = core.info()
            self.assertAlmostEqual(av.fps, 59.7275, places=2)
            frame = core.last_frame
            self.assertEqual((frame.width, frame.height), (240, 160))
            self.assertEqual(len(frame.rgb), 240 * 160 * 3)
            self.assertEqual(core.read_bus(0x080000A0, 12), b"POKEMON FIRE")   # ROM via memory map
            with self.assertRaises(LibretroError):
                core.read_bus(0xF0000000, 4)
            state = core.save_state()
            core.run_frames(10)
            core.load_state(state)
            sram = core.memory(MEMORY_SAVE_RAM)
            if sram:
                n = core.export_save_ram(self.tmp / "game.sav")
                self.assertEqual(core.import_save_ram(self.tmp / "game.sav"), n)
            self.assertGreater(core.audio_frames, 0)

    def test_single_instance_and_reload(self):
        core = LibretroCore(CORE, self.tmp, self.tmp)
        with self.assertRaises(LibretroError):
            LibretroCore(CORE, self.tmp, self.tmp)
        core.close()
        with LibretroCore(CORE, self.tmp, self.tmp) as again:   # re-init after deinit works
            again.load_game(self.rom)
            again.run_frames(5)

    def test_adapter_smoke_test(self):
        adapter = IntegratedEmulatorAdapter(components=COMPONENTS)
        res = adapter.smoke_test(self.rom, self.tmp / "work", frames=60, screenshot=self.tmp / "s.png",
                                 press_start=False)
        self.assertEqual((res.core, res.width, res.height, res.frames), ("mGBA", 240, 160, 60))
        self.assertEqual(res.header_via_bus[:12], "POKEMON FIRE")
        self.assertTrue((self.tmp / "s.png").exists())


if __name__ == "__main__":
    unittest.main()
