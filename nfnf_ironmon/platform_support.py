"""Platform abstraction: OS detection, executable lookup, opening folders.

Application logic asks this module questions ("which executable names does
BizHawk use here?") instead of branching on OS or hard-coding paths.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def os_family() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def platform_tag() -> str:
    """Component platform key, e.g. ``linux-x64`` or ``windows-x64``."""
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return f"{os_family()}-{arch}"


def describe_host() -> dict:
    return {"os": os_family(), "platform": platform.platform(),
            "python": platform.python_version(), "machine": platform.machine()}


def find_executable(candidates: list[str], configured: str | Path | None = None,
                    base: Path | None = None) -> Path | None:
    """Resolve an executable from an explicit configured path, then PATH.

    ``configured`` may point at the executable itself or at the folder that
    contains it; relative paths are resolved against ``base`` (the app home).
    """
    if configured:
        p = Path(configured).expanduser()
        if not p.is_absolute() and base is not None:
            p = base / p
        if p.is_file():
            return p.resolve()
        if p.is_dir():
            for name in candidates:
                if (p / name).is_file():
                    return (p / name).resolve()
        return None
    for name in candidates:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def pid_alive(pid: int | None) -> bool:
    """Is a process with this id running? (used to detect crashed game sessions)"""
    if not pid or pid <= 0:
        return False
    if os_family() == "windows":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def open_in_file_manager(path: Path) -> None:
    fam = os_family()
    if fam == "windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif fam == "macos":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
