"""IronMON career: attempts, results, best progress and play time across all runs.

* An *attempt* is a run that reached ACTIVE (runs abandoned during setup do not
  count). Attempts are numbered in order (``runs.attempt_number``).
* Progress = number of distinct badges from BADGE_ACQUIRED events.
* Play time uses in-game time (``play_time_frames`` reported by a tracker)
  when available, otherwise wall-clock time from start to end (or now). Each
  figure says which source it used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database

GBA_FPS = 16777216 / 280896  # 59.7275 frames per second


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    s = int(round(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


@dataclass
class RunSummary:
    run_id: str
    attempt: int
    status: str
    badges: int
    seconds: float | None
    time_source: str          # "in-game" | "wall-clock"


@dataclass
class Career:
    attempts: int = 0
    active_attempt: int | None = None
    active_run_id: str | None = None
    completed: int = 0
    failed: int = 0
    abandoned: int = 0
    best_badges: int = 0
    best_run_id: str | None = None
    longest_seconds: float | None = None
    longest_run_id: str | None = None
    total_seconds: float = 0.0
    time_sources: set[str] = field(default_factory=set)
    runs: list[RunSummary] = field(default_factory=list)

    @property
    def best_progress(self) -> str:
        return f"Gym {self.best_badges}" if self.best_badges else "No badges yet"

    def to_dict(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "active_attempt": self.active_attempt,
                "active_run_id": self.active_run_id, "completed": self.completed,
                "failed": self.failed, "abandoned": self.abandoned,
                "best_progress": self.best_progress, "best_badges": self.best_badges,
                "best_run_id": self.best_run_id,
                "longest_run": fmt_duration(self.longest_seconds), "longest_run_id": self.longest_run_id,
                "total_play_time": fmt_duration(self.total_seconds),
                "time_sources": sorted(self.time_sources),
                "runs": [r.__dict__ for r in self.runs]}

    def render(self) -> str:
        src = "/".join(sorted(self.time_sources)) or "n/a"
        return "\n".join([
            "IRONMON CAREER", "",
            f"Attempts:         {self.attempts}",
            f"Active Run:       {'#%d (%s)' % (self.active_attempt, self.active_run_id) if self.active_attempt else '-'}",
            f"Completed:        {self.completed}",
            f"Failed:           {self.failed}",
            "",
            f"Best Progress:    {self.best_progress}" + (f" ({self.best_run_id})" if self.best_run_id else ""),
            f"Longest Run:      {fmt_duration(self.longest_seconds)}"
            + (f" ({self.longest_run_id})" if self.longest_run_id else ""),
            f"Total Play Time:  {fmt_duration(self.total_seconds)}  [{src}]",
        ])


def compute_career(db: Database, now: datetime | None = None) -> Career:
    now = now or datetime.now(timezone.utc)
    c = Career()
    rows = db.query("SELECT id, attempt_number, status, started_at, ended_at FROM runs"
                    " WHERE attempt_number IS NOT NULL ORDER BY attempt_number")
    for r in rows:
        badges_rows = db.query("SELECT payload_json FROM events WHERE run_id = ? AND type = 'BADGE_ACQUIRED'",
                               (r["id"],))
        badges = len({json.loads(b["payload_json"]).get("badge", i) for i, b in enumerate(badges_rows)})
        frames = db.query_one(
            "SELECT MAX(CAST(json_extract(payload_json, '$.play_time_frames') AS INTEGER)) AS m"
            " FROM events WHERE run_id = ?", (r["id"],))["m"]
        if frames:
            seconds, source = frames / GBA_FPS, "in-game"
        else:
            start, end = _parse(r["started_at"]), _parse(r["ended_at"]) or now
            seconds, source = ((end - start).total_seconds() if start else None), "wall-clock"
        c.runs.append(RunSummary(r["id"], r["attempt_number"], r["status"], badges, seconds, source))
        c.attempts += 1
        c.completed += r["status"] == "COMPLETED"
        c.failed += r["status"] == "FAILED"
        c.abandoned += r["status"] == "ABANDONED"
        if r["status"] == "ACTIVE":
            c.active_attempt, c.active_run_id = r["attempt_number"], r["id"]
        if badges > c.best_badges:
            c.best_badges, c.best_run_id = badges, r["id"]
        if seconds is not None:
            c.total_seconds += seconds
            c.time_sources.add(source)
            if c.longest_seconds is None or seconds > c.longest_seconds:
                c.longest_seconds, c.longest_run_id = seconds, r["id"]
    return c
