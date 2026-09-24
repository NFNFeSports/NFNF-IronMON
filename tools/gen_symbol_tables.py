"""Regenerate nfnf_ironmon/tracker/data/*.json — memory address tables for the native tracker.

Sources (addresses are facts about the retail binaries):
  * pret decompilation symbol files (github.com/pret/<project>, branch "symbols")
  * besteon/Ironmon-Tracker GameAddresses/*.json (MIT) — cross-checked where both exist

Run:  python3 tools/gen_symbol_tables.py   (network; developer step only)
"""

import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "nfnf_ironmon" / "tracker" / "data"
PRET = "https://raw.githubusercontent.com/pret/{repo}/symbols/{sym}"
TRACKER = ("https://raw.githubusercontent.com/besteon/Ironmon-Tracker/"
           "41e671124fbc1e944480adfe62fc62dac26fd5b8/ironmon_tracker/GameAddresses/{name}")

GEN3_SYMBOLS = ["gPlayerParty", "gPlayerPartyCount", "gEnemyParty", "gBattleTypeFlags", "gBattleOutcome",
                "gBattleMons", "gMain", "gSaveBlock1Ptr", "gSaveBlock2Ptr", "gMapHeader", "BattleMainCB2",
                "CB2_Overworld", "gSpeciesNames", "sMapNames", "gTrainerBattleOpponent_A",
                "gBattlerPartyIndexes"]
# tracker key -> pret symbol, for cross-checking
CROSS = {"pstats": "gPlayerParty", "estats": "gEnemyParty", "gPlayerPartyCount": "gPlayerPartyCount",
         "gBattleTypeFlags": "gBattleTypeFlags", "gBattleOutcome": "gBattleOutcome",
         "gSaveBlock1ptr": "gSaveBlock1Ptr", "gSaveBlock2ptr": "gSaveBlock2Ptr", "gMapHeader": "gMapHeader",
         "gBattleMons": "gBattleMons", "gTrainerBattleOpponent_A": "gTrainerBattleOpponent_A"}

# pret include/constants/battle.h (pokefirered) and data/layouts/layouts.json
FRLG_EXTRA = {"saveblock1_flags_offset": 0xEE0, "badge_flag_base": 0x820, "mapsec_base": 0x58,
              "battle_type": {"DOUBLE": 1 << 0, "TRAINER": 1 << 3, "FIRST_BATTLE": 1 << 4, "SAFARI": 1 << 7,
                              "OLD_MAN_TUTORIAL": 1 << 9, "GHOST": 1 << 15, "POKEDUDE": 1 << 16},
              "pokemon_center_1f_layouts": [8, 212, 271]}

GAMES = {
    "firered-v1.1": dict(repo="pokefirered", sym="pokefirered_rev1.sym", tracker="Pokemon FireRed v1.1.json",
                         symbols=GEN3_SYMBOLS, extra=FRLG_EXTRA),
    "firered-v1.0": dict(repo="pokefirered", sym="pokefirered.sym", tracker="Pokemon FireRed v1.0.json",
                         symbols=GEN3_SYMBOLS, extra=FRLG_EXTRA),
    "leafgreen-v1.1": dict(repo="pokefirered", sym="pokeleafgreen_rev1.sym",
                           tracker="Pokemon LeafGreen v1.1.json", symbols=GEN3_SYMBOLS,
                           extra=FRLG_EXTRA),
    "red": dict(repo="pokered", sym="pokered.sym", tracker=None, symbols=[
        "wPartyCount", "wPartySpecies", "wPartyMons", "wPartyMonNicks", "wIsInBattle", "wEnemyMonSpecies",
        "wEnemyMon", "wCurMap", "wObtainedBadges", "wPlayTimeHours", "wPlayTimeMinutes", "wPlayTimeSeconds",
        "wPlayTimeFrames", "wBattleResult", "MonsterNames", "wCurOpponent", "wNumHoFTeams", "wXCoord",
        "wYCoord", "wBattleType"], extra={"battle_type": {"OLD_MAN": 1, "SAFARI": 2}},
        maps=("pokered", "flat")),
    "silver": dict(repo="pokegold", sym="pokesilver.sym", tracker=None, symbols=[
        "wPartyCount", "wPartySpecies", "wPartyMon1", "wBattleMode", "wEnemyMonSpecies", "wEnemyMon",
        "wMapGroup", "wMapNumber", "wJohtoBadges", "wKantoBadges", "wGameTimeHours", "wGameTimeMinutes",
        "wGameTimeSeconds", "wGameTimeFrames", "wBattleResult", "PokemonNames", "wOtherTrainerClass",
        "wXCoord", "wYCoord", "wBattleType"], extra={"battle_type": {"TUTORIAL": 3, "CONTEST": 6}},
        maps=("pokegold", "grouped")),
}


