"""Memory views over the running core, plus a fake for deterministic tests."""

from __future__ import annotations

import struct
from typing import Protocol


class MemoryView(Protocol):
    def read(self, address: int, length: int) -> bytes: ...


def u8(m: MemoryView, a: int) -> int:
    return m.read(a, 1)[0]


def u16(m: MemoryView, a: int) -> int:
    return struct.unpack("<H", m.read(a, 2))[0]


def u16be(m: MemoryView, a: int) -> int:
    return struct.unpack(">H", m.read(a, 2))[0]


def u32(m: MemoryView, a: int) -> int:
    return struct.unpack("<I", m.read(a, 4))[0]


class GbaBus:
    """GBA bus addresses through the libretro memory map (tested: EWRAM/IWRAM/ROM)."""

    def __init__(self, core):
        self.core = core

    def read(self, address: int, length: int) -> bytes:
        return self.core.read_bus(address, length)


class GbWram:
    """Game Boy / Color WRAM through the core's flat SYSTEM_RAM block.

    Addresses use pret's ``bank << 16 | address`` convention: C000-CFFF is
    bank 0; D000-DFFF is the switchable bank (bank 1 when unspecified, which
    is also the only bank on the original Game Boy).
    """

    def __init__(self, ram: bytes):
        self.ram = ram      # one snapshot per tracker poll (core.memory(MEMORY_SYSTEM_RAM))

    def offset(self, address: int) -> int:
        bank, addr = address >> 16, address & 0xFFFF
        if 0xC000 <= addr < 0xD000:
            return addr - 0xC000
        if 0xD000 <= addr < 0xE000:
            return 0x1000 * max(bank, 1) + (addr - 0xD000)
        raise ValueError(f"Not a WRAM address: {address:#x}")

    def read(self, address: int, length: int) -> bytes:
        off = self.offset(address)
        if off + length > len(self.ram):
            raise ValueError("WRAM read out of range")
        return self.ram[off:off + length]


class FakeMemory:
    """Sparse writable memory for tests: ``fake.write(addr, bytes)``."""

    def __init__(self):
        self.cells: dict[int, int] = {}

    def write(self, address: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.cells[address + i] = b

    def read(self, address: int, length: int) -> bytes:
        return bytes(self.cells.get(address + i, 0) for i in range(length))
