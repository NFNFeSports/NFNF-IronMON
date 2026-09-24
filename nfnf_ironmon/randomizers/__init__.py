"""Randomizer adapters and randomizer profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..hashing import sha256_json
from ..util import read_json
from .base import (RandomizationRequest, RandomizationResult, RandomizerAdapter, RandomizerError,
                   RandomizerInfo)
from .mock import MockRandomizer
from .upr_zx import UprZxRandomizer


@dataclass
class RandomizerProfile:
    """A named combination of randomizer + settings for one game (JSON file)."""
    id: str
    name: str
    game_id: str
    randomizer: str
    settings: dict[str, Any]
    path: Path

    @property
    def settings_sha256(self) -> str:
        return sha256_json(self.settings)


class ProfileRepository:
    def __init__(self, directory: Path):
        self.directory = directory

    def list(self) -> list[RandomizerProfile]:
        return [self.load(p.stem) for p in sorted(self.directory.glob("*.json"))]

    def load(self, profile_id: str) -> RandomizerProfile:
        path = self.directory / f"{profile_id}.json"
        if not path.is_file():
            raise KeyError(f"Randomizer profile not found: {profile_id}")
        data = read_json(path)
        for key in ("id", "game_id", "randomizer", "settings"):
            if key not in data:
                raise ValueError(f"Profile {path.name} is missing {key!r}")
        if data["id"] != profile_id:
            raise ValueError(f"Profile id {data['id']!r} does not match file name {path.name}")
        return RandomizerProfile(id=data["id"], name=data.get("name", data["id"]),
                                 game_id=data["game_id"], randomizer=data["randomizer"],
                                 settings=data["settings"], path=path)


class RandomizerRegistry:
    def __init__(self, adapters: list[RandomizerAdapter]):
        self._adapters = {a.randomizer_id: a for a in adapters}

    def get(self, randomizer_id: str) -> RandomizerAdapter:
        try:
            return self._adapters[randomizer_id]
        except KeyError:
            raise KeyError(f"Unknown randomizer {randomizer_id!r}") from None

    def all(self) -> list[RandomizerAdapter]:
        return list(self._adapters.values())


__all__ = ["MockRandomizer", "UprZxRandomizer", "RandomizerAdapter", "RandomizerError",
           "RandomizerInfo", "RandomizationRequest", "RandomizationResult", "RandomizerProfile",
           "ProfileRepository", "RandomizerRegistry"]
