"""Pokémon FireRed adapter — the Phase 1 reference implementation."""

from __future__ import annotations

from typing import Any

from .base import GameAdapter, RomIdentity, RunContext
from .headers import parse_gba_header

# Game code -> (region label, language)
FIRERED_CODES = {
    "BPRE": ("USA/Europe", "English"),
    "BPRJ": ("Japan", "Japanese"),
    "BPRF": ("France", "French"),
    "BPRD": ("Germany", "German"),
    "BPRS": ("Spain", "Spanish"),
    "BPRI": ("Italy", "Italian"),
}


class FireRedGameAdapter(GameAdapter):
    game_id = "firered"
    display_name = "Pokémon FireRed"
    generation = 3
    platform = "gba"
    rom_extensions = (".gba",)
    status = "supported"
    supported_emulators = ("nfnf-libretro", "bizhawk", "mgba", "mock")
    supported_trackers = ("ironmon-tracker", "mock", "none")
    supported_randomizers = ("upr-zx", "mock")
    known_dumps = {
        # Verified locally on 2026-09-24 against the user's No-Intro-named dump.
        "dd5945db9b930750cb39d00c84da8571feebf417": "No-Intro: Pokemon - FireRed Version (USA, Europe) (Rev 1)",
        # Reference value from No-Intro; not verified against a local file.
        "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc": "No-Intro: Pokemon - FireRed Version (USA, Europe)",
    }

    def identify(self, header: bytes, size: int) -> RomIdentity | None:
        h = parse_gba_header(header)
        if not h or h.game_code not in FIRERED_CODES or not h.title.startswith("POKEMON FIRE"):
            return None
        region, language = FIRERED_CODES[h.game_code]
        notes = []
        if size != 16 * 1024 * 1024:
            notes.append(f"Unexpected ROM size {size} bytes (retail FireRed is 16 MiB)")
        if not h.checksum_ok:
            notes.append("GBA header checksum mismatch — possibly a hacked or corrupted ROM")
        if h.game_code != "BPRE":
            notes.append("Non-English FireRed: tracker support varies by language")
        return RomIdentity(
            game_id=self.game_id, game_name=self.display_name, platform=self.platform,
            generation=self.generation,
            version=f"{region} Rev {h.version} ({language})",
            region=region, revision=h.version, header_title=h.title,
            game_code=h.game_code, header_checksum_ok=h.checksum_ok, notes=notes)

    def emulator_config(self, emulator_id: str) -> dict[str, Any]:
        if emulator_id == "bizhawk":
            return {"core": "mGBA", "min_version": "2.8",
                    "notes": "Ironmon-Tracker supports BizHawk 2.8+ (Windows/Linux)"}
        if emulator_id == "mgba":
            return {"min_version": "0.10.0",
                    "notes": "Tracker runs text-only in mGBA (no drawing API)"}
        return {}

    def tracker_config(self, tracker_id: str) -> dict[str, Any]:
        if tracker_id == "ironmon-tracker":
            return {"entry_script": "Ironmon-Tracker.lua", "supported_codes": ["BPRE"]}
        return {}

    def initialize_run(self, ctx: RunContext) -> dict[str, Any]:
        super().initialize_run(ctx)
        return {
            "game_code": ctx.identity.game_code,
            "revision": ctx.identity.revision,
            "emulator": self.emulator_config(ctx.emulator_id),
            "tracker": self.tracker_config(ctx.tracker_id),
        }
