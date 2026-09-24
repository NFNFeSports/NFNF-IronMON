"""InputMapping: physical (Xbox layout) → logical NFNF buttons, loaded from JSON.

Mapping file (``input-mappings/<id>.json``)::

    {
      "id": "xbox-gba-labels",
      "name": "Xbox → GBA (button labels match)",
      "buttons": {"A": ["A"], "B": ["B"], "LB": ["L"], ...},   # physical -> logical list
      "axes": {"LX": {"negative": "LEFT", "positive": "RIGHT"}, ...},
      "deadzone": 0.35
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util import read_json
from .base import LOGICAL_BUTTONS, PHYSICAL_AXES, PHYSICAL_BUTTONS, PadState


class MappingError(ValueError):
    pass


@dataclass
class InputMapping:
    id: str
    name: str
    buttons: dict[str, list[str]]
    axes: dict[str, dict[str, str]]
    deadzone: float = 0.35
    trigger_threshold: float = 0.5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InputMapping":
        for key in ("id", "name", "buttons"):
            if key not in data:
                raise MappingError(f"Mapping missing {key!r}")
        buttons = {}
        for phys, logical in data["buttons"].items():
            if phys not in PHYSICAL_BUTTONS and not phys.startswith("BUTTON_"):
                raise MappingError(f"Unknown physical button {phys!r}")
            logical = [logical] if isinstance(logical, str) else list(logical)
            bad = [l for l in logical if l not in LOGICAL_BUTTONS]
            if bad:
                raise MappingError(f"Unknown logical button(s) {bad} for {phys}")
            buttons[phys] = logical
        axes = {}
        for axis, dirs in data.get("axes", {}).items():
            if axis not in PHYSICAL_AXES:
                raise MappingError(f"Unknown axis {axis!r}")
            for side in ("negative", "positive"):
                if side in dirs and dirs[side] not in LOGICAL_BUTTONS:
                    raise MappingError(f"Unknown logical button {dirs[side]!r} on axis {axis}")
            axes[axis] = dict(dirs)
        dz = float(data.get("deadzone", 0.35))
        if not 0.0 <= dz < 1.0:
            raise MappingError("deadzone must be in [0, 1)")
        return cls(data["id"], data["name"], buttons, axes, dz,
                   float(data.get("trigger_threshold", 0.5)))

    @classmethod
    def load(cls, path: Path) -> "InputMapping":
        m = cls.from_dict(read_json(path))
        if m.id != Path(path).stem:
            raise MappingError(f"Mapping id {m.id!r} does not match file name {Path(path).name}")
        return m

    def apply(self, state: PadState) -> set[str]:
        pressed: set[str] = set()
        for phys in state.buttons | state.raw_buttons:
            pressed.update(self.buttons.get(phys, ()))
        for axis, value in state.axes.items():
            dirs = self.axes.get(axis)
            if not dirs:
                continue
            threshold = self.trigger_threshold if axis in ("LT", "RT") else self.deadzone
            if value <= -threshold and "negative" in dirs:
                pressed.add(dirs["negative"])
            elif value >= threshold and "positive" in dirs:
                pressed.add(dirs["positive"])
        # opposite directions cancel (a worn stick must not press LEFT+RIGHT)
        for a, b in (("LEFT", "RIGHT"), ("UP", "DOWN")):
            if a in pressed and b in pressed:
                pressed -= {a, b}
        return pressed

    def describe(self) -> list[tuple[str, str]]:
        rows = [(p, ", ".join(l)) for p, l in self.buttons.items()]
        rows += [(f"{a} −/+", f"{d.get('negative', '-')}/{d.get('positive', '-')}") for a, d in self.axes.items()]
        return rows


class MappingRepository:
    def __init__(self, directory: Path):
        self.directory = directory

    def list(self) -> list[InputMapping]:
        return [InputMapping.load(p) for p in sorted(self.directory.glob("*.json"))]

    def load(self, mapping_id: str) -> InputMapping:
        path = self.directory / f"{mapping_id}.json"
        if not path.is_file():
            raise KeyError(f"Input mapping not found: {mapping_id}")
        return InputMapping.load(path)
