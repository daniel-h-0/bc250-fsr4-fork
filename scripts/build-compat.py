#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build portable RADV against pinned Debian 12 libraries without changing the host."""

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
import build
import runtime_bundle

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "v4/build-targets/linux-glibc236.json"
spec = importlib.util.spec_from_file_location("steamos_build", ROOT / "scripts/build-steamos.py")
steamos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(steamos)


def prepare_sysroot(destination, policy, cache, offline):
    destination.mkdir(parents=True, exist_ok=False)

    def obtain(package):
        return runtime_bundle.download(package["url"], package["sha256"], cache, offline)

    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(obtain, policy["packages"]))
    for archive in archives:
        names = subprocess.check_output(["bsdtar", "-tf", str(archive)], text=True).splitlines()
        payload = [n for n in names if n.startswith("data.tar.")]
        if len(payload) != 1:
            raise RuntimeError("Unexpected Debian package contents.")
        # Extract only the pinned data payload. Never execute Debian maintainer scripts.
        with tempfile.TemporaryFile() as data:
            subprocess.run(["bsdtar", "-xOf", str(archive), payload[0]], stdout=data, check=True)
            data.seek(0)
            subprocess.run(["bsdtar", "-xf", "-", "-C", str(destination)], stdin=data, check=True)
    for path in destination.rglob("*"):
        if path.is_symlink() and os.readlink(path).startswith("/"):
            target = destination / Path(os.path.normpath(os.readlink(path))).relative_to("/")
            path.unlink()
            path.symlink_to(os.path.relpath(target, path.parent))
    for path in destination.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(destination.resolve()):
            raise RuntimeError("Target package symlink escapes the sysroot: " + str(path))
    # The XML generator is a build-time program. Give its older libxml2 and
    # libc an isolated loader invocation, without changing host LD_LIBRARY_PATH.
    scanner = destination / "usr/bin/wayland-scanner"
    scanner.rename(scanner.with_suffix(".real"))
    command = [
        str(destination / "lib64/ld-linux-x86-64.so.2"),
        "--library-path",
        os.pathsep.join(
            str(destination / d) for d in ("lib/x86_64-linux-gnu", "usr/lib/x86_64-linux-gnu")
        ),
        str(scanner.with_suffix(".real")),
    ]
    scanner.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
    scanner.chmod(0o755)
    (destination / "target.json").write_text(json.dumps(policy, indent=2) + "\n")


def target_environment(sysroot, inherited):
    env = steamos.target_environment(sysroot, inherited)
    env.update(
        CC=shlex.quote(str(sysroot / "usr/bin/gcc-12")),
        CXX=shlex.quote(str(sysroot / "usr/bin/g++-12")),
        PKG_CONFIG_LIBDIR=os.pathsep.join(
            str(sysroot / d)
            for d in (
                "usr/lib/pkgconfig",
                "usr/lib/x86_64-linux-gnu/pkgconfig",
                "usr/share/pkgconfig",
            )
        ),
    )
    return env


def verify_target_abi(library):
    steamos.verify_target_abi(library)
    dynamic = subprocess.check_output(["readelf", "-d", str(library)], text=True)
    versions = subprocess.check_output(["readelf", "--version-info", str(library)], text=True)
    if "libSPIRV-Tools" in dynamic or "RUNPATH" in dynamic or "RPATH" in dynamic:
        raise RuntimeError(
            "Portable build retained a host-specific library dependency or search path."
        )
    for prefix, maximum in (("GLIBC", (2, 36)), ("GLIBCXX", (3, 4, 30))):
        required = [
            tuple(map(int, version.split(".")))
            for version in re.findall(r"\b" + prefix + r"_([0-9.]+)", versions)
        ]
        if any(version > maximum for version in required):
            raise RuntimeError("Portable build exceeds its " + prefix + " baseline.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".work/linux-glibc236")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/bc250-fsr4-compat")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    work = args.work.expanduser().resolve()
    if work.exists():
        raise RuntimeError("Choose a new --work directory.")
    policy = json.loads(TARGET.read_text())
    sysroot = work / "sysroot"
    cache = args.cache.expanduser().resolve()
    prepare_sysroot(sysroot, policy, cache, args.offline)
    env = target_environment(sysroot, os.environ)
    # Produce current DRM headers and PIC archives in the private sysroot.
    component = policy["libdrm"]
    archive = runtime_bundle.download(component["url"], component["sha256"], cache, args.offline)
    import tarfile

    with tarfile.open(archive) as source:
        source.extractall(work, filter="data")
    directory = work / "libdrm-build"
    subprocess.run(
        [
            "meson",
            "setup",
            str(directory),
            str(work / ("libdrm-" + component["version"])),
            "--prefix=/usr",
            "--libdir=lib",
            "--buildtype=release",
            "--default-library=static",
            "--auto-features=disabled",
            "-Db_staticpic=true",
            "-Damdgpu=enabled",
            "-Dtests=false",
        ],
        env=env,
        check=True,
    )
    subprocess.run(["ninja", "-C", str(directory), "-j", str(args.jobs)], env=env, check=True)
    subprocess.run(
        ["meson", "install", "-C", str(directory), "--no-rebuild", "--destdir", str(sysroot)],
        env=env,
        check=True,
    )
    for package, before, after in (
        ("libdrm", "-ldrm", "-l:libdrm.a"),
        ("libdrm_amdgpu", "-ldrm_amdgpu", "-l:libdrm_amdgpu.a -l:libdrm.a"),
    ):
        path = sysroot / "usr/lib/pkgconfig" / (package + ".pc")
        data = path.read_text()
        if before not in data:
            raise RuntimeError("Unexpected libdrm pkg-config metadata.")
        path.write_text(data.replace(before, after))
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build.py"),
            "--work",
            str(work / "mesa"),
            "--jobs",
            str(args.jobs),
            "--display-info",
            "disabled",
            "--spirv-tools",
            "disabled",
        ],
        env=env,
        check=True,
    )
    result_path = work / "mesa/build-result.json"
    result = json.loads(result_path.read_text())
    verify_target_abi(Path(result["library"]))
    source_name = "libdrm-" + component["version"] + ".tar.xz"
    sources = work / "mesa/target-sources"
    sources.mkdir()
    shutil.copy2(archive, sources / source_name)
    result["target"] = {
        "id": policy["id"],
        "definition_sha256": build.digest(TARGET),
        "builder_sha256": build.digest(Path(__file__)),
        "recipe_hashes": build.target_recipe_hashes(ROOT, portable=True),
        "packages": policy["packages"],
        "source_archives": {source_name: component["sha256"]},
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print("Package with scripts/package.py --work " + shlex.quote(str(work / "mesa")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
