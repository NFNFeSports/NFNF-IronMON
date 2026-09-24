"""Cartridge header parsing for GBA and Game Boy / Game Boy Color ROMs.

Only header bytes are read; ROM contents are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GbaHeader:
    title: str
    game_code: str
    maker_code: str
    version: int
    checksum_ok: bool


def parse_gba_header(data: bytes) -> GbaHeader | None:
    if len(data) < 0xC0 or data[0xB2] != 0x96:  # 0x96 is a fixed header value
        return None
    chk = 0
    for b in data[0xA0:0xBD]:
        chk = (chk - b) & 0xFF
    chk = (chk - 0x19) & 0xFF
    return GbaHeader(
        title=data[0xA0:0xAC].rstrip(b"\x00").decode("ascii", "replace"),
        game_code=data[0xAC:0xB0].decode("ascii", "replace"),
        maker_code=data[0xB0:0xB2].decode("ascii", "replace"),
        version=data[0xBC],
        checksum_ok=(chk == data[0xBD]),
    )


@dataclass(frozen=True)
class GbHeader:
    title: str          # 15-byte title area, NULs stripped
    cgb_flag: int
    version: int
    checksum_ok: bool


def parse_gb_header(data: bytes) -> GbHeader | None:
    if len(data) < 0x150:
        return None
    chk = 0
    for b in data[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    return GbHeader(
        # 0x143 is the CGB flag on colour-era carts, so the title stops before it.
        title=data[0x134:0x143].split(b"\x00")[0].decode("ascii", "replace"),
        cgb_flag=data[0x143],
        version=data[0x14C],
        checksum_ok=(chk == data[0x14D]),
    )


def gba_header_checksum(data: bytes) -> int:
    """Checksum for bytes 0xA0..0xBC (used by tests to build synthetic headers)."""
    chk = 0
    for b in data[0xA0:0xBD]:
        chk = (chk - b) & 0xFF
    return (chk - 0x19) & 0xFF


def gb_header_checksum(data: bytes) -> int:
    chk = 0
    for b in data[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    return chk
