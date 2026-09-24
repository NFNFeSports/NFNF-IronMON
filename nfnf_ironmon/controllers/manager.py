"""ControllerManager: device discovery, selection, mapping and polling."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Callable

from ..platform_support import os_family
from .base import ControllerBackend, ControllerDevice, OpenController
from .mapping import InputMapping, MappingRepository


class Sdl2BackendStatus(ControllerBackend):
    """SDL2 GameController (wide HID coverage, hot-plug). Planned; reports availability only."""
    backend_id = "sdl2"
    display_name = "SDL2 GameController"
    status = "PLANNED"

    def availability(self) -> tuple[str, str]:
        found = importlib.util.find_spec("sdl2") is not None
        return "PLANNED", "PySDL2 importable" if found else "PySDL2/SDL2 not bundled yet"

    def list_devices(self) -> list[ControllerDevice]:
        return []

    def open(self, device: ControllerDevice) -> OpenController:
        raise NotImplementedError("SDL2 backend is planned")


def default_backends() -> list[ControllerBackend]:
    backends: list[ControllerBackend] = []
    if os_family() == "linux":
        from .linux_joystick import LinuxJoystickBackend
        backends.append(LinuxJoystickBackend())
    if os_family() == "windows":
        from .xinput import XInputBackend
        backends.append(XInputBackend())
    backends.append(Sdl2BackendStatus())
    return backends


class ControllerManager:
    def __init__(self, mappings: MappingRepository, backends: list[ControllerBackend] | None = None,
                 mapping_id: str = "xbox-gba-labels", preferred_device: str | None = None):
        self.mappings = mappings
        self.backends = backends if backends is not None else default_backends()
        self.mapping_id = mapping_id
        self.preferred_device = preferred_device
        self._open: OpenController | None = None

    def backend_status(self) -> list[dict]:
        out = []
        for b in self.backends:
            state, detail = b.availability()
            out.append({"backend": b.backend_id, "name": b.display_name, "status": state, "detail": detail})
        return out

    def devices(self) -> list[ControllerDevice]:
        out = []
        for b in self.backends:
            if b.availability()[0] == "READY":
                out.extend(b.list_devices())
        return out

    def select_device(self) -> ControllerDevice | None:
        devs = self.devices()
        if self.preferred_device:
            for d in devs:
                if d.id == self.preferred_device:
                    return d
        pads = [d for d in devs if d.is_gamepad]
        xinput = [d for d in pads if d.kind == "xinput"]   # Xbox/XInput first
        return (xinput or pads or [None])[0]

    def mapping(self) -> InputMapping:
        return self.mappings.load(self.mapping_id)

    def open(self, device: ControllerDevice | None = None) -> OpenController | None:
        device = device or self.select_device()
        if device is None:
            return None
        backend = next(b for b in self.backends if b.backend_id == device.backend)
        self.close()
        self._open = backend.open(device)
        return self._open

    def poll_logical(self) -> set[str]:
        """Logical NFNF buttons currently pressed on the open controller."""
        if self._open is None:
            return set()
        return self.mapping().apply(self._open.poll())

    def test(self, seconds: float, on_change: Callable[[set[str], set[str]], None],
             interval: float = 1 / 60) -> int:
        """Poll for ``seconds``; call ``on_change(physical, logical)`` when input changes."""
        if self._open is None and self.open() is None:
            return 0
        mapping = self.mapping()
        last, changes, end = None, 0, time.monotonic() + seconds
        while time.monotonic() < end:
            state = self._open.poll()
            phys = state.buttons | state.raw_buttons
            logical = mapping.apply(state)
            key = (frozenset(phys), frozenset(logical))
            if key != last:
                on_change(phys, logical)
                last, changes = key, changes + 1
            time.sleep(interval)
        return changes

    def close(self) -> None:
        if self._open:
            self._open.close()
            self._open = None
