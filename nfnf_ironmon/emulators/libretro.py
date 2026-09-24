"""In-process libretro host (ctypes) — the integrated emulator engine prototype.

Loads a libretro core (Phase 2: the mGBA core, MPL-2.0) into the NFNF process
and drives it directly: load a ROM, run frames, capture video, feed input,
read emulated memory through the core's memory map, and move save RAM in and
out of the run's ``saves/`` folder.

Phase 2 scope is **headless**: no window, no audio output. Presentation
(window, audio, real-time pacing) is Phase 3 work on top of this host.
"""

from __future__ import annotations

import ctypes as C
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

# --- libretro constants (libretro.h) ---------------------------------------
ENV_EXPERIMENTAL = 0x10000
ENV_GET_CAN_DUPE = 3
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_SET_PIXEL_FORMAT = 10
ENV_GET_VARIABLE = 15
ENV_SET_VARIABLES = 16
ENV_GET_VARIABLE_UPDATE = 17
ENV_GET_SAVE_DIRECTORY = 31
ENV_SET_MEMORY_MAPS = 36 | ENV_EXPERIMENTAL

PIXEL_0RGB1555, PIXEL_XRGB8888, PIXEL_RGB565 = 0, 1, 2

MEMORY_SAVE_RAM, MEMORY_RTC, MEMORY_SYSTEM_RAM, MEMORY_VIDEO_RAM = 0, 1, 2, 3

DEVICE_JOYPAD = 1
#: libretro joypad ids, keyed by NFNF normalized button names.
JOYPAD_IDS = {"B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5, "LEFT": 6,
              "RIGHT": 7, "A": 8, "X": 9, "L": 10, "R": 11}


class LibretroError(Exception):
    pass


class GameGeometry(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint), ("max_width", C.c_uint),
                ("max_height", C.c_uint), ("aspect_ratio", C.c_float)]


class SystemTiming(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class SystemAvInfo(C.Structure):
    _fields_ = [("geometry", GameGeometry), ("timing", SystemTiming)]


class SystemInfo(C.Structure):
    _fields_ = [("library_name", C.c_char_p), ("library_version", C.c_char_p),
                ("valid_extensions", C.c_char_p), ("need_fullpath", C.c_bool),
                ("block_extract", C.c_bool)]


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t),
                ("meta", C.c_char_p)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class MemoryDescriptor(C.Structure):
    _fields_ = [("flags", C.c_uint64), ("ptr", C.c_void_p), ("offset", C.c_size_t),
                ("start", C.c_size_t), ("select", C.c_size_t), ("disconnect", C.c_size_t),
                ("len", C.c_size_t), ("addrspace", C.c_char_p)]


class MemoryMap(C.Structure):
    _fields_ = [("descriptors", C.POINTER(MemoryDescriptor)), ("num_descriptors", C.c_uint)]


ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
INPUT_POLL_CB = C.CFUNCTYPE(None)
INPUT_STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


@dataclass
class Region:
    start: int
    length: int
    ptr: int
    select: int
    disconnect: int
    name: str


@dataclass
class Frame:
    width: int
    height: int
    rgb: bytes            # packed RGB888, row-major


@dataclass
class CoreInfo:
    library_name: str
    library_version: str
    valid_extensions: list[str]
    fps: float = 0.0
    sample_rate: float = 0.0
    width: int = 0
    height: int = 0


@dataclass
class InputSource:
    """Pressed NFNF buttons for the current frame (fed by ControllerManager)."""
    pressed: set[str] = field(default_factory=set)


