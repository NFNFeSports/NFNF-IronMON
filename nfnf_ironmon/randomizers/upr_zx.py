"""Universal Pokémon Randomizer ZX adapter (GPL-3.0, run as a separate process).

Verified against the UPR ZX 4.6.1 source (``CliRandomizer``, ``NewRandomizerGUI.main``):

* invocation: ``java -jar PokeRandoZX.jar cli -s <settings.rnqs> -i <source> -o <output> [-l]``
  (also ``-d`` save 3DS as directory, ``-u`` 3DS update file, ``--help``)
* exit code 0 only after printing ``Randomized successfully!``; 1 on any error
* ``-l`` writes the log to ``<output>.log`` (UTF-8 with BOM); the log records
  ``Randomizer Version``, ``Random Seed`` and ``Settings String``
* there is **no seed option**: UPR picks a 48-bit seed from ``SecureRandom``,
  so runs are not reproducible from NFNF's seed → ``deterministic = False``

Java is resolved from, in order: ``randomizers.upr-zx.java`` in config, the
bundled runtime (``runtime/java/<platform>/``), then the system PATH
(development fallback only — reported as such by ``doctor``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..components import ComponentManager
from ..games import RomIdentity
from ..platform_support import find_executable, os_family
from .base import RandomizationRequest, RandomizerAdapter, RandomizerError, RandomizerInfo
from .rnqs import UPR_SETTINGS_VERSION, RnqsError, RnqsInfo, load_rnqs

UPR_SUPPORTED_GAMES = {"red", "blue", "yellow", "gold", "silver", "crystal", "ruby", "sapphire",
                       "emerald", "firered", "leafgreen"}
SUCCESS_MARKER = "Randomized successfully!"
TESTED_VERSION = "4.6.1"


@dataclass(frozen=True)
class UprLog:
    version: str | None
    seed: int | None
    settings_string: str | None
    rom_name: str | None


def parse_upr_log(text: str) -> UprLog:
    text = text.lstrip("﻿")

    def first(pattern: str) -> str | None:
        m = re.search(pattern, text, re.MULTILINE)
        return m.group(1).strip() if m else None

    seed = first(r"^Random Seed: (-?\d+)\s*$")
    return UprLog(version=first(r"^Randomizer Version: (.+)$"),
                  seed=int(seed) if seed is not None else None,
                  settings_string=first(r"^Settings String: (\S+)\s*$"),
                  rom_name=first(r"^Randomization of (.+) completed\.\s*$"))


class UprZxRandomizer(RandomizerAdapter):
    randomizer_id = "upr-zx"
    timeout_seconds = 300

    def __init__(self, config: dict[str, Any] | None = None, base: Path | None = None,
                 components: ComponentManager | None = None,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.config = config or {}
        self.base = base
        self.components = components
        self.runner = runner

    # --- resolution -----------------------------------------------------
    def java_path(self) -> tuple[Path | None, str]:
        names = ["java.exe"] if os_family() == "windows" else ["java"]
        if self.config.get("java"):
            p = find_executable(names, self.config["java"], self.base)
            return (p, "config") if p else (None, "config (not found)")
        if self.components:
            p = self.components.resolve("java-runtime")
            if p:
                return p, "bundled"
        p = find_executable(names)
        return (p, "system") if p else (None, "missing")

    def jar_path(self) -> tuple[Path | None, str]:
        jar = self.config.get("jar")
        if jar:
            p = Path(jar).expanduser()
            if not p.is_absolute() and self.base:
                p = self.base / p
            return (p, "config") if p.is_file() else (None, "config (not found)")
        if self.components:
            p = self.components.resolve("upr-zx")
            if p:
                return p, "bundled"
        return None, "missing"

    def info(self) -> RandomizerInfo:
        version = "unknown"
        jar, source = self.jar_path()
        if jar and source == "bundled" and self.components:
            version = self.components.spec("upr-zx")["version"]
        elif jar:
            m = re.search(r"(\d+)[._](\d+)[._](\d+)", jar.name)
            if m:
                version = ".".join(m.groups())
        return RandomizerInfo(name="Universal Pokemon Randomizer ZX", version=version,
                              license="GPL-3.0", deterministic=False, modifies_rom=True)

    def is_available(self) -> tuple[bool, str]:
        java, jsrc = self.java_path()
        if not java:
            return False, "Java runtime not found (bundle it with `components fetch`)"
        jar, _ = self.jar_path()
        if not jar:
            return False, "PokeRandoZX.jar not found (bundle it with `components fetch`)"
        return True, f"java: {jsrc}"

    def detect_supported_game(self, identity: RomIdentity) -> bool:
        return identity.game_id in UPR_SUPPORTED_GAMES

    # --- settings -------------------------------------------------------
    def settings_file(self, settings: dict[str, Any]) -> Path:
        value = settings.get("settings_file")
        if not value:
            raise RandomizerError("Profile settings need 'settings_file' (a UPR .rnqs file)")
        p = Path(value)
        return p if p.is_absolute() or self.base is None else self.base / p

    def validate_settings(self, settings: dict[str, Any]) -> RnqsInfo:
        path = self.settings_file(settings)
        max_version = UPR_SETTINGS_VERSION.get(self.info().version)
        try:
            return load_rnqs(path, max_version)
        except RnqsError as exc:
            raise RandomizerError(f"Invalid randomizer settings {path}: {exc}") from None

    def effective_settings(self, request: RandomizationRequest) -> dict[str, Any]:
        info = self.validate_settings(request.settings)
        return {**request.settings, "settings_file_sha256": info.sha256,
                "settings_file_version": info.version, "settings_file_rom_name": info.rom_name}

    # --- execution ------------------------------------------------------
    def build_command(self, settings_file: Path, source_rom: Path, output_rom: Path,
                      with_log: bool = True) -> list[str]:
        java = self.java_path()[0] or Path("java")
        jar = self.jar_path()[0] or Path("PokeRandoZX.jar")
        cmd = [str(java), "-Djava.awt.headless=true", f"-Xmx{self.config.get('max_heap', '4096M')}",
               "-jar", str(jar), "cli", "-s", str(settings_file), "-i", str(source_rom),
               "-o", str(output_rom)]
        if with_log:
            cmd.append("-l")
        return cmd

    def _randomize(self, request: RandomizationRequest) -> tuple[Path, dict[str, Any], Path | None]:
        ok, why = self.is_available()
        if not ok:
            raise RandomizerError(why)
        settings_path = self.settings_file(request.settings)
        rnqs = self.validate_settings(request.settings)
        out = request.output_dir / f"{request.output_name}{request.source_rom.suffix}"
        if out.exists():
            raise RandomizerError(f"Refusing to overwrite existing output {out}")
        cmd = self.build_command(settings_path.resolve(), request.source_rom.resolve(), out.resolve())
        try:
            proc = self.runner(cmd, cwd=str(request.output_dir), capture_output=True, text=True,
                               timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            raise RandomizerError(f"UPR ZX timed out after {self.timeout_seconds}s") from None
        except OSError as exc:
            raise RandomizerError(f"Could not start Java: {exc}") from None

        log_dir = request.log_dir or request.output_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "upr-zx-console.log").write_text(
            f"$ {' '.join(cmd)}\n[exit {proc.returncode}]\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n", encoding="utf-8")
        if proc.returncode != 0 or SUCCESS_MARKER not in (proc.stdout or ""):
            tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-3:])
            raise RandomizerError(f"UPR ZX failed (exit {proc.returncode}): {tail}")
        if not out.is_file():
            raise RandomizerError(f"UPR ZX reported success but {out.name} was not written")

        upr_log = Path(str(out) + ".log")
        log_path = None
        parsed = UprLog(None, None, None, None)
        if upr_log.is_file():
            log_path = log_dir / "upr-zx.log"
            shutil.move(str(upr_log), log_path)
            parsed = parse_upr_log(log_path.read_text(encoding="utf-8", errors="replace"))
        warnings = [l[len("WARNING: "):] for l in (proc.stderr or "").splitlines()
                    if l.startswith("WARNING: ")]
        manifest = {
            "actual_seed": parsed.seed,
            "reported_version": parsed.version,
            "reported_rom_name": parsed.rom_name,
            "settings_string": parsed.settings_string,
            "settings_file": {"path": str(settings_path), "sha256": rnqs.sha256,
                              "version": rnqs.version, "rom_name": rnqs.rom_name},
            "warnings": warnings,
            "exit_code": proc.returncode,
            "tested_version": TESTED_VERSION,
        }
        if parsed.seed is None:
            manifest["warnings"].append("UPR log did not report a seed")
        return out, manifest, log_path
