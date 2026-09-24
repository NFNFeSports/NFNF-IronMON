"""Scripted input for automated gameplay (integration tests, `selftest`, bots).

A script is a list of tokens, run against a live core with feedback from the
game's memory reader:

    A / B / START / UP ...   tap (hold 6 frames, then wait; ``A:40`` waits 40)
    A*12                     repeat
    WAIT120                  idle frames
    HOLDUP20                 hold a button for N frames
    GOTO7,4                  walk to map coordinates (x first, then y) using live position
    DIR_UNTIL:UP,y=1         step in a direction until x=/y= reaches a value, or "map" changes
    FACE:RIGHT               turn to face a direction
    UNTIL_VALID              cycle A,A,A,START until the game clock runs (intro, naming screens)
    MASH_UNTIL_FREE:DOWN     advance dialogue (A,A,B) until stepping DOWN really moves the player
    MASH_UNTIL_AREA:Oaks_Lab tap A until the area name matches
    UNTIL_PARTY[:RIGHT]      cycle (face RIGHT,) A,A,A until the party is non-empty
    WILD                     walk left/right until a battle starts (max 80 laps)
    POKE_ENEMY_HP:1          TEST ONLY: set the opposing active Pokémon's HP (deterministic battles)
    POKE_PARTY_HP:0          TEST ONLY: set party slot 0 HP (deterministic faint)

The paths below were recorded by playing each game from boot to the starter
with the NFNF tracker as feedback. Randomization changes species/trainers but
not maps or script flow, so the paths work for any IronMON seed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Iterator

from .emulators.libretro import MEMORY_SYSTEM_RAM, LibretroCore
from .tracker.memory import GbaBus, GbWram
from .tracker.state import GameState

INTRO_TO_STARTER = {
    "firered": ["WAIT400", "START:60*4", "A:40*30", "A*12", "START WAIT30 A*10", "DOWN A WAIT30 A*14 WAIT200",
                "GOTO10,6 GOTO10,2 HOLDUP20 WAIT60", "HOLDLEFT20 WAIT90",
                "GOTO10,8 GOTO4,8 HOLDDOWN30 WAIT90", "GOTO12,8 GOTO12,1 HOLDUP40 WAIT60",
                "A*10 WAIT200 A*10 WAIT200 A*20 WAIT100", "A*20 WAIT60 B*4 WAIT30",
                "GOTO7,4 HOLDRIGHT4 WAIT20 A WAIT60 A WAIT60", "A WAIT120 A*3 WAIT60 DOWN A WAIT60",
                "UNTIL_PARTY", "START WAIT60 A*25 WAIT120"],
    "red": ["WAIT400", "UNTIL_VALID", "DIR_UNTIL:RIGHT,x=5", "DIR_UNTIL:UP,y=1", "DIR_UNTIL:RIGHT,map",
            "DIR_UNTIL:DOWN,y=6", "DIR_UNTIL:LEFT,x=3", "DIR_UNTIL:DOWN,map",
            "DIR_UNTIL:RIGHT,x=10", "DIR_UNTIL:UP,y=1", "MASH_UNTIL_AREA:Oaks_Lab", "MASH_UNTIL_FREE:UP",
            "DIR_UNTIL:UP,y=3", "A*12 WAIT60", "UNTIL_PARTY:RIGHT", "START WAIT90 A*10 WAIT120"],
    "silver": ["WAIT600", "UNTIL_VALID", "DIR_UNTIL:UP,y=1", "DIR_UNTIL:RIGHT,x=7", "DIR_UNTIL:UP,map",
               "DIR_UNTIL:LEFT,x=8", "MASH_UNTIL_FREE:DOWN", "DIR_UNTIL:DOWN,y=7", "DIR_UNTIL:LEFT,x=7",
               "DIR_UNTIL:DOWN,map", "DIR_UNTIL:LEFT,x=6", "DIR_UNTIL:UP,map", "MASH_UNTIL_FREE:DOWN",
               "DIR_UNTIL:RIGHT,x=5", "UNTIL_PARTY:RIGHT", "START WAIT90 A*20 WAIT60"],
}

#: FireRed after the starter: rival battle (forced win), then Route 1 grass.
FIRERED_TO_ROUTE1_ENCOUNTER = [
    "GOTO7,6 GOTO7,10 HOLDDOWN30 WAIT60 A*8 WAIT200",   # rival battle starts
    "POKE_ENEMY_HP:1", "A*40 WAIT120 A*20 WAIT120",     # our first hit wins deterministically
    "A*10 WAIT60 B*6", "GOTO7,12 HOLDDOWN40 WAIT90",     # leave the lab
    "GOTO12,13 GOTO12,1 HOLDUP60 WAIT60", "WILD",       # north onto Route 1 grass
]


@dataclass
class ScriptContext:
    core: LibretroCore
    reader: object

    def state(self) -> GameState:
        if getattr(self.reader, "generation", 3) == 3:
            return self.reader.read_state(GbaBus(self.core), self.core.frames_run)
        return self.reader.read_state(GbWram(self.core.memory(MEMORY_SYSTEM_RAM)), self.core.frames_run)


def _tokens(script: list[str]) -> list[str]:
    return [t for line in script for t in line.split()]


def run_script(ctx: ScriptContext, script: list[str],
               on_frame: Callable[[int], None] | None = None) -> Iterator[set[str]]:
    """Generator: yields the buttons to hold for each successive frame."""

    def frames(n: int, pressed: set[str] | None = None):
        for _ in range(n):
            yield set(pressed or ())

    for tok in _tokens(script):
        name, _, count = tok.partition("*")
        n = int(count or 1)
        for _ in range(n):
            if name.startswith("WAIT"):
                yield from frames(int(name[4:]))
            elif name.startswith("HOLD"):
                btn = name[4:].rstrip("0123456789")
                yield from frames(int(name[4 + len(btn):]), {btn})
            elif name.startswith("GOTO"):
                tx, ty = map(int, name[4:].split(","))
                for axis, target in ((0, tx), (1, ty)):
                    for _ in range(80):
                        xy = ctx.state().player_xy
                        if xy is None or xy[axis] == target:
                            break
                        btn = (("RIGHT" if xy[0] < target else "LEFT") if axis == 0
                               else ("DOWN" if xy[1] < target else "UP"))
                        yield from frames(8, {btn})
                        yield from frames(8)
            elif name.split(":")[0] in ("UNTIL_PARTY", "UNTIL_VALID"):
                face = name.split(":")[1] if ":" in name else None   # re-face the target each cycle
                done = (lambda st: bool(st.party)) if name.startswith("UNTIL_PARTY") else (lambda st: st.session_valid)
                presses = 0
                while presses < 1500 and not done(ctx.state()):
                    # START finishes naming screens.
                    # (no B during the intro: on Gen 1's main menu B returns to the title screen)
                    # (B is never used: it declines YES/NO prompts such as "You want this Pokémon?")
                    # UNTIL_PARTY never presses START either: in the overworld it opens the menu.
                    cycle = ("A", "A", "A") if name.startswith("UNTIL_PARTY") else ("A", "A", "A", "START")
                    btn = cycle[presses % len(cycle)]
                    if face:
                        # before EVERY press: a turn made while a text box is closing is ignored,
                        # and an A while facing the NPC restarts their dialogue. 16 frames is long
                        # enough for Gen 1/2 to register the turn; the target blocks movement.
                        yield from frames(16, {face})
                        yield from frames(20)
                    yield from frames(6, {btn})
                    yield from frames(24)
                    presses += 1
                for _ in range(2):   # a trailing START may have opened the menu: close it
                    yield from frames(6, {"B"})
                    yield from frames(24)
            elif name.startswith("DIR_UNTIL:"):
                direction, cond = name.split(":", 1)[1].split(",")
                start = ctx.state()
                for _ in range(40):
                    st = ctx.state()
                    if cond == "map" and st.area_id != start.area_id:
                        break
                    if cond.startswith(("x=", "y=")) and st.player_xy and \
                            st.player_xy[0 if cond[0] == "x" else 1] == int(cond[2:]):
                        break
                    yield from frames(12, {direction})   # one tile per short press (no overshoot)
                    yield from frames(12)
                yield from frames(20)
            elif name.startswith("FACE:"):
                yield from frames(6, {name[5:]})
                yield from frames(12)
            elif name.startswith("MASH_UNTIL_FREE:"):
                direction = name.split(":", 1)[1]
                dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[direction]
                for _ in range(200):
                    before = ctx.state().player_xy
                    yield from frames(12, {direction})
                    yield from frames(12)
                    after = ctx.state().player_xy
                    # only a step in the pressed direction counts (cutscenes move the player too)
                    if before and after and (after[0] - before[0], after[1] - before[1]) == (dx, dy):
                        break
                    for btn in ("A", "A", "B"):
                        yield from frames(6, {btn})
                        yield from frames(24)
            elif name.startswith("MASH_UNTIL_AREA:"):
                want = name.split(":", 1)[1].replace("_", " ")
                for _ in range(300):
                    if (ctx.state().area_name or "") == want:
                        break
                    yield from frames(6, {"A"})
                    yield from frames(24)
            elif name == "WILD":
                for _ in range(80):
                    if ctx.state().battle.in_battle:
                        break
                    yield from frames(34, {"LEFT"})
                    yield from frames(34, {"RIGHT"})
            elif name.startswith("POKE_ENEMY_HP:"):
                poke_enemy_hp(ctx, int(name.split(":")[1]))
            elif name.startswith("POKE_PARTY_HP:"):
                poke_party_hp(ctx, 0, int(name.split(":")[1]))
            else:
                btn, _, wait = name.partition(":")
                yield from frames(6, {btn})
                yield from frames(int(wait or 24))


def poke_enemy_hp(ctx: ScriptContext, hp: int) -> None:
    """TEST ONLY: set the opposing active battler's HP (Gen 3: gBattleMons[1].hp)."""
    a = ctx.reader.a
    ctx.core.write_bus(a["gBattleMons"] + 0x58 + 0x28, struct.pack("<H", hp))


