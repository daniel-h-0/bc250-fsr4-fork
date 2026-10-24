#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rebuild the single DLL from the pinned SDK, DXC and frozen DXIL assembly.

`--config rc11` (default) reproduces the RC11 release from its frozen sources. `--config rederived`
builds the same sources with the re-derived elements of dll/rederived.json (see
dll/port/rederive.py) and checks the DLL against that configuration's pin instead: a comparison
build. `--config rc12` builds the RC12 release of dll/rc12.json: the re-derived configuration,
RC11's eligibility edit applied unchanged and the C10 programs, under its own provider label (see
dll/port/rc12.py).
"""

import argparse
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .port import drop_profile, rc12, rederive
    from .repack_dll import build
else:
    from port import drop_profile, rc12, rederive
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
    p.add_argument(
        "--config",
        choices=("rc11", "rederived", "rc12"),
        default="rc11",
        help="rc11 reproduces RC11; rederived applies dll/rederived.json (comparison build); "
        "rc12 builds the RC12 release of dll/rc12.json",
    )
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
    _, profile = drop_profile.select(manifest["sdk_sha256"])
    drop_profile.check_manifest(profile, manifest)
    config, restored, omitted, c10 = None, {}, [], {}
    provider_name = manifest["provider_name"]
    if a.config == "rederived":
        config = rederive.load_config()
        summary = rederive.check_config(config, manifest, sha(ROOT / "manifest.json"), profile)
        restored = rederive.restorations(config)
        omitted = rederive.pending_host_patches(config)
    elif a.config == "rc12":
        config = rc12.load_config()
        summary = rc12.check_config(config, manifest, sha(ROOT / "manifest.json"), profile)
        base = rederive.load_config(ROOT / config["base"]["path"])
        restored = rederive.restorations(base)
        omitted = rc12.omitted_host_patches(config, base)
        c10 = {int(k, 16): (k, slot) for k, slot in config["slots"].items()}
        provider_name = config["provider_name"]
    if a.verify_sources:
        print(
            f"Verified {len(rows)} shader sources, the complete DLL source inventory and "
            f"their slots in drop profile {profile['drop']['tag']}"
            + (f"; {summary}" if config else "")
        )
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
    drop_profile.verify(sdk.read_bytes(), profile)
    out.mkdir(parents=True)
    (out / "shaders").mkdir()
    if restored:
        (out / "rederived-sources").mkdir()
    if c10:
        (out / "rc12-sources").mkdir()
    by_offset = {r["original_offset"]: r for r in rows}
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
        slot = restored.get(row["original_offset"])
        if slot is not None:
            src = out / "rederived-sources" / f"{row['original_offset']:08x}.ll"
            src.write_text(rederive.restored_source(row, slot))
            row["source_sha256"] = slot["source_sha256"]
            row["replacement_sha256"] = slot["replacement_sha256"]
        if row["original_offset"] in c10:
            key, slot = c10[row["original_offset"]]
            src = out / "rc12-sources" / f"{row['original_offset']:08x}.ll"
            src.write_text(rc12.slot_source(by_offset, key, slot))
            row["source_sha256"] = slot["source_sha256"]
            row["replacement_sha256"] = slot["replacement_sha256"]
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
    record = build(
        sdk,
        dict(
            sdk_sha256=manifest["sdk_sha256"],
            provider_name=provider_name,
            replacements=rows,
        ),
        dll,
        profile,
        omit_host_patches=omitted,
    )
    expected = config if config else manifest
    if (
        record["sha256"] != expected["expected_dll_sha256"]
        or record["bytes"] != expected["expected_dll_bytes"]
    ):
        raise ValueError(
            f"Rebuilt DLL differs ({record['sha256']}, {record['bytes']} bytes)"
            if config
            else "Rebuilt DLL differs"
        )
    print(
        json.dumps(
            dict(
                shaders_validated=len(rows),
                dll=str(dll),
                sha256=record["sha256"],
                bytes=record["bytes"],
                **(
                    dict(
                        config=a.config,
                        restored_slots=[f"0x{o:x}" for o in sorted(restored)],
                        omitted_host_patches=omitted,
                        c10_slots=[f"0x{o:x}" for o in sorted(c10)],
                        provider_name=provider_name,
                    )
                    if config
                    else {}
                ),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
