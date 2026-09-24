"""Integrated emulator engine: mGBA core hosted in-process via libretro.

Status (Phase 2): PROTOTYPE — headless. Loading a run ROM, running frames,
video capture, input injection, memory-map reads, save RAM and save states
work and are tested. Interactive play (window, audio, real-time pacing,
live controller) is Phase 3, so ``launch()`` refuses rather than pretending.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..components import ComponentManager
from .base import EmulatorAdapter, EmulatorError, EmulatorInstallation, EmulatorSession, LaunchRequest
from .libretro import MEMORY_SAVE_RAM, LibretroCore, write_png

CORE_COMPONENT = "mgba-libretro"


@dataclass
class SmokeResult:
    core: str
    core_version: str
    frames: int
    seconds: float
    fps: float
    width: int
    height: int
    save_ram_bytes: int
    state_bytes: int
    header_via_bus: str
    screenshot: str | None = None
    distinct_colors: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class IntegratedEmulatorAdapter(EmulatorAdapter):
    emulator_id = "nfnf-libretro"
    display_name = "NFNF integrated emulator (mGBA core via libretro)"
    platforms = ("gba", "gb", "gbc")
    status = "PROTOTYPE"
    interactive = False   # headless until Phase 3

    def __init__(self, config: dict[str, Any] | None = None, base: Path | None = None,
                 components: ComponentManager | None = None):
        super().__init__(config, base)
        self.components = components

    def core_path(self) -> Path | None:
        custom = self.config.get("core")
        if custom:
            p = Path(custom)
            p = p if p.is_absolute() or not self.base else self.base / p
            return p if p.is_file() else None
        return self.components.resolve(CORE_COMPONENT) if self.components else None

    def detect_installation(self) -> EmulatorInstallation:
        core = self.core_path()
        if not core:
            return EmulatorInstallation(self.emulator_id, False, notes=[
                "mGBA libretro core not bundled (python3 -m nfnf_ironmon components fetch)"])
        return EmulatorInstallation(self.emulator_id, True, core,
                                    ["headless prototype; interactive play is Phase 3"])

    def capabilities(self) -> dict[str, Any]:
        return {"status": self.status, "embedded": True, "platforms": list(self.platforms),
                "memory_access": True, "save_ram": True, "save_states": True,
                "input": "NFNF ControllerManager (logical buttons)", "window": False,
                "audio_output": False, "scripting": "Python (in-process)"}

    def build_launch_command(self, request: LaunchRequest) -> list[str]:
        return ["<in-process libretro>", str(self.core_path()), str(request.rom_path)]

    def launch(self, request: LaunchRequest) -> EmulatorSession:
        raise EmulatorError("Interactive play in the integrated emulator arrives in Phase 3 "
                            "(window/audio/pacing). Use `emulator smoke <run>` for the headless test.")

    def smoke_test(self, rom: Path, work_dir: Path, frames: int = 600,
                   screenshot: Path | None = None, press_start: bool = True) -> SmokeResult:
        core_path = self.core_path()
        if not core_path:
            raise EmulatorError("mGBA libretro core not available")
        (work_dir / "system").mkdir(parents=True, exist_ok=True)
        (work_dir / "saves").mkdir(parents=True, exist_ok=True)
        with LibretroCore(core_path, work_dir / "system", work_dir / "saves") as core:
            core.load_game(rom)
            t0 = time.perf_counter()
            core.run_frames(frames)
            if press_start:  # walk past the title screen to prove input reaches the game
                for _ in range(6):
                    core.run_frames(10, {"START"})
                    core.run_frames(50)
                    core.run_frames(10, {"A"})
                    core.run_frames(50)
            dt = time.perf_counter() - t0
            info = core.info()
            frame = core.last_frame
            if frame is None:
                raise EmulatorError("Core produced no video frames")
            colors = len({frame.rgb[i:i + 3] for i in range(0, len(frame.rgb), 3)})
            if screenshot:
                write_png(screenshot, frame, 2)
            try:
                header = core.read_bus(0x080000A0, 16).decode("ascii", "replace")
            except Exception as exc:  # noqa: BLE001
                header = f"<unavailable: {exc}>"
            return SmokeResult(core=info.library_name, core_version=info.library_version,
                               frames=core.frames_run, seconds=round(dt, 3),
                               fps=round(core.frames_run / dt, 1) if dt else 0.0,
                               width=frame.width, height=frame.height,
                               save_ram_bytes=len(core.memory(MEMORY_SAVE_RAM)),
                               state_bytes=len(core.save_state()), header_via_bus=header,
                               screenshot=str(screenshot) if screenshot else None,
                               distinct_colors=colors)
