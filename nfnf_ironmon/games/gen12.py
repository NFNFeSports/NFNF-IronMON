"""Gen 1 (Red) and Gen 2 (Silver) game adapters.

Runs use the integrated mGBA core (GB/GBC), the NFNF memory tracker
(nfnf_ironmon/tracker/gen12.py) and UPR ZX with the official IronMON settings.
"""

from __future__ import annotations

from .base import GameAdapter, RomIdentity
from .headers import parse_gb_header


class _GameBoyAdapter(GameAdapter):
    titles: tuple[str, ...] = ()
    region_by_title: dict[str, str] = {}

    def identify(self, header: bytes, size: int) -> RomIdentity | None:
        h = parse_gb_header(header)
        if not h or h.title not in self.titles:
            return None
        notes = [] if h.checksum_ok else ["Game Boy header checksum mismatch"]
        region = self.region_by_title.get(h.title, "Unknown")
        return RomIdentity(
            game_id=self.game_id, game_name=self.display_name, platform=self.platform,
            generation=self.generation, version=f"{region} Rev {h.version}", region=region,
            revision=h.version, header_title=h.title, game_code=h.title,
            header_checksum_ok=h.checksum_ok, notes=notes)


class RedGameAdapter(_GameBoyAdapter):
    game_id = "red"
    display_name = "Pokémon Red"
    generation = 1
    platform = "gb"
    rom_extensions = (".gb",)
    status = "supported"
    supported_emulators = ("nfnf-libretro", "mock")
    supported_trackers = ("nfnf", "none", "mock")
    supported_randomizers = ("upr-zx", "mock")
    titles = ("POKEMON RED",)
    region_by_title = {"POKEMON RED": "USA/Europe"}
    known_dumps = {
        # Verified locally on 2026-09-24.
        "ea9bcae617fdf159b045185467ae58b2e4a48b9a": "No-Intro: Pokemon - Red Version (USA, Europe)",
    }


class SilverGameAdapter(_GameBoyAdapter):
    game_id = "silver"
    display_name = "Pokémon Silver"
    generation = 2
    platform = "gbc"
    rom_extensions = (".gbc", ".gb")
    status = "supported"
    supported_emulators = ("nfnf-libretro", "mock")
    supported_trackers = ("nfnf", "none", "mock")
    supported_randomizers = ("upr-zx", "mock")
    # 11-char title + 4-char manufacturer code share the 16-byte title area.
    titles = ("POKEMON_SLVAAXE",)
    region_by_title = {"POKEMON_SLVAAXE": "USA/Europe"}
    known_dumps = {
        # Verified locally on 2026-09-24.
        "49b163f7e57702bc939d642a18f591de55d92dae":
            "No-Intro: Pokemon - Silver Version (USA, Europe) (SGB Enhanced) (GB Compatible)",
    }
