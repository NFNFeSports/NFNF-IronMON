"""Universal Pokémon Randomizer ZX settings files (``.rnqs``) — validation only.

Format, as implemented by UPR ZX 4.6.1 ``Settings.write`` / ``Settings.read``:

    int32 big-endian   settings version  (e.g. 322 = UPR ZX 4.6.1)
    int32 big-endian   length N of the settings string
    N bytes UTF-8      Base64 settings data

Decoded settings data: the byte at offset 51 is the length L of an ASCII ROM
name that follows it; the last 8 bytes are two big-endian int32s — a CRC32 of
everything before them, then a checksum of UPR's custom-names file.

UPR refuses versions newer than its own and versions whose top byte is in
1..172 ("too old to update"); older-but-supported versions are upgraded on
load with a warning.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..hashing import sha256_bytes

UPR_SETTINGS_VERSION = {"4.6.1": 322}
ROM_NAME_OFFSET = 51


class RnqsError(ValueError):
    pass


@dataclass(frozen=True)
class RnqsInfo:
    version: int
    settings_string: str
    rom_name: str
    crc_ok: bool
    sha256: str
    size: int

    @property
    def settings_string_with_version(self) -> str:
        """The shareable form UPR shows/logs: version digits + Base64."""
        return f"{self.version}{self.settings_string}"


def parse_rnqs(data: bytes, max_version: int | None = None) -> RnqsInfo:
    if len(data) < 8:
        raise RnqsError("File too short to be a UPR settings file")
    version, length = struct.unpack(">ii", data[:8])
    if 0 < ((version >> 24) & 0xFF) <= 172:
        raise RnqsError("Settings file is too old for UPR ZX to load")
    if max_version is not None and version > max_version:
        raise RnqsError(f"Settings version {version} is newer than the randomizer ({max_version})")
    if length <= 0 or 8 + length != len(data):
        raise RnqsError(f"Declared settings length {length} does not match file size {len(data)}")
    try:
        text = data[8:].decode("utf-8")
        raw = base64.b64decode(text, validate=True)
    except (UnicodeDecodeError, binascii.Error) as exc:
        raise RnqsError(f"Settings payload is not Base64 text: {exc}") from None
    if len(raw) < ROM_NAME_OFFSET + 1 + 8:
        raise RnqsError("Settings payload too short")
    (crc,) = struct.unpack(">I", raw[-8:-4])
    crc_ok = (zlib.crc32(raw[:-8]) & 0xFFFFFFFF) == crc
    if not crc_ok:
        raise RnqsError("Settings checksum mismatch (file is corrupted or edited)")
    name_len = raw[ROM_NAME_OFFSET]
    rom_name = raw[ROM_NAME_OFFSET + 1:ROM_NAME_OFFSET + 1 + name_len].decode("ascii", "replace")
    return RnqsInfo(version=version, settings_string=text, rom_name=rom_name, crc_ok=crc_ok,
                    sha256=sha256_bytes(data), size=len(data))


def load_rnqs(path: Path | str, max_version: int | None = None) -> RnqsInfo:
    path = Path(path)
    if not path.is_file():
        raise RnqsError(f"Settings file not found: {path}")
    return parse_rnqs(path.read_bytes(), max_version)
