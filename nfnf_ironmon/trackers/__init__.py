"""Tracker adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (CAPABILITIES, TrackerAdapter, TrackerEvent, TrackerInstallation,
                   TrackerPreparation)
from .ironmon_tracker import IronmonTrackerAdapter
from .mock import MockTracker
from .native import NativeTrackerAdapter
from .null import NullTracker


def build_trackers(config: dict[str, Any], base: Path) -> dict[str, TrackerAdapter]:
    return {
        "ironmon-tracker": IronmonTrackerAdapter(config.get("ironmon-tracker", {}), base),
        "mock": MockTracker(),
        "none": NullTracker(),
        "nfnf": NativeTrackerAdapter(),
    }


__all__ = ["CAPABILITIES", "TrackerAdapter", "TrackerEvent", "TrackerInstallation",
           "TrackerPreparation", "IronmonTrackerAdapter", "MockTracker", "NullTracker", "NativeTrackerAdapter", "build_trackers"]
