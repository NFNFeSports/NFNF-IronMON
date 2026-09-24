"""Windows backend: XInput via ctypes (XInput1_4.dll ships with Windows 8+).

Covers Xbox 360 / One / Series and any XInput-compatible pad, up to 4 slots.
XInput reports no device names, so slots are named "XInput controller N".
"""

from __future__ import annotations

import ctypes
from typing import Any

from ..platform_support import os_family
from .base import ControllerBackend, ControllerDevice, OpenController, PadState

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167

BUTTON_BITS = {0x0001: "DPAD_UP", 0x0002: "DPAD_DOWN", 0x0004: "DPAD_LEFT", 0x0008: "DPAD_RIGHT",
               0x0010: "START", 0x0020: "BACK", 0x0040: "LS", 0x0080: "RS", 0x0100: "LB",
               0x0200: "RB", 0x1000: "A", 0x2000: "B", 0x4000: "X", 0x8000: "Y"}

DLL_NAMES = ("xinput1_4", "xinput1_3", "xinput9_1_0")


class XInputGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", XInputGamepad)]


def state_to_pad(gp: XInputGamepad) -> PadState:
    s = PadState()
    for bit, name in BUTTON_BITS.items():
        if gp.wButtons & bit:
            s.buttons.add(name)
    s.axes = {"LX": max(-1.0, gp.sThumbLX / 32767), "LY": max(-1.0, -gp.sThumbLY / 32767),
              "RX": max(-1.0, gp.sThumbRX / 32767), "RY": max(-1.0, -gp.sThumbRY / 32767),
              "LT": gp.bLeftTrigger / 255, "RT": gp.bRightTrigger / 255}
    return s


class XInputBackend(ControllerBackend):
    backend_id = "xinput"
    display_name = "Windows XInput"
    status = "READY"

    def __init__(self, dll: Any = None):
        self._dll = dll
        self._dll_error = ""

    def _get_dll(self) -> Any:
        if self._dll is None and os_family() == "windows":
            for name in DLL_NAMES:
                try:
                    self._dll = getattr(ctypes, "WinDLL")(name)
                    break
                except OSError as exc:
                    self._dll_error = str(exc)
        return self._dll

    def availability(self) -> tuple[str, str]:
        if self._dll is None and os_family() != "windows":
            return "UNAVAILABLE", "XInput exists only on Windows"
        if self._get_dll() is None:
            return "UNAVAILABLE", f"No XInput DLL: {self._dll_error}"
        return "READY", ""

    def _get_state(self, slot: int) -> XInputState | None:
        st = XInputState()
        rc = self._get_dll().XInputGetState(slot, ctypes.byref(st))
        return st if rc == ERROR_SUCCESS else None

    def list_devices(self) -> list[ControllerDevice]:
        if self.availability()[0] != "READY":
            return []
        return [ControllerDevice(id=f"xinput:{slot}", name=f"XInput controller {slot + 1}",
                                 backend=self.backend_id, kind="xinput", buttons=14, axes=6)
                for slot in range(4) if self._get_state(slot) is not None]

    def open(self, device: ControllerDevice) -> "XInputController":
        return XInputController(self, device, int(device.id.split(":")[1]))


class XInputController(OpenController):
    def __init__(self, backend: XInputBackend, device: ControllerDevice, slot: int):
        self.backend, self.device, self.slot = backend, device, slot

    def poll(self) -> PadState:
        st = self.backend._get_state(self.slot)
        if st is None:
            self.device.connected = False
            return PadState()
        self.device.connected = True
        return state_to_pad(st.Gamepad)
