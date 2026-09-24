"""BizHawk (EmuHawk) adapter — MIT licensed, Windows + Linux (Mono)."""

from __future__ import annotations

from ..platform_support import find_executable, os_family
from .base import EmulatorAdapter, EmulatorInstallation, LaunchRequest


class BizHawkAdapter(EmulatorAdapter):
    emulator_id = "bizhawk"
    display_name = "BizHawk"
    platforms = ("gba", "gb", "gbc")
    supports_lua_on_command_line = True

    @staticmethod
    def executable_names() -> list[str]:
        # Linux releases ship a launcher script that runs EmuHawk under Mono.
        return ["EmuHawk.exe"] if os_family() == "windows" else ["EmuHawkMono.sh"]

    def detect_installation(self) -> EmulatorInstallation:
        exe = find_executable(self.executable_names(), self.config.get("path"), self.base)
        if not exe:
            return EmulatorInstallation(self.emulator_id, False, notes=[
                "Set emulators.bizhawk.path to the BizHawk folder or EmuHawk executable"])
        notes = []
        if os_family() == "linux":
            notes.append("Linux build requires Mono and Lua 5.4 runtime libraries")
        return EmulatorInstallation(self.emulator_id, True, exe, notes)

    def build_launch_command(self, request: LaunchRequest) -> list[str]:
        exe = self.detect_installation().path
        cmd = [str(exe)]
        # BizHawk on Linux needs absolute paths.
        cmd += [f"--lua={s.resolve()}" for s in request.lua_scripts]
        if request.rom_path:
            cmd.append(str(request.rom_path.resolve()))
        return cmd
