"""Hashing helpers used by the ROM manager and the integrity subsystem."""

from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024


def file_digests(path: Path | str) -> dict[str, Any]:
    """SHA-256, SHA-1 and CRC32 in one pass (SHA-1/CRC32 match No-Intro DATs)."""
    sha256, sha1, crc, size = hashlib.sha256(), hashlib.sha1(), 0, 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            sha256.update(chunk)
            sha1.update(chunk)
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return {"sha256": sha256.hexdigest(), "sha1": sha1.hexdigest(),
            "crc32": f"{crc & 0xFFFFFFFF:08x}", "size": size}


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """Stable JSON encoding: key order and whitespace never change the hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))
