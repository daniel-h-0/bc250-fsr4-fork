#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify the complete experimental source capsule without modifying any files."""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(mesa_source=None):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    source = ROOT / "source-overlay.tar.xz"
    if digest(source.read_bytes()) != manifest["source_overlay_sha256"]:
        raise ValueError("Source capsule checksum differs")
    expected = {
        "src/amd/vulkan/bc250_rc9_shaders.h",
        "src/amd/vulkan/radv_shader.c",
        "src/amd/vulkan/radv_physical_device.c",
    }
    if set(manifest["source_files"]) != expected:
        raise ValueError("Unexpected source inventory")
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != expected:
            raise ValueError("Source capsule member inventory differs")
        for member in members:
            metadata = manifest["source_files"][member.name]
            if not member.isfile() or member.size != metadata["bytes"]:
                raise ValueError("Unexpected source member type or size")
            with archive.extractfile(member) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]:
                    raise ValueError("Source member checksum differs")
            if mesa_source:
                path = mesa_source / member.name
                if path.is_symlink() or digest(path.read_bytes()) != metadata["sha256"]:
                    raise ValueError("Prepared Mesa source differs: " + member.name)
    return "three complete prototype sources and their capsule checksum"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesa-source", type=Path)
    args = parser.parse_args()
    print("PASS: " + verify(args.mesa_source))


if __name__ == "__main__":
    main()
