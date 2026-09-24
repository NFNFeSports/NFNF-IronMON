"""Linux backend: kernel joystick API (/dev/input/js*), standard library only.

Xbox 360 / One / Series pads on USB are driven by the in-kernel ``xpad``
driver, whose button/axis numbering is fixed (see XPAD_* below). Other
drivers are exposed with raw ``BUTTON_n`` names so a mapping can still be
configured for them.
"""

from __future__ import annotations

import fcntl
import os
import struct
from pathlib import Path

from .base import ControllerBackend, ControllerDevice, OpenController, PadState

JS_EVENT_BUTTON, JS_EVENT_AXIS, JS_EVENT_INIT = 0x01, 0x02, 0x80
JSIOCGAXES = 0x80016A11
JSIOCGBUTTONS = 0x80016A12


def JSIOCGNAME(length: int) -> int:
    return 0x80006A13 | (length << 16)


EVENT = struct.Struct("<IhBB")  # time (ms), value, type, number

# xpad driver layout (drivers/input/joystick/xpad.c, joydev numbering)
XPAD_BUTTONS = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB", 6: "BACK", 7: "START",
                8: "GUIDE", 9: "LS", 10: "RS"}
XPAD_AXES = {0: "LX", 1: "LY", 2: "LT", 3: "RX", 4: "RY", 5: "RT", 6: "HAT_X", 7: "HAT_Y"}

MICROSOFT_VENDOR = "045e"
XBOX_PRODUCTS = {"028e": "Xbox 360 Controller", "0719": "Xbox 360 Wireless Receiver",
                 "02d1": "Xbox One Controller", "02dd": "Xbox One Controller",
                 "02ea": "Xbox One S Controller", "0b12": "Xbox Series X|S Controller",
                 "0b13": "Xbox Wireless Controller (Bluetooth)", "0b00": "Xbox Elite Series 2"}


def _read_sys(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


class LinuxJoystickBackend(ControllerBackend):
    backend_id = "linux-joystick"
    display_name = "Linux joystick API"
    status = "READY"

    def __init__(self, dev_dir: Path = Path("/dev/input"), sys_dir: Path = Path("/sys/class/input")):
        self.dev_dir, self.sys_dir = dev_dir, sys_dir

    def availability(self) -> tuple[str, str]:
        if not self.dev_dir.is_dir():
            return "UNAVAILABLE", f"{self.dev_dir} not present"
        return "READY", ""

    def _query(self, node: Path) -> tuple[str, int, int]:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        try:
            name_buf = bytearray(128)
            fcntl.ioctl(fd, JSIOCGNAME(len(name_buf)), name_buf)
            ax, bt = bytearray(1), bytearray(1)
            fcntl.ioctl(fd, JSIOCGAXES, ax)
            fcntl.ioctl(fd, JSIOCGBUTTONS, bt)
            return name_buf.split(b"\0")[0].decode(errors="replace"), ax[0], bt[0]
        finally:
            os.close(fd)

    def list_devices(self) -> list[ControllerDevice]:
        out = []
        for node in sorted(self.dev_dir.glob("js*")):
            sysdev = self.sys_dir / node.name / "device"
            vendor = _read_sys(sysdev / "id" / "vendor")
            product = _read_sys(sysdev / "id" / "product")
            driver_link = sysdev / "device" / "driver"
            driver = os.path.basename(os.path.realpath(driver_link)) if driver_link.exists() else None
            notes = []
            try:
                name, axes, buttons = self._query(node)
            except OSError as exc:
                name, axes, buttons = _read_sys(sysdev / "name") or node.name, None, None
                notes.append(f"cannot open {node}: {exc.strerror} (check 'input' group membership)")
            is_xbox = vendor == MICROSOFT_VENDOR and (product in XBOX_PRODUCTS or driver == "xpad")
            kind = "xinput" if (driver == "xpad" or is_xbox) else "gamepad"
            # Mice/touchpads also get js nodes; a pad needs axes and several buttons.
            is_pad = axes is None or (axes >= 2 and buttons >= 4)
            if not is_pad:
                kind = "unknown"
                notes.append("not a gamepad (joystick node without pad axes/buttons)")
            if is_xbox and product in XBOX_PRODUCTS and not name:
                name = XBOX_PRODUCTS[product]
            out.append(ControllerDevice(id=f"{self.backend_id}:{node}", name=name,
                                        backend=self.backend_id, kind=kind, is_gamepad=is_pad,
                                        vendor_id=vendor, product_id=product, driver=driver,
                                        buttons=buttons, axes=axes, notes=notes))
        return out

    def open(self, device: ControllerDevice) -> "LinuxJoystick":
        return LinuxJoystick(device, Path(device.id.split(":", 1)[1]))


class LinuxJoystick(OpenController):
    def __init__(self, device: ControllerDevice, node: Path, fd: int | None = None):
        self.device = device
        self.fd = fd if fd is not None else os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        self.xpad = device.kind == "xinput"
        self.buttons: dict[int, bool] = {}
        self.axes: dict[int, int] = {}

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self.fd, EVENT.size * 64)
            except BlockingIOError:
                return
            except OSError:
                self.device.connected = False
                return
            if not chunk:
                return
            for i in range(0, len(chunk) - EVENT.size + 1, EVENT.size):
                _, value, etype, number = EVENT.unpack_from(chunk, i)
                if etype & JS_EVENT_BUTTON:
                    self.buttons[number] = bool(value)
                elif etype & JS_EVENT_AXIS:
                    self.axes[number] = value

    def poll(self) -> PadState:
        self._drain()
        state = PadState()
        for num, down in self.buttons.items():
            if not down:
                continue
            name = XPAD_BUTTONS.get(num) if self.xpad else None
            (state.buttons if name else state.raw_buttons).add(name or f"BUTTON_{num}")
        for num, raw in self.axes.items():
            name = XPAD_AXES.get(num) if self.xpad else None
            if not name:
                continue
            if name in ("LT", "RT"):          # xpad triggers: -32767 released .. 32767 pressed
                state.axes[name] = (raw + 32767) / 65534
            elif name == "HAT_X":
                if raw < 0:
                    state.buttons.add("DPAD_LEFT")
                elif raw > 0:
                    state.buttons.add("DPAD_RIGHT")
            elif name == "HAT_Y":
                if raw < 0:
                    state.buttons.add("DPAD_UP")
                elif raw > 0:
                    state.buttons.add("DPAD_DOWN")
            else:
                state.axes[name] = max(-1.0, raw / 32767)
        return state

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
