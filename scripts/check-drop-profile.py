#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Re-derive the SDK drop profile and repack the release DLL from it, byte for byte.

The SDK DLL must be allowlisted and must derive exactly the reviewed profile. The validated
replacement programs come from dll/build.py's output (--shaders) or from the published release
DLL itself (--from-dll; its hash must be the manifest's). Either way each program is checked
against the manifest's replacement hash before the profile-driven repack, whose output must equal
the manifest's expected DLL.
"""

import argparse
import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dll import repack_dll
from dll.port import drop_profile, sdk_intake


def sha(data):
    return hashlib.sha256(data).hexdigest()


def release_programs(path, manifest, profile):
    """Replacement programs read back through the profile's pointers of our own release DLL."""
    data = path.read_bytes()
    if sha(data) != manifest["expected_dll_sha256"]:
        raise ValueError("--from-dll is not the manifest's release DLL")
    image = drop_profile.Image(data)
    slots = {slot["offset"]: slot for slot in profile["shaders"]["slots"]}
    programs = {}
    for row in manifest["replacements"]:
        pointers = slots[row["original_offset"]]["pointers"]
        lengths = {struct.unpack_from("<QQ", data, ptr - 8) for ptr in pointers}
        if len(lengths) != 1:
            raise ValueError("Release pointers of one slot disagree")
        length, va = lengths.pop()
        offset = image.to_offset(va - image.image_base)
        programs[row["original_offset"]] = data[offset : offset + length]
    return programs


def built_programs(directory, manifest):
    return {
        row["original_offset"]: (directory / f"{row['original_offset']:08x}.dxil").read_bytes()
        for row in manifest["replacements"]
    }


def check(sdk, shaders=None, from_dll=None, output=None):
    manifest = json.loads((ROOT / "dll/manifest.json").read_text())
    original = sdk.read_bytes()
    path, profile = drop_profile.select(sha(original))
    drop_profile.verify(original, profile)
    drop_profile.check_manifest(profile, manifest)
    if from_dll is not None:
        programs = release_programs(from_dll, manifest, profile)
    else:
        programs = built_programs(shaders, manifest)
    with tempfile.TemporaryDirectory(prefix="bc250-drop-profile-") as scratch:
        work = Path(scratch)
        if output is not None:
            work = sdk_intake.outside_source(output)
            if work.exists():
                raise FileExistsError(work)
        (work / "shaders").mkdir(parents=True)
        rows = []
        for row in manifest["replacements"]:
            program = programs[row["original_offset"]]
            if sha(program) != row["replacement_sha256"]:
                raise ValueError(f"Program for slot 0x{row['original_offset']:x} differs")
            target = work / "shaders" / f"{row['original_offset']:08x}.dxil"
            with target.open("xb") as stream:
                stream.write(program)
            rows.append(dict(row, replacement=str(target)))
        dll = work / sdk_intake.DLL_NAME
        record = repack_dll.build(
            sdk,
            dict(
                sdk_sha256=manifest["sdk_sha256"],
                provider_name=manifest["provider_name"],
                replacements=rows,
            ),
            dll,
            profile,
        )
    reproduced = (
        record["sha256"] == manifest["expected_dll_sha256"]
        and record["bytes"] == manifest["expected_dll_bytes"]
    )
    result = dict(
        drop_profile=str(path.relative_to(ROOT)),
        drop_profile_sha256=record["drop_profile"]["sha256"],
        profile_rederived=True,
        programs_from="release DLL" if from_dll is not None else str(shaders),
        programs=len(rows),
        sha256=record["sha256"],
        bytes=record["bytes"],
        reproduced=reproduced,
    )
    if not reproduced:
        raise ValueError(
            "Profile-driven repack differs from the release DLL: " + json.dumps(result)
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk", type=Path, required=True, help="allowlisted SDK upscaler DLL")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--shaders", type=Path, help="dll/build.py output's shaders/ directory")
    source.add_argument("--from-dll", type=Path, help="the manifest's release DLL")
    parser.add_argument("--output", type=Path, help="keep the repack in this new directory")
    args = parser.parse_args()
    print(json.dumps(check(args.sdk, args.shaders, args.from_dll, args.output), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit("ERROR: " + str(error))
