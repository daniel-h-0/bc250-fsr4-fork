#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare serialized Mesa shader programs using offsets derived by inspect_layout.c."""

import argparse
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inspect(path, layout):
    data = path.read_bytes()

    def value(name):
        return struct.unpack_from("<I", data, layout[name])[0]

    if value("total_size") != len(data) or layout["legacy_bytes"] != layout["data"]:
        raise ValueError("Binary does not match the recorded Mesa layout")
    names = ("code_size", "exec_size", "ir_size", "disasm_size", "stats_size", "debug_info_size")
    sizes = {name: value(name) for name in names}
    if sizes["ir_size"] or sizes["disasm_size"] or sizes["debug_info_size"]:
        raise ValueError("Use a capture without shader debug strings")
    if not 0 < sizes["exec_size"] <= sizes["code_size"]:
        raise ValueError("Invalid executable size")
    start = layout["data"] + sizes["stats_size"]
    if start + sizes["code_size"] != len(data):
        raise ValueError("Shader data size differs")
    code = data[start:]
    config = bytearray(data[layout["config"] : layout["config"] + layout["config_bytes"]])
    # The compiler-derived gap after bool wgp_mode is C padding, not a GPU field.
    begin, end = layout["wgp_mode"] + 1, layout["rsrc1"]
    config[begin:end] = bytes(end - begin)
    return {
        "code_and_constants": digest(code),
        "instructions": digest(code[: sizes["exec_size"]]),
        "hardware_config": digest(config),
        "shader_info": digest(data[layout["info"] : layout["total_size"]]),
        "statistics": digest(data[layout["data"] : start]),
        "sizes": sizes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--layout", type=Path, default=ROOT / "mesa-26.2.2-layout.json")
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text())
    before, after = inspect(args.before, layout), inspect(args.after, layout)
    equal = {key: before[key] == after[key] for key in before}
    print(json.dumps({"equal": equal, "before": before, "after": after}, indent=2))
    if not all(equal.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
