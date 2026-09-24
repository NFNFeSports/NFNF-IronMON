"""mGBA adapter — MPL-2.0, Windows/macOS/Linux, Lua scripting since 0.10."""

from __future__ import annotations

from ..platform_support import find_executable, os_family
from .base import EmulatorAdapter, EmulatorInstallation, LaunchRequest


class MgbaAdapter(EmulatorAdapter):
    emulator_id = "mgba"
    display_name = "mGBA"
    platforms = ("gba", "gb", "gbc")

    @property
    def supports_lua_on_command_line(self) -> bool:  # type: ignore[override]
        # Loading a script from the command line has not been verified for the
        # installed mGBA version, so it is opt-in via emulators.mgba.script_flag.
        return bool(self.config.get("script_flag"))

    @staticmethod
    def executable_names() -> list[str]:
        return ["mGBA.exe"] if os_family() == "windows" else ["mgba-qt", "mgba"]

    def detect_installation(self) -> EmulatorInstallation:
        exe = find_executable(self.executable_names(), self.config.get("path"), self.base)
        if not exe:
            return EmulatorInstallation(self.emulator_id, False, notes=[
                "Set emulators.mgba.path or install mGBA 0.10+ on PATH"])
        return EmulatorInstallation(self.emulator_id, True, exe, [
            "Load the tracker via Tools > Scripting unless script_flag is configured"])

    def build_launch_command(self, request: LaunchRequest) -> list[str]:
        cmd = [str(self.detect_installation().path)]
        flag = self.config.get("script_flag")
        if flag:
            for s in request.lua_scripts:
                cmd += [flag, str(s.resolve())]
        if request.rom_path:
            cmd.append(str(request.rom_path.resolve()))
        return cmd
