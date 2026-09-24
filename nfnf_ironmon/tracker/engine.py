"""Tracker engine: successive GameStates → gameplay events (pure and deterministic).

The engine never reads memory itself. It compares the previous and current
state and emits events for the event bus. Anything uncertain is either
delayed until confirmed (faints) or labelled as heuristic in the payload.

Faint confirmation: a Pokémon counts as fainted only after its HP reads 0
(with a valid, checksum-verified struct and a positive max HP) in
``faint_confirm_polls`` consecutive polls. A single transient zero — e.g. a
half-updated party during a swap — never fails a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..events import EventType, register_event_type
from .state import GameState, PokemonState

STARTER_OBTAINED = register_event_type("STARTER_OBTAINED", "First Pokémon of the run obtained")
PARTY_HEALED = register_event_type("PARTY_HEALED", "Whole party restored outside a Pokémon Center")
POKEMON_REVIVED = register_event_type("POKEMON_REVIVED", "A fainted Pokémon has HP again")


@dataclass
class TrackerEvent:
    type: str
    payload: dict[str, Any]


@dataclass
class TrackerConfig:
    faint_confirm_polls: int = 3
    battle_enemy_wait_polls: int = 8
    #: a party composition must be read identically this many polls in a row
    #: (the game rewrites party memory at battle start; one odd read is noise)
    party_stable_polls: int = 2


@dataclass
class TrackerMemory:
    """Per-run tracker knowledge, persisted in runs/<id>/tracker.json."""
    starter_uid: str | None = None
    starter_species: str | None = None
    session_valid: bool = False
    last_play_time: int | None = None
    area_id: str | None = None
    area_name: str | None = None
    badges: int | None = None
    party_uids: list[str] = field(default_factory=list)
    party_levels: dict[str, int] = field(default_factory=dict)
    known_uids: list[str] = field(default_factory=list)
    fainted: list[str] = field(default_factory=list)
    pending_faint: dict[str, int] = field(default_factory=dict)
    encounters: dict[str, dict] = field(default_factory=dict)     # area_id -> first encounter
    seen_species: list[str] = field(default_factory=list)
    caught_species: list[str] = field(default_factory=list)
    in_battle: bool = False
    battle_announced: bool = False
    battle_wait: int = 0
    battle_kind: str | None = None
    battle_enemy: str | None = None
    battle_area: str | None = None
    last_outcome: str | None = None
    party_damaged: bool = False
    blackout_pending: bool = False
    candidate_party: list[str] = field(default_factory=list)
    candidate_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackerMemory":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _mon(p: PokemonState, starter: str | None) -> dict[str, Any]:
    return {"uid": p.uid, "species": p.species, "level": p.level, "hp": p.hp, "max_hp": p.max_hp,
            "status": p.status, "slot": p.slot, "is_starter": p.uid == starter}


class TrackerEngine:
    def __init__(self, game_id: str, *, badge_names: tuple[str, ...] = (),
                 outcome_name: Callable[[int | None], str | None] = lambda c: None,
                 is_pokemon_center: Callable[[GameState], bool | None] = lambda s: None,
                 config: TrackerConfig | None = None, memory: TrackerMemory | None = None):
        self.game_id = game_id
        self.badge_names = badge_names
        self.outcome_name = outcome_name
        self.is_pokemon_center = is_pokemon_center
        self.cfg = config or TrackerConfig()
        self.m = memory or TrackerMemory()
        self.last_state: GameState | None = None

    # ------------------------------------------------------------------
    def update(self, s: GameState) -> list[TrackerEvent]:
        m, out = self.m, []

        def emit(etype: str, **payload: Any) -> None:
            payload.setdefault("play_time_frames", s.play_time_frames)
            payload.setdefault("area", m.area_name)
            payload.setdefault("area_id", m.area_id)
            out.append(TrackerEvent(etype, payload))

        # --- session: title/reset/load --------------------------------
        if not s.session_valid:
            if m.session_valid:
                emit(EventType.GAME_RESET, kind="soft_reset_or_title", heuristic=True)
                m.session_valid = False
                m.in_battle = False
                m.pending_faint.clear()
            self.last_state = s
            return out
        if not m.session_valid:
            if m.last_play_time is not None:
                emit(EventType.SAVE_LOADED, kind="save", previous_play_time_frames=m.last_play_time)
            else:
                emit(EventType.GAME_LOADED)
            m.session_valid = True
        if s.play_time_frames is not None:
            m.last_play_time = s.play_time_frames

        # --- area ------------------------------------------------------
        if s.area_id and s.area_id != m.area_id:
            prev = m.area_name
            m.area_id, m.area_name = s.area_id, s.area_name
            emit(EventType.AREA_CHANGED, previous=prev)

        # --- badges ----------------------------------------------------
        if s.badges is not None:
            if m.badges is not None:
                new = s.badges & ~m.badges
                for bit in range(16):
                    if new >> bit & 1:
                        name = self.badge_names[bit] if bit < len(self.badge_names) else f"Badge {bit + 1}"
                        emit(EventType.BADGE_ACQUIRED, badge=name, badge_index=bit + 1,
                             badge_count=bin(s.badges).count("1"))
            m.badges = s.badges

        # --- party (composition debounced) -------------------------------
        party = list(s.party)
        uids = [p.uid for p in party]
        party_stable = True
        if uids != m.party_uids and m.party_uids:
            if uids == m.candidate_party:
                m.candidate_count += 1
            else:
                m.candidate_party, m.candidate_count = uids, 1
            party_stable = m.candidate_count >= self.cfg.party_stable_polls
        if party_stable:
            m.candidate_party, m.candidate_count = [], 0
            self._party_checks(s, party, uids, emit)
        self._battle_checks(s, emit)
        self.last_state = s
        return out

    def _party_checks(self, s: GameState, party: list[PokemonState], uids: list[str], emit) -> None:
        m = self.m
        if m.starter_uid is None and party:
            m.starter_uid, m.starter_species = party[0].uid, party[0].species
            emit(STARTER_OBTAINED, species=party[0].species, level=party[0].level, uid=party[0].uid)
        for p in party:
            if p.uid not in m.known_uids:
                if m.known_uids:   # not the starter
                    method = "caught" if m.last_outcome == "CAUGHT" else "received"
                    emit(EventType.POKEMON_CAPTURED, species=p.species, level=p.level, uid=p.uid,
                         method=method, is_starter=False)
                    if method == "caught" and p.species not in m.caught_species:
                        m.caught_species.append(p.species)
                m.known_uids.append(p.uid)
        levels = {p.uid: p.level for p in party if p.level is not None}
        if uids != m.party_uids or levels != m.party_levels:
            emit(EventType.PARTY_CHANGED, party=[_mon(p, m.starter_uid) for p in party],
                 starter_in_party=m.starter_uid in uids if m.starter_uid else None)
            m.party_uids, m.party_levels = uids, levels

        # --- faints (debounced) --------------------------------------------
        for p in party:
            if p.max_hp and p.hp == 0:
                if p.uid in m.fainted:
                    continue
                m.pending_faint[p.uid] = m.pending_faint.get(p.uid, 0) + 1
                if m.pending_faint[p.uid] >= self.cfg.faint_confirm_polls:
                    m.fainted.append(p.uid)
                    m.pending_faint.pop(p.uid, None)
                    enemy = s.battle.enemy
                    emit(EventType.POKEMON_FAINTED, species=p.species, level=p.level, uid=p.uid,
                         is_starter=p.uid == m.starter_uid, in_battle=s.battle.in_battle,
                         cause=enemy.species if (s.battle.in_battle and enemy) else None,
                         confirmed_polls=self.cfg.faint_confirm_polls)
            else:
                m.pending_faint.pop(p.uid, None)
                if p.uid in m.fainted and p.hp:
                    m.fainted.remove(p.uid)
                    emit(POKEMON_REVIVED, species=p.species, uid=p.uid, is_starter=p.uid == m.starter_uid)
        if party and all(p.hp == 0 for p in party if p.max_hp):
            m.blackout_pending = True

        # --- heals -----------------------------------------------------------
        full = bool(party) and all(p.hp == p.max_hp and p.status in ("OK", None) for p in party)
        if not s.battle.in_battle:
            if full and m.party_damaged:
                at_center = self.is_pokemon_center(s)
                if at_center:
                    emit(EventType.POKECENTER_HEAL, forced=m.blackout_pending, heuristic=False)
                else:
                    emit(PARTY_HEALED, at_pokemon_center=at_center, forced=m.blackout_pending)
                m.blackout_pending = False
            m.party_damaged = bool(party) and not full

    def _battle_checks(self, s: GameState, emit) -> None:
        m = self.m
        b = s.battle
        if b.in_battle and not m.in_battle:
            m.in_battle, m.battle_announced, m.battle_wait = True, False, 0
            m.battle_kind, m.battle_area = b.kind, m.area_id
        if m.in_battle and b.in_battle and not m.battle_announced:
            m.battle_wait += 1
            if b.enemy or m.battle_wait >= self.cfg.battle_enemy_wait_polls:
                m.battle_announced = True
                enemy = b.enemy
                m.battle_enemy = enemy.species if enemy else None
                lead_is_starter = (b.player_active_uid == m.starter_uid) if (b.player_active_uid and m.starter_uid) else None
                emit(EventType.BATTLE_STARTED, kind=b.kind, tutorial=b.tutorial, safari=b.safari,
                     enemy=m.battle_enemy, enemy_level=enemy.level if enemy else None,
                     lead_is_starter=lead_is_starter)
                if b.kind == "wild" and not b.tutorial and enemy:
                    first = m.area_id not in m.encounters
                    duplicate = enemy.species in m.seen_species
                    if first:
                        m.encounters[m.area_id] = {"species": enemy.species, "level": enemy.level,
                                                   "area": m.area_name, "captured": False}
                    if enemy.species not in m.seen_species:
                        m.seen_species.append(enemy.species)
                    emit(EventType.WILD_ENCOUNTER, species=enemy.species, level=enemy.level,
                         first_in_area=first, duplicate=duplicate)
                elif b.kind == "trainer":
                    emit(EventType.TRAINER_BATTLE, trainer_id=b.trainer_id, enemy=m.battle_enemy,
                         enemy_level=enemy.level if enemy else None, tutorial=b.tutorial)
        if not b.in_battle and m.in_battle:
            outcome = self.outcome_name(b.outcome)
            m.last_outcome = outcome
            if outcome == "CAUGHT" and m.battle_area in m.encounters and \
                    m.encounters[m.battle_area]["species"] == m.battle_enemy:
                m.encounters[m.battle_area]["captured"] = True
            emit(EventType.BATTLE_ENDED, kind=m.battle_kind, outcome=outcome, enemy=m.battle_enemy)
            m.in_battle = False
        elif b.in_battle:
            m.last_outcome = None

    # ------------------------------------------------------------------
    def rules_view(self) -> dict[str, Any]:
        """Compact summary for the HUD."""
        return {"starter": self.m.starter_species, "encounters": dict(self.m.encounters),
                "fainted": len(self.m.fainted), "badges": bin(self.m.badges or 0).count("1")}
