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
    def settings_files(self, settings: dict[str, Any]) -> list[Path]:
        """One .rnqs, or several applied in order (official Gen 1 two-pass method)."""
        values = settings.get("settings_files") or ([settings["settings_file"]] if settings.get("settings_file") else [])
        if not values:
            raise RandomizerError("Profile settings need 'settings_file' or 'settings_files' (UPR .rnqs)")
        return [Path(v) if Path(v).is_absolute() or self.base is None else self.base / v for v in values]

    def settings_file(self, settings: dict[str, Any]) -> Path:
        return self.settings_files(settings)[0]

    def validate_settings(self, settings: dict[str, Any]) -> list[RnqsInfo]:
        max_version = UPR_SETTINGS_VERSION.get(self.info().version)
        infos = []
        for path in self.settings_files(settings):
            try:
                infos.append(load_rnqs(path, max_version))
            except RnqsError as exc:
                raise RandomizerError(f"Invalid randomizer settings {path}: {exc}") from None
        return infos

    def effective_settings(self, request: RandomizationRequest) -> dict[str, Any]:
        infos = self.validate_settings(request.settings)
        out = {**request.settings, "settings_files_sha256": [i.sha256 for i in infos],
               "settings_files_version": [i.version for i in infos]}
        if len(infos) == 1:   # keep the single-file keys used since Phase 2
            out.update(settings_file_sha256=infos[0].sha256, settings_file_version=infos[0].version,
                       settings_file_rom_name=infos[0].rom_name)
        return out

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

    def _run_pass(self, settings_path: Path, source: Path, out: Path, log_dir: Path, tag: str) -> tuple[UprLog, list[str]]:
        cmd = self.build_command(settings_path.resolve(), source.resolve(), out.resolve())
        try:
            proc = self.runner(cmd, cwd=str(out.parent), capture_output=True, text=True,
                               timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            raise RandomizerError(f"UPR ZX timed out after {self.timeout_seconds}s") from None
        except OSError as exc:
            raise RandomizerError(f"Could not start Java: {exc}") from None
        (log_dir / f"upr-zx{tag}-console.log").write_text(
            f"$ {' '.join(cmd)}\n[exit {proc.returncode}]\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n", encoding="utf-8")
        if proc.returncode != 0 or SUCCESS_MARKER not in (proc.stdout or ""):
            tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-3:])
            raise RandomizerError(f"UPR ZX failed (exit {proc.returncode}): {tail}")
        if not out.is_file():
            raise RandomizerError(f"UPR ZX reported success but {out.name} was not written")
        parsed = UprLog(None, None, None, None)
        upr_log = Path(str(out) + ".log")
        if upr_log.is_file():
            dest = log_dir / f"upr-zx{tag}.log"
            shutil.move(str(upr_log), dest)
            parsed = parse_upr_log(dest.read_text(encoding="utf-8", errors="replace"))
        warnings = [l[len("WARNING: "):] for l in (proc.stderr or "").splitlines() if l.startswith("WARNING: ")]
        return parsed, warnings

    def _randomize(self, request: RandomizationRequest) -> tuple[Path, dict[str, Any], Path | None]:
        ok, why = self.is_available()
        if not ok:
            raise RandomizerError(why)
        paths = self.settings_files(request.settings)
        infos = self.validate_settings(request.settings)
        out = request.output_dir / f"{request.output_name}{request.source_rom.suffix}"
        if out.exists():
            raise RandomizerError(f"Refusing to overwrite existing output {out}")
        log_dir = request.log_dir or request.output_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        passes, warnings, source = [], [], request.source_rom
        for i, path in enumerate(paths):
            last = i == len(paths) - 1
            tag = "" if len(paths) == 1 else f"-pass{i + 1}"
            target = out if last else request.output_dir / f".{request.output_name}.pass{i + 1}{request.source_rom.suffix}"
            parsed, warn = self._run_pass(path, source, target, log_dir, tag)
            passes.append({"pass": i + 1, "settings_file": str(path), "settings_sha256": infos[i].sha256,
                           "actual_seed": parsed.seed, "reported_version": parsed.version,
                           "settings_string": parsed.settings_string, "rom_name": parsed.rom_name})
            warnings += [f"pass {i + 1}: {w}" if tag else w for w in warn]
            if parsed.seed is None:
                warnings.append(f"pass {i + 1}: UPR log did not report a seed")
            if i > 0:
                source.unlink()        # intermediate ROM of the previous pass (inside the run only)
            source = target
        manifest = {
            "actual_seed": passes[0]["actual_seed"],
            "actual_seeds": [p["actual_seed"] for p in passes],
            "passes": passes,
            "reported_version": passes[-1]["reported_version"],
            "reported_rom_name": passes[-1]["rom_name"],
            "settings_string": passes[-1]["settings_string"],
            "settings_files": [{"path": str(p), "sha256": inf.sha256, "version": inf.version,
                                "rom_name": inf.rom_name} for p, inf in zip(paths, infos)],
            "warnings": warnings,
            "exit_code": 0,
            "tested_version": TESTED_VERSION,
        }
        if len(paths) == 1:
            manifest["settings_file"] = manifest["settings_files"][0]
        log_path = log_dir / ("upr-zx.log" if len(paths) == 1 else f"upr-zx-pass{len(paths)}.log")
        return out, manifest, log_path if log_path.exists() else None
