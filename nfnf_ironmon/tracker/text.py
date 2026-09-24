"""In-game text encodings (Gen 1/2 share one table; Gen 3 has its own).

Only the characters needed for Pokémon/area names are mapped; anything else
decodes to "?" rather than a guess.
"""

from __future__ import annotations

GEN12 = {0x7F: " ", 0xE0: "'", 0xE3: "-", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xEF: "♂", 0xF5: "♀",
         0xF4: ",", 0xF3: "/", 0xBA: "é"}
GEN12.update({0x80 + i: chr(ord("A") + i) for i in range(26)})
GEN12.update({0xA0 + i: chr(ord("a") + i) for i in range(26)})
GEN12.update({0xF6 + i: str(i) for i in range(10)})
GEN12_END = 0x50

GEN3 = {0x00: " ", 0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xB0: "…", 0xB1: "“", 0xB2: "”",
        0xB3: "‘", 0xB4: "’", 0xB5: "♂", 0xB6: "♀", 0xB8: ",", 0xBA: "/", 0x1B: "é", 0x2D: "&",
        0x5C: "(", 0x5D: ")", 0xF0: ":"}
GEN3.update({0xBB + i: chr(ord("A") + i) for i in range(26)})
GEN3.update({0xD5 + i: chr(ord("a") + i) for i in range(26)})
GEN3.update({0xA1 + i: str(i) for i in range(10)})
GEN3_END = 0xFF


def decode_gen12(data: bytes) -> str:
    out = []
    for b in data:
        if b == GEN12_END:
            break
        out.append(GEN12.get(b, "?"))
    return "".join(out).strip()


def decode_gen3(data: bytes) -> str:
    out = []
    for b in data:
        if b == GEN3_END:
            break
        out.append(GEN3.get(b, "?"))
    return "".join(out).strip()
