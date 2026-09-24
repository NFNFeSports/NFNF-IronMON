"""Convert a UPR ZX settings string ("<version digits><base64>") into a .rnqs file.

UPR ZX 4.6.1 writes .rnqs as: int32 BE version, int32 BE length, UTF-8 Base64
(Settings.write). Settings strings shown by UPR / published by the IronMON
community are str(version) + the same Base64 text, so the conversion is exact.

usage: python3 tools/settings_string_to_rnqs.py <string> <out.rnqs>
"""

import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nfnf_ironmon.randomizers.rnqs import parse_rnqs  # noqa: E402


def convert(settings_string: str) -> bytes:
    m = re.fullmatch(r"(\d{3})([A-Za-z0-9+/=]+)", settings_string.strip())
    if not m:
        raise ValueError("Not a UPR settings string")
    payload = m.group(2).encode("ascii")
    data = struct.pack(">ii", int(m.group(1)), len(payload)) + payload
    parse_rnqs(data)          # validates Base64 + CRC
    return data


if __name__ == "__main__":
    out = Path(sys.argv[2])
    out.write_bytes(convert(sys.argv[1]))
    info = parse_rnqs(out.read_bytes())
    print(f"{out}: version {info.version}, rom name '{info.rom_name}', sha256 {info.sha256}")
