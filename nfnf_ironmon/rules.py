"""Generic rules engine. Rulesets are JSON files in ``rules/``; nothing is hard-coded.

Rule fields
-----------
id, description
severity          HARD   – detectable automatically, provable from events
                  SOFT   – suspicious signal, cannot be proven
                  MANUAL – requires player confirmation
detection         automatic | heuristic | manual | unavailable
event_type        event that triggers evaluation (null for checklist-only rules)
condition         declarative match on the event payload (optional)
failure_behavior  FAIL_RUN | WARN | FLAG_FOR_REVIEW | CONFIRM_WITH_PLAYER | IGNORE
games             optional list of game ids the rule applies to

Conditions
----------
{"field": "is_starter", "op": "eq", "value": true}
{"all": [c1, c2]} / {"any": [c1, c2]} / {"not": c}
ops: eq ne gt gte lt lte in not_in exists missing contains
``field`` is a dotted path into the event payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import Event, is_registered
from .hashing import sha256_json
from .util import read_json

SEVERITIES = ("HARD", "SOFT", "MANUAL")
DETECTIONS = ("automatic", "heuristic", "manual", "unavailable")
BEHAVIORS = ("FAIL_RUN", "WARN", "FLAG_FOR_REVIEW", "CONFIRM_WITH_PLAYER", "IGNORE")
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists", "missing", "contains")

_MISSING = object()

#: Outcome of an event against a ruleset (Game State → Events → Rules → outcome).
OUTCOMES = ("VALID", "WARNING", "VIOLATION", "RUN_FAILED")
_OUTCOME_BY_BEHAVIOR = {"FAIL_RUN": "RUN_FAILED", "FLAG_FOR_REVIEW": "VIOLATION",
                        "CONFIRM_WITH_PLAYER": "VIOLATION", "WARN": "WARNING", "IGNORE": "VALID"}


class RulesetError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    severity: str
    detection: str
    event_type: str | None
    failure_behavior: str
    condition: dict[str, Any] | None = None
    games: tuple[str, ...] = ()

    def applies_to(self, game_id: str | None) -> bool:
        return not self.games or game_id is None or game_id in self.games


@dataclass
class Ruleset:
    id: str
    name: str
    version: int
    rules: list[Rule]
    raw: dict[str, Any]
    path: Path | None = None
    games: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        return sha256_json(self.raw)

    def applies_to(self, game_id: str) -> bool:
        return not self.games or "*" in self.games or game_id in self.games


@dataclass
class RuleViolation:
    rule: Rule
    event: Event
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return _OUTCOME_BY_BEHAVIOR[self.rule.failure_behavior]

    def to_payload(self) -> dict[str, Any]:
        return {"rule_id": self.rule.id, "description": self.rule.description,
                "outcome": self.outcome,
                "severity": self.rule.severity, "failure_behavior": self.rule.failure_behavior,
                "trigger_event_id": self.event.event_id, "trigger_event_type": self.event.type,
                **self.details}


# --- parsing ---------------------------------------------------------------

def _validate_condition(cond: Any, where: str) -> None:
    if not isinstance(cond, dict):
        raise RulesetError(f"{where}: condition must be an object")
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        if not isinstance(cond[key], list) or not cond[key]:
            raise RulesetError(f"{where}: {key!r} needs a non-empty list")
        for i, c in enumerate(cond[key]):
            _validate_condition(c, f"{where}.{key}[{i}]")
    elif "not" in cond:
        _validate_condition(cond["not"], f"{where}.not")
    else:
        if "field" not in cond or cond.get("op") not in OPS:
            raise RulesetError(f"{where}: needs 'field' and an 'op' in {OPS}")
        if cond["op"] not in ("exists", "missing") and "value" not in cond:
            raise RulesetError(f"{where}: op {cond['op']!r} needs a 'value'")


def parse_ruleset(data: dict[str, Any], path: Path | None = None) -> Ruleset:
    for key in ("id", "name", "version", "rules"):
        if key not in data:
            raise RulesetError(f"Ruleset missing {key!r}")
    if not isinstance(data["rules"], list):
        raise RulesetError("'rules' must be a list")
    rules, seen = [], set()
    for i, r in enumerate(data["rules"]):
        where = f"rules[{i}]"
        for key in ("id", "description", "severity", "detection", "failure_behavior"):
            if key not in r:
                raise RulesetError(f"{where}: missing {key!r}")
        if r["id"] in seen:
            raise RulesetError(f"{where}: duplicate rule id {r['id']!r}")
        seen.add(r["id"])
        if r["severity"] not in SEVERITIES:
            raise RulesetError(f"{where}: severity must be one of {SEVERITIES}")
        if r["detection"] not in DETECTIONS:
            raise RulesetError(f"{where}: detection must be one of {DETECTIONS}")
        if r["failure_behavior"] not in BEHAVIORS:
            raise RulesetError(f"{where}: failure_behavior must be one of {BEHAVIORS}")
        if r["severity"] == "HARD" and r["detection"] != "automatic":
            raise RulesetError(f"{where}: HARD rules must be automatically detectable")
        if r["severity"] == "MANUAL" and r["failure_behavior"] == "FAIL_RUN":
            raise RulesetError(f"{where}: MANUAL rules cannot fail a run without confirmation")
        et = r.get("event_type")
        if et is not None and not is_registered(et):
            raise RulesetError(f"{where}: unknown event_type {et!r}")
        if r["detection"] == "automatic" and et is None:
            raise RulesetError(f"{where}: automatic rules need an event_type")
        if r.get("condition") is not None:
            _validate_condition(r["condition"], f"{where}.condition")
        rules.append(Rule(id=r["id"], description=r["description"], severity=r["severity"],
                          detection=r["detection"], event_type=et,
                          failure_behavior=r["failure_behavior"], condition=r.get("condition"),
                          games=tuple(r.get("games", ()))))
    return Ruleset(id=data["id"], name=data["name"], version=int(data["version"]), rules=rules,
                   raw=data, path=path, games=tuple(data.get("games", ("*",))))


def load_ruleset(path: Path | str) -> Ruleset:
    path = Path(path)
    return parse_ruleset(read_json(path), path)


class RulesetRepository:
    def __init__(self, directory: Path):
        self.directory = directory

    def list(self) -> list[Ruleset]:
        return [load_ruleset(p) for p in sorted(self.directory.glob("*.json"))]

    def load(self, ruleset_id: str) -> Ruleset:
        path = self.directory / f"{ruleset_id}.json"
        if not path.is_file():
            raise KeyError(f"Ruleset not found: {ruleset_id}")
        rs = load_ruleset(path)
        if rs.id != ruleset_id:
            raise RulesetError(f"Ruleset id {rs.id!r} does not match file name {path.name}")
        return rs


# --- evaluation ------------------------------------------------------------

def _lookup(payload: dict[str, Any], dotted: str) -> Any:
    node: Any = payload
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def evaluate_condition(cond: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    if cond is None:
        return True
    if "all" in cond:
        return all(evaluate_condition(c, payload) for c in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(c, payload) for c in cond["any"])
    if "not" in cond:
        return not evaluate_condition(cond["not"], payload)
    actual, op, expected = _lookup(payload, cond["field"]), cond["op"], cond.get("value")
    if op == "exists":
        return actual is not _MISSING
    if op == "missing":
        return actual is _MISSING
    if actual is _MISSING:
        return False
    try:
        return {
            "eq": lambda: actual == expected,
            "ne": lambda: actual != expected,
            "gt": lambda: actual > expected,
            "gte": lambda: actual >= expected,
            "lt": lambda: actual < expected,
            "lte": lambda: actual <= expected,
            "in": lambda: actual in expected,
            "not_in": lambda: actual not in expected,
            "contains": lambda: expected in actual,
        }[op]()
    except TypeError:
        return False  # incomparable types never match


class RulesEngine:
    """Evaluates events against one ruleset."""

    def __init__(self, ruleset: Ruleset, game_id: str | None = None):
        self.ruleset = ruleset
        self.game_id = game_id

    def evaluate(self, event: Event) -> list[RuleViolation]:
        out = []
        for rule in self.ruleset.rules:
            if rule.event_type != event.type or rule.failure_behavior == "IGNORE":
                continue
            if rule.detection in ("manual", "unavailable") or not rule.applies_to(self.game_id):
                continue
            if evaluate_condition(rule.condition, event.payload):
                out.append(RuleViolation(rule, event))
        return out

    def outcome(self, event: Event) -> str:
        """Worst outcome of ``event``: VALID < WARNING < VIOLATION < RUN_FAILED."""
        found = [v.outcome for v in self.evaluate(event)]
        return max(found, key=OUTCOMES.index) if found else "VALID"

    def manual_checklist(self) -> list[Rule]:
        return [r for r in self.ruleset.rules if r.severity == "MANUAL"]