class LibretroCore:
    """One loaded core with at most one loaded game. Not thread-safe."""

    _active: "LibretroCore | None" = None   # libretro cores are process-global singletons

    def __init__(self, core_path: Path | str, system_dir: Path, save_dir: Path,
                 options: dict[str, str] | None = None):
        if LibretroCore._active is not None:
            raise LibretroError("Only one libretro core instance may be active per process")
        self.core_path = Path(core_path)
        if not self.core_path.is_file():
            raise LibretroError(f"Core not found: {self.core_path}")
        self.system_dir = str(Path(system_dir).resolve()).encode()
        self.save_dir = str(Path(save_dir).resolve()).encode()
        self._sys_c = C.c_char_p(self.system_dir)
        self._save_c = C.c_char_p(self.save_dir)
        self.lib = C.CDLL(str(self.core_path.resolve()))
        self.pixel_format = PIXEL_0RGB1555
        self.regions: list[Region] = []
        self._last_raw: tuple[bytes, int, int, int, int] | None = None
        self.video_frames = 0
        self.frames_run = 0
        self.audio_frames = 0
        #: interleaved S16LE stereo samples produced since the last take_audio()
        self.audio_buffer = bytearray()
        self.capture_audio = True
        self.input = InputSource()
        self._rom_buf = None
        self._game_loaded = False
        #: core option values NFNF sets (key -> value); unknown keys keep core defaults
        self.options = {k.encode(): v.encode() for k, v in (options or {}).items()}
        self._option_bufs: dict[bytes, C.Array] = {}
        #: options the core declared: key -> (description, [allowed values])
        self.declared_options: dict[str, tuple[str, list[str]]] = {}
        self._declare()
        # keep callback objects referenced for the lifetime of the core
        self._cbs = (ENV_CB(self._env), VIDEO_CB(self._video), AUDIO_CB(self._audio),
                     AUDIO_BATCH_CB(self._audio_batch), INPUT_POLL_CB(lambda: None),
                     INPUT_STATE_CB(self._input_state))
        if self.lib.retro_api_version() != 1:
            raise LibretroError("Unsupported libretro API version")
        self.lib.retro_set_environment(self._cbs[0])
        self.lib.retro_set_video_refresh(self._cbs[1])
        self.lib.retro_set_audio_sample(self._cbs[2])
        self.lib.retro_set_audio_sample_batch(self._cbs[3])
        self.lib.retro_set_input_poll(self._cbs[4])
        self.lib.retro_set_input_state(self._cbs[5])
        self.lib.retro_init()
        LibretroCore._active = self

    def _declare(self) -> None:
        L = self.lib
        L.retro_api_version.restype = C.c_uint
        L.retro_get_system_info.argtypes = [C.POINTER(SystemInfo)]
        L.retro_get_system_av_info.argtypes = [C.POINTER(SystemAvInfo)]
        L.retro_load_game.argtypes = [C.POINTER(GameInfo)]
        L.retro_load_game.restype = C.c_bool
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_serialize.restype = C.c_bool
        L.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        L.retro_unserialize.restype = C.c_bool
        L.retro_get_memory_data.argtypes = [C.c_uint]
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_size.argtypes = [C.c_uint]
        L.retro_get_memory_size.restype = C.c_size_t
        for name in ("retro_set_environment", "retro_set_video_refresh", "retro_set_audio_sample",
                     "retro_set_audio_sample_batch", "retro_set_input_poll", "retro_set_input_state"):
            getattr(L, name).restype = None

    # --- callbacks ------------------------------------------------------
    def _env(self, cmd: int, data: int) -> bool:
        try:
            if cmd == ENV_GET_SYSTEM_DIRECTORY:
                C.cast(data, C.POINTER(C.c_char_p))[0] = self._sys_c.value
                return True
            if cmd == ENV_GET_SAVE_DIRECTORY:
                C.cast(data, C.POINTER(C.c_char_p))[0] = self._save_c.value
                return True
            if cmd == ENV_SET_PIXEL_FORMAT:
                fmt = C.cast(data, C.POINTER(C.c_int))[0]
                if fmt in (PIXEL_0RGB1555, PIXEL_XRGB8888, PIXEL_RGB565):
                    self.pixel_format = fmt
                    return True
                return False
            if cmd == ENV_GET_CAN_DUPE:
                C.cast(data, C.POINTER(C.c_bool))[0] = True
                return True
            if cmd == ENV_SET_VARIABLES:
                arr = C.cast(data, C.POINTER(Variable))
                i = 0
                while arr[i].key:
                    desc, _, values = (arr[i].value or b"").decode(errors="replace").partition("; ")
                    self.declared_options[arr[i].key.decode()] = (desc, values.split("|"))
                    i += 1
                return True
            if cmd == ENV_GET_VARIABLE:
                var = C.cast(data, C.POINTER(Variable))[0]
                value = self.options.get(var.key)
                if value is None:
                    return False
                buf = self._option_bufs.setdefault(var.key, C.create_string_buffer(value))
                C.cast(data, C.POINTER(Variable))[0].value = C.cast(buf, C.c_char_p)
                return True
            if cmd == ENV_GET_VARIABLE_UPDATE:
                C.cast(data, C.POINTER(C.c_bool))[0] = False
                return True
            if cmd == ENV_SET_MEMORY_MAPS:
                mmap = C.cast(data, C.POINTER(MemoryMap))[0]
                self.regions = []
                for i in range(mmap.num_descriptors):
                    d = mmap.descriptors[i]
                    if d.ptr and d.len:
                        self.regions.append(Region(d.start, d.len, d.ptr + d.offset, d.select,
                                                   d.disconnect,
                                                   (d.addrspace or b"").decode(errors="replace")))
                return True
        except Exception:  # never let a Python error unwind through C
            return False
        return False

    def _video(self, data: int, width: int, height: int, pitch: int) -> None:
        if not data:  # duplicated frame
            return
        # Keep the raw buffer; RGB conversion happens only when a frame is requested.
        self._last_raw = (C.string_at(data, pitch * height), width, height, pitch, self.pixel_format)
        self.video_frames += 1

    def _audio(self, left: int, right: int) -> None:
        self.audio_frames += 1
        if self.capture_audio:
            self.audio_buffer += struct.pack("<hh", left, right)

    def _audio_batch(self, data, frames: int) -> int:
        self.audio_frames += frames
        if self.capture_audio and frames:
            self.audio_buffer += C.string_at(data, frames * 4)
        return frames

    def take_audio(self) -> bytes:
        out = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        return out

    @property
    def last_raw(self):
        """(bytes, width, height, pitch, pixel_format) of the newest video frame, or None."""
        return self._last_raw

    def _input_state(self, port: int, device: int, index: int, button: int) -> int:
        if port != 0 or device != DEVICE_JOYPAD:
            return 0
        for name in self.input.pressed:
            if JOYPAD_IDS.get(name) == button:
                return 1
        return 0

    # --- API ------------------------------------------------------------
    def info(self) -> CoreInfo:
        si = SystemInfo()
        self.lib.retro_get_system_info(C.byref(si))
        ci = CoreInfo(si.library_name.decode(), si.library_version.decode(),
                      (si.valid_extensions or b"").decode().split("|"))
        if self._game_loaded:
            av = SystemAvInfo()
            self.lib.retro_get_system_av_info(C.byref(av))
            ci.fps, ci.sample_rate = av.timing.fps, av.timing.sample_rate
            ci.width, ci.height = av.geometry.base_width, av.geometry.base_height
        return ci

    def load_game(self, rom_path: Path) -> None:
        if self._game_loaded:
            raise LibretroError("A game is already loaded; call unload_game() first")
        data = Path(rom_path).read_bytes()      # the core gets a private in-memory copy
        self._rom_buf = C.create_string_buffer(data, len(data))
        self._rom_path_c = str(Path(rom_path).resolve()).encode()
        gi = GameInfo(self._rom_path_c, C.cast(self._rom_buf, C.c_void_p), len(data), None)
        if not self.lib.retro_load_game(C.byref(gi)):
            raise LibretroError(f"Core refused to load {rom_path}")
        self._game_loaded = True

    def run_frames(self, n: int, pressed: set[str] | None = None) -> None:
        if not self._game_loaded:
            raise LibretroError("No game loaded")
        self.input.pressed = set(pressed or ())
        for _ in range(n):
            self.lib.retro_run()
            self.frames_run += 1

    @property
    def last_frame(self) -> Frame | None:
        if self._last_raw is None:
            return None
        raw, w, h, pitch, fmt = self._last_raw
        return Frame(w, h, _to_rgb(raw, w, h, pitch, fmt))

    def reset(self) -> None:
        self.lib.retro_reset()

    def memory(self, kind: int) -> bytes:
        size = self.lib.retro_get_memory_size(kind)
        ptr = self.lib.retro_get_memory_data(kind)
        return C.string_at(ptr, size) if ptr and size else b""

    def read_bus(self, address: int, length: int) -> bytes:
        """Read ``length`` bytes at a guest bus address via the core's memory map.

        Simplified libretro descriptor matching (no disconnect-bit compaction),
        sufficient for linear regions such as GBA EWRAM/IWRAM/ROM."""
        for r in self.regions:
            if r.select:
                hit = (address & r.select) == (r.start & r.select)
            else:
                hit = r.start <= address < r.start + r.length
            if hit:
                rel = (address - r.start) % r.length
                if rel + length > r.length:
                    raise LibretroError("Read crosses the end of a memory region")
                return C.string_at(r.ptr + rel, length)
        raise LibretroError(f"Address 0x{address:08X} is not mapped by the core")

    def save_state(self) -> bytes:
        size = self.lib.retro_serialize_size()
        buf = C.create_string_buffer(size)
        if not self.lib.retro_serialize(buf, size):
            raise LibretroError("retro_serialize failed")
        return buf.raw

    def load_state(self, state: bytes) -> None:
        buf = C.create_string_buffer(state, len(state))
        if not self.lib.retro_unserialize(buf, len(state)):
            raise LibretroError("retro_unserialize failed")

    def export_save_ram(self, path: Path) -> int:
        data = self.memory(MEMORY_SAVE_RAM)
        Path(path).write_bytes(data)
        return len(data)

    def import_save_ram(self, path: Path) -> int:
        data = Path(path).read_bytes()
        size = self.lib.retro_get_memory_size(MEMORY_SAVE_RAM)
        ptr = self.lib.retro_get_memory_data(MEMORY_SAVE_RAM)
        if not ptr or len(data) != size:
            raise LibretroError(f"Save RAM size mismatch ({len(data)} vs {size})")
        C.memmove(ptr, data, size)
        return size

    def unload_game(self) -> None:
        if self._game_loaded:
            self.lib.retro_unload_game()
            self._game_loaded = False
            self._last_raw = None
            self.audio_buffer.clear()
            self.regions = []

    def write_bus(self, address: int, data: bytes) -> None:
        """Write guest memory (used by tests/tools to create deterministic game states)."""
        for r in self.regions:
            hit = ((address & r.select) == (r.start & r.select)) if r.select else r.start <= address < r.start + r.length
            if hit:
                rel = (address - r.start) % r.length
                if rel + len(data) > r.length:
                    raise LibretroError("Write crosses the end of a memory region")
                C.memmove(r.ptr + rel, data, len(data))
                return
        raise LibretroError(f"Address 0x{address:08X} is not mapped by the core")

    def close(self) -> None:
        if self._game_loaded:
            self.lib.retro_unload_game()
            self._game_loaded = False
        self.lib.retro_deinit()
        LibretroCore._active = None

    def __enter__(self) -> "LibretroCore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --- pixel conversion / PNG -------------------------------------------------

