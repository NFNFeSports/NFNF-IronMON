"""Test helpers. Synthetic ROM files contain only a fabricated cartridge header
(no game code or data), so the test-suite never needs a real Pokémon ROM."""

from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from nfnf_ironmon.app import Application
from nfnf_ironmon.games.headers import gb_header_checksum, gba_header_checksum


def synthetic_gba(path: Path, title: bytes = b"POKEMON FIRE", code: bytes = b"BPRE",
                  version: int = 1, size: int = 4096, filler: int = 0) -> Path:
    data = bytearray([filler & 0xFF]) * size
    # Minimal valid program so real emulator cores accept it: `b 0xC0` at the
    # entry point, then `b .` (spin forever) at 0xC0.
    data[0:4] = (0xEA00002E).to_bytes(4, "little")
    if size > 0xC4:
        data[0xC0:0xC4] = (0xEAFFFFFE).to_bytes(4, "little")
    data[0xA0:0xAC] = title.ljust(12, b"\x00")
    data[0xAC:0xB0] = code
    data[0xB0:0xB2] = b"01"
    data[0xB2] = 0x96
    data[0xBC] = version
    data[0xBD] = gba_header_checksum(bytes(data))
    path.write_bytes(bytes(data))
    return path


def synthetic_gb(path: Path, title: bytes, cgb_flag: int = 0, size: int = 4096) -> Path:
    data = bytearray(size)
    data[0x134:0x134 + len(title)] = title
    data[0x143] = cgb_flag
    data[0x14D] = gb_header_checksum(bytes(data))
    path.write_bytes(bytes(data))
    return path


def make_rnqs(version: int = 322, rom_name: bytes = b"Fire Red (U) 1.1", corrupt: bool = False) -> bytes:
    """A structurally valid UPR settings file (same layout as Settings.write)."""
    body = bytes(51) + bytes([len(rom_name)]) + rom_name
    crc = zlib.crc32(body) & 0xFFFFFFFF
    raw = body + struct.pack(">I", crc ^ (1 if corrupt else 0)) + struct.pack(">I", 0)
    text = base64.b64encode(raw)
    return struct.pack(">ii", version, len(text)) + text


class FakeUprRunner:
    """Stands in for `java -jar PokeRandoZX.jar cli ...`: writes a changed ROM + log."""

    def __init__(self, rc: int = 0, seed: int = 424242, change: bool = True, write: bool = True,
                 stdout: str = "Randomized successfully!\n", stderr: str = ""):
        self.rc, self.seed, self.change, self.write = rc, seed, change, write
        self.stdout, self.stderr = stdout, stderr
        self.calls: list = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.write:
            src = Path(cmd[cmd.index("-i") + 1])
            out = Path(cmd[cmd.index("-o") + 1])
            data = bytearray(src.read_bytes())
            if self.change:
                data[0x800] ^= 0xFF
            out.write_bytes(bytes(data))
            Path(str(out) + ".log").write_text(
                f"\ufeffRandomizer Version: 4.6.1\nRandom Seed: {self.seed}\nSettings String: 322X\n",
                encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.rc, self.stdout, self.stderr)


def use_fake_upr(app, tmp: Path, runner: "FakeUprRunner | None" = None) -> FakeUprRunner:
    """Point the app's UPR adapter at fake java/jar files and a fake process runner."""
    java, jar = tmp / "fake-java", tmp / "fake-PokeRandoZX-4.6.1.jar"
    java.write_bytes(b"")
    jar.write_bytes(b"")
    (app.paths.profiles_dir / "firered-ironmon.rnqs").write_bytes(make_rnqs())
    upr = app.randomizers.get("upr-zx")
    upr.config.update({"java": str(java), "jar": str(jar)})
    upr.runner = runner or FakeUprRunner()
    return upr.runner


#: Unit tests exercise the full pipeline with mock tools; real-tool tests opt in.
MOCK_CONFIG = {"schema": 1, "default_randomizer_profile": "firered-mock-standard",
               "emulator": "mock", "tracker": "mock"}


def write_test_config(home: Path, overrides: dict | None = None) -> None:
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "settings.json").write_text(json.dumps({**MOCK_CONFIG, **(overrides or {})}))


class AppTestCase(unittest.TestCase):
    """Fresh portable home in a temp folder for every test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nfnf-test-"))
        self.home = self.tmp / "NFNF-IronMON"
        write_test_config(self.home)
        self.app = Application(self.home, strict_events=True)
        self.user_rom = synthetic_gba(self.tmp / "my-firered.gba")

    def tearDown(self) -> None:
        self.app.close()
        # archived ROMs are read-only; make everything writable before cleanup
        for p in self.tmp.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def import_firered(self):
        return self.app.import_rom(self.user_rom)

    def reopen(self) -> Application:
        self.app.close()
        self.app = Application(self.home, strict_events=True)
        return self.app
