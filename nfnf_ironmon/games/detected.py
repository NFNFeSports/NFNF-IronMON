"""Games NFNF IronMON can identify but not yet run (status "detected").

The integrated core emulates all of them and UPR ZX supports them, but no dump
was available to test randomization, emulation or a memory tracker, so runs
stay disabled until they are validated like FireRed/Red/Silver.
"""

from __future__ import annotations

from .base import GameAdapter, RomIdentity
from .gen12 import _GameBoyAdapter
from .headers import parse_gba_header


class _DetectedGb(_GameBoyAdapter):
    status = "detected"
    supported_emulators = ("nfnf-libretro",)
    supported_randomizers = ("upr-zx",)


class BlueGameAdapter(_DetectedGb):
    game_id, display_name, generation, platform = "blue", "Pokémon Blue", 1, "gb"
    rom_extensions = (".gb",)
    titles = ("POKEMON BLUE",)
    region_by_title = {"POKEMON BLUE": "USA/Europe"}


class YellowGameAdapter(_DetectedGb):
    game_id, display_name, generation, platform = "yellow", "Pokémon Yellow", 1, "gb"
    rom_extensions = (".gb", ".gbc")
    titles = ("POKEMON YELLOW",)
    region_by_title = {"POKEMON YELLOW": "USA/Europe"}


class GoldGameAdapter(_DetectedGb):
    game_id, display_name, generation, platform = "gold", "Pokémon Gold", 2, "gbc"
    rom_extensions = (".gbc", ".gb")
    titles = ("POKEMON_GLDAAUE",)
    region_by_title = {"POKEMON_GLDAAUE": "USA/Europe"}


class CrystalGameAdapter(_DetectedGb):
    game_id, display_name, generation, platform = "crystal", "Pokémon Crystal", 2, "gbc"
    rom_extensions = (".gbc",)
    titles = ("PM_CRYSTAL",)
    region_by_title = {"PM_CRYSTAL": "USA/Europe"}


class _DetectedGba(GameAdapter):
    status = "detected"
    generation, platform = 3, "gba"
    rom_extensions = (".gba",)
    supported_emulators = ("nfnf-libretro",)
    supported_randomizers = ("upr-zx",)
    codes: dict[str, str] = {}
    title = ""

    def identify(self, header: bytes, size: int) -> RomIdentity | None:
        h = parse_gba_header(header)
        if not h or h.game_code not in self.codes or not h.title.startswith(self.title):
            return None
        return RomIdentity(game_id=self.game_id, game_name=self.display_name, platform="gba", generation=3,
                           version=f"{self.codes[h.game_code]} Rev {h.version}", region=self.codes[h.game_code],
                           revision=h.version, header_title=h.title, game_code=h.game_code,
                           header_checksum_ok=h.checksum_ok, notes=["Detected only: runs not validated yet"])


class LeafGreenGameAdapter(_DetectedGba):
    game_id, display_name, title = "leafgreen", "Pokémon LeafGreen", "POKEMON LEAF"
    codes = {"BPGE": "USA/Europe"}


class RubyGameAdapter(_DetectedGba):
    game_id, display_name, title = "ruby", "Pokémon Ruby", "POKEMON RUBY"
    codes = {"AXVE": "USA/Europe"}


class SapphireGameAdapter(_DetectedGba):
    game_id, display_name, title = "sapphire", "Pokémon Sapphire", "POKEMON SAPP"
    codes = {"AXPE": "USA/Europe"}


class EmeraldGameAdapter(_DetectedGba):
    game_id, display_name, title = "emerald", "Pokémon Emerald", "POKEMON EMER"
    codes = {"BPEE": "USA/Europe"}


DETECTED_ADAPTERS = (LeafGreenGameAdapter, BlueGameAdapter, YellowGameAdapter, GoldGameAdapter,
                     CrystalGameAdapter, RubyGameAdapter, SapphireGameAdapter, EmeraldGameAdapter)