def fetch(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nfnf"}), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def parse_sym(text: str) -> dict[str, int]:
    """pret sym lines: 'ADDR [g|l] SIZE name' (GBA) or 'BB:ADDR name' (GB, bank:address)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and len(parts[0]) == 8:
            out[parts[3]] = int(parts[0], 16)
        elif len(parts) == 2 and ":" in parts[0]:
            bank, addr = parts[0].split(":")
            out[parts[1]] = (int(bank, 16) << 16) | int(addr, 16)
    return out


def parse_maps(repo: str, style: str) -> dict[str, str]:
    """Map id -> readable name from pret constants/map_constants.asm."""
    text = fetch(f"https://raw.githubusercontent.com/pret/{repo}/master/constants/map_constants.asm")
    names, value, group, num = {}, 0, 0, 0
    for raw in text.splitlines():
        line = raw.split(";")[0].strip()
        if style == "flat":
            if line.startswith("map_const "):
                name = line.split()[1].rstrip(",")
                names[str(value)] = name.replace("_", " ").title()
                value += 1
            elif line.startswith("const_skip"):
                value += 1
        else:
            if line.startswith("newgroup"):
                group, num = group + 1, 0
            elif line.startswith("map_const "):
                num += 1
                name = line.split()[1].rstrip(",")
                names[f"{group}:{num}"] = name.replace("_", " ").title()
    return names


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for game, g in GAMES.items():
        syms = parse_sym(fetch(PRET.format(repo=g["repo"], sym=g["sym"])))
        missing = [s for s in g["symbols"] if s not in syms]
        if missing:
            print(f"{game}: missing symbols {missing}", file=sys.stderr)
        table = {s: syms[s] for s in g["symbols"] if s in syms}
        checked = []
        if g["tracker"]:
            tj = json.loads(fetch(TRACKER.format(name=g["tracker"].replace(" ", "%20"))))["Addresses"]
            for tk, sym in CROSS.items():
                if tk in tj and sym in table:
                    if int(tj[tk], 16) != table[sym]:
                        print(f"{game}: MISMATCH {tk} tracker={tj[tk]} pret={table[sym]:X}", file=sys.stderr)
                        return 1
                    checked.append(sym)
        data = {"game": game,
                "sources": {"pret": f"github.com/pret/{g['repo']} symbols/{g['sym']}",
                            "ironmon_tracker": (f"besteon/Ironmon-Tracker@41e67112 GameAddresses/{g['tracker']} (MIT)"
                                                if g["tracker"] else None)},
                "cross_checked": checked,
                "addresses": {k: f"0x{v:X}" for k, v in table.items()},
                "constants": g["extra"]}
        if g.get("maps"):
            data["map_names"] = parse_maps(*g["maps"])
            data["sources"]["map_names"] = f"github.com/pret/{g['maps'][0]} constants/map_constants.asm"
        (OUT / f"{game}.json").write_text(json.dumps(data, indent=2) + "\n")
        print(f"{game}: {len(table)} symbols, cross-checked {len(checked)}, missing {missing}, "
              f"maps {len(data.get('map_names', {}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
