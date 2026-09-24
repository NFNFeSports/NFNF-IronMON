"""Game audio through SDL2's queued audio API, with volume and mute.

The emulator produces interleaved signed 16-bit stereo at the core's native
rate (mGBA: 65536 Hz GBA, 131072 Hz GB); SDL converts to the device format.
The queue level also paces emulation (audio-driven sync).
"""

from __future__ import annotations

import ctypes as C

from . import sdl2 as S

MAX_VOLUME = 128   # SDL_MIX_MAXVOLUME


class AudioOutput:
    def __init__(self, sdl: S.SDL, sample_rate: int, volume: int = 70, muted: bool = False):
        self.sdl = sdl
        self.rate = int(sample_rate)
        self.volume = max(0, min(100, volume))
        self.muted = muted
        self.device = 0
        self.driver: str | None = None
        self.error: str | None = None
        self.bytes_per_second = self.rate * 4
        want, have = S.AudioSpec(), S.AudioSpec()
        want.freq, want.format, want.channels, want.samples = self.rate, S.AUDIO_S16LSB, 2, 2048
        dev = sdl.OpenAudioDevice(None, 0, C.byref(want), C.byref(have), 0)
        if dev == 0:
            self.error = sdl.error()
            return
        self.device = dev
        drv = sdl.GetCurrentAudioDriver()
        self.driver = drv.decode() if drv else None
        sdl.PauseAudioDevice(dev, 0)

    @property
    def available(self) -> bool:
        return self.device != 0

    def queue(self, samples: bytes) -> None:
        if not self.device or not samples:
            return
        n = len(samples)
        src = (C.c_uint8 * n).from_buffer_copy(samples)
        if self.muted or self.volume == 0:
            dst = (C.c_uint8 * n)()                    # silence keeps timing intact
        elif self.volume >= 100:
            dst = src
        else:
            dst = (C.c_uint8 * n)()
            self.sdl.MixAudioFormat(dst, src, S.AUDIO_S16LSB, n, self.volume * MAX_VOLUME // 100)
        self.sdl.QueueAudio(self.device, dst, n)

    def queued_seconds(self) -> float:
        if not self.device:
            return 0.0
        return self.sdl.GetQueuedAudioSize(self.device) / self.bytes_per_second

    def clear(self) -> None:
        if self.device:
            self.sdl.ClearQueuedAudio(self.device)

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, volume))

    def close(self) -> None:
        if self.device:
            self.sdl.CloseAudioDevice(self.device)
            self.device = 0
