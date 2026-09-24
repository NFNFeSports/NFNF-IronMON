"""Emulator adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (EmulatorAdapter, EmulatorError, EmulatorInstallation, EmulatorSession,
                   LaunchRequest)
from .bizhawk import BizHawkAdapter
from .integrated import IntegratedEmulatorAdapter
from .mgba import MgbaAdapter
from .mock import MockEmulator


def build_emulators(config: dict[str, Any], base: Path, components=None) -> dict[str, EmulatorAdapter]:
    return {
        "nfnf-libretro": IntegratedEmulatorAdapter(config.get("nfnf-libretro", {}), base, components),
        "bizhawk": BizHawkAdapter(config.get("bizhawk", {}), base),
        "mgba": MgbaAdapter(config.get("mgba", {}), base),
        "mock": MockEmulator(),
    }


__all__ = ["EmulatorAdapter", "EmulatorError", "EmulatorInstallation", "EmulatorSession",
           "LaunchRequest", "BizHawkAdapter", "IntegratedEmulatorAdapter", "MgbaAdapter", "MockEmulator", "build_emulators"]
