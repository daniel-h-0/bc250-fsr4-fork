#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproduce the pinned source in a new directory; optionally build private RADV."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLAGS = "-O2 -march=x86-64 -mtune=generic"
OPTIONS = [
    "--prefix=/usr",
    "--libdir=lib",
    "--buildtype=release",
    "--wrap-mode=nodownload",
    "-Db_ndebug=true",
    "-Dvulkan-drivers=amd",
    "-Dgallium-drivers=",
    "-Dllvm=disabled",
    "-Dplatforms=x11,wayland",
    "-Dglx=disabled",
    "-Degl=disabled",
    "-Dgbm=disabled",
    "-Dgles1=disabled",
    "-Dgles2=disabled",
    "-Dopengl=false",
    "-Dvideo-codecs=",
    "-Dvalgrind=disabled",
    "-Dbuild-tests=false",
    "-Dglvnd=disabled",
    "-Dradv-u_trace=false",
]
ENVIRONMENT_KEYS = (
    "CC",
    "CXX",
    "CFLAGS",
    "CXXFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "CC_LD",
    "CXX_LD",
    "AR",
    "AS",
    "LD",
    "NM",
    "STRIP",
    "RANLIB",
    "CPATH",
    "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH",
    "LIBRARY_PATH",
    "PKG_CONFIG",
    "PKG_CONFIG_PATH",
    "PKG_CONFIG_LIBDIR",
    "PKG_CONFIG_SYSROOT_DIR",
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def verify_inputs(root=ROOT):
    manifest = read_json(root / "v4/manifest.json")
    for relative, expected in manifest["source_inputs"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError("Unsafe source input: " + relative)
        if digest(root / "v4" / path) != expected:
            raise RuntimeError("Bundle source input changed: " + relative)
    if not set(manifest["patch_order"]).issubset(manifest["source_inputs"]):
        raise RuntimeError("Every ordered patch must have a pinned source-input hash.")
    return manifest


def recipe_hashes(root=ROOT):
    return {name: digest(root / name) for name in ("scripts/build.py", "requirements-build.txt")}


def source_directory(work, manifest):
    name = "mesa-" + manifest["mesa"]
    if Path(name).name != name or name in (".", ".."):
        raise RuntimeError("Unsafe Mesa version.")
    return work / name


def snapshot_source(source):
    """Record original source files, including files outside the patched subset."""
    return {
        str(path.relative_to(source)): digest(path)
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }


def verify_source(source, expected):
    for relative, checksum in expected.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or digest(source / path) != checksum:
            raise RuntimeError("Materialized source mismatch: " + relative)


def configuration(build):
    """Read actual Meson settings, rather than assuming the requested flags won."""
    info = build / "meson-info"
    options = read_json(info / "intro-buildoptions.json")
    compilers = read_json(info / "intro-compilers.json")
    dependencies = read_json(info / "intro-dependencies.json")
    return {
        "meson_version": read_json(info / "meson-info.json")["meson_version"]["full"],
        "options": {item["machine"] + ":" + item["name"]: item["value"] for item in options},
        "compilers": {
            machine: {
                language: {
                    key: item[key]
                    for key in ("id", "version", "full_version", "exelist", "linker_id")
                }
                for language, item in languages.items()
            }
            for machine, languages in compilers.items()
        },
        "dependencies": [
            {key: item[key] for key in ("name", "type", "version")} for item in dependencies
        ],
    }


def verify_configuration(build, expected):
    if configuration(build) != expected:
        raise RuntimeError("Meson configuration changed; use a new work directory.")


def tool_identity(command):
    arguments = shlex.split(command)
    executable = shutil.which(arguments[0])
    if not executable:
        raise RuntimeError("Missing build tool: " + arguments[0])
    return {
        "command": arguments,
        "executable_sha256": digest(executable),
        "version": subprocess.check_output(arguments + ["--version"], text=True).splitlines()[0],
    }


def verify_dependency_versions(config, env):
    command = shlex.split(env.get("PKG_CONFIG", "pkg-config"))
    for item in config["dependencies"]:
        if item["type"] == "pkgconfig":
            current = subprocess.check_output(
                command + ["--modversion", item["name"]], text=True, env=env
            ).strip()
            if current != item["version"]:
                raise RuntimeError(
                    "Build dependency changed: " + item["name"] + "; use a new work directory."
                )


def verify_completed_build(work, root=ROOT):
    """Reject stale or edited build evidence before generating a release."""
    manifest = verify_inputs(root)
    result = read_json(work / "build-result.json")
    if result.get("schema") != 2:
        raise RuntimeError("Build provenance predates these checks; build in a new work directory.")
    if result["manifest_sha256"] != digest(root / "v4/manifest.json"):
        raise RuntimeError("Source manifest changed since this build.")
    if result["recipe_hashes"] != recipe_hashes(root):
        raise RuntimeError("Build recipe changed since this build.")
    if result["options"] != OPTIONS:
        raise RuntimeError("Recorded build options do not match the recipe.")
    if digest(work / "build-inputs.json") != result["build_inputs_sha256"]:
        raise RuntimeError("Build input record changed since this build.")
    if digest(work / "configuration.json") != result["configuration_sha256"]:
        raise RuntimeError("Meson configuration record changed since this build.")
    library = work / "build/src/amd/vulkan/libvulkan_radeon.so"
    if digest(library) != result["sha256"]:
        raise RuntimeError("Built library changed since this build.")
    if digest(work / "source-files.json") != result["source_files_sha256"]:
        raise RuntimeError("Materialized source record changed since this build.")
    verify_source(source_directory(work, manifest), read_json(work / "source-files.json"))
    verify_configuration(work / "build", result["configuration"])
    return manifest, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mesa-archive",
        type=Path,
        help="Verified offline Mesa archive; downloads the pinned input otherwise",
    )
    parser.add_argument("--work", type=Path, default=ROOT / ".work/native")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--arch", choices=["64"], default="64")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    manifest = verify_inputs()
    archive = (
        (args.mesa_archive or ROOT / ".work/downloads" / manifest["base_archive"]["name"])
        .expanduser()
        .resolve()
    )
    if not archive.exists() and not args.mesa_archive:
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(".partial." + str(os.getpid()))
        try:
            with (
                urllib.request.urlopen(manifest["base_archive"]["url"], timeout=120) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            if digest(temporary) != manifest["base_archive"]["sha256"]:
                raise RuntimeError("Downloaded Mesa archive failed SHA256 validation.")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(archive) != manifest["base_archive"]["sha256"]:
        raise RuntimeError("The Mesa source archive does not match the pinned input.")
    work = args.work.expanduser().resolve()
    source = source_directory(work, manifest)
    build = work / "build"
    env = os.environ.copy()
    env.setdefault("CFLAGS", DEFAULT_FLAGS)
    env.setdefault("CXXFLAGS", DEFAULT_FLAGS)
    # Preparing source needs patch and Python, but no compiler or Meson install.
    inputs = {
        "schema": 2,
        "manifest_sha256": digest(ROOT / "v4/manifest.json"),
        "recipe_hashes": recipe_hashes(),
        "arch": args.arch,
    }
    if args.resume:
        if read_json(work / "inputs.json") != inputs:
            raise RuntimeError("Resume inputs changed; use a new work directory.")
        verify_source(source, read_json(work / "source-files.json"))
    else:
        work.mkdir(parents=True, exist_ok=False)
        with tarfile.open(archive) as bundle:
            bundle.extractall(work, filter="data")
        for relative in manifest["patch_order"]:
            subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(ROOT / "v4" / relative)],
                cwd=source,
                check=True,
            )
        cache = source / "subprojects/packagecache"
        cache.mkdir(exist_ok=True)
        for relative in manifest["source_inputs"]:
            if relative.startswith("source-dependencies/"):
                shutil.copyfile(ROOT / "v4" / relative, cache / Path(relative).name)
        verify_source(source, manifest["sources"])
        write_json(work / "source-files.json", snapshot_source(source))
        write_json(work / "inputs.json", inputs)
    verify_source(source, manifest["sources"])
    print(
        f"PASS: all {len(manifest['sources'])} changed source files match the production source.",
        flush=True,
    )
    if args.prepare_only:
        return
    toolchain = {
        name: tool_identity(command)
        for name, command in (
            ("c", env.get("CC", "cc")),
            ("cpp", env.get("CXX", "c++")),
            ("meson", "meson"),
            ("ninja", "ninja"),
        )
    }
    build_inputs = {
        "environment": {key: env.get(key) for key in ENVIRONMENT_KEYS},
        "toolchain": toolchain,
        "options": OPTIONS,
    }
    if (build / "build.ninja").exists():
        if read_json(work / "build-inputs.json") != build_inputs:
            raise RuntimeError("Build tools or environment changed; use a new work directory.")
        previous = read_json(work / "configuration.json")
        verify_configuration(build, previous)
        verify_dependency_versions(previous, env)
    else:
        subprocess.run(["meson", "setup", str(build), str(source), *OPTIONS], env=env, check=True)
        write_json(work / "build-inputs.json", build_inputs)
        write_json(work / "configuration.json", configuration(build))
        # Meson may unpack pinned wrap dependencies into the source tree.
        verify_source(source, read_json(work / "source-files.json"))
        write_json(work / "source-files.json", snapshot_source(source))
    subprocess.run(
        ["ninja", "-C", str(build), "-j", str(args.jobs), "src/amd/vulkan/libvulkan_radeon.so"],
        env=env,
        check=True,
    )
    verify_source(source, read_json(work / "source-files.json"))
    actual = configuration(build)
    verify_configuration(build, read_json(work / "configuration.json"))
    library = build / "src/amd/vulkan/libvulkan_radeon.so"
    write_json(
        work / "icd64.json",
        {
            "file_format_version": "1.0.0",
            "ICD": {"library_path": str(library), "api_version": "1.4.0"},
        },
    )
    write_json(
        work / "build-result.json",
        {
            "schema": 2,
            "sha256": digest(library),
            "library": str(library),
            "options": OPTIONS,
            "cflags": env["CFLAGS"],
            "cxxflags": env["CXXFLAGS"],
            "compiler": toolchain["c"]["version"],
            "toolchain": toolchain,
            "build_environment": build_inputs["environment"],
            "configuration": actual,
            "recipe_hashes": recipe_hashes(),
            "manifest_sha256": inputs["manifest_sha256"],
            "source_files_sha256": digest(work / "source-files.json"),
            "build_inputs_sha256": digest(work / "build-inputs.json"),
            "configuration_sha256": digest(work / "configuration.json"),
            "note": "Fresh source build; qualify this exact release binary before deployment.",
        },
    )
    print("Built a private library:", library)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
