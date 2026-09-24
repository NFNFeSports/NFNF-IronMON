"""User configuration (``config/settings.json``).

Defaults are merged under whatever the user wrote, so new keys can be added in
later versions without breaking old config files. External tool locations are
*configured*, never assumed; relative paths resolve against the app home.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .paths import AppPaths
from .util import read_json, write_json

DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "default_game": "firered",
    "default_ruleset": "standard-ironmon",
    # "auto" = the game's real randomizer profile (never silently the mock).
    "default_randomizer_profile": "auto",
    # Integrated engine. Until interactive play exists (Phase 3), runs stop at READY.
    "emulator": "nfnf-libretro",
    # Built-in memory tracker; games without a reader report gameplay as UNKNOWN.
    "tracker": "nfnf",
    "auto_new_run_on_failure": False,
    "controller": {"mapping": "xbox-gba-labels", "device": None},
    "emulators": {
        "nfnf-libretro": {"core": None},
        "bizhawk": {"path": None},
        "mgba": {"path": None, "script_flag": None},
    },
    "trackers": {
        "ironmon-tracker": {"path": None},
    },
    "randomizers": {
        "upr-zx": {"jar": None, "java": None, "max_heap": "4096M"},
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class AppConfig:
    def __init__(self, paths: AppPaths, data: dict[str, Any]):
        self.paths = paths
        self.data = data

    @classmethod
    def load(cls, paths: AppPaths) -> "AppConfig":
        user = read_json(paths.config_file) if paths.config_file.exists() else {}
        return cls(paths, _merge(DEFAULTS, user))

    def save(self, overrides: dict[str, Any] | None = None) -> None:
        """Write user overrides only, so improved defaults reach existing installs."""
        write_json(self.paths.config_file, overrides if overrides is not None else {"schema": 1})

    def set_override(self, keys: tuple[str, ...], value: Any) -> None:
        """Persist one user override (e.g. ("controller", "mapping")) and apply it."""
        user = read_json(self.paths.config_file) if self.paths.config_file.exists() else {"schema": 1}
        node, live = user, self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
            live = live.setdefault(k, {})
        node[keys[-1]] = value
        live[keys[-1]] = value
        self.save(user)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def section(self, *keys: str) -> dict[str, Any]:
        node: Any = self.data
        for k in keys:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        return node if isinstance(node, dict) else {}

    def resolve_path(self, value: str | None) -> Path | None:
        return self.paths.abs(value) if value else None
