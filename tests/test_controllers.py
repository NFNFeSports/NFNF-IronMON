"""Controller abstraction: mappings, manager selection, Linux joystick and XInput backends."""

import ctypes
import os
import tempfile
import unittest
from pathlib import Path

from nfnf_ironmon.controllers import (ControllerDevice, ControllerManager, InputMapping, MappingError,
                                      MappingRepository, PadState)
from nfnf_ironmon.controllers.linux_joystick import (EVENT, JS_EVENT_AXIS, JS_EVENT_BUTTON, JS_EVENT_INIT,
                                                     LinuxJoystick, LinuxJoystickBackend)
from nfnf_ironmon.controllers.mock import MockControllerBackend
from nfnf_ironmon.controllers.xinput import ERROR_DEVICE_NOT_CONNECTED, XInputBackend, XInputState
from nfnf_ironmon.emulators.libretro import JOYPAD_IDS
from nfnf_ironmon.paths import BUNDLE_ROOT

MAPPINGS = MappingRepository(BUNDLE_ROOT / "input-mappings")


def dev(i, kind="xinput", pad=True, backend="mock"):
    return ControllerDevice(id=f"{backend}:{i}", name=f"Pad {i}", backend=backend, kind=kind, is_gamepad=pad)


class MappingTests(unittest.TestCase):
    def test_bundled_mappings_valid(self):
        ids = {m.id for m in MAPPINGS.list()}
        self.assertEqual(ids, {"xbox-gba-labels", "xbox-gba-positional"})

    def test_labels_mapping(self):
        m = MAPPINGS.load("xbox-gba-labels")
        s = PadState(buttons={"A", "BACK", "DPAD_UP", "LB"})
        self.assertEqual(m.apply(s), {"A", "SELECT", "UP", "L"})
        self.assertEqual(MAPPINGS.load("xbox-gba-positional").apply(PadState(buttons={"A"})), {"B"})

    def test_sticks_deadzone_triggers_and_cancel(self):
        m = MAPPINGS.load("xbox-gba-labels")
        self.assertEqual(m.apply(PadState(axes={"LX": 0.2, "LY": -0.2})), set())       # inside deadzone
        self.assertEqual(m.apply(PadState(axes={"LX": -0.9, "LY": 0.9})), {"LEFT", "DOWN"})
        self.assertEqual(m.apply(PadState(axes={"RT": 0.8, "LT": 0.1})), {"R"})
        self.assertEqual(m.apply(PadState(buttons={"DPAD_LEFT"}, axes={"LX": 0.9})), set())  # L+R cancel

    def test_every_logical_output_reaches_the_emulator(self):
        for m in MAPPINGS.list():
            outputs = {l for ls in m.buttons.values() for l in ls}
            outputs |= {v for d in m.axes.values() for v in d.values()}
            self.assertTrue(outputs <= set(JOYPAD_IDS), outputs - set(JOYPAD_IDS))

    def test_validation(self):
        base = {"id": "x", "name": "x", "buttons": {"A": ["A"]}}
        for bad in ({**base, "buttons": {"Z": ["A"]}}, {**base, "buttons": {"A": ["TURBO"]}},
                    {**base, "axes": {"LX": {"negative": "NOPE"}}}, {**base, "deadzone": 1.5},
                    {"id": "x", "buttons": {}}):
            with self.subTest(bad=bad), self.assertRaises(MappingError):
                InputMapping.from_dict(bad)
        self.assertEqual(InputMapping.from_dict({**base, "buttons": {"BUTTON_12": "START"}})
                         .apply(PadState(raw_buttons={"BUTTON_12"})), {"START"})


class ManagerTests(unittest.TestCase):
    def test_selection_prefers_xinput_and_skips_non_pads(self):
        backend = MockControllerBackend([dev(0, "unknown", pad=False), dev(1, "gamepad"), dev(2, "xinput")])
        mgr = ControllerManager(MAPPINGS, [backend])
        self.assertEqual(mgr.select_device().id, "mock:2")
        mgr.preferred_device = "mock:1"
        self.assertEqual(mgr.select_device().id, "mock:1")
        self.assertIsNone(ControllerManager(MAPPINGS, [MockControllerBackend([dev(0, "unknown", False)])])
                          .select_device())

    def test_poll_logical_and_test_loop(self):
        backend = MockControllerBackend([dev(0)])
        mgr = ControllerManager(MAPPINGS, [backend])
        self.assertEqual(mgr.poll_logical(), set())       # nothing open yet
        mgr.open()
        backend.states["mock:0"] = PadState(buttons={"START", "B"})
        self.assertEqual(mgr.poll_logical(), {"START", "B"})
        seen = []
        self.assertEqual(mgr.test(0.05, lambda p, l: seen.append(l)), 1)
        self.assertEqual(seen, [{"START", "B"}])

    def test_backend_status_reported(self):
        names = {b["backend"] for b in ControllerManager(MAPPINGS).backend_status()}
        self.assertIn("sdl2", names)


