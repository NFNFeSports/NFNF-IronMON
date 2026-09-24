"""Per-game capability matrix, shown in the UI, `nfnf-ironmon games` and the docs.

Statuses (never inflated):
    TESTED       exercised by automated tests on real game data (the user's dumps)
    PARTIAL      works with a documented limitation
    IMPLEMENTED  code exists and is unit-tested with synthetic data, not on a real game
    UNTESTED     should work through shared code, but nothing here exercised it
    UNAVAILABLE  not possible / not implemented
"""

from __future__ import annotations

FEATURES = ("Emulation", "Controller", "Audio", "Save", "Randomizer", "Tracker", "Encounters", "Deaths",
            "Rules", "Automatic runs")

_EVIDENCE = {
    # tests/integration/test_real_gameplay.py + manual play-through with the tracker (docs/tracker.md)
    "firered": {"Emulation": "TESTED", "Controller": "PARTIAL", "Audio": "PARTIAL", "Save": "TESTED",
                "Randomizer": "TESTED", "Tracker": "TESTED", "Encounters": "TESTED", "Deaths": "TESTED",
                "Rules": "TESTED", "Automatic runs": "TESTED"},
    "red": {"Emulation": "TESTED", "Controller": "PARTIAL", "Audio": "PARTIAL", "Save": "TESTED",
            "Randomizer": "TESTED", "Tracker": "TESTED", "Encounters": "IMPLEMENTED", "Deaths": "TESTED",
            "Rules": "TESTED", "Automatic runs": "TESTED"},
    "silver": {"Emulation": "TESTED", "Controller": "PARTIAL", "Audio": "PARTIAL", "Save": "TESTED",
               "Randomizer": "PARTIAL", "Tracker": "TESTED", "Encounters": "TESTED", "Deaths": "TESTED",
               "Rules": "TESTED", "Automatic runs": "TESTED"},
}
NOTES = {
    "Controller": "keyboard + simulated pads tested; no physical Xbox controller was available",
    "Audio": "output verified to reach PulseAudio at the right rate; speakers not verifiable here",
    "silver:Randomizer": "official IronMON strings target Crystal; the community notes Gold/Silver may not "
                         "randomize completely",
    "red:Encounters": "wild-encounter detection unit-tested; a live Gen 1 wild battle was not recorded",
}


def game_capabilities(adapter) -> dict[str, str]:
    if adapter.game_id in _EVIDENCE:
        return dict(_EVIDENCE[adapter.game_id])
    if adapter.status == "supported":
        return {f: "UNTESTED" for f in FEATURES}
    caps = {f: "UNAVAILABLE" for f in FEATURES}
    caps.update({"Emulation": "UNTESTED", "Randomizer": "UNTESTED"})   # shared core / UPR support it
    return caps


def overall(adapter) -> str:
    if adapter.status != "supported":
        return "DETECTED ONLY"
    caps = game_capabilities(adapter).values()
    return "SUPPORTED" if all(c in ("TESTED", "PARTIAL") for c in caps) and \
        sum(c == "PARTIAL" for c in caps) <= 3 else "PARTIAL"


def matrix(games) -> list[dict]:
    return [{"id": g.game_id, "name": g.display_name, "generation": g.generation, "overall": overall(g),
             "capabilities": game_capabilities(g)} for g in games.all()]


def render(games) -> str:
    rows = matrix(games)
    head = f"{'Game':<18}{'Overall':<15}" + "".join(f"{f[:10]:<12}" for f in FEATURES)
    lines = [head]
    for r in rows:
        lines.append(f"{r['name']:<18}{r['overall']:<15}" + "".join(f"{r['capabilities'][f][:10]:<12}" for f in FEATURES))
    lines += ["", "Notes:"] + [f"  {k}: {v}" for k, v in NOTES.items()]
    return "\n".join(lines)
