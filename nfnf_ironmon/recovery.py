"""Crash recovery: detect game sessions that ended without a clean shutdown.

A running game session keeps ``runs/<id>/session.json`` up to date
(``state``: running | paused | closed, ``pid``, ``heartbeat``). A session file
that still says running/paused while its process is gone (or its heartbeat is
stale) means NFNF IronMON crashed or was killed. The run is then offered for
RESUME or ABANDON — never silently discarded — and the interruption is
recorded by the integrity system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .platform_support import pid_alive
from .runs import Run, RunState
from .util import read_json, utc_now, write_json

SESSION_FILE = "session.json"
STALE_SECONDS = 20.0


@dataclass
class InterruptedRun:
    run: Run
    session: dict[str, Any]
    has_recovery_state: bool
    has_save: bool

    @property
    def summary(self) -> str:
        s = self.session
        return (f"{self.run.id} ({self.run.game_id}) — last heartbeat {s.get('heartbeat', '?')}, "
                f"in-game time frames {s.get('play_time_frames')}, area {s.get('area') or 'UNKNOWN'}")


def write_session(run_dir: Path, state: str, **extra: Any) -> None:
    data = {"pid": os.getpid(), "state": state, "heartbeat": utc_now(), **extra}
    write_json(run_dir / SESSION_FILE, data)


def read_session(run_dir: Path) -> dict[str, Any] | None:
    p = run_dir / SESSION_FILE
    try:
        return read_json(p) if p.exists() else None
    except ValueError:
        return {"state": "running", "corrupt": True}


def _age_seconds(ts: str | None) -> float:
    if not ts:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return float("inf")


def is_interrupted(session: dict[str, Any] | None) -> bool:
    if not session or session.get("state") not in ("running", "paused"):
        return False
    if session.get("pid") == os.getpid():
        return False
    return not pid_alive(session.get("pid")) or _age_seconds(session.get("heartbeat")) > STALE_SECONDS


def find_interrupted(runs) -> list[InterruptedRun]:
    """ACTIVE runs whose game session did not shut down cleanly."""
    out = []
    for run in runs.list():
        if run.status != RunState.ACTIVE or run.archived:
            continue
        session = read_session(run.path)
        if is_interrupted(session):
            out.append(InterruptedRun(run, session or {}, (run.path / "states" / "recovery.state").exists(),
                                      (run.path / "saves" / "game.sav").exists()))
    return out
