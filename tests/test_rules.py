"""6. Ruleset loading and rule evaluation."""

import copy
import unittest

from helpers import AppTestCase

from nfnf_ironmon.events import Event, EventType
from nfnf_ironmon.paths import BUNDLE_ROOT
from nfnf_ironmon.rules import (RulesEngine, RulesetError, RulesetRepository, evaluate_condition,
                                parse_ruleset)

VALID = {
    "id": "t", "name": "T", "version": 1,
    "rules": [{"id": "r1", "description": "d", "severity": "HARD", "detection": "automatic",
               "event_type": "POKEMON_FAINTED", "failure_behavior": "FAIL_RUN",
               "condition": {"field": "is_starter", "op": "eq", "value": True}}],
}


def variant(**rule_changes):
    data = copy.deepcopy(VALID)
    data["rules"][0].update(rule_changes)
    return data


class RulesetLoadingTests(unittest.TestCase):
    def test_bundled_rulesets_load(self):
        repo = RulesetRepository(BUNDLE_ROOT / "rules")
        ids = {rs.id for rs in repo.list()}
        self.assertEqual(ids, {"standard-ironmon", "kaizo-ironmon", "custom"})
        std = repo.load("standard-ironmon")
        self.assertEqual(std.name, "Standard IronMON")
        self.assertEqual({r.severity for r in std.rules}, {"HARD", "SOFT", "MANUAL"})
        self.assertEqual(len(std.sha256), 64)

    def test_validation_errors(self):
        bad = [
            variant(severity="FATAL"),
            variant(detection="manual"),                       # HARD must be automatic
            variant(event_type="NOT_AN_EVENT"),
            variant(failure_behavior="EXPLODE"),
            variant(condition={"field": "x", "op": "like", "value": 1}),
            variant(condition={"field": "x", "op": "eq"}),     # missing value
            variant(severity="MANUAL", detection="manual", event_type=None),  # MANUAL can't FAIL_RUN
        ]
        for data in bad:
            with self.subTest(data=data["rules"][0]):
                with self.assertRaises(RulesetError):
                    parse_ruleset(data)
        dup = copy.deepcopy(VALID)
        dup["rules"].append(dup["rules"][0])
        with self.assertRaises(RulesetError):
            parse_ruleset(dup)

    def test_hash_changes_with_content(self):
        a = parse_ruleset(VALID)
        b = parse_ruleset(variant(description="changed"))
        self.assertNotEqual(a.sha256, b.sha256)


class ConditionTests(unittest.TestCase):
    def test_operators(self):
        p = {"level": 12, "species": "Pidgey", "tags": ["x"], "nested": {"a": 1}}
        cases = [
            ({"field": "level", "op": "gt", "value": 10}, True),
            ({"field": "level", "op": "lte", "value": 11}, False),
            ({"field": "species", "op": "in", "value": ["Pidgey", "Rattata"]}, True),
            ({"field": "species", "op": "not_in", "value": ["Pidgey"]}, False),
            ({"field": "nested.a", "op": "eq", "value": 1}, True),
            ({"field": "missing", "op": "missing"}, True),
            ({"field": "missing", "op": "eq", "value": None}, False),
            ({"field": "tags", "op": "contains", "value": "x"}, True),
            ({"field": "species", "op": "gt", "value": 5}, False),  # incomparable -> no match
            ({"all": [{"field": "level", "op": "eq", "value": 12},
                      {"not": {"field": "species", "op": "eq", "value": "Mew"}}]}, True),
            ({"any": [{"field": "level", "op": "eq", "value": 1},
                      {"field": "level", "op": "eq", "value": 2}]}, False),
        ]
        for cond, expected in cases:
            with self.subTest(cond=cond):
                self.assertEqual(evaluate_condition(cond, p), expected)


class EngineTests(AppTestCase):
    def test_standard_rules(self):
        engine = RulesEngine(self.app.rulesets.load("standard-ironmon"), "firered")
        fainted = engine.evaluate(Event(EventType.POKEMON_FAINTED, {"is_starter": True}))
        self.assertEqual([v.rule.id for v in fainted], ["starter-faint-ends-run"])
        self.assertEqual(engine.evaluate(Event(EventType.POKEMON_FAINTED, {"is_starter": False})), [])
        forced = engine.evaluate(Event(EventType.POKECENTER_HEAL, {"forced": True}))
        self.assertEqual(forced, [])
        state = engine.evaluate(Event(EventType.SAVE_LOADED, {"kind": "savestate"}))
        self.assertEqual(state[0].rule.failure_behavior, "FLAG_FOR_REVIEW")
        self.assertEqual({r.id for r in engine.manual_checklist()},
                         {"randomizer-settings", "item-usage-limits"})

    def test_outcomes(self):
        engine = RulesEngine(self.app.rulesets.load("standard-ironmon"), "firered")
        self.assertEqual(engine.outcome(Event(EventType.BADGE_ACQUIRED, {"badge": "Boulder"})), "VALID")
        self.assertEqual(engine.outcome(Event(EventType.SAVE_LOADED, {"kind": "savestate"})), "VIOLATION")
        self.assertEqual(engine.outcome(Event(EventType.POKEMON_FAINTED, {"is_starter": True})), "RUN_FAILED")
        custom = RulesEngine(self.app.rulesets.load("custom"), "firered")
        self.assertEqual(custom.outcome(Event(EventType.AREA_CHANGED, {"area": "Safari Zone"})), "WARNING")
        v = engine.evaluate(Event(EventType.POKEMON_FAINTED, {"is_starter": True}))[0]
        self.assertEqual(v.to_payload()["outcome"], "RUN_FAILED")

    def test_game_specific_rule(self):
        custom = self.app.rulesets.load("custom")
        ev = Event(EventType.AREA_CHANGED, {"area": "Safari Zone Entrance"})
        self.assertEqual(len(RulesEngine(custom, "firered").evaluate(ev)), 1)
        self.assertEqual(RulesEngine(custom, "red").evaluate(ev), [])


if __name__ == "__main__":
    unittest.main()
