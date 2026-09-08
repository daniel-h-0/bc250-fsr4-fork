#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build against a pinned SteamOS userspace without modifying the host OS."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
import build
import runtime_bundle

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "v4/build-targets/steamos-3.8.json"


def target_environment(sysroot, inherited):
    # The target owns compiler/include/link selection. Do not mix an inherited
    # host CFLAGS, CPATH or pkg-config search path into the pinned sysroot.
    env = {
        key: value
        for key, value in inherited.items()
        if key not in (*build.ENVIRONMENT_KEYS, "GCC_EXEC_PREFIX", "COMPILER_PATH")
        and not key.startswith(("PKG_CONFIG_", "CMAKE_"))
    }
    flags = shlex.join([*shlex.split(build.DEFAULT_FLAGS), "--sysroot=" + str(sysroot)])
    env.update(
        CC=shlex.quote(str(sysroot / "usr/bin/gcc")),
        CXX=shlex.quote(str(sysroot / "usr/bin/g++")),
        CFLAGS=flags,
        CXXFLAGS=flags,
        LDFLAGS=shlex.quote("--sysroot=" + str(sysroot)),
        PKG_CONFIG_SYSROOT_DIR=str(sysroot),
        PKG_CONFIG_LIBDIR=os.pathsep.join(
            str(sysroot / directory) for directory in ("usr/lib/pkgconfig", "usr/share/pkgconfig")
        ),
        PKG_CONFIG_PATH="",
    )
    return env


def prepare_sysroot(destination, policy, cache, offline):
    destination.mkdir(parents=True, exist_ok=False)

    def obtain(package):
        return runtime_bundle.download(package["url"], package["sha256"], cache, offline)

    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(obtain, policy["packages"]))
    for archive in archives:
        # Only /usr is needed. Package install scripts and OS configuration
        # are never run or installed. The exact archives are SHA256-pinned.
        subprocess.run(
            ["bsdtar", "-xf", str(archive), "-C", str(destination), "--include", "usr/*"],
            check=True,
        )
    for name, target in (("lib", "usr/lib"), ("lib64", "usr/lib"), ("bin", "usr/bin")):
        (destination / name).symlink_to(target)
    # Arch packages can contain /usr-absolute compiler aliases. Relocate those
    # within the private sysroot; never resolve them into the build host's /usr.
    for path in destination.rglob("*"):
        if path.is_symlink() and os.readlink(path).startswith("/"):
            target = Path(os.path.normpath(os.readlink(path)))
            if not target.is_relative_to("/usr"):
                raise RuntimeError("Unsupported absolute target symlink: " + str(path))
            target = destination / target.relative_to("/")
            path.unlink()
            path.symlink_to(os.path.relpath(target, path.parent))
    # Refuse an accidental package escape before using any compiler/header.
    for path in destination.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(destination.resolve()):
            raise RuntimeError("Target package symlink escapes the sysroot: " + str(path))
    (destination / "target.json").write_text(json.dumps(policy, indent=2) + "\n")


def static_drm(work, sysroot, policy, cache, offline, jobs, env):
    component = policy["libdrm"]
    archive = runtime_bundle.download(component["url"], component["sha256"], cache, offline)
    name = "libdrm-" + component["version"]
    with tarfile.open(archive) as source:
        if any(Path(member.name).parts[0] != name for member in source.getmembers()):
            raise RuntimeError("Unexpected libdrm source archive root.")
        source.extractall(work, filter="data")
    directory = work / "libdrm-build"
    subprocess.run(
        [
            "meson",
            "setup",
            str(directory),
            str(work / name),
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
    subprocess.run(
        ["ninja", "-C", str(directory), "-j", str(jobs), "libdrm.a", "amdgpu/libdrm_amdgpu.a"],
        env=env,
        check=True,
    )
    for relative in ("libdrm.a", "amdgpu/libdrm_amdgpu.a"):
        shutil.copy2(directory / relative, sysroot / "usr/lib" / Path(relative).name)
    # Keep all ordinary system libraries dynamic. Select only these two private
    # archives explicitly, even when a shared libdrm exists in the sysroot.
    for package, before, after in (
        ("libdrm", "-ldrm", "-l:libdrm.a"),
        ("libdrm_amdgpu", "-ldrm_amdgpu", "-l:libdrm_amdgpu.a -l:libdrm.a"),
    ):
        path = sysroot / "usr/lib/pkgconfig" / (package + ".pc")
        text = path.read_text()
        if before not in text:
            raise RuntimeError("Unexpected libdrm pkg-config metadata.")
        path.write_text(text.replace(before, after))
    return archive


def verify_target_abi(library):
    dynamic = subprocess.check_output(["readelf", "-d", str(library)], text=True)
    versions = subprocess.check_output(["readelf", "--version-info", str(library)], text=True)
    symbols = subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(library)], text=True)
    if any(name in dynamic for name in ("libdrm.so", "libdrm_amdgpu.so", "libdisplay-info.so")):
        raise RuntimeError(
            "SteamOS target retained an incompatible dynamic display/DRM dependency."
        )
    if "GLIBC_ABI_GNU2_TLS" in versions or "wl_fixes_interface" in symbols:
        raise RuntimeError("SteamOS target retained a newer libc/Wayland ABI requirement.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".work/steamos-3.8")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/bc250-fsr4-steamos")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if not shutil.which("bsdtar"):
        raise RuntimeError("Install libarchive's bsdtar before preparing the target.")
    work = args.work.expanduser().resolve()
    if work.exists():
        raise RuntimeError("Build directory already exists; choose a new --work directory.")
    policy = json.loads(TARGET.read_text())
    if policy["schema"] != 1 or policy["id"] != "steamos-3.8-x86_64":
        raise RuntimeError("Unexpected SteamOS build target.")
    sysroot = work / "sysroot"
    cache = args.cache.expanduser().resolve()
    prepare_sysroot(sysroot, policy, cache, args.offline)
    env = target_environment(sysroot, os.environ)
    drm_source = static_drm(work, sysroot, policy, cache, args.offline, args.jobs, env)
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
        ],
        env=env,
        check=True,
    )
    # Preserve the exact toolchain-package provenance in the ordinary package
    # record, in addition to the builder's compiler hashes and Meson evidence.
    result_path = work / "mesa/build-result.json"
    result = json.loads(result_path.read_text())
    verify_target_abi(Path(result["library"]))
    source_name = "libdrm-" + policy["libdrm"]["version"] + ".tar.xz"
    sources = work / "mesa/target-sources"
    sources.mkdir()
    shutil.copy2(drm_source, sources / source_name)
    result["target"] = {
        "id": policy["id"],
        "definition_sha256": build.digest(TARGET),
        "builder_sha256": build.digest(Path(__file__)),
        "packages": policy["packages"],
        "source_archives": {source_name: policy["libdrm"]["sha256"]},
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print("Package with scripts/package.py --work " + shlex.quote(str(work / "mesa")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
