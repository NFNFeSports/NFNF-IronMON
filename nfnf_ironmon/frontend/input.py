"""Input for the game window: SDL2 GameController (hot-plug) + keyboard → NFNF logical buttons.

    physical pad (SDL GameController, Xbox layout)  ─┐
    fallback: ControllerManager backend (js/XInput) ─┼─► PadState ─► InputMapping ─► logical buttons
    keyboard (fixed default layout)                  ─┘                                   │
                                                                                          ▼
                                                                                   emulator input
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..controllers import ControllerManager, InputMapping, PadState
from . import sdl2 as S

#: keyboard scancode -> logical button (documented in docs/controller.md)
KEYBOARD = {S.SC["UP"]: "UP", S.SC["DOWN"]: "DOWN", S.SC["LEFT"]: "LEFT", S.SC["RIGHT"]: "RIGHT",
            S.SC["x"]: "A", S.SC["z"]: "B", S.SC["a"]: "L", S.SC["s"]: "R",
            S.SC["RETURN"]: "START", S.SC["BACKSPACE"]: "SELECT", S.SC["RSHIFT"]: "SELECT"}


@dataclass
class ConnectedPad:
    instance_id: int
    handle: int
    name: str


@dataclass
class InputSnapshot:
    logical: set[str] = field(default_factory=set)       # buttons for the game
    physical: set[str] = field(default_factory=set)      # pad buttons (for menus / edges)
    menu_combo: bool = False                             # GUIDE, or BACK+START held


class InputRouter:
    def __init__(self, sdl: S.SDL, mapping: InputMapping, fallback: ControllerManager | None = None):
        self.sdl = sdl
        self.mapping = mapping
        self.fallback = fallback
        self.pads: dict[int, ConnectedPad] = {}
        self.changes: list[tuple[str, str]] = []       # ("connected"/"disconnected", name) for integrity/HUD
        self._fallback_open = False
        self.keyboard_enabled = True

    # --- hot-plug ---------------------------------------------------------
    def handle_event(self, ev: S.Event) -> None:
        if ev.type == S.CONTROLLERDEVICEADDED:
            self._open(ev.cdevice_which)
        elif ev.type == S.CONTROLLERDEVICEREMOVED:
            pad = self.pads.pop(ev.cdevice_which, None)
            if pad:
                self.sdl.GameControllerClose(pad.handle)
                self.changes.append(("disconnected", pad.name))

    def _open(self, index: int) -> None:
        if not self.sdl.IsGameController(index):
            return
        handle = self.sdl.GameControllerOpen(index)
        if not handle:
            return
        inst = self.sdl.JoystickInstanceID(self.sdl.GameControllerGetJoystick(handle))
        if inst in self.pads:
            return
        name = (self.sdl.GameControllerName(handle) or b"Controller").decode(errors="replace")
        self.pads[inst] = ConnectedPad(inst, handle, name)
        self.changes.append(("connected", name))

    def scan(self) -> None:
        for i in range(self.sdl.NumJoysticks()):
            self._open(i)
        if not self.pads and self.fallback is not None and not self._fallback_open:
            self._fallback_open = self.fallback.open() is not None

    @property
    def active_name(self) -> str | None:
        if self.pads:
            return next(iter(self.pads.values())).name
        if self._fallback_open and self.fallback and self.fallback._open:
            return self.fallback._open.device.name + " (fallback)"
        return None

    # --- polling ------------------------------------------------------------
    def _pad_state(self) -> PadState:
        st = PadState()
        if self.pads:
            pad = next(iter(self.pads.values()))
            for idx, name in S.CONTROLLER_BUTTONS.items():
                if self.sdl.GameControllerGetButton(pad.handle, idx):
                    st.buttons.add(name)
            for idx, name in S.CONTROLLER_AXES.items():
                v = self.sdl.GameControllerGetAxis(pad.handle, idx)
                st.axes[name] = max(-1.0, v / 32767.0)
        elif self._fallback_open and self.fallback and self.fallback._open:
            st = self.fallback._open.poll()
        return st

    def poll(self) -> InputSnapshot:
        pad = self._pad_state()
        logical = self.mapping.apply(pad)
        if self.keyboard_enabled:
            state = self.sdl.GetKeyboardState(None)
            for sc, name in KEYBOARD.items():
                if state[sc]:
                    logical.add(name)
        for a, b in (("LEFT", "RIGHT"), ("UP", "DOWN")):
            if a in logical and b in logical:
                logical -= {a, b}
        phys = pad.buttons | pad.raw_buttons
        return InputSnapshot(logical, phys, "GUIDE" in phys or {"BACK", "START"} <= phys)

    def close(self) -> None:
        for pad in self.pads.values():
            self.sdl.GameControllerClose(pad.handle)
        self.pads.clear()
        if self.fallback:
            self.fallback.close()

