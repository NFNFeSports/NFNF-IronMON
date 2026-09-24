"""Emulator adapter contract.

Emulators are external programs the user installs; NFNF IronMON only detects,
launches, watches and closes them. No emulator or game is bundled.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import utc_now


class EmulatorError(Exception):
    pass


@dataclass
class EmulatorInstallation:
    emulator_id: str
    found: bool
    path: Path | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"emulator": self.emulator_id, "found": self.found,
                "path": str(self.path) if self.path else None, "notes": self.notes}


@dataclass
class LaunchRequest:
    rom_path: Path | None = None
    lua_scripts: list[Path] = field(default_factory=list)
    working_dir: Path | None = None
    log_file: Path | None = None


@dataclass
class EmulatorSession:
    emulator_id: str
    command: list[str]
    started_at: str = field(default_factory=utc_now)
    process: subprocess.Popen | None = None
    rom_path: Path | None = None
    running: bool = True

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None


class EmulatorAdapter(ABC):
    emulator_id: str = ""
    display_name: str = ""
    platforms: tuple[str, ...] = ()        # ROM platforms it can run: gba, gb, gbc
    supports_lua_on_command_line = False
    #: EXTERNAL = separate program the user installs (dev/legacy path);
    #: PROTOTYPE / READY for the integrated engine; MOCK for tests.
    status: str = "EXTERNAL"
    #: Can ``launch()`` start interactive play right now?
    interactive: bool = True

    def __init__(self, config: dict[str, Any] | None = None, base: Path | None = None):
        self.config = config or {}
        self.base = base

    @abstractmethod
    def detect_installation(self) -> EmulatorInstallation: ...

    @abstractmethod
    def build_launch_command(self, request: LaunchRequest) -> list[str]: ...

    def launch(self, request: LaunchRequest) -> EmulatorSession:
        """Launch the emulator (optionally with a ROM and Lua scripts)."""
        inst = self.detect_installation()
        if not inst.found:
            raise EmulatorError(f"{self.display_name} not found: {'; '.join(inst.notes)}")
        cmd = self.build_launch_command(request)
        log = open(request.log_file, "ab") if request.log_file else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(cmd, cwd=str(request.working_dir or inst.path.parent),
                                    stdout=log, stderr=subprocess.STDOUT)
        finally:
            if request.log_file:
                log.close()
        return EmulatorSession(self.emulator_id, cmd, process=proc, rom_path=request.rom_path)

    def launch_game(self) -> EmulatorSession:
        return self.launch(LaunchRequest())

    def launch_with_rom(self, rom_path: Path, lua_scripts: list[Path] | None = None,
                        log_file: Path | None = None) -> EmulatorSession:
        return self.launch(LaunchRequest(rom_path=rom_path, lua_scripts=lua_scripts or [],
                                         log_file=log_file))

    def is_running(self, session: EmulatorSession) -> bool:
        if session.process is None:
            return session.running
        return session.process.poll() is None

    def close(self, session: EmulatorSession, timeout: float = 10.0) -> None:
        if session.process and session.process.poll() is None:
            session.process.terminate()
            try:
                session.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                session.process.kill()
        session.running = False

    def capabilities(self) -> dict[str, Any]:
        """What NFNF can do through this adapter (not what the emulator can do on its own)."""
        return {"status": self.status, "embedded": False, "platforms": list(self.platforms),
                "memory_access": False, "save_ram": False, "save_states": False,
                "input": "emulator's own configuration", "window": True,
                "audio_output": True, "scripting": "Lua inside the emulator process"}

    def connect_tracker(self, session: EmulatorSession, scripts: list[Path]) -> dict[str, Any]:
        """How the tracker gets attached. Default: scripts were passed at launch."""
        return {"method": "launch-argument" if self.supports_lua_on_command_line else "manual",
                "scripts": [str(s) for s in scripts]}
