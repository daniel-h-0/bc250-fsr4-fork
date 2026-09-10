#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rebuild the single DLL from the pinned SDK, DXC and frozen DXIL assembly."""

import argparse
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .repack_dll import build
else:
    from repack_dll import build

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources():
    inventory_path = ROOT / "source-inventory.json"
    if inventory_path.is_symlink() or not inventory_path.is_file():
        raise ValueError("Source inventory must be a regular file")
    inventory = json.loads(inventory_path.read_text())
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Source inventory must be a nonempty mapping")
    if any(p.is_symlink() for p in ROOT.rglob("*")):
        raise ValueError("Source symlinks are not supported")
    actual = {
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p != inventory_path
    }
    if actual != set(inventory):
        raise ValueError("Source file inventory mismatch")
    for name, digest in inventory.items():
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Invalid source hash: " + name)
        p = ROOT / name
        if p.is_symlink() or ROOT not in p.resolve().parents or sha(p) != digest:
            raise ValueError("Source identity mismatch: " + name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--verify-sources", action="store_true", help="Check all source inputs without compiling"
    )
    p.add_argument("--sdk", type=Path)
    p.add_argument("--dxcompiler", type=Path, help="Pinned libdxcompiler.so from DXC 1.9.2607")
    p.add_argument("--output", type=Path, help="A new directory outside the source tree")
    p.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    a = p.parse_args()
    if a.verify_sources and any((a.sdk, a.dxcompiler, a.output)):
        p.error("--verify-sources does not accept build paths")
    if not a.verify_sources and not all((a.sdk, a.dxcompiler, a.output)):
        p.error("Rebuilding requires --sdk, --dxcompiler and --output")
    verify_sources()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    rows = manifest["replacements"]
    if len(rows) != 348 or len({r["original_offset"] for r in rows}) != 348:
        raise ValueError("Incomplete or duplicate shader set")
    for row in rows:
        source = Path(row["source"])
        if (
            source.is_absolute()
            or ".." in source.parts
            or not source.parts
            or source.parts[0] != "shaders"
        ):
            raise ValueError("Shader source must stay inside shaders/")
        if sha(ROOT / source) != row["source_sha256"]:
            raise ValueError("Shader source identity mismatch: " + row["source"])
    if a.verify_sources:
        print(f"Verified {len(rows)} shader sources and the complete DLL source inventory")
        return
    sdk = a.sdk.resolve()
    dxc = a.dxcompiler.resolve()
    out = a.output.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Output must be outside the source tree")
    if out.exists():
        raise FileExistsError(out)
    if sha(sdk) != manifest["sdk_sha256"]:
        raise ValueError("SDK identity mismatch")
    if sha(dxc) != manifest["dxcompiler_sha256"]:
        raise ValueError("DXC identity mismatch")
    out.mkdir(parents=True)
    (out / "shaders").mkdir()
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(ROOT / "assemble.cpp"),
            "-ldl",
            "-o",
            str(out / "assemble"),
        ],
        check=True,
    )

    def compile_one(row):
        row = dict(row)
        src = ROOT / row["source"]
        if sha(src) != row["source_sha256"]:
            raise ValueError("Shader source identity mismatch")
        raw = out / "shaders" / f"{row['original_offset']:08x}.raw.dxil"
        dxil = raw.with_name(raw.name.replace(".raw.dxil", ".dxil"))
        for action, first, last in [("assemble", src, raw), ("validate", raw, dxil)]:
            q = subprocess.run(
                [str(out / "assemble"), str(dxc), action, str(first), str(last)],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if q.returncode:
                raise RuntimeError(q.stderr)
        if sha(dxil) != row["replacement_sha256"]:
            raise ValueError("Rebuilt shader differs: " + row["source"])
        raw.unlink()
        row["replacement"] = str(dxil)
        return row

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        rows = list(pool.map(compile_one, manifest["replacements"]))
    dll = out / "amd_fidelityfx_upscaler_dx12.dll"
    record = build(sdk, dict(sdk_sha256=manifest["sdk_sha256"], replacements=rows), dll)
    if (
        record["sha256"] != manifest["expected_dll_sha256"]
        or record["bytes"] != manifest["expected_dll_bytes"]
    ):
        raise ValueError("Rebuilt DLL differs")
    print(
        json.dumps(
            dict(
                shaders_validated=len(rows),
                dll=str(dll),
                sha256=record["sha256"],
                bytes=record["bytes"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