def _to_rgb(raw: bytes, w: int, h: int, pitch: int, fmt: int) -> bytes:
    out = bytearray(w * h * 3)
    o = 0
    if fmt == PIXEL_XRGB8888:
        for y in range(h):
            row = raw[y * pitch: y * pitch + w * 4]
            for x in range(0, w * 4, 4):
                out[o] = row[x + 2]; out[o + 1] = row[x + 1]; out[o + 2] = row[x]
                o += 3
    else:
        for y in range(h):
            row = raw[y * pitch: y * pitch + w * 2]
            for (px,) in struct.iter_unpack("<H", row):
                if fmt == PIXEL_RGB565:
                    r, g, b = (px >> 11) & 0x1F, (px >> 5) & 0x3F, px & 0x1F
                    out[o], out[o + 1], out[o + 2] = r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2
                else:
                    r, g, b = (px >> 10) & 0x1F, (px >> 5) & 0x1F, px & 0x1F
                    out[o], out[o + 1], out[o + 2] = r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2
                o += 3
    return bytes(out)


def write_png(path: Path, frame: Frame, scale: int = 1) -> None:
    w, h = frame.width * scale, frame.height * scale
    rows = []
    for y in range(frame.height):
        src = frame.rgb[y * frame.width * 3:(y + 1) * frame.width * 3]
        row = b"".join(src[x * 3:x * 3 + 3] * scale for x in range(frame.width))
        rows.extend([b"\x00" + row] * scale)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)
