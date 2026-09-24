"""Minimal ctypes binding to SDL2 (zlib license) — only what NFNF IronMON uses.

Library resolution: the bundled copy (``runtime/sdl2/<platform>/``, see
components.json) first, then the system library (development fallback).
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
import os
from pathlib import Path

from ..platform_support import os_family

# --- constants (SDL2 headers) ---------------------------------------------------
INIT_TIMER, INIT_AUDIO, INIT_VIDEO = 0x1, 0x10, 0x20
INIT_JOYSTICK, INIT_GAMECONTROLLER, INIT_EVENTS = 0x200, 0x2000, 0x4000

WINDOWPOS_CENTERED = 0x2FFF0000
WINDOW_FULLSCREEN, WINDOW_SHOWN, WINDOW_RESIZABLE, WINDOW_HIDDEN = 0x1, 0x4, 0x20, 0x8
WINDOW_FULLSCREEN_DESKTOP = 0x1001

RENDERER_SOFTWARE, RENDERER_ACCELERATED, RENDERER_PRESENTVSYNC = 0x1, 0x2, 0x4
TEXTUREACCESS_STATIC, TEXTUREACCESS_STREAMING = 0, 1
BLENDMODE_NONE, BLENDMODE_BLEND = 0, 1

PIXELFORMAT_RGB555 = 0x15130F02
PIXELFORMAT_RGB565 = 0x15151002
PIXELFORMAT_RGB888 = 0x16161804      # XRGB8888
PIXELFORMAT_ARGB8888 = 0x16362004
PIXELFORMAT_RGB24 = 0x17101803

AUDIO_S16LSB = 0x8010

QUIT = 0x100
WINDOWEVENT = 0x200
KEYDOWN, KEYUP = 0x300, 0x301
MOUSEMOTION, MOUSEBUTTONDOWN, MOUSEBUTTONUP = 0x400, 0x401, 0x402
CONTROLLERAXISMOTION, CONTROLLERBUTTONDOWN, CONTROLLERBUTTONUP = 0x650, 0x651, 0x652
CONTROLLERDEVICEADDED, CONTROLLERDEVICEREMOVED = 0x653, 0x654

WINDOWEVENT_FOCUS_LOST = 13

# SDL_GameControllerButton / Axis (Xbox-style names)
CONTROLLER_BUTTONS = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "BACK", 5: "GUIDE", 6: "START", 7: "LS", 8: "RS",
                      9: "LB", 10: "RB", 11: "DPAD_UP", 12: "DPAD_DOWN", 13: "DPAD_LEFT", 14: "DPAD_RIGHT"}
CONTROLLER_AXES = {0: "LX", 1: "LY", 2: "RX", 3: "RY", 4: "LT", 5: "RT"}

# SDL_Keycode values for keys NFNF uses
K = {"RETURN": 13, "ESCAPE": 27, "BACKSPACE": 8, "TAB": 9, "SPACE": 32, "MINUS": 45, "EQUALS": 61,
     "PLUS": 43, "a": 97, "h": 104, "i": 105, "m": 109, "p": 112, "q": 113, "s": 115, "x": 120, "z": 122,
     "F1": 0x4000003A, "F5": 0x4000003E, "F11": 0x40000044, "F12": 0x40000045,
     "RIGHT": 0x4000004F, "LEFT": 0x40000050, "DOWN": 0x40000051, "UP": 0x40000052,
     "KP_PLUS": 0x40000057, "KP_MINUS": 0x40000056, "LSHIFT": 0x400000E1, "RSHIFT": 0x400000E5}
KMOD_ALT = 0x0100 | 0x0200

# SDL_Scancode values (for held-key polling via SDL_GetKeyboardState)
SC = {"a": 4, "s": 22, "x": 27, "z": 29, "q": 20, "w": 26, "RETURN": 40, "BACKSPACE": 42, "RSHIFT": 229,
      "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82}


class SDLError(RuntimeError):
    pass


class Rect(C.Structure):
    _fields_ = [("x", C.c_int), ("y", C.c_int), ("w", C.c_int), ("h", C.c_int)]


class AudioSpec(C.Structure):
    _fields_ = [("freq", C.c_int), ("format", C.c_uint16), ("channels", C.c_uint8), ("silence", C.c_uint8),
                ("samples", C.c_uint16), ("padding", C.c_uint16), ("size", C.c_uint32),
                ("callback", C.c_void_p), ("userdata", C.c_void_p)]


class Event(C.Union):
    _fields_ = [("type", C.c_uint32), ("padding", C.c_uint8 * 56)]

    def u8(self, off: int) -> int:
        return self.padding[off]

    def i32(self, off: int) -> int:
        return int.from_bytes(bytes(self.padding[off:off + 4]), "little", signed=True)

    def u32(self, off: int) -> int:
        return int.from_bytes(bytes(self.padding[off:off + 4]), "little")

    def i16(self, off: int) -> int:
        return int.from_bytes(bytes(self.padding[off:off + 2]), "little", signed=True)

    # typed accessors -------------------------------------------------------
    @property
    def key_sym(self) -> int:          # SDL_KeyboardEvent.keysym.sym
        return self.i32(20)

    @property
    def key_mod(self) -> int:
        return int.from_bytes(bytes(self.padding[24:26]), "little")

    @property
    def key_repeat(self) -> int:
        return self.u8(13)

    @property
    def mouse_button(self) -> int:     # SDL_MouseButtonEvent
        return self.u8(16)

    @property
    def mouse_xy(self) -> tuple[int, int]:
        return self.i32(20), self.i32(24)

    @property
    def window_event(self) -> int:
        return self.u8(12)

    @property
    def cdevice_which(self) -> int:    # SDL_ControllerDeviceEvent.which
        return self.i32(8)


def _candidates(bundled_dir: Path | None) -> list[str]:
    names = ["SDL2.dll"] if os_family() == "windows" else ["libSDL2-2.0.so.0", "libSDL2-2.0.so", "libSDL2.so"]
    out = []
    if bundled_dir is not None:
        out += [str(bundled_dir / n) for n in names if (bundled_dir / n).exists()]
    env = os.environ.get("NFNF_SDL2_LIB")
    if env:
        out.insert(0, env)
    found = ctypes.util.find_library("SDL2") or ctypes.util.find_library("SDL2-2.0")
    out += names + ([found] if found else [])
    return out


class SDL:
    """Loaded SDL2 library with argtypes declared. One instance per process."""

    def __init__(self, bundled_dir: Path | None = None):
        self.lib = None
        self.path = None
        errors = []
        for cand in _candidates(bundled_dir):
            try:
                self.lib = C.CDLL(cand)
                self.path = cand
                break
            except OSError as exc:
                errors.append(f"{cand}: {exc}")
        if self.lib is None:
            raise SDLError("SDL2 library not found: " + "; ".join(errors[:3]))
        self.bundled = bundled_dir is not None and str(bundled_dir) in str(self.path)
        self._declare()

    def _fn(self, name, restype, *argtypes):
        f = getattr(self.lib, name)
        f.restype = restype
        f.argtypes = list(argtypes)
        setattr(self, name[4:] if name.startswith("SDL_") else name, f)

    def _declare(self) -> None:
        P, I, U32, U8 = C.c_void_p, C.c_int, C.c_uint32, C.c_uint8
        f = self._fn
        f("SDL_Init", I, U32)
        f("SDL_InitSubSystem", I, U32)
        f("SDL_QuitSubSystem", None, U32)
        f("SDL_Quit", None)
        f("SDL_GetError", C.c_char_p)
        f("SDL_SetHint", C.c_bool, C.c_char_p, C.c_char_p)
        f("SDL_GetVersion", None, P)
        f("SDL_GetCurrentVideoDriver", C.c_char_p)
        f("SDL_GetCurrentAudioDriver", C.c_char_p)
        f("SDL_CreateWindow", P, C.c_char_p, I, I, I, I, U32)
        f("SDL_DestroyWindow", None, P)
        f("SDL_SetWindowTitle", None, P, C.c_char_p)
        f("SDL_SetWindowFullscreen", I, P, U32)
        f("SDL_GetWindowFlags", U32, P)
        f("SDL_GetWindowSize", None, P, C.POINTER(I), C.POINTER(I))
        f("SDL_SetWindowSize", None, P, I, I)
        f("SDL_SetWindowMinimumSize", None, P, I, I)
        f("SDL_RaiseWindow", None, P)
        f("SDL_CreateRenderer", P, P, I, U32)
        f("SDL_DestroyRenderer", None, P)
        f("SDL_GetRendererOutputSize", I, P, C.POINTER(I), C.POINTER(I))
        f("SDL_SetRenderDrawColor", I, P, U8, U8, U8, U8)
        f("SDL_SetRenderDrawBlendMode", I, P, I)
        f("SDL_RenderClear", I, P)
        f("SDL_RenderPresent", None, P)
        f("SDL_RenderCopy", I, P, P, C.POINTER(Rect), C.POINTER(Rect))
        f("SDL_RenderFillRect", I, P, C.POINTER(Rect))
        f("SDL_RenderDrawRect", I, P, C.POINTER(Rect))
        f("SDL_RenderReadPixels", I, P, C.POINTER(Rect), U32, P, I)
        f("SDL_CreateTexture", P, P, U32, I, I, I)
        f("SDL_DestroyTexture", None, P)
        f("SDL_UpdateTexture", I, P, C.POINTER(Rect), P, I)
        f("SDL_SetTextureBlendMode", I, P, I)
        f("SDL_SetTextureColorMod", I, P, U8, U8, U8)
        f("SDL_PollEvent", I, C.POINTER(Event))
        f("SDL_PumpEvents", None)
        f("SDL_GetKeyboardState", C.POINTER(U8), C.POINTER(I))
        f("SDL_GetTicks", U32)
        f("SDL_GetPerformanceCounter", C.c_uint64)
        f("SDL_GetPerformanceFrequency", C.c_uint64)
        f("SDL_Delay", None, U32)
        f("SDL_NumJoysticks", I)
        f("SDL_IsGameController", C.c_bool, I)
        f("SDL_JoystickNameForIndex", C.c_char_p, I)
        f("SDL_GameControllerOpen", P, I)
        f("SDL_GameControllerClose", None, P)
        f("SDL_GameControllerName", C.c_char_p, P)
        f("SDL_GameControllerGetAttached", C.c_bool, P)
        f("SDL_GameControllerGetButton", U8, P, I)
        f("SDL_GameControllerGetAxis", C.c_int16, P, I)
        f("SDL_GameControllerGetJoystick", P, P)
        f("SDL_JoystickInstanceID", I, P)
        f("SDL_GameControllerUpdate", None)
        f("SDL_OpenAudioDevice", U32, C.c_char_p, I, C.POINTER(AudioSpec), C.POINTER(AudioSpec), I)
        f("SDL_CloseAudioDevice", None, U32)
        f("SDL_PauseAudioDevice", None, U32, I)
        f("SDL_QueueAudio", I, U32, P, U32)
        f("SDL_GetQueuedAudioSize", U32, U32)
        f("SDL_ClearQueuedAudio", None, U32)
        f("SDL_MixAudioFormat", None, P, P, C.c_uint16, U32, I)

    def error(self) -> str:
        e = self.GetError()
        return e.decode(errors="replace") if e else "unknown SDL error"

    def version(self) -> str:
        buf = (C.c_uint8 * 3)()
        self.GetVersion(buf)
        return f"{buf[0]}.{buf[1]}.{buf[2]}"

    def check(self, result, what: str, pointer: bool = False):
        """Raise SDLError for a negative int result, or a NULL pointer when ``pointer``."""
        if (pointer and not result) or (not pointer and isinstance(result, int) and result < 0):
            raise SDLError(f"{what} failed: {self.error()}")
        return result


_INSTANCE: SDL | None = None


def load_sdl(bundled_dir: Path | None = None) -> SDL:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SDL(bundled_dir)
    return _INSTANCE
