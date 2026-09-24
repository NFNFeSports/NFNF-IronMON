"""In-process mock emulator: lets the full run pipeline execute with no emulator."""

from __future__ import annotations

from pathlib import Path

from .base import EmulatorAdapter, EmulatorInstallation, EmulatorSession, LaunchRequest


class MockEmulator(EmulatorAdapter):
    emulator_id = "mock"
    display_name = "Mock Emulator"
    platforms = ("gba", "gb", "gbc")
    supports_lua_on_command_line = True
    status = "MOCK"

    def detect_installation(self) -> EmulatorInstallation:
        return EmulatorInstallation(self.emulator_id, True, Path("<mock>"), ["No real process"])

    def build_launch_command(self, request: LaunchRequest) -> list[str]:
        cmd = ["<mock-emulator>"] + [f"--lua={s}" for s in request.lua_scripts]
        return cmd + ([str(request.rom_path)] if request.rom_path else [])

    def launch(self, request: LaunchRequest) -> EmulatorSession:
        return EmulatorSession(self.emulator_id, self.build_launch_command(request),
                               rom_path=request.rom_path)
