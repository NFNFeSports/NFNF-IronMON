"""Controller detection/testing for the launcher (SDL2 GameController, no video needed).

Uses the same SDL backend and NFNF InputMapping as the game window, so what the
launcher shows is what the game will receive. Falls back to the OS backends
(Linux joystick / XInput) when SDL2 cannot be loaded.
"""

from __future__ import annotations

from . import sdl2 as S

_sdl: S.SDL | None = None
_router = None


def _init(app):
    global _sdl, _router
    if _sdl is None:
        from .input import InputRouter
        comp = app.components.resolve("sdl2")
        _sdl = S.load_sdl(comp.parent if comp else None)
        _sdl.SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if _sdl.InitSubSystem(S.INIT_GAMECONTROLLER | S.INIT_EVENTS) < 0:
            raise S.SDLError(_sdl.error())
        _router = InputRouter(_sdl, app.controllers.mapping(), app.controllers)
    return _sdl, _router


def pump(app) -> None:
    sdl, router = _init(app)
    ev = S.Event()
    import ctypes
    while sdl.PollEvent(ctypes.byref(ev)):
        router.handle_event(ev)
    router.scan()


def list_pads(app) -> list[str]:
    try:
        pump(app)
        names = [p.name for p in _router.pads.values()]
        if names:
            return names
    except Exception:  # noqa: BLE001 — SDL missing: use the OS backends
        pass
    return [d.name for d in app.controllers.devices() if d.is_gamepad]


def poll(app) -> tuple[set[str], set[str], str | None]:
    """(physical buttons, NFNF logical buttons, controller name) right now."""
    pump(app)
    _router.mapping = app.controllers.mapping()
    snap = _router.poll()
    return snap.physical, snap.logical, _router.active_name
