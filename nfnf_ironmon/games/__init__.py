"""Game adapter registry."""

from __future__ import annotations

from pathlib import Path

from .base import GameAdapter, GameNotSupportedError, RomIdentity, RunContext
from .firered import FireRedGameAdapter
from .detected import DETECTED_ADAPTERS
from .gen12 import RedGameAdapter, SilverGameAdapter


class GameRegistry:
    def __init__(self, adapters: list[GameAdapter] | None = None):
        self._adapters: dict[str, GameAdapter] = {}
        for a in adapters if adapters is not None else default_game_adapters():
            self.register(a)

    def register(self, adapter: GameAdapter) -> None:
        if adapter.game_id in self._adapters:
            raise ValueError(f"Duplicate game adapter {adapter.game_id}")
        self._adapters[adapter.game_id] = adapter

    def get(self, game_id: str) -> GameAdapter:
        try:
            return self._adapters[game_id]
        except KeyError:
            raise KeyError(f"Unknown game {game_id!r}. Known: {', '.join(self._adapters)}") from None

    def all(self) -> list[GameAdapter]:
        return list(self._adapters.values())

    def identify_file(self, path: Path | str, sha1: str | None = None) -> RomIdentity | None:
        for adapter in self._adapters.values():
            ident = adapter.identify_file(path, sha1)
            if ident:
                return ident
        return None


def default_game_adapters() -> list[GameAdapter]:
    return [FireRedGameAdapter(), RedGameAdapter(), SilverGameAdapter(), *(a() for a in DETECTED_ADAPTERS)]


__all__ = ["GameAdapter", "GameNotSupportedError", "GameRegistry", "RomIdentity", "RunContext",
           "FireRedGameAdapter", "RedGameAdapter", "SilverGameAdapter"]
