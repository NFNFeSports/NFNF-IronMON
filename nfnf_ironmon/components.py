"""Bundled third-party components (Java runtime, randomizer, emulator core, ...).

``components.json`` at the application root lists each component with its
license, per-platform download URL, SHA-256 and install location. Components
are fetched only when the developer/packager runs ``components fetch``; the
application itself never downloads anything.
"""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import sha256_bytes, sha256_file
from .platform_support import platform_tag
from .util import read_json, write_json

MANIFEST_NAME = "components.json"


class ComponentError(Exception):
    pass


@dataclass
class ComponentStatus:
    id: str
    name: str
    version: str
    license: str
    platform: str | None
    state: str            # PRESENT | MISSING | UNSUPPORTED_PLATFORM
    path: Path | None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version, "license": self.license,
                "platform": self.platform, "state": self.state,
                "path": str(self.path) if self.path else None, "detail": self.detail}


class ComponentManager:
    def __init__(self, app_root: Path, manifest: dict[str, Any] | None = None,
                 platform: str | None = None):
        self.app_root = Path(app_root)
        self.manifest_path = self.app_root / MANIFEST_NAME
        if manifest is None:
            manifest = read_json(self.manifest_path) if self.manifest_path.exists() else {"components": []}
        self.manifest = manifest
        self.platform = platform or platform_tag()

    def ids(self) -> list[str]:
        return [c["id"] for c in self.manifest["components"]]

    def spec(self, component_id: str) -> dict[str, Any]:
        for c in self.manifest["components"]:
            if c["id"] == component_id:
                return c
        raise KeyError(f"Unknown component {component_id!r}")

    def platform_spec(self, component_id: str, platform: str | None = None) -> tuple[str, dict[str, Any]] | None:
        plats = self.spec(component_id)["platforms"]
        platform = platform or self.platform
        if platform in plats:
            return platform, plats[platform]
        if "any" in plats:
            return "any", plats["any"]
        return None

    def entry_path(self, component_id: str, platform: str | None = None) -> Path | None:
        found = self.platform_spec(component_id, platform)
        if not found:
            return None
        _, p = found
        return self.app_root / p["dest"] / p["entry"]

    def resolve(self, component_id: str) -> Path | None:
        """Path of the component's entry file if it is installed for this platform."""
        path = self.entry_path(component_id)
        return path if path and path.exists() else None

    def status(self, component_id: str) -> ComponentStatus:
        c = self.spec(component_id)
        found = self.platform_spec(component_id)
        base = dict(id=c["id"], name=c["name"], version=c["version"], license=c["license"])
        if not found:
            return ComponentStatus(**base, platform=None, state="UNSUPPORTED_PLATFORM", path=None,
                                   detail=f"No build listed for {self.platform}")
        plat, _ = found
        path = self.entry_path(component_id)
        if path and path.exists():
            return ComponentStatus(**base, platform=plat, state="PRESENT", path=path)
        return ComponentStatus(**base, platform=plat, state="MISSING", path=path,
                               detail="Run: python3 -m nfnf_ironmon components fetch")

    def all_status(self) -> list[ComponentStatus]:
        return [self.status(i) for i in self.ids()]

    # --- fetching (developer / packaging step, never at runtime) -----------
    def fetch(self, component_id: str, platform: str | None = None, pin: bool = False,
              downloader=None) -> Path:
        found = self.platform_spec(component_id, platform)
        if not found:
            raise ComponentError(f"{component_id} has no build for {platform or self.platform}")
        plat, p = found
        data = (downloader or _download)(p["url"])
        digest = sha256_bytes(data)
        expected = p.get("sha256")
        if expected and digest != expected:
            raise ComponentError(f"{component_id}: sha256 mismatch (got {digest}, expected {expected})")
        if not expected:
            if not pin:
                raise ComponentError(f"{component_id} ({plat}) has no pinned sha256. Downloaded file has "
                                     f"{digest}; re-run with --pin to trust and record it.")
            p["sha256"] = digest
            write_json(self.manifest_path, self.manifest)
        dest = self.app_root / p["dest"]
        _install(data, p, dest)
        entry = dest / p["entry"]
        if not entry.exists():
            raise ComponentError(f"{component_id}: expected {p['entry']} after extraction")
        return entry


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "NFNF-IronMON-component-fetch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _strip(name: str, n: int) -> str | None:
    parts = [x for x in name.replace("\\", "/").split("/") if x]
    if len(parts) <= n:
        return None
    rel = "/".join(parts[n:])
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ComponentError(f"Unsafe path in archive: {name}")
    return rel


def _install(data: bytes, p: dict[str, Any], dest: Path) -> None:
    kind, strip = p.get("archive"), int(p.get("strip_components") or 0)
    dest.mkdir(parents=True, exist_ok=True)
    if kind is None:
        (dest / p["entry"]).write_bytes(data)
        return
    staging = Path(tempfile.mkdtemp(prefix="nfnf-component-", dir=dest.parent))
    try:
        if kind == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    rel = _strip(info.filename, strip)
                    if rel is None or info.is_dir():
                        continue
                    target = staging / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(info))
                    mode = (info.external_attr >> 16) & 0o777
                    if mode:
                        target.chmod(mode)
        elif kind == "tar.gz":
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
                for m in t.getmembers():
                    rel = _strip(m.name, strip)
                    if rel is None:
                        continue
                    target = staging / rel
                    if m.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif m.issym():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        pointed = (target.parent / m.linkname).resolve()
                        if Path(m.linkname).is_absolute() or not pointed.is_relative_to(staging.resolve()):
                            raise ComponentError(f"Unsafe symlink in archive: {m.name}")
                        target.symlink_to(m.linkname)
                    elif m.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with t.extractfile(m) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        target.chmod(m.mode & 0o777)
        else:
            raise ComponentError(f"Unsupported archive type {kind}")
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
