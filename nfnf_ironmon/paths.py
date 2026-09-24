"""Portable directory layout.

Nothing in application logic assumes an OS-specific absolute path. Paths
persisted to disk or to the database are stored relative to home (POSIX
separators) so the portable ``NFNF-IronMON/`` folder can be copied to another
machine.

Two roots:

* **app root** — where the application and its bundled components live
  (``app/``, ``runtime/``, ``emulator/``, ``randomizer/``, rules, profiles,
  input mappings, ``components.json``). Resolved from the executable
  (frozen build, ``NFNF-IronMON/app/nfnf-ironmon``) or the source checkout.
* **home** — user data (``config/``, ``games/``, ``runs/``, ``data/``).
  Defaults to the app root, which makes the whole folder portable.

Home resolution order:
  1. explicit argument (``--home``)
  2. ``NFNF_IRONMON_HOME`` environment variable
  3. the app root
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ENV_HOME = "NFNF_IRONMON_HOME"

#: Bundled default data copied into a fresh home (never overwriting user files).
BUNDLED_DATA = {"rules": ("*.json",), "randomizer-profiles": ("*.json", "*.rnqs"),
                "input-mappings": ("*.json",)}


def resolve_app_root(frozen: bool, executable: str, package_file: str) -> Path:
    """App root for a frozen build (``<root>/app/<exe>`` or ``<root>/<exe>``) or a source tree."""
    if frozen:
        exe_dir = Path(executable).resolve().parent
        return exe_dir.parent if exe_dir.name == "app" else exe_dir
    return Path(package_file).resolve().parent.parent


BUNDLE_ROOT = resolve_app_root(bool(getattr(sys, "frozen", False)), sys.executable, __file__)


def default_home() -> Path:
    return BUNDLE_ROOT


class AppPaths:
    def __init__(self, home: Path | str, app_root: Path | str | None = None):
        self.home = Path(home).expanduser().resolve()
        self.app_root = Path(app_root).resolve() if app_root else BUNDLE_ROOT

    @classmethod
    def resolve(cls, home: Path | str | None = None) -> "AppPaths":
        if home:
            return cls(home)
        env = os.environ.get(ENV_HOME)
        if env:
            return cls(env)
        return cls(default_home())

    # --- layout -----------------------------------------------------------
    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nfnf-ironmon.sqlite3"

    @property
    def games_dir(self) -> Path:
        return self.home / "games"

    @property
    def originals_dir(self) -> Path:
        return self.games_dir / "original"

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def rules_dir(self) -> Path:
        return self.home / "rules"

    @property
    def profiles_dir(self) -> Path:
        return self.home / "randomizer-profiles"

    @property
    def input_mappings_dir(self) -> Path:
        return self.home / "input-mappings"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    # --- helpers ----------------------------------------------------------
    def ensure(self) -> None:
        for d in (self.config_dir, self.data_dir, self.originals_dir, self.runs_dir,
                  self.rules_dir, self.profiles_dir, self.input_mappings_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        # A separate home (e.g. a data folder elsewhere, or a test sandbox) gets
        # the bundled default rulesets/profiles/mappings. Existing files are
        # never replaced.
        if self.home != self.app_root:
            for name, patterns in BUNDLED_DATA.items():
                src = self.app_root / name
                if not src.is_dir():
                    continue
                for pattern in patterns:
                    for f in src.glob(pattern):
                        dst = self.home / name / f.name
                        if not dst.exists():
                            shutil.copy2(f, dst)

    def rel(self, path: Path | str) -> str:
        """Home-relative POSIX path; absolute path if outside home."""
        p = Path(path).resolve()
        try:
            return p.relative_to(self.home).as_posix()
        except ValueError:
            return str(p)

    def abs(self, stored: str | Path) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else (self.home / p)
