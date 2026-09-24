"""Assemble the portable NFNF-IronMON-<OS>/ folder from a PyInstaller onedir build.

    NFNF-IronMON-Linux/
    ├── NFNF-IronMON            launcher (runs app/nfnf-ironmon)
    ├── app/                    frozen application (no Python needed)
    ├── runtime/java/<plat>/    bundled JRE (UPR ZX)
    ├── emulator/cores/<plat>/  mGBA libretro core
    ├── randomizer/upr-zx/      PokeRandoZX.jar
    ├── rules/ randomizer-profiles/ input-mappings/ components.json
    ├── games/original/         ← the user puts their own ROMs here
    ├── runs/ data/ config/     user data (created empty)
    └── THIRD-PARTY-NOTICES.md

Standard library only. Never copies ROMs, saves, runs or user data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = ("rules", "randomizer-profiles", "input-mappings")
FORBIDDEN_SUFFIXES = {".gb", ".gbc", ".gba", ".sav", ".state", ".srm"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frozen_app", type=Path, help="PyInstaller onedir output folder")
    ap.add_argument("out", type=Path)
    ap.add_argument("--platform", required=True, help="linux-x64 | windows-x64")
    args = ap.parse_args(argv)

    manifest = json.loads((ROOT / "components.json").read_text())
    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(args.frozen_app, out / "app", symlinks=True)

    missing = []
    for comp in manifest["components"]:
        plats = comp["platforms"]
        spec = plats.get(args.platform) or plats.get("any")
        if not spec:
            continue
        src = ROOT / spec["dest"]
        if not (src / spec["entry"]).exists():
            missing.append(comp["id"])
            continue
        if spec["dest"] in DATA_DIRS:
            continue  # copied with the data folders below
        shutil.copytree(src, out / spec["dest"], symlinks=True)
    if missing:
        print(f"error: components not fetched for {args.platform}: {missing}", file=sys.stderr)
        return 1

    for d in DATA_DIRS:
        shutil.copytree(ROOT / d, out / d)
    shutil.copy2(ROOT / "components.json", out / "components.json")
    for d in ("games/original", "runs", "data", "config"):
        (out / d).mkdir(parents=True, exist_ok=True)
    (out / "games" / "original" / "PUT-YOUR-ROMS-HERE.txt").write_text(
        "Copy your own legally obtained game dumps (.gba/.gb/.gbc) into this folder.\n"
        "NFNF IronMON only reads them; they are never modified or uploaded.\n")

    if args.platform.startswith("windows"):
        (out / "NFNF-IronMON.bat").write_text('@echo off\r\n"%~dp0app\\nfnf-ironmon.exe" %*\r\n')
    else:
        launcher = out / "NFNF-IronMON"
        launcher.write_text('#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/app/nfnf-ironmon" "$@"\n')
        launcher.chmod(0o755)

    notes = ["# Third-party components bundled with NFNF IronMON", ""]
    for comp in manifest["components"]:
        notes += [f"## {comp['name']} {comp['version']}", f"- License: {comp['license']} ({comp['license_url']})"]
        if comp.get("source_url"):
            notes.append(f"- Source: {comp['source_url']}")
        notes += [f"- Purpose: {comp['purpose']}", ""]
    notes += ["The Python runtime and standard library are bundled by PyInstaller (PSF License).", ""]
    (out / "THIRD-PARTY-NOTICES.md").write_text("\n".join(notes))

    bad = [p for p in out.rglob("*") if p.suffix.lower() in FORBIDDEN_SUFFIXES]
    if bad:
        print(f"error: ROM/save files must never be packaged: {bad}", file=sys.stderr)
        return 1
    print(f"assembled {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
