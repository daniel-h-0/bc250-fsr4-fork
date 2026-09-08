#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproduce the pinned source in a new directory; optionally build private RADV."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Newer host toolchains can default to GNU2 TLS and emit a
# GLIBC_ABI_GNU2_TLS requirement absent from SteamOS 3.7/3.8.
# Keep the original x86 TLS ABI explicit for both C and C++ objects.
DEFAULT_FLAGS = "-O2 -march=x86-64 -mtune=generic -mtls-dialect=gnu"
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
    "PYTHONDONTWRITEBYTECODE",
)


def build_options(display_info="auto", spirv_tools="auto"):
    if display_info not in ("auto", "enabled", "disabled"):
        raise RuntimeError("Invalid display-info build policy.")
    if spirv_tools not in ("auto", "enabled", "disabled"):
        raise RuntimeError("Invalid SPIRV-Tools build policy.")
    return (
        OPTIONS
        + ([] if display_info == "auto" else ["-Ddisplay-info=" + display_info])
        + ([] if spirv_tools == "auto" else ["-Dspirv-tools=" + spirv_tools])
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


def target_recipe_hashes(root, portable):
    names = [
        "scripts/build-steamos.py",
        "scripts/runtime_bundle.py",
        "scripts/driver.py",
        "scripts/safe_archive.py",
    ]
    if portable:
        names.append("scripts/build-compat.py")
    return {name: digest(root / name) for name in names}


def source_directory(work, manifest):
    name = "mesa-" + manifest["mesa"]
    if Path(name).name != name or name in (".", ".."):
        raise RuntimeError("Unsafe Mesa version.")
    return work / name


def snapshot_source(source):
    """Record every source file and internal link, including executable modes."""
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("Materialized source must be an owned directory.")
    result = {}
    for path in sorted(source.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        name = str(path.relative_to(source))
        result[name] = source_entry(source, name)
    return result


def source_entry(source, relative):
    name = Path(relative)
    path = source / name
    if (
        name.is_absolute()
        or ".." in name.parts
        or not path.resolve().is_relative_to(source.resolve())
    ):
        raise RuntimeError("Unsafe materialized source path: " + relative)
    if path.is_symlink():
        return {"target": os.readlink(path)}
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise RuntimeError("Nonregular materialized source file: " + relative)
    return {"sha256": digest(path), "mode": stat.S_IMODE(mode)}


def verify_source(source, expected, *, complete=False):
    if complete:
        actual = snapshot_source(source)
        if set(actual) != set(expected):
            raise RuntimeError("Materialized source inventory changed; use a new work directory.")
    else:
        actual = {relative: source_entry(source, relative) for relative in expected}
    for relative, recorded in expected.items():
        # The release manifest pins only the patched files by their hashes.
        # Full build snapshots also own modes and every internal symlink.
        matches = (
            actual[relative].get("sha256") == recorded
            if isinstance(recorded, str)
            else actual[relative] == recorded
        )
        if not matches:
            raise RuntimeError("Materialized source mismatch: " + relative)


def command_files(arguments):
    """Fingerprint explicit executables and script/configuration file arguments.

    A wrapper's version and bytes do not identify the compiler it invokes.
    Follow executable arguments through PATH, and retain regular file arguments
    such as the non-executable script in ``python compiler.py``. Commands hidden
    inside wrapper code and the wider host dependency tree remain out of scope.
    """
    files = []
    for index, argument in enumerate(arguments):
        if argument.startswith("-"):
            continue
        executable = shutil.which(argument)
        paths = {Path(executable).resolve()} if executable else set()
        # An interpreter consumes an explicit relative script from its working
        # directory even if PATH contains an executable with the same name.
        if index > 0 and Path(argument).is_file():
            paths.add(Path(argument).resolve())
        for path in sorted(paths):
            files.append(
                {"argument_index": index, "path": str(path.resolve()), "sha256": digest(path)}
            )
    return files


def configuration(build, *, include_tool_files=True):
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
                    **{
                        key: item[key]
                        for key in ("id", "version", "full_version", "exelist", "linker_id")
                    },
                    **(
                        {
                            "executable_files": command_files(item["exelist"]),
                            "linker_executable_files": command_files(
                                item.get("linker_exelist", [])
                            ),
                        }
                        if include_tool_files
                        else {}
                    ),
                }
                for language, item in languages.items()
            }
            for machine, languages in compilers.items()
        },
        "dependencies": [
            {key: item[key] for key in ("name", "type", "version")} for item in dependencies
        ],
    }


