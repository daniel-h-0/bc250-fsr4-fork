#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create the binary/source release archive from a completed pinned source build."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
import build
import driver
from release_common import source_files, source_identity

ROOT = Path(__file__).resolve().parents[1]


def public_provenance(value, work, root):
    """Retain effective build settings while removing local workspace/home paths."""
    if isinstance(value, dict):
        return {key: public_provenance(item, work, root) for key, item in value.items()}
    if isinstance(value, list):
        return [public_provenance(item, work, root) for item in value]
    if isinstance(value, str):
        for path, label in (
            (work, "<build-work>"),
            (root, "<source-root>"),
            (Path.home(), "<home>"),
        ):
            value = value.replace(str(path), label)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--work", type=Path, default=ROOT / ".work/native")
    p.add_argument("--output", type=Path, default=ROOT / "dist")
    p.add_argument("--label", default="linux-x86_64", help="ABI/build label, e.g. cachyos-x86_64")
    args = p.parse_args()
    if not args.label or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.label):
        p.error("Use lowercase letters, digits, - or _ in the build label.")
    args.work = args.work.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    manifest, built = build.verify_completed_build(args.work, ROOT)
    inventory = source_files(ROOT)
    identity = source_identity(ROOT)
    library = args.work / "build/src/amd/vulkan/libvulkan_radeon.so"
    name = "bc250-fsr4-v" + manifest["version"] + "-" + args.label
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / (name + ".tar.gz")
    checksum = Path(str(archive) + ".sha256")
    if archive.exists() or checksum.exists():
        raise RuntimeError("Output already exists; choose a new output directory or label.")
    with tempfile.TemporaryDirectory(prefix=".package-", dir=args.output) as tmp:
        root = Path(tmp) / name
        (root / "lib").mkdir(parents=True)
        shutil.copy2(library, root / "lib/libvulkan_radeon.so")
        # Strip only the copied release, never mutate build evidence.
        subprocess.run(
            ["strip", "--strip-unneeded", str(root / "lib/libvulkan_radeon.so")], check=True
        )
        for relative in inventory:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        copied_manifest_sha256 = driver.digest(root / "v4/manifest.json")
        if copied_manifest_sha256 != built["manifest_sha256"]:
            raise RuntimeError("Source manifest changed while packaging.")
        build.verify_inputs(root)
        if build.recipe_hashes(root) != built["recipe_hashes"]:
            raise RuntimeError("Source recipe changed while packaging.")
        (root / "licenses").mkdir()
        shutil.copy2(
            build.source_directory(args.work, manifest) / "docs/license.rst",
            root / "licenses/Mesa-license.rst",
        )
        shutil.copytree(
            build.source_directory(args.work, manifest) / "licenses", root / "licenses/Mesa"
        )
        source_notice = build.source_directory(args.work, manifest) / "LICENSE"
        if source_notice.exists():
            shutil.copy2(source_notice, root / "licenses/Mesa-LICENSE")
        # Redact owned source/work/home paths, retaining the effective flags.
        provenance = public_provenance(
            {key: value for key, value in built.items() if key != "library"}, args.work, ROOT
        )
        provenance["source"] = identity
        provenance["qualification"] = "unqualified; qualify this exact archive before publication"
        provenance["unstripped_sha256"] = provenance.pop("sha256")
        provenance["dependencies"] = subprocess.check_output(
            ["readelf", "-d", str(library)], text=True
        )
        provenance["symbol_versions"] = subprocess.check_output(
            ["readelf", "--version-info", str(library)], text=True
        )
        (root / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        release = {
            "schema": 1,
            "version": manifest["version"],
            "mesa": manifest["mesa"],
            "architecture": "x86_64",
            "label": args.label,
            "driver_sha256": driver.digest(root / "lib/libvulkan_radeon.so"),
            "source_manifest_sha256": copied_manifest_sha256,
            "source": identity,
            "qualification": "unqualified",
            "files": {
                str(f.relative_to(root)): driver.digest(f)
                for f in sorted(root.rglob("*"))
                if f.is_file()
            },
        }
        (root / "release.json").write_text(json.dumps(release, indent=2) + "\n")
        driver.verify_release(root)
        temporary_archive = Path(tmp) / archive.name
        with tarfile.open(temporary_archive, "w:gz") as tar:
            tar.add(root, arcname=name)
        temporary_checksum = Path(tmp) / checksum.name
        temporary_checksum.write_text(driver.digest(temporary_archive) + "  " + archive.name + "\n")
        # Only complete archives receive their final name; never replace an existing asset.
        os.link(temporary_archive, archive)
        try:
            os.link(temporary_checksum, checksum)
        except BaseException:
            archive.unlink()
            raise
    print(archive)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