class LinuxJoystickTests(unittest.TestCase):
    def pad(self, kind="xinput"):
        r, w = os.pipe()
        os.set_blocking(r, False)
        js = LinuxJoystick(dev(0, kind, backend="linux-joystick"), Path("/dev/null"), fd=r)
        self.addCleanup(js.close)
        self.addCleanup(os.close, w)
        return js, lambda etype, num, val: os.write(w, EVENT.pack(0, val, etype, num))

    def test_xpad_layout(self):
        js, send = self.pad()
        send(JS_EVENT_BUTTON | JS_EVENT_INIT, 0, 1)   # A (initial state)
        send(JS_EVENT_BUTTON, 7, 1)                    # START
        send(JS_EVENT_AXIS, 6, -32767)                 # hat left
        send(JS_EVENT_AXIS, 7, 32767)                  # hat down
        send(JS_EVENT_AXIS, 5, 32767)                  # RT fully pressed
        send(JS_EVENT_AXIS, 0, 32767)                  # left stick right
        s = js.poll()
        self.assertEqual(s.buttons, {"A", "START", "DPAD_LEFT", "DPAD_DOWN"})
        self.assertAlmostEqual(s.axes["RT"], 1.0)
        self.assertAlmostEqual(s.axes["LX"], 1.0)
        send(JS_EVENT_BUTTON, 0, 0)                    # release A
        self.assertNotIn("A", js.poll().buttons)
        self.assertEqual(MAPPINGS.load("xbox-gba-labels").apply(js.poll()),
                         {"START", "DOWN", "R"})       # LEFT (hat) and RIGHT (stick) cancel

    def test_unknown_driver_uses_raw_buttons(self):
        js, send = self.pad("gamepad")
        send(JS_EVENT_BUTTON, 3, 1)
        self.assertEqual(js.poll().raw_buttons, {"BUTTON_3"})

    def test_list_devices_handles_unopenable_nodes(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "dev").mkdir()
        (tmp / "dev" / "js0").write_text("")          # a plain file: ioctl fails
        sysdev = tmp / "sys" / "js0" / "device"
        (sysdev / "id").mkdir(parents=True)
        (sysdev / "id" / "vendor").write_text("045e\n")
        (sysdev / "id" / "product").write_text("0b12\n")
        (sysdev / "name").write_text("Microsoft Xbox Series S|X Controller\n")
        devices = LinuxJoystickBackend(tmp / "dev", tmp / "sys").list_devices()
        self.assertEqual(len(devices), 1)
        d = devices[0]
        self.assertEqual((d.kind, d.vendor_id, d.product_id), ("xinput", "045e", "0b12"))
        self.assertTrue(d.notes)


class FakeXInputDll:
    def __init__(self, connected: dict[int, int]):
        self.connected = connected   # slot -> wButtons

    def XInputGetState(self, slot, state_ref):
        if slot not in self.connected:
            return ERROR_DEVICE_NOT_CONNECTED
        st = ctypes.cast(state_ref, ctypes.POINTER(XInputState))[0]
        st.Gamepad.wButtons = self.connected[slot]
        st.Gamepad.bRightTrigger = 255
        st.Gamepad.sThumbLY = 32767       # stick up
        return 0


class XInputTests(unittest.TestCase):
    def test_devices_and_state(self):
        be = XInputBackend(FakeXInputDll({1: 0x1000 | 0x0010 | 0x0004}))   # A + START + DPAD_LEFT
        devices = be.list_devices()
        self.assertEqual([d.id for d in devices], ["xinput:1"])
        s = be.open(devices[0]).poll()
        self.assertEqual(s.buttons, {"A", "START", "DPAD_LEFT"})
        self.assertAlmostEqual(s.axes["RT"], 1.0)
        self.assertAlmostEqual(s.axes["LY"], -1.0)   # XInput up (+) → NFNF up (−)
        self.assertEqual(MAPPINGS.load("xbox-gba-labels").apply(s), {"A", "START", "LEFT", "UP", "R"})

    def test_disconnect(self):
        dll = FakeXInputDll({0: 0})
        be = XInputBackend(dll)
        ctl = be.open(be.list_devices()[0])
        del dll.connected[0]
        self.assertEqual(ctl.poll().buttons, set())
        self.assertFalse(ctl.device.connected)


if __name__ == "__main__":
    unittest.main()
