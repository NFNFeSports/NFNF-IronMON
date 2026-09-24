"""HUD drawing: bitmap-font text and clickable buttons on the SDL renderer."""

from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from typing import Callable

from . import sdl2 as S
from .font8x8 import GLYPHS

WHITE = (235, 235, 235)
GREY = (150, 150, 160)
GREEN = (90, 220, 120)
YELLOW = (240, 210, 80)
RED = (240, 90, 80)
CYAN = (110, 200, 240)
PANEL = (22, 24, 30)
BUTTON = (48, 52, 64)
BUTTON_HOT = (80, 90, 120)

_TRANSLATE = str.maketrans({"é": "e", "♂": "M", "♀": "F", "…": "...", "“": '"', "”": '"', "‘": "'", "’": "'",
                            "✓": "*", "—": "-", "–": "-", "→": ">"})


class TextRenderer:
    """128 ASCII glyphs in a 16x8 atlas texture (8x8 px each)."""

    def __init__(self, sdl: S.SDL, renderer):
        self.sdl, self.r = sdl, renderer
        w, h = 128, 64
        pixels = (C.c_uint32 * (w * h))()
        for code in range(128):
            gx, gy = (code % 16) * 8, (code // 16) * 8
            for row in range(8):
                bits = GLYPHS[code * 8 + row]
                for col in range(8):
                    if bits >> col & 1:
                        pixels[(gy + row) * w + gx + col] = 0xFFFFFFFF
        self.tex = sdl.CreateTexture(renderer, S.PIXELFORMAT_ARGB8888, S.TEXTUREACCESS_STATIC, w, h)
        sdl.check(self.tex, "SDL_CreateTexture(font)", pointer=True)
        sdl.UpdateTexture(self.tex, None, pixels, w * 4)
        sdl.SetTextureBlendMode(self.tex, S.BLENDMODE_BLEND)

    def draw(self, x: int, y: int, text: str, scale: int = 2, color=WHITE, max_chars: int | None = None) -> int:
        """Draw ``text``; returns the x after the last glyph."""
        text = str(text).translate(_TRANSLATE)
        if max_chars is not None and len(text) > max_chars:
            text = text[:max(0, max_chars - 1)] + "~"
        self.sdl.SetTextureColorMod(self.tex, *color)
        src, dst = S.Rect(0, 0, 8, 8), S.Rect(0, y, 8 * scale, 8 * scale)
        for ch in text:
            code = ord(ch) if ord(ch) < 128 else ord("?")
            src.x, src.y = (code % 16) * 8, (code // 16) * 8
            dst.x = x
            self.sdl.RenderCopy(self.r, self.tex, C.byref(src), C.byref(dst))
            x += 8 * scale
        return x

    def width(self, text: str, scale: int = 2) -> int:
        return len(str(text)) * 8 * scale

    def destroy(self) -> None:
        self.sdl.DestroyTexture(self.tex)


@dataclass
class Button:
    label: str
    action: Callable[[], None]
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    enabled: bool = True

    def hit(self, x: int, y: int) -> bool:
        bx, by, bw, bh = self.rect
        return self.enabled and bx <= x < bx + bw and by <= y < by + bh


def fill(sdl: S.SDL, r, rect: tuple[int, int, int, int], color, alpha: int = 255) -> None:
    sdl.SetRenderDrawColor(r, *color, alpha)
    sdl.RenderFillRect(r, C.byref(S.Rect(*rect)))


def outline(sdl: S.SDL, r, rect: tuple[int, int, int, int], color) -> None:
    sdl.SetRenderDrawColor(r, *color, 255)
    sdl.RenderDrawRect(r, C.byref(S.Rect(*rect)))


def draw_button(sdl: S.SDL, r, text: TextRenderer, b: Button, x: int, y: int, scale: int = 2,
                hot: bool = False) -> int:
    pad = 4 * scale
    w, h = text.width(b.label, scale) + 2 * pad, 8 * scale + 2 * pad
    b.rect = (x, y, w, h)
    fill(sdl, r, b.rect, BUTTON_HOT if hot else BUTTON)
    outline(sdl, r, b.rect, YELLOW if hot else GREY)
    text.draw(x + pad, y + pad, b.label, scale, WHITE if b.enabled else GREY)
    return x + w + 3 * scale
