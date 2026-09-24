"""Randomizer adapter contract.

Every randomization is recorded with: seed, randomizer name+version, settings
(+hash), source ROM hash, generated ROM hash and timestamp. Adapters declare
whether they are *deterministic* for (ROM + settings + seed); the stock
Universal Pokémon Randomizer ZX CLI is not (it has no seed argument), so the
generated ROM hash — not the seed — is the ground truth for a run.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..games import RomIdentity
from ..hashing import sha256_file, sha256_json
from ..util import utc_now, write_json

SEED_MIN, SEED_MAX = 1, 2**31 - 1  # fits a signed 32-bit int (Java-friendly)


class RandomizerError(Exception):
    pass


@dataclass(frozen=True)
class RandomizerInfo:
    name: str
    version: str
    license: str
    deterministic: bool
    #: False only for stand-ins (the mock copies the ROM unchanged).
    modifies_rom: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RandomizationRequest:
    source_rom: Path
    source_rom_sha256: str
    identity: RomIdentity
    settings: dict[str, Any]
    seed: int
    output_dir: Path
    log_dir: Path | None = None       # where tool logs go (the run's logs/ folder)
    output_name: str = "randomized"   # file stem; extension follows the source ROM


@dataclass
class RandomizationResult:
    seed: int
    randomizer: RandomizerInfo
    settings: dict[str, Any]
    settings_sha256: str
    source_rom_sha256: str
    generated_rom_sha256: str
    output_rom: Path
    timestamp: str = field(default_factory=utc_now)
    manifest: dict[str, Any] = field(default_factory=dict)
    log_path: Path | None = None
    #: Seed the randomizer really used (from its own log), when it reports one.
    actual_seed: int | None = None

    def to_dict(self, rel=lambda p: str(p)) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "requestedSeed": self.seed,
            "actualRandomizerSeed": self.actual_seed,
            "deterministic": self.randomizer.deterministic,
            "randomizer": self.randomizer.to_dict(),
            "settingsSha256": self.settings_sha256,
            "sourceRomSha256": self.source_rom_sha256,
            "generatedRomSha256": self.generated_rom_sha256,
            "outputRom": rel(self.output_rom),
            "logPath": rel(self.log_path) if self.log_path else None,
            "timestamp": self.timestamp,
            "manifest": self.manifest,
        }


class RandomizerAdapter(ABC):
    randomizer_id: str = ""

    @abstractmethod
    def info(self) -> RandomizerInfo: ...

    def is_available(self) -> tuple[bool, str]:
        return True, "built-in"

    @abstractmethod
    def detect_supported_game(self, identity: RomIdentity) -> bool: ...

    def validate_rom(self, path: Path, identity: RomIdentity) -> list[str]:
        problems = []
        if not path.is_file():
            problems.append(f"ROM not found: {path}")
        if not self.detect_supported_game(identity):
            problems.append(f"{self.randomizer_id} does not support {identity.game_name}")
        return problems

    def generate_seed(self, rng: Any = None) -> int:
        if rng is not None:
            return rng.randint(SEED_MIN, SEED_MAX)
        return secrets.randbelow(SEED_MAX) + SEED_MIN

    def effective_settings(self, request: RandomizationRequest) -> dict[str, Any]:
        """Settings as recorded/hashed for the run (adapters may add file hashes)."""
        return dict(request.settings)

    def randomize(self, request: RandomizationRequest) -> RandomizationResult:
        problems = self.validate_rom(request.source_rom, request.identity)
        if problems:
            raise RandomizerError("; ".join(problems))
        if not SEED_MIN <= request.seed <= SEED_MAX:
            raise RandomizerError(f"Seed out of range: {request.seed}")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        if request.output_dir.resolve() == request.source_rom.resolve().parent:
            raise RandomizerError("Randomizer output must not go into the source ROM's folder")
        settings = self.effective_settings(request)
        output_rom, manifest, log_path = self._randomize(request)
        if output_rom.resolve() == request.source_rom.resolve():
            raise RandomizerError("Randomizer output must never overwrite the source ROM")
        if not output_rom.is_file() or output_rom.stat().st_size == 0:
            raise RandomizerError(f"Randomizer produced no output ROM at {output_rom}")
        actual = manifest.get("actual_seed")
        return RandomizationResult(
            seed=request.seed, randomizer=self.info(), settings=settings,
            settings_sha256=sha256_json(settings),
            source_rom_sha256=request.source_rom_sha256,
            generated_rom_sha256=sha256_file(output_rom), output_rom=output_rom,
            manifest=manifest, log_path=log_path, actual_seed=actual)

    @abstractmethod
    def _randomize(self, request: RandomizationRequest) -> tuple[Path, dict[str, Any], Path | None]:
        """Produce the output ROM inside ``request.output_dir``."""

    def write_output(self, result: RandomizationResult, path: Path, rel=lambda p: str(p)) -> None:
        write_json(path, result.to_dict(rel))
