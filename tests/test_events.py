"""7. Event processing."""

import unittest

from nfnf_ironmon.events import (Event, EventBus, EventType, is_registered, register_event_type)


class EventBusTests(unittest.TestCase):
    def test_typed_and_wildcard_subscribers(self):
        bus, seen = EventBus(strict=True), []
        bus.subscribe(lambda e: seen.append(("typed", e.type)), EventType.BADGE_ACQUIRED)
        bus.subscribe(lambda e: seen.append(("all", e.type)))
        bus.publish(Event(EventType.BADGE_ACQUIRED, {"badge": "Boulder"}))
        bus.publish(Event(EventType.AREA_CHANGED))
        self.assertEqual(seen, [("typed", "BADGE_ACQUIRED"), ("all", "BADGE_ACQUIRED"),
                                ("all", "AREA_CHANGED")])

    def test_unsubscribe(self):
        bus, seen = EventBus(), []
        unsub = bus.subscribe(seen.append, EventType.GAME_RESET)
        unsub()
        bus.publish(Event(EventType.GAME_RESET))
        self.assertEqual(seen, [])

    def test_unknown_types_rejected(self):
        bus = EventBus()
        with self.assertRaises(ValueError):
            bus.publish(Event("NOT_REGISTERED"))
        with self.assertRaises(ValueError):
            bus.subscribe(print, "NOT_REGISTERED")
        with self.assertRaises(ValueError):
            register_event_type("bad-name")

    def test_extensible(self):
        name = register_event_type("SHINY_ENCOUNTER", "plugin event")
        self.assertTrue(is_registered(name))
        bus, seen = EventBus(strict=True), []
        bus.subscribe(seen.append, name)
        bus.publish(Event(name, {"species": "Gyarados"}))
        self.assertEqual(seen[0].payload["species"], "Gyarados")

    def test_breadth_first_and_call_soon(self):
        bus, order = EventBus(strict=True), []

        def on_faint(e):
            order.append("faint:A")
            bus.publish(Event(EventType.RULE_VIOLATION))
            bus.call_soon(lambda: order.append("deferred"))

        bus.subscribe(on_faint, EventType.POKEMON_FAINTED)
        bus.subscribe(lambda e: order.append("faint:B"), EventType.POKEMON_FAINTED)
        bus.subscribe(lambda e: order.append("violation"), EventType.RULE_VIOLATION)
        bus.publish(Event(EventType.POKEMON_FAINTED))
        # every faint handler runs before the nested event, deferred work runs last
        self.assertEqual(order, ["faint:A", "faint:B", "violation", "deferred"])

    def test_handler_errors_are_isolated(self):
        bus, seen = EventBus(strict=False), []
        bus.subscribe(lambda e: 1 / 0, EventType.GAME_LOADED)
        bus.subscribe(seen.append, EventType.GAME_LOADED)
        with self.assertLogs("nfnf_ironmon.events", "ERROR"):
            bus.publish(Event(EventType.GAME_LOADED))
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(bus.errors), 1)

    def test_all_spec_event_types_registered(self):
        spec = """RUN_STARTED RUN_ENDED GAME_LOADED GAME_RESET SAVE_LOADED SAVE_CREATED AREA_CHANGED
        BATTLE_STARTED BATTLE_ENDED WILD_ENCOUNTER TRAINER_BATTLE POKEMON_CAPTURED POKEMON_FAINTED
        PARTY_CHANGED ITEM_ACQUIRED BADGE_ACQUIRED ROM_LOADED EMULATOR_STARTED EMULATOR_STOPPED
        RULE_VIOLATION INTEGRITY_WARNING""".split()
        self.assertEqual([t for t in spec if not is_registered(t)], [])


if __name__ == "__main__":
    unittest.main()
