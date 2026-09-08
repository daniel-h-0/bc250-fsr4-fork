# SPDX-License-Identifier: MIT
"""Reproduce the name check in one identified Steam client, without a login.

This is a qualification artifact, not a runtime dependency. Refuse every other
binary before loading it or calling the private registration routine. The
offsets were audited against this binary's registration parser and root setup.
No Steam interface, game, cloud API or filesystem writer is called.
"""

import ctypes
import hashlib
import json
import sys
from pathlib import Path

EXPECTED = "6e18905cd6677c77f8e6a50bf41e1eefbd4caeffaa95b8cd2c85d5bf11414ce1"
path = Path(sys.argv[1]).resolve()
with path.open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != EXPECTED:
    raise SystemExit("Different Steam client: the recorded offsets do not apply.")
library = ctypes.CDLL(str(path), mode=ctypes.RTLD_LOCAL)
base = None
for line in Path("/proc/self/maps").read_text().splitlines():
    fields = line.split(maxsplit=5)
    if len(fields) == 6 and fields[5] == str(path) and fields[2] == "00000000":
        base = int(fields[0].split("-")[0], 16)
        break
if base is None:
    raise SystemExit("Cannot identify the loaded Steam client.")
initialize = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(base + 0x1170880)
rows = []
for name in ("BC250-FSR4", "proton-bc250-fsr4", "BC250-Proton-FSR4", "GE-Proton11-6-x86_64"):
    record = ctypes.create_string_buffer(0x200)
    key = ctypes.create_string_buffer(name.encode())
    platform = ctypes.create_string_buffer(b"windows")
    ctypes.c_void_p.from_buffer(record, 0x40).value = ctypes.addressof(key)
    ctypes.c_void_p.from_buffer(record, 0x70).value = ctypes.addressof(platform)
    initialize(None, ctypes.addressof(record))
    row = {"name": name}
    for label, offset in (("documents", 0x98), ("local_appdata", 0xA0), ("saved_games", 0xD0), ("profile", 0x118)):
        pointer = ctypes.c_void_p.from_buffer(record, offset).value
        row[label] = ctypes.string_at(pointer).decode() if pointer else None
    rows.append(row)
print(json.dumps({"steamclient_sha256": actual, "function_offset": "0x1170880", "results": rows}, indent=2))