def verify_configuration(build, expected, *, check_tools=True):
    if not check_tools:
        # Packaging may run outside the build container. Verify its recorded
        # Meson settings without resolving compiler names against a new host.
        expected = {
            **expected,
            "compilers": {
                machine: {
                    language: {
                        key: value
                        for key, value in item.items()
                        if key not in ("executable_files", "linker_executable_files")
                    }
                    for language, item in languages.items()
                }
                for machine, languages in expected["compilers"].items()
            },
        }
    if configuration(build, include_tool_files=check_tools) != expected:
        raise RuntimeError("Meson configuration changed; use a new work directory.")


def tool_identity(command):
    arguments = shlex.split(command)
    if not arguments:
        raise RuntimeError("Build tool command must not be empty.")
    executable = shutil.which(arguments[0])
    if not executable:
        raise RuntimeError("Missing build tool: " + arguments[0])
    return {
        "command": arguments,
        "executable_sha256": digest(executable),
        "command_files": command_files(arguments),
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
    if result["options"] != build_options(
        result.get("display_info", "auto"), result.get("spirv_tools", "auto")
    ):
        raise RuntimeError("Recorded build options do not match the recipe.")
    if target := result.get("target"):
        portable = target.get("id") == "linux-glibc236-x86_64"
        definition = root / (
            "v4/build-targets/linux-glibc236.json"
            if portable
            else "v4/build-targets/steamos-3.8.json"
        )
        if (
            target.get("id") not in ("steamos-3.8-x86_64", "linux-glibc236-x86_64")
            or target.get("definition_sha256") != digest(definition)
            or target.get("builder_sha256")
            != digest(
                root / ("scripts/build-compat.py" if portable else "scripts/build-steamos.py")
            )
            or target.get("packages") != read_json(definition)["packages"]
            or target.get("recipe_hashes") != target_recipe_hashes(root, portable)
            or result.get("display_info") != "disabled"
        ):
            raise RuntimeError("Target build recipe changed since this build.")
        drm = read_json(definition)["libdrm"]
        sources = {"libdrm-" + drm["version"] + ".tar.xz": drm["sha256"]}
        if target.get("source_archives") != sources:
            raise RuntimeError("SteamOS static dependency provenance changed.")
        for name, expected in sources.items():
            if digest(work / "target-sources" / name) != expected:
                raise RuntimeError("SteamOS static dependency source changed.")
    if digest(work / "build-inputs.json") != result["build_inputs_sha256"]:
        raise RuntimeError("Build input record changed since this build.")
    if digest(work / "configuration.json") != result["configuration_sha256"]:
        raise RuntimeError("Meson configuration record changed since this build.")
    library = work / "build/src/amd/vulkan/libvulkan_radeon.so"
    if digest(library) != result["sha256"]:
        raise RuntimeError("Built library changed since this build.")
    if digest(work / "source-files.json") != result["source_files_sha256"]:
        raise RuntimeError("Materialized source record changed since this build.")
    verify_source(
        source_directory(work, manifest), read_json(work / "source-files.json"), complete=True
    )
    verify_configuration(work / "build", result["configuration"], check_tools=False)
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
    parser.add_argument("--display-info", choices=["auto", "enabled", "disabled"], default="auto")
    parser.add_argument("--spirv-tools", choices=["auto", "enabled", "disabled"], default="auto")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    manifest = verify_inputs()
    options = build_options(args.display_info, args.spirv_tools)
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
    # Generators must not add unrecorded Python bytecode to the source tree.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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
        verify_source(source, read_json(work / "source-files.json"), complete=True)
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
        "options": options,
    }
    if (build / "build.ninja").exists():
        if read_json(work / "build-inputs.json") != build_inputs:
            raise RuntimeError("Build tools or environment changed; use a new work directory.")
        previous = read_json(work / "configuration.json")
        verify_configuration(build, previous)
        verify_dependency_versions(previous, env)
    else:
        subprocess.run(["meson", "setup", str(build), str(source), *options], env=env, check=True)
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
    verify_source(source, read_json(work / "source-files.json"), complete=True)
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
            "options": options,
            "display_info": args.display_info,
            "spirv_tools": args.spirv_tools,
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
