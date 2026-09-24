"""Controller abstractions.

    USB / XInput device
          │  backend (platform specific)
          ▼
    PadState      physical, Xbox-layout names ("A", "LB", "DPAD_UP", axes "LX"...)
          │  InputMapping (JSON, user configurable)
          ▼
    logical NFNF buttons ("A", "B", "L", "R", "START", "SELECT", "UP", ...)
          │
          ▼
    emulator input (libretro joypad) — never the emulator's own controller config
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: Physical buttons in Xbox layout. Backends translate native codes into these.
PHYSICAL_BUTTONS = ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "BACK", "START", "GUIDE",
                    "LS", "RS", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT")
#: Physical axes, normalised to -1.0 .. 1.0 (triggers 0.0 .. 1.0). Y axes: down is positive.
PHYSICAL_AXES = ("LX", "LY", "RX", "RY", "LT", "RT")

#: Logical buttons NFNF understands (superset of GB/GBC/GBA pads).
LOGICAL_BUTTONS = ("A", "B", "X", "Y", "L", "R", "START", "SELECT", "UP", "DOWN", "LEFT", "RIGHT")


@dataclass
class ControllerDevice:
    id: str                    # stable within a session, e.g. "linux-js:/dev/input/js0"
    name: str
    backend: str
    kind: str                  # "xinput", "gamepad", "unknown"
    connected: bool = True
    is_gamepad: bool = True
    vendor_id: str | None = None
    product_id: str | None = None
    driver: str | None = None
    buttons: int | None = None
    axes: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("id", "name", "backend", "kind", "connected",
                                              "is_gamepad", "vendor_id", "product_id", "driver",
                                              "buttons", "axes", "notes")}


@dataclass
class PadState:
    buttons: set[str] = field(default_factory=set)       # pressed physical buttons
    axes: dict[str, float] = field(default_factory=dict)
    raw_buttons: set[str] = field(default_factory=set)   # unmapped native ids, e.g. "BUTTON_12"


class ControllerBackend(ABC):
    backend_id: str = ""
    display_name: str = ""
    #: READY = implemented and usable here; UNAVAILABLE = not on this OS / missing library;
    #: PLANNED = designed, not implemented.
    status: str = "PLANNED"

    def availability(self) -> tuple[str, str]:
        return self.status, ""

    @abstractmethod
    def list_devices(self) -> list[ControllerDevice]: ...

    @abstractmethod
    def open(self, device: ControllerDevice) -> "OpenController": ...


class OpenController(ABC):
    device: ControllerDevice

    @abstractmethod
    def poll(self) -> PadState:
        """Latest state; never blocks."""

    def close(self) -> None:
        pass