def poke_party_hp(ctx: ScriptContext, slot: int, hp: int) -> None:
    """TEST ONLY: set a party Pokémon's HP in real game memory (HP is outside the Gen 3 checksum)."""
    a = ctx.reader.a
    if getattr(ctx.reader, "generation", 3) == 3:
        base = a["gPlayerParty"] + slot * 100
        ctx.core.write_bus(base + 86, struct.pack("<H", hp))
        if hp == 0:   # keep the active battler consistent when in battle
            ctx.core.write_bus(a["gBattleMons"] + 0x28, struct.pack("<H", 0))
    else:
        base = a["wPartyMons" if "wPartyMons" in a else "wPartyMon1"]
        size = ctx.reader.party_size
        off = GbWram(b"").offset(base + slot * size + ctx.reader.layout["hp"])
        ctx.core.write_memory(MEMORY_SYSTEM_RAM, off, struct.pack(">H", hp))


def play_script(core: LibretroCore, reader, script: list[str], poll_every: int = 15,
                on_state: Callable[[GameState], None] | None = None) -> None:
    """Run a script directly on a core (no window), optionally feeding states to a tracker."""
    ctx = ScriptContext(core, reader)
    for i, pressed in enumerate(run_script(ctx, script)):
        core.run_frames(1, pressed)
        if on_state and (i + 1) % poll_every == 0:
            on_state(ctx.state())
