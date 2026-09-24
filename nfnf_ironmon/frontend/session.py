"""The NFNF IronMON game window: play a run, track it, fail it, start the next one.

One SDL2 window hosts the whole loop:

    START NEW RUN → (background) randomize → load ROM into the in-process mGBA core
    → fresh save → tracker + rules → RUN ACTIVE → play (video, audio, pad, keyboard)
    → rules confirm a failure → archive → RUN FAILED screen → next run (button or automatic)

Threads: this (render/emulation) thread owns SDL and the core. The tracker
worker persists events and runs the rules. Run preparation (UPR ZX) runs in a
short-lived background thread so the window keeps rendering.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import logging
import os
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import APP_NAME, __version__
from ..career import fmt_duration
from ..emulators.libretro import (MEMORY_SAVE_RAM, MEMORY_SYSTEM_RAM, PIXEL_0RGB1555, PIXEL_RGB565,
                                  PIXEL_XRGB8888, LibretroCore, write_png)
from ..events import EventType
from ..recovery import write_session
from ..runs import RunState
from ..tracker import TrackerConfig, engine_for, reader_for
from ..tracker.memory import GbaBus, GbWram
from ..tracker.worker import TrackerWorker, load_memory
from . import sdl2 as S
from .audio import AudioOutput
from .hud import (BUTTON, CYAN, GREEN, GREY, PANEL, RED, WHITE, YELLOW, Button, TextRenderer, draw_button,
                  fill, outline)
from .input import InputRouter

log = logging.getLogger(__name__)

TEXFMT = {PIXEL_RGB565: S.PIXELFORMAT_RGB565, PIXEL_XRGB8888: S.PIXELFORMAT_RGB888,
          PIXEL_0RGB1555: S.PIXELFORMAT_RGB555}
CORE_OPTIONS = {"mgba_sgb_borders": "OFF", "mgba_skip_bios": "ON"}


@dataclass
class SessionOptions:
    headless: bool = False               # dummy video/audio drivers (tests, clean-container checks)
    max_frames: int | None = None        # stop after N emulated frames (automation)
    script: Callable[["GameSession", int], set[str] | None] | None = None   # automation input per frame
    auto_new_run: bool = False
    auto_new_run_delay: float = 5.0
    volume: int = 70
    muted: bool = False
    integer_scaling: bool = True
    fullscreen: bool = False
    show_hud: bool = True
    tracker_poll_frames: int = 15
    save_check_frames: int = 60
    recovery_seconds: float = 60.0
    heartbeat_seconds: float = 5.0
    unthrottled: bool = False            # run as fast as possible (tests)


@dataclass
class Overlay:
    kind: str
    title: str
    lines: list[tuple[str, tuple[int, int, int]]] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    rows: list[tuple[str, Callable[[], str], Callable[[int], None]]] = field(default_factory=list)
    selected: int = 0
    on_cancel: Callable[[], None] | None = None
    created: float = field(default_factory=time.monotonic)
    auto: tuple[float, Callable[[], None]] | None = None   # (deadline, action)


@dataclass
class SessionResult:
    runs_played: list[str] = field(default_factory=list)
    frames: int = 0
    quit_reason: str = "quit"


class GameSession:
    def __init__(self, app, options: SessionOptions | None = None):
        self.app = app
        self.opt = options or SessionOptions()
        cfg = app.config.section("session")
        for key in ("volume", "muted", "integer_scaling", "fullscreen", "show_hud", "auto_new_run",
                    "tracker_poll_frames", "recovery_seconds"):
            if key in cfg and cfg[key] is not None:
                setattr(self.opt, key, cfg[key])
        if self.opt.headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        sdl_dir = app.components.resolve("sdl2")
        self.sdl = S.load_sdl(sdl_dir.parent if sdl_dir else None)
        sdl = self.sdl
        for k, v in (("SDL_APP_NAME", APP_NAME), ("SDL_RENDER_SCALE_QUALITY", "0"),
                     ("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1"), ("SDL_VIDEO_ALLOW_SCREENSAVER", "0")):
            sdl.SetHint(k.encode(), v.encode())
        sdl.check(sdl.Init(S.INIT_VIDEO | S.INIT_AUDIO | S.INIT_GAMECONTROLLER | S.INIT_EVENTS), "SDL_Init")
        flags = S.WINDOW_RESIZABLE | (S.WINDOW_HIDDEN if self.opt.headless else S.WINDOW_SHOWN)
        self.window = sdl.CreateWindow(f"{APP_NAME}".encode(), S.WINDOWPOS_CENTERED, S.WINDOWPOS_CENTERED,
                                       720, 480 + self._hud_height(2), flags)
        sdl.check(self.window, "SDL_CreateWindow", pointer=True)
        sdl.SetWindowMinimumSize(self.window, 480, 400)
        self.renderer = sdl.CreateRenderer(self.window, -1, S.RENDERER_ACCELERATED)
        if not self.renderer:
            self.renderer = sdl.CreateRenderer(self.window, -1, S.RENDERER_SOFTWARE)
        sdl.check(self.renderer, "SDL_CreateRenderer", pointer=True)
        sdl.SetRenderDrawBlendMode(self.renderer, S.BLENDMODE_BLEND)
        self.video_driver = (sdl.GetCurrentVideoDriver() or b"?").decode()
        self.text = TextRenderer(sdl, self.renderer)
        if self.opt.fullscreen:
            sdl.SetWindowFullscreen(self.window, S.WINDOW_FULLSCREEN_DESKTOP)

        self.router = InputRouter(sdl, app.controllers.mapping(), app.controllers)
        self.router.scan()
        self.core: LibretroCore | None = None
        self.audio: AudioOutput | None = None
        self.texture = None
        self.tex_key = None
        self.run = None
        self.reader = None
        self.worker: TrackerWorker | None = None
        self._unsub = None
        self.paused = False
        self.overlays: list[Overlay] = []
        self.pending_failure: str | None = None
        self.running = True
        self.frames = 0                 # frames of the current run in this session
        self.total_frames = 0
        self.fps = 59.7275
        self.last_sram_crc: int | None = None
        self.sram_dirty_checks = 0
        self.last_recovery = time.monotonic()
        self.last_heartbeat = 0.0
        self.message: tuple[str, float] | None = None
        self.prev_inputs: set[str] = set()
        self.prev_combo = False
        self.hud_buttons: list[Button] = []
        self.prepare_thread: threading.Thread | None = None
        self.prepare_result: dict[str, Any] = {}
        self.new_run_params: dict[str, Any] = {}
        self.result = SessionResult()
        self.rendered_frames = 0
        self.capture_request: Path | None = None
        self.last_capture: bytes | None = None

    # ================================================================== runs
    def start_new_run(self, **params: Any) -> None:
        """Prepare a new run in the background (randomization), then load it."""
        self.new_run_params = dict(params)
        self.prepare_result = {}

        def work():
            try:
                run = self.app.orchestrator.start_new_run(launch=False, **params)
                self.prepare_result["run"] = run
            except Exception as exc:   # noqa: BLE001 — shown to the player
                log.exception("run preparation failed")
                self.prepare_result["error"] = str(exc)
        self.prepare_thread = threading.Thread(target=work, name="nfnf-prepare", daemon=True)
        self.prepare_thread.start()
        game = params.get("game_id") or self.app.config.get("default_game")
        self.overlays = [Overlay("preparing", "PREPARING NEW RUN", [
            (f"Game: {self.app.games.get(game).display_name}", WHITE),
            ("Randomizing a fresh copy of your ROM...", GREY),
            ("Creating a clean save and tracker...", GREY)])]

    def _poll_preparation(self) -> None:
        if not self.prepare_thread or self.prepare_thread.is_alive():
            return
        self.prepare_thread = None
        self.overlays = [o for o in self.overlays if o.kind != "preparing"]
        if "error" in self.prepare_result:
            self._show_error("Could not prepare the run", self.prepare_result["error"])
        else:
            self.load_run(self.prepare_result["run"].id)

    def load_run(self, run_id: str, *, resume: bool = False, restore_state: bool = False,
                 after_crash: bool = False) -> None:
        app = self.app
        self._close_run_session()
        run = app.runs.get(run_id)
        if run.status not in (RunState.READY, RunState.ACTIVE):
            raise RuntimeError(f"{run_id} is {run.status.value}; only READY/ACTIVE runs can be played")
        meta = app.runs.metadata(run_id)
        rom_path = app.paths.abs(meta["rom"])
        rom_bytes = rom_path.read_bytes()
        rom_sha = hashlib.sha256(rom_bytes).hexdigest()
        if self.core is None:
            core_path = app.emulators["nfnf-libretro"].core_path()
            if not core_path:
                raise RuntimeError("Integrated emulator core not found (components fetch)")
            sysdir = app.paths.data_dir / "emulator-system"
            sysdir.mkdir(parents=True, exist_ok=True)
            self.core = LibretroCore(core_path, sysdir, sysdir, options=CORE_OPTIONS)
        self.core.unload_game()
        self.core.load_game(rom_path)
        for sub in ("saves", "states", "screenshots"):
            (run.path / sub).mkdir(exist_ok=True)
        sav = run.path / "saves" / "game.sav"
        if sav.exists():
            self.core.import_save_ram(sav)            # this run's own save, never another run's
        restored = False
        rec = run.path / "states" / "recovery.state"
        if restore_state and rec.exists():
            self.core.load_state(rec.read_bytes())
            restored = True
        info = self.core.info()
        self.fps = info.fps or 59.7275
        if self.audio is None or self.audio.rate != int(info.sample_rate):
            if self.audio:
                self.audio.close()
            self.audio = AudioOutput(self.sdl, int(info.sample_rate or 32768), self.opt.volume, self.opt.muted)
        else:
            self.audio.clear()
        ident = meta.get("identity", {})
        self.reader = reader_for(run.game_id, ident.get("revision"), rom_bytes, ident.get("game_code"))
        memory_path = run.path / "tracker.json"
        engine_memory = load_memory(memory_path) if (resume and memory_path.exists()) else None
        if self.reader:
            engine = engine_for(self.reader, TrackerConfig(), engine_memory)
            self.worker = TrackerWorker(
                lambda t, p, rid=run_id: app.runs.record_event(rid, t, p, source="tracker:nfnf"),
                engine, memory_path)
        else:
            self.worker = TrackerWorker(lambda t, p, rid=run_id: app.runs.record_event(rid, t, p, source="session"),
                                        engine_for_none(run.game_id), None)
        worker = self.worker
        self._unsub = app.bus.subscribe(
            lambda e, rid=run_id: worker.on_rule_violation(e) if e.run_id == rid else None,
            EventType.RULE_VIOLATION)
        app.orchestrator.run_owners[run_id] = self._on_rule_failure
        self.run = app.orchestrator.activate_integrated(
            run_id, rom_sha256=rom_sha, resumed=resume,
            emulator_detail={"core": info.library_name, "core_version": info.library_version,
                             "fps": round(self.fps, 4), "sample_rate": info.sample_rate,
                             "video_driver": self.video_driver,
                             "audio": (self.audio.driver if self.audio.available else f"unavailable: {self.audio.error}")},
            tracker_detail={"memory_reader": type(self.reader).__name__ if self.reader else None,
                            "poll_frames": self.opt.tracker_poll_frames})
        if resume:
            self.worker.submit_event(EventType.SESSION_RESUMED, {"after_crash": after_crash, "restored_state": restored})
        if restored:
            self.worker.submit_event(EventType.STATE_RESTORED, {"kind": "recovery_state", "after_crash": after_crash})
        self.frames = 0
        self.last_sram_crc = zlib.crc32(self.core.memory(MEMORY_SAVE_RAM))
        self.sram_dirty_checks = 0
        self.last_recovery = time.monotonic()
        self.pending_failure = None
        self.paused = False
        self.overlays = []
        self.result.runs_played.append(run_id)
        self._heartbeat(force=True)
        self.sdl.SetWindowTitle(self.window, f"{APP_NAME} - RUN #{self.run.row['attempt_number']:03d}".encode())
        self.toast(f"RUN #{self.run.row['attempt_number']:03d} ACTIVE - good luck!")

    def _close_run_session(self, clean: bool = True) -> None:
        """Flush saves and stop the tracker for the current run (the run itself is untouched)."""
        if self.run is None:
            return
        run_id = self.run.id
        self._flush_save(force=True)
        if self.worker:
            self.worker.stop()
            self.worker = None
        if self._unsub:
            self._unsub()
            self._unsub = None
        self.app.orchestrator.run_owners.pop(run_id, None)
        current = self.app.runs.get(run_id)
        if clean and current.status == RunState.ACTIVE and not current.archived:
            if self.core:
                (current.path / "states").mkdir(exist_ok=True)
                _atomic_write(current.path / "states" / "recovery.state", self.core.save_state())
            self.app.runs.record_event(run_id, EventType.SESSION_CLOSED, {"frames": self.frames})
            write_session(current.path, "closed", frames=self.frames)
        self.run = None
        self.reader = None

    # ============================================================== failure
    def _on_rule_failure(self, reason: str) -> None:
        # called on the tracker worker thread → just flag it
        self.pending_failure = reason

    def _handle_failure(self) -> None:
        reason, self.pending_failure = self.pending_failure, None
        run_id = self.run.id
        attempt = self.run.row["attempt_number"]
        state = self.worker.latest if self.worker else None
        self._flush_save(force=True)
        if self.worker:
            self.worker.stop()
            self.worker = None
        if self._unsub:
            self._unsub()
            self._unsub = None
        self.app.orchestrator.run_owners.pop(run_id, None)
        self.audio.clear()
        run = self.app.orchestrator.fail_run(run_id, reason)
        write_session(run.path, "closed", frames=self.frames, ended="failed")
        self.run = None
        career = self.app.career()
        badges = state.badge_count if state else None
        play = state.play_time_frames if state else None
        lines = [(f"Reason: {_pretty_reason(reason)}", RED),
                 (f"Time: {fmt_duration(play / self.fps) if play else fmt_duration(None)}", WHITE),
                 (f"Progress: {'Gym ' + str(badges) if badges else 'No badges'}", WHITE),
                 (f"Integrity: {run.integrity_status}", GREY),
                 ("", WHITE),
                 (f"Attempts: {career.attempts}   Best: {career.best_progress}", CYAN)]
        ov = Overlay("failed", f"RUN #{attempt:03d} FAILED", lines,
                     [Button("START NEW RUN", self._next_run), Button("VIEW RUN", lambda: self._view_run(run_id)),
                      Button("QUIT", self._quit)])
        if self.opt.auto_new_run:
            ov.auto = (time.monotonic() + self.opt.auto_new_run_delay, self._next_run)
        self.overlays = [ov]

    def _next_run(self) -> None:
        params = {k: v for k, v in self.new_run_params.items() if k in ("game_id", "ruleset_id", "profile_id")}
        last = self.result.runs_played[-1] if self.result.runs_played else None
        if last:
            prev = self.app.runs.get(last)
            params.setdefault("game_id", prev.game_id)
            params.setdefault("ruleset_id", prev.row["ruleset_id"])
            params.setdefault("profile_id", prev.row["randomizer_profile_id"])
            params["previous_run_id"] = last
        self.start_new_run(**params)

    def _view_run(self, run_id: str) -> None:
        run = self.app.runs.get(run_id)
        enc = self.app.db.query("SELECT species, area, captured, first_in_area FROM encounters WHERE run_id=?"
                                " AND encounter_type='wild' ORDER BY id LIMIT 8", (run_id,))
        lines = [(f"Status: {run.status.value}   Integrity: {run.integrity_status}", WHITE),
                 (f"Seed: {run.row['seed']}  (randomizer seed {run.row['actual_randomizer_seed']})", GREY),
                 (f"Reason: {run.row['end_reason'] or '-'}", GREY), ("Encounters:", CYAN)]
        lines += [(f"  {e['area'] or '?'}: {e['species']}{' (caught)' if e['captured'] else ''}", WHITE)
                  for e in enc] or [("  none recorded", GREY)]
        self.overlays.append(Overlay("view", run_id, lines, [Button("BACK", self._pop)], on_cancel=self._pop))

    def _abandon(self) -> None:
        run = self.run
        attempt = run.row["attempt_number"]

        def confirm():
            self._pop()
            run_id = run.id
            self._flush_save(force=True)
            if self.worker:
                self.worker.stop()
                self.worker = None
            if self._unsub:
                self._unsub()
                self._unsub = None
            self.app.orchestrator.run_owners.pop(run_id, None)
            done = self.app.orchestrator.abandon_run(run_id, "abandoned by player")
            write_session(done.path, "closed", frames=self.frames, ended="abandoned")
            self.run = None
            self.overlays = [Overlay("abandoned", f"RUN #{attempt:03d} ABANDONED",
                                     [("Recorded as ABANDONED (not a death).", GREY)],
                                     [Button("START NEW RUN", self._next_run), Button("QUIT", self._quit)])]
        self.overlays.append(Overlay("confirm", f"ABANDON RUN #{attempt:03d}?",
                                     [("This cannot be undone.", RED)],
                                     [Button("ABANDON", confirm), Button("CANCEL", self._pop)], selected=1,
                                     on_cancel=self._pop))

    # ============================================================= overlays
    def _pop(self) -> None:
        if self.overlays:
            self.overlays.pop()

    def _pause_menu(self) -> None:
        if self.run is None or any(o.kind in ("pause", "failed", "preparing") for o in self.overlays):
            return
        self.paused = True
        self._heartbeat(force=True)
        self.overlays.append(Overlay("pause", "PAUSED", [], [
            Button("RESUME", self._resume), Button("OPTIONS", self._options),
            Button("SCREENSHOT", self.screenshot), Button("RESET GAME", self._confirm_reset),
            Button("ABANDON RUN", self._abandon), Button("QUIT TO MENU", self._quit)], on_cancel=self._resume))

    def _resume(self) -> None:
        self.overlays = [o for o in self.overlays if o.kind not in ("pause", "options", "help")]
        self.paused = False
        self._heartbeat(force=True)

    def _confirm_reset(self) -> None:
        def do():
            self._pop()
            self.core.reset()
            self.worker.submit_event(EventType.GAME_RESET, {"kind": "hard", "by": "player (NFNF menu)"})
            self._resume()
            self.toast("Game reset (recorded)")
        self.overlays.append(Overlay("confirm", "RESET THE GAME?", [
            ("Unsaved progress is lost. The reset is recorded.", YELLOW)],
            [Button("RESET", do), Button("CANCEL", self._pop)], selected=1, on_cancel=self._pop))

    def _options(self) -> None:
        o = self.opt

        def set_cfg(key, value):
            setattr(o, key, value)
            self.app.config.set_override(("session", key), value)

        def vol(d):
            set_cfg("volume", max(0, min(100, o.volume + 10 * d)))
            if self.audio:
                self.audio.set_volume(o.volume)

        def mute(_d):
            set_cfg("muted", not o.muted)
            if self.audio:
                self.audio.muted = o.muted

        def scaling(_d):
            set_cfg("integer_scaling", not o.integer_scaling)

        def fullscreen(_d):
            self.toggle_fullscreen()

        def hud(_d):
            set_cfg("show_hud", not o.show_hud)

        def auto(_d):
            set_cfg("auto_new_run", not o.auto_new_run)

        def mapping(d):
            ids = [m.id for m in self.app.controllers.mappings.list()]
            cur = self.app.controllers.mapping_id
            nxt = ids[(ids.index(cur) + d) % len(ids)] if cur in ids else ids[0]
            self.app.controllers.mapping_id = nxt
            self.app.config.set_override(("controller", "mapping"), nxt)
            self.router.mapping = self.app.controllers.mapping()

        rows = [("Volume", lambda: f"{o.volume}%", vol), ("Mute", lambda: "ON" if o.muted else "OFF", mute),
                ("Integer scaling", lambda: "ON" if o.integer_scaling else "OFF", scaling),
                ("Fullscreen", lambda: "ON" if self.is_fullscreen() else "OFF", fullscreen),
                ("Show run panel", lambda: "ON" if o.show_hud else "OFF", hud),
                ("Auto new run after failure", lambda: "ON" if o.auto_new_run else "OFF", auto),
                ("Controller mapping", lambda: self.app.controllers.mapping_id, mapping)]
        self.overlays.append(Overlay("options", "OPTIONS", [], [Button("BACK", self._pop)], rows=rows,
                                     on_cancel=self._pop))

    def _help(self) -> None:
        lines = [("Keyboard: arrows=D-Pad  X=A  Z=B  A=L  S=R", WHITE),
                 ("          Enter=START  Backspace=SELECT", WHITE),
                 ("Esc / Guide / Back+Start = menu   P = pause", WHITE),
                 ("F11 / Alt+Enter = fullscreen   F12 = screenshot", WHITE),
                 ("-/+ = volume   M = mute   H = run panel", WHITE),
                 (f"Controller: {self.router.active_name or 'none'}", CYAN)]
        self.overlays.append(Overlay("help", "CONTROLS", lines, [Button("BACK", self._pop)], on_cancel=self._pop))

    def _show_error(self, title: str, detail: str) -> None:
        words, lines, cur = detail.split(), [], ""
        for w in words:
            if len(cur) + len(w) > 48:
                lines.append((cur, RED))
                cur = ""
            cur += w + " "
        lines.append((cur, RED))
        self.overlays = [Overlay("error", title, lines[:8], [Button("QUIT", self._quit)], on_cancel=self._quit)]

    def _quit(self) -> None:
        self.running = False
        self.result.quit_reason = "player"

    # ============================================================ utilities
    def toast(self, text: str, seconds: float = 3.0) -> None:
        self.message = (text, time.monotonic() + seconds)

    def is_fullscreen(self) -> bool:
        return bool(self.sdl.GetWindowFlags(self.window) & S.WINDOW_FULLSCREEN)

    def toggle_fullscreen(self) -> None:
        full = not self.is_fullscreen()
        self.sdl.SetWindowFullscreen(self.window, S.WINDOW_FULLSCREEN_DESKTOP if full else 0)
        self.opt.fullscreen = full
        self.app.config.set_override(("session", "fullscreen"), full)

    def screenshot(self) -> Path | None:
        if not (self.core and self.run and self.core.last_frame):
            return None
        path = self.run.path / "screenshots" / f"{time.strftime('%Y%m%d-%H%M%S')}-f{self.frames}.png"
        write_png(path, self.core.last_frame, 2)
        if self.worker:
            self.worker.submit_event(EventType.SCREENSHOT_TAKEN, {"file": self.app.paths.rel(path)})
        self.toast(f"Screenshot saved: {path.name}")
        return path

    def _flush_save(self, force: bool = False) -> None:
        """Write the game's save RAM to runs/<id>/saves/game.sav once it stops changing."""
        if not (self.core and self.run):
            return
        data = self.core.memory(MEMORY_SAVE_RAM)
        crc = zlib.crc32(data)
        if crc == self.last_sram_crc:
            self.sram_dirty_checks = 0
            return
        self.sram_dirty_checks += 1
        if not force and self.sram_dirty_checks < 2:
            return                  # the game may still be writing; wait one more check
        path = self.run.path / "saves" / "game.sav"
        _atomic_write(path, data)
        self.last_sram_crc, self.sram_dirty_checks = crc, 0
        event = (EventType.SAVE_CREATED, {"kind": "ingame", "size": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest(),
                                          "play_time_frames": self.worker.latest.play_time_frames
                                          if self.worker and self.worker.latest else None})
        if self.worker and not force:
            self.worker.submit_event(*event)
        elif self.run.status == RunState.ACTIVE:
            self.app.runs.record_event(self.run.id, *event, source="session")

    def _heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if self.run is None or (not force and now - self.last_heartbeat < self.opt.heartbeat_seconds):
            return
        self.last_heartbeat = now
        st = self.worker.latest if self.worker else None
        write_session(self.run.path, "paused" if self.paused else "running", frames=self.frames,
                      play_time_frames=st.play_time_frames if st else None, area=st.area_name if st else None)

    # ================================================================ events
    def _handle_events(self) -> set[str]:
        ev, keys = S.Event(), set()
        while self.sdl.PollEvent(C.byref(ev)):
            t = ev.type
            if t == S.QUIT:
                self._quit()
            elif t in (S.CONTROLLERDEVICEADDED, S.CONTROLLERDEVICEREMOVED):
                self.router.handle_event(ev)
            elif t == S.KEYDOWN and not ev.key_repeat:
                k, mod = ev.key_sym, ev.key_mod
                if k == S.K["F11"] or (k == S.K["RETURN"] and mod & S.KMOD_ALT):
                    self.toggle_fullscreen()
                elif k == S.K["F12"]:
                    self.screenshot()
                elif k == S.K["F1"]:
                    self._help()
                elif k in (S.K["MINUS"], S.K["KP_MINUS"]) and self.audio:
                    self.opt.volume = max(0, self.opt.volume - 10)
                    self.audio.set_volume(self.opt.volume)
                    self.toast(f"Volume {self.opt.volume}%")
                elif k in (S.K["EQUALS"], S.K["PLUS"], S.K["KP_PLUS"]) and self.audio:
                    self.opt.volume = min(100, self.opt.volume + 10)
                    self.audio.set_volume(self.opt.volume)
                    self.toast(f"Volume {self.opt.volume}%")
                elif k == S.K["m"] and self.audio:
                    self.opt.muted = self.audio.muted = not self.opt.muted
                    self.toast("Muted" if self.opt.muted else "Sound on")
                elif k == S.K["h"]:
                    self.opt.show_hud = not self.opt.show_hud
                elif k == S.K["p"] and not self.overlays:
                    self.paused = not self.paused
                    self.toast("Paused" if self.paused else "Resumed")
                elif k == S.K["ESCAPE"]:
                    if self.overlays and self.overlays[-1].on_cancel:
                        self.overlays[-1].on_cancel()
                    elif not self.overlays:
                        self._pause_menu()
                elif self.overlays:
                    nav = {S.K["UP"]: "UP", S.K["DOWN"]: "DOWN", S.K["LEFT"]: "LEFT", S.K["RIGHT"]: "RIGHT",
                           S.K["RETURN"]: "A", S.K["x"]: "A", S.K["z"]: "B", S.K["SPACE"]: "A"}
                    if k in nav:
                        keys.add(nav[k])
            elif t == S.MOUSEBUTTONDOWN and ev.mouse_button == 1:
                self._click(*ev.mouse_xy)
            elif t == S.WINDOWEVENT and ev.window_event == S.WINDOWEVENT_FOCUS_LOST:
                pass   # keep running: controllers work without focus (SDL hint)
        for kind, name in self.router.changes:
            if self.worker:
                self.worker.submit_event(EventType.CONTROLLER_CONNECTED if kind == "connected"
                                         else EventType.CONTROLLER_DISCONNECTED, {"controller": name})
            self.toast(f"Controller {kind}: {name}")
            if kind == "disconnected" and self.run and not self.overlays:
                self._pause_menu()
        self.router.changes.clear()
        return keys

    def _click(self, x: int, y: int) -> None:
        buttons = self.overlays[-1].buttons if self.overlays else self.hud_buttons
        for b in buttons:
            if b.hit(x, y):
                b.action()
                return

    def _navigate(self, edges: set[str]) -> None:
        ov = self.overlays[-1]
        n_rows = len(ov.rows)
        count = n_rows + len(ov.buttons)
        if not count:
            return
        if "UP" in edges:
            ov.selected = (ov.selected - 1) % count
        if "DOWN" in edges:
            ov.selected = (ov.selected + 1) % count
        in_rows = ov.selected < n_rows
        if in_rows and ("LEFT" in edges or "RIGHT" in edges):
            ov.rows[ov.selected][2](-1 if "LEFT" in edges else 1)
        elif not in_rows and ("LEFT" in edges or "RIGHT" in edges):
            ov.selected = n_rows + (ov.selected - n_rows + (1 if "RIGHT" in edges else -1)) % len(ov.buttons)
        if "A" in edges or "START" in edges:
            if in_rows:
                ov.rows[ov.selected][2](1)
            else:
                ov.buttons[ov.selected - n_rows].action()
        elif "B" in edges and ov.on_cancel:
            ov.on_cancel()

    # ================================================================ render
    def _layout(self) -> tuple[int, int, int, tuple[int, int, int, int]]:
        w, h = C.c_int(), C.c_int()
        self.sdl.GetRendererOutputSize(self.renderer, C.byref(w), C.byref(h))
        W, H = w.value, h.value
        scale = 2 if W >= 640 else 1
        hud_h = self._hud_height(scale) if (self.opt.show_hud and not self.is_fullscreen()) else 0
        area_h = max(1, H - hud_h)
        gw, gh = (self.core.last_raw[1], self.core.last_raw[2]) if (self.core and self.core.last_raw) else (240, 160)
        if self.opt.integer_scaling and min(W // gw, area_h // gh) >= 1:
            s = min(W // gw, area_h // gh)
            dw, dh = gw * s, gh * s
        else:
            f = min(W / gw, area_h / gh)
            dw, dh = max(1, int(gw * f)), max(1, int(gh * f))
        return W, H, scale, ((W - dw) // 2, (area_h - dh) // 2, dw, dh)

    def _render(self) -> None:
        sdl, r = self.sdl, self.renderer
        W, H, scale, dst = self._layout()
        sdl.SetRenderDrawColor(r, 0, 0, 0, 255)
        sdl.RenderClear(r)
        raw = self.core.last_raw if self.core else None
        if raw:
            data, gw, gh, pitch, fmt = raw
            key = (gw, gh, fmt)
            if key != self.tex_key:
                if self.texture:
                    sdl.DestroyTexture(self.texture)
                self.texture = sdl.CreateTexture(r, TEXFMT.get(fmt, S.PIXELFORMAT_RGB565),
                                                 S.TEXTUREACCESS_STREAMING, gw, gh)
                self.tex_key = key
            sdl.UpdateTexture(self.texture, None, data, pitch)
            sdl.RenderCopy(r, self.texture, None, C.byref(S.Rect(*dst)))
        if self.opt.show_hud and not self.is_fullscreen():
            self._render_hud(W, H, scale, dst[1] + dst[3])
        else:
            self.hud_buttons = []
        if self.message and time.monotonic() < self.message[1]:
            t = self.message[0]
            fill(sdl, r, (8, 8, self.text.width(t, scale) + 12, 8 * scale + 10), PANEL, 220)
            self.text.draw(14, 13, t, scale, YELLOW)
        for ov in self.overlays:
            self._render_overlay(ov, W, H, scale)
        if self.capture_request:
            self.last_capture = self._read_window(W, H)
            path, self.capture_request = self.capture_request, None
            if path:
                from ..emulators.libretro import Frame
                write_png(path, Frame(W, H, self.last_capture), 1)
        sdl.RenderPresent(r)
        self.rendered_frames += 1

    def _read_window(self, W: int, H: int) -> bytes:
        """RGB bytes of what is on the window right now (debug capture / render tests)."""
        buf = (C.c_uint8 * (W * H * 3))()
        self.sdl.RenderReadPixels(self.renderer, None, S.PIXELFORMAT_RGB24, buf, W * 3)
        return bytes(buf)

    def request_window_capture(self, path: Path | None = None) -> None:
        self.capture_request = path or Path(os.devnull)

    HUD_LINES = 5

    def _hud_height(self, scale: int) -> int:
        return (8 * scale + 6) * self.HUD_LINES + (16 * scale) + 14

    def _render_hud(self, W: int, H: int, scale: int, top: int) -> None:
        sdl, r, t = self.sdl, self.renderer, self.text
        y0 = max(top, H - self._hud_height(scale))
        fill(sdl, r, (0, y0, W, H - y0), PANEL)
        lh = 8 * scale + 6
        x, y = 8, y0 + 6
        maxc = max(10, (W - 16) // (8 * scale))
        run = self.run
        st = self.worker.latest if self.worker else None
        valid = bool(st and st.session_valid)
        if run:
            status = "PAUSED" if self.paused else run.status.value
            play = st.play_time_frames if valid and st.play_time_frames else None
            tm = fmt_duration(play / self.fps) if play else fmt_duration(self.frames / self.fps)
            t.draw(x, y, f"RUN #{run.row['attempt_number']:03d}  {status}  Time {tm}", scale,
                   GREEN if status == "ACTIVE" else YELLOW, maxc)
            y += lh
            area = (st.area_name or st.area_id) if valid else None
            t.draw(x, y, f"Area: {area or 'UNKNOWN'}", scale, WHITE, maxc)
            y += lh
            if valid and st.party:
                t.draw(x, y, "Party: " + "  ".join(p.label() for p in st.party), scale, WHITE, maxc)
            else:
                t.draw(x, y, "Party: " + ("UNKNOWN (no tracker for this game)" if not self.reader
                                          else "waiting for game..."), scale, GREY, maxc)
            y += lh
            rs = self.worker.rules_status if self.worker else "UNKNOWN"
            badges = st.badge_count if valid else None
            enc = len(self.worker.engine.m.encounters) if (self.worker and self.reader) else 0
            t.draw(x, y, f"Rules: {rs}  Badges: {badges if badges is not None else '?'}  Enc: {enc}", scale,
                   {"VALID": GREEN, "WARNING": YELLOW}.get(rs, RED), maxc)
            y += lh
        else:
            t.draw(x, y, "No active run", scale, GREY)
            y += lh * 4
        ctl = self.router.active_name
        vol = "MUTED" if self.opt.muted else f"{self.opt.volume}%"
        au = "" if (self.audio and self.audio.available) else " (no audio)"
        t.draw(x, y, f"Pad: {ctl or 'keyboard'}  Vol: {vol}{au}  F1: help", scale, GREEN if ctl else GREY, maxc)
        y += lh + 2
        self.hud_buttons = [Button("RESUME" if self.paused else "PAUSE", self._toggle_pause, enabled=bool(run)),
                            Button("SAVE", self._manual_save, enabled=bool(run)),
                            Button("OPTIONS", self._options), Button("ABANDON RUN", self._abandon, enabled=bool(run))]
        bx = x
        for b in self.hud_buttons:
            bx = draw_button(self.sdl, r, t, b, bx, y, scale)

    def _toggle_pause(self) -> None:
        self.paused = not self.paused

    def _manual_save(self) -> None:
        """Persist the run now: save RAM + a recovery state (does not create an in-game save)."""
        if not (self.run and self.core):
            return
        self._flush_save(force=True)
        _atomic_write(self.run.path / "states" / "recovery.state", self.core.save_state())
        self.last_recovery = time.monotonic()
        self.toast("Run data saved to disk (use the in-game menu to SAVE the game)")

    def _render_overlay(self, ov: Overlay, W: int, H: int, scale: int) -> None:
        sdl, r, t = self.sdl, self.renderer, self.text
        fill(sdl, r, (0, 0, W, H), (0, 0, 0), 150)
        lh = 8 * scale + 8
        rows_h = len(ov.rows) * lh
        box_h = lh * (2 + len(ov.lines)) + rows_h + (16 * scale + 16) + 16
        box_w = min(W - 20, max(360, 8 * scale * 44))
        bx, by = (W - box_w) // 2, max(10, (H - box_h) // 2)
        fill(sdl, r, (bx, by, box_w, box_h), PANEL, 245)
        outline(sdl, r, (bx, by, box_w, box_h), YELLOW)
        y = by + 10
        t.draw(bx + 12, y, ov.title, scale, YELLOW if ov.kind != "failed" else RED)
        y += lh + 4
        maxc = (box_w - 24) // (8 * scale)
        for text, color in ov.lines:
            t.draw(bx + 12, y, text, scale, color, maxc)
            y += lh
        for i, (label, value, _fn) in enumerate(ov.rows):
            hot = ov.selected == i
            if hot:
                fill(sdl, r, (bx + 6, y - 3, box_w - 12, lh), BUTTON)
            t.draw(bx + 12, y, f"{label}", scale, WHITE)
            v = f"< {value()} >"
            t.draw(bx + box_w - 12 - t.width(v, scale), y, v, scale, CYAN if hot else GREY)
            y += lh
        if ov.auto:
            left = max(0, ov.auto[0] - time.monotonic())
            t.draw(bx + 12, y, f"Next run starts automatically in {left:0.0f}s", scale, CYAN)
            y += lh
        x = bx + 12
        for i, b in enumerate(ov.buttons):
            x = draw_button(sdl, r, t, b, x, y + 4, scale, hot=ov.selected == len(ov.rows) + i)

    # ================================================================== loop
    def play(self, *, new_run: dict[str, Any] | None = None, run_id: str | None = None,
             resume: bool = False, restore_state: bool = False, after_crash: bool = False) -> SessionResult:
        try:
            if run_id:
                self.load_run(run_id, resume=resume, restore_state=restore_state, after_crash=after_crash)
            elif new_run is not None:
                self.start_new_run(**new_run)
            self._loop()
        finally:
            self.shutdown()
        return self.result

    def _loop(self) -> None:
        frame_time = 1.0 / self.fps
        next_t = time.perf_counter()
        loops = 0
        # automation safety: a scripted/limited session can never spin forever on a menu
        max_loops = None if self.opt.max_frames is None else self.opt.max_frames * 40 + 20000
        while self.running:
            loops += 1
            if max_loops is not None and loops > max_loops:
                self.result.quit_reason = "automation_loop_limit"
                break
            keys = self._handle_events()
            self._poll_preparation()
            inp = self.router.poll()
            edges = (inp.logical | inp.physical | keys) - self.prev_inputs
            edges |= keys
            self.prev_inputs = inp.logical | inp.physical
            if inp.menu_combo and not self.prev_combo:
                if any(o.kind == "pause" for o in self.overlays):
                    self._resume()
                elif not self.overlays:
                    self._pause_menu()
                edges = set()      # the combo itself must not also press a menu button
            self.prev_combo = inp.menu_combo
            if self.overlays:
                ov = self.overlays[-1]
                if ov.auto and time.monotonic() >= ov.auto[0]:
                    action, ov.auto = ov.auto[1], None
                    action()
                elif edges:
                    self._navigate(edges)
            active = (self.core is not None and self.run is not None and not self.paused
                      and not self.overlays and self.pending_failure is None)
            if active:
                pressed = inp.logical
                if self.opt.script:
                    scripted = self.opt.script(self, self.frames)
                    if scripted is not None:
                        pressed = scripted
                self.core.run_frames(1, pressed)
                self.frames += 1
                self.total_frames += 1
                samples = self.core.take_audio()
                if self.audio:
                    self.audio.queue(samples)
                if self.reader and self.frames % self.opt.tracker_poll_frames == 0:
                    self._poll_tracker()
                if self.frames % self.opt.save_check_frames == 0:
                    self._flush_save()
                if time.monotonic() - self.last_recovery > self.opt.recovery_seconds:
                    _atomic_write(self.run.path / "states" / "recovery.state", self.core.save_state())
                    self.last_recovery = time.monotonic()
            elif self.core:
                self.core.take_audio()
            if self.pending_failure and self.run:
                self._handle_failure()
            self._heartbeat()
            self._render()
            if self.opt.max_frames is not None and self.total_frames >= self.opt.max_frames:
                self.result.quit_reason = "max_frames"
                break
            if self.opt.script and not active and self.opt.max_frames is not None and not self.prepare_thread \
                    and self.overlays and self.overlays[-1].kind in ("failed", "error", "abandoned"):
                # automation: let the script decide at the failure screen
                decision = self.opt.script(self, -1)
                if decision == {"NEW_RUN"}:
                    self._next_run()
                elif decision == {"QUIT"}:
                    break
            # pacing: audio-driven when a device plays, else timer
            if self.opt.unthrottled:
                continue
            if active and self.audio and self.audio.available and self.audio.driver != "dummy":
                while self.audio.queued_seconds() > 0.07:
                    self.sdl.Delay(1)
                next_t = time.perf_counter()
            else:
                next_t += frame_time
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_t = time.perf_counter()
        self.result.frames = self.total_frames

    def _poll_tracker(self) -> None:
        mem = GbaBus(self.core) if self.reader.generation == 3 else GbWram(self.core.memory(MEMORY_SYSTEM_RAM))
        try:
            state = self.reader.read_state(mem, self.frames)
        except Exception as exc:  # noqa: BLE001 — an unreadable moment must not stop the game
            log.debug("tracker read failed: %s", exc)
            return
        self.worker.submit_state(state)

    def wait_tracker(self) -> None:
        if self.worker:
            self.worker.flush()

    def shutdown(self) -> None:
        try:
            self._close_run_session(clean=True)
        finally:
            self.router.close()
            if self.audio:
                self.audio.close()
            if self.core:
                self.core.close()
                self.core = None
            if self.texture:
                self.sdl.DestroyTexture(self.texture)
            self.text.destroy()
            self.sdl.DestroyRenderer(self.renderer)
            self.sdl.DestroyWindow(self.window)
            self.sdl.QuitSubSystem(S.INIT_VIDEO | S.INIT_AUDIO | S.INIT_GAMECONTROLLER)


def engine_for_none(game_id: str):
    from ..tracker.engine import TrackerEngine
    return TrackerEngine(game_id)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _pretty_reason(reason: str | None) -> str:
    if not reason:
        return "unknown"
    names = {"rule:starter-faint-ends-run": "Starter fainted", "rule:no-pokecenter-healing": "Pokemon Center heal",
             "rule:no-healing-items-in-battle": "Healing item used in battle"}
    return names.get(reason, reason.replace("rule:", "Rule: "))

