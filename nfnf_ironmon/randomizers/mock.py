"""Deterministic mock randomizer (Phase 1 stand-in).

It does NOT alter game data: the output ROM is a byte-for-byte copy of the
source, so ``generatedRomSha256 == sourceRomSha256``. What it does produce is a
deterministic *manifest* (fake starter picks) derived from ROM + settings +
seed, which lets the whole pipeline and its determinism guarantees be tested
without Java or a real randomizer.
"""

from __future__ import annotations

import hashlib
import random
import shutil
from pathlib import Path
from typing import Any

from ..games import RomIdentity
from ..hashing import canonical_json
from .base import RandomizationRequest, RandomizerAdapter, RandomizerInfo


class MockRandomizer(RandomizerAdapter):
    randomizer_id = "mock"

    def info(self) -> RandomizerInfo:
        return RandomizerInfo(name="NFNF Mock Randomizer", version="1.0", license="project",
                              deterministic=True, modifies_rom=False)

    def detect_supported_game(self, identity: RomIdentity) -> bool:
        return True

    def _randomize(self, request: RandomizationRequest) -> tuple[Path, dict[str, Any], Path | None]:
        material = f"{request.source_rom_sha256}|{canonical_json(request.settings)}|{request.seed}"
        rng = random.Random(int.from_bytes(hashlib.sha256(material.encode()).digest(), "big"))
        species_max = int(request.settings.get("species_max", 386))
        manifest = {
            "mock": True,
            "note": "Mock output: ROM bytes are an unmodified copy of the source.",
            "starters": [rng.randint(1, species_max) for _ in range(3)],
            "derivation": hashlib.sha256(material.encode()).hexdigest(),
            "actual_seed": request.seed,   # the mock uses exactly the requested seed
        }
        out = request.output_dir / f"{request.output_name}{request.source_rom.suffix}"
        shutil.copyfile(request.source_rom, out)
        return out, manifest, None
