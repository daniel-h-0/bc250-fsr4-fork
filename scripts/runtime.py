#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install, inspect or roll back the additive BC250 FSR4 Steam compatibility tool."""

import argparse
import contextlib
import ctypes
import errno
import fcntl
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
import driver
import safe_archive

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_LIBRARY = Path("/usr/lib/libvulkan_radeon.so")
SYSTEM_METADATA = Path("/usr/share/bc250-fsr4-v4/system.json")
TOOL = "BC250-FSR4"
REQUIRED_FILES = {
    "launch.py",
    "runtime-lock.json",
    "ge/proton",
    "ge/version",
    "ge/toolmanifest.vdf",
    "ge/protonfixes/upscalers.py",
    "ge/upscaler-manifest.json",
    "ge/files/bin/wine",
}
STATIC_FILES = {
    "compatibilitytool.vdf": (
        '"compatibilitytools"\n{\n  "compat_tools"\n  {\n    "BC250-FSR4"\n    {\n'
        '      "install_path" "."\n      "display_name" "BC250 FSR4 (4.1.1 INT8)"\n'
        '      "from_oslist" "windows"\n      "to_oslist" "linux"\n    }\n  }\n}\n'
    ),
    "toolmanifest.vdf": (
        '"manifest"\n{\n  "version" "2"\n  "commandline" "/proton %verb%"\n'
        '  "require_tool_appid" "4183110"\n  "use_sessions" "1"\n'
        '  "compatmanager_layer_name" "proton"\n}\n'
    ),
    "proton": '#!/bin/sh\nexec python3 -B "$(dirname -- "$0")/current/launch.py" "$@"\n',
}


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"bc250-fsr4-runtime-[A-Za-z0-9._-]+", value):
        raise RuntimeError("Invalid runtime version identifier.")
    return value


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != value:
        raise RuntimeError("Unsafe runtime inventory path: " + value)
    return path


def verify_version(root):
    """Check the complete owned tree, including link targets and executable modes."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Runtime version must be a regular owned directory.")
    manifest_path = root / "runtime-release.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("Runtime inventory is missing or is a symlink.")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or safe_id(manifest["id"]) != root.name:
        raise RuntimeError("Runtime inventory has the wrong schema or directory identity.")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise RuntimeError("Runtime inventory has no version.")
    files = manifest["files"]
    if not isinstance(files, dict) or not REQUIRED_FILES.issubset(files):
        raise RuntimeError("Runtime inventory is missing required files.")
    critical = manifest["critical_files"]
    if not isinstance(critical, list) or not REQUIRED_FILES.issubset(critical):
        raise RuntimeError("Runtime inventory does not pin its critical files.")
    if not set(critical).issubset(files):
        raise RuntimeError("Runtime critical file is missing from the inventory.")
    entries = list(root.rglob("*"))
    actual = {str(p.relative_to(root)) for p in entries if not p.is_dir() or p.is_symlink()}
    # GE's FileLock may create this empty, non-code file during prefix setup.
    if "ge/dist.lock" in actual and "ge/dist.lock" not in files:
        info = (root / "ge/dist.lock").lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size or info.st_nlink != 1:
            raise RuntimeError("Unexpected contents or link at GE's runtime lock file.")
        actual.remove("ge/dist.lock")
    if actual != set(files) | {"runtime-release.json"}:
        raise RuntimeError("Unexpected or missing files in the runtime version.")
    inodes = {}
    for name, expected in files.items():
        safe_relative(name)
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Runtime link escapes its owned version: " + name)
        if "target" in expected:
            if set(expected) != {"target"} or not path.is_symlink():
                raise RuntimeError("Runtime symlink does not match the inventory: " + name)
            target = expected["target"]
            if (
                PurePosixPath(target).is_absolute()
                or os.readlink(path) != target
                or not path.exists()
            ):
                raise RuntimeError("Runtime symlink target differs or is missing: " + name)
            if name in critical:
                raise RuntimeError("Critical runtime files must be regular files: " + name)
            continue
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or set(expected) != {"sha256", "mode"}
            or stat.S_IMODE(info.st_mode) != expected["mode"]
            or driver.digest(path) != driver.normalize_checksum(expected["sha256"])
        ):
            raise RuntimeError("Runtime file differs from its inventory: " + name)
        key = (info.st_dev, info.st_ino)
        inodes.setdefault(key, [0, info.st_nlink])[0] += 1
    if any(count != links for count, links in inodes.values()):
        raise RuntimeError("Runtime contains hardlinks to files outside its owned version.")
    if not (root / "ge/proton").stat().st_mode & 0o111:
        raise RuntimeError("Bundled Proton is not executable.")
    with (root / "ge/files/bin/wine").open("rb") as stream:
        header = stream.read(20)
    if header[:5] != b"\x7fELF\x02" or header[18:20] != b"\x3e\x00":
        raise RuntimeError("Bundled Wine is not an x86_64 ELF binary.")
    return manifest


def archive_members(bundle, name):
    """Preflight one owned archive tree, including all symlink and hardlink targets."""
    members, names, expanded = [], set(), 0
    for member in bundle:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != name
            or str(path) in names
        ):
            raise RuntimeError("Unsafe or duplicate runtime archive path: " + member.name)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise RuntimeError("Unsupported runtime archive entry: " + member.name)
        if len(path.parts) == 1 and not member.isdir():
            raise RuntimeError("Runtime archive root must be a directory.")
        if member.issym() or member.islnk():
            link = PurePosixPath(member.linkname)
            target = link if member.islnk() else path.parent / link
            target = PurePosixPath(posixpath.normpath(str(target)))
            if (
                link.is_absolute()
                or not target.parts
                or target.parts[0] != name
                or ".." in target.parts
            ):
                raise RuntimeError("Runtime archive link escapes its root: " + member.name)
        names.add(str(path))
        expanded += member.size
        members.append(member)
        if member.size < 0 or expanded > 4 * 1024**3 or len(members) > 100_000:
            raise RuntimeError("Runtime archive exceeds extraction limits.")
    return members, expanded


def promote_runtime(source, destination):
    """Atomically promote on Linux without replacing an existing directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise RuntimeError("This Linux runtime lacks atomic no-replace directory promotion.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise RuntimeError("Runtime destination appeared during setup; preserving it.")
        raise OSError(error, os.strerror(error), str(destination))


def extract_verified(archive, destination, checksum):
    if driver.digest(archive) != driver.normalize_checksum(checksum):
        raise RuntimeError("Runtime archive SHA256 mismatch; nothing installed.")
    with tarfile.open(archive, "r:gz") as bundle:
        first = bundle.next()
        if first is None or not PurePosixPath(first.name).parts:
            raise RuntimeError("Runtime archive is empty or has no root directory.")
        name = safe_id(PurePosixPath(first.name).parts[0])
        members, expanded = archive_members(bundle, name)
        if shutil.disk_usage(destination).free < expanded:
            raise RuntimeError("Not enough space for the complete BC250 FSR4 runtime.")
        safe_archive.extractall(bundle, destination, members=members)
    root = destination / name
    if set(destination.iterdir()) != {root}:
        raise RuntimeError("Expected exactly one runtime bundle directory.")
    return root, verify_version(root)


def steam_root(explicit=None):
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError("The selected native Steam root does not exist.")
        return root
    roots = {
        path.resolve()
        for path in (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "Steam",
            Path.home() / ".local/share/Steam",
            Path.home() / ".steam/root",
            Path.home() / ".steam/steam",
        )
        if path.is_dir()
    }
    if len(roots) != 1:
        if not roots and (Path.home() / ".var/app/com.valvesoftware.Steam").is_dir():
            raise RuntimeError(
                "Only Flatpak Steam was found. RC4 currently requires native Steam; "
                "its host driver binding is not qualified for the Flatpak sandbox."
            )
        raise RuntimeError("Select one native Steam installation with --steam-root PATH.")
    return roots.pop()


def steam_runtime(root):
    """Find Runtime 4 in Steam's library metadata, including secondary disks."""
    libraries = {root}
    for metadata in (root / "steamapps/libraryfolders.vdf", root / "config/libraryfolders.vdf"):
        if metadata.is_file():
            for raw in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', metadata.read_text()):
                decoded = re.sub(r'\\(["\\])', r"\1", raw)
                path = Path(decoded)
                if path.is_absolute():
                    libraries.add(path)
    for library in sorted(
        libraries,
        key=lambda path: (not (path / "steamapps/appmanifest_4183110.acf").is_file(), str(path)),
    ):
        candidate = library / "steamapps/common/SteamLinuxRuntime_4"
        if (candidate / "run").is_file():
            return candidate.resolve()
    return None


def probe_driver(selected, root):
    """Check the host and, when downloaded, Steam's actual launch container."""
    container = steam_runtime(root)
    # Pressure-vessel prepares a small mutable view of its runtime. Staying on
    # the runtime's filesystem permits hardlinks instead of copying its payload.
    staging_parent = (
        container.parent if container and os.access(container.parent, os.W_OK) else None
    )
    with tempfile.TemporaryDirectory(
        prefix=".bc250-driver-check-", dir=staging_parent
    ) as temporary:
        directory = Path(temporary)
        library = Path(selected["library"])
        report = {"host": driver.probe(library, directory)}
        if container is None:
            report["steam_runtime"] = {
                "state": "pending",
                "app_id": 4183110,
                "detail": "Steam Linux Runtime 4 is not downloaded yet. Steam installs it when this tool is selected.",
            }
            return report
        script = directory / "vulkan_probe.py"
        shutil.copy2(ROOT / "scripts/vulkan_probe.py", script)
        exported = library
        if library.is_relative_to("/usr") or library.is_relative_to("/lib"):
            exported = Path("/run/host") / library.relative_to("/")
        icd = directory / "container-icd.json"
        driver.write_json(icd, driver.icd(exported))
        command = [
            str(container / "run"),
            "--batch",
            "--no-gc-runtimes",
            "--no-copy-runtime",
            "--variable-dir=" + str(directory / "pressure-vessel"),
            "--filesystem=" + str(directory),
        ]
        if exported == library:
            command.append("--filesystem=" + str(library.parent) + ":ro")
        command += [
            "--",
            "python3",
            "-I",
            str(script),
            "--library",
            str(exported),
            "--icd",
            str(icd),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(
                "Steam Linux Runtime 4 driver check failed:\n"
                + (result.stdout + result.stderr)[-8000:]
            )
        report["steam_runtime"] = {
            "state": "passed",
            "path": str(container),
            "probe": json.loads(result.stdout),
        }
        return report


def select_driver(mode, prefix):
    """Accept the qualified binary or a recorded build from the same driver source."""
    source = json.loads((ROOT / "runtime/manifest.json").read_text())["driver"]
    expected = source["sha256"]
    if mode != "private" and SYSTEM_LIBRARY.is_file():
        checksum = driver.digest(SYSTEM_LIBRARY)
        known = checksum in [expected, *source.get("qualified_system_sha256", [])]
        if SYSTEM_METADATA.is_file():
            metadata = json.loads(SYSTEM_METADATA.read_text())
            known = known or (
                metadata.get("driver_sha256") == checksum
                and metadata.get("version") == source["version"]
                and metadata.get("mesa") == source.get("mesa")
            )
        if known:
            return {
                "mode": "system",
                "library": str(SYSTEM_LIBRARY),
                "sha256": checksum,
                "source_manifest_sha256": source["source_manifest_sha256"],
                "environment": {},
            }
    if mode != "system":
        report = driver.status(prefix)
        if report["active"]:
            release = driver.verify_release(prefix / driver.current_target(prefix))
            if release["source_manifest_sha256"] != source["source_manifest_sha256"]:
                raise RuntimeError("The private driver was built from another source version.")
            return {
                "mode": "private",
                "library": report["library"],
                "sha256": report["driver_sha256"],
                "source_manifest_sha256": source["source_manifest_sha256"],
                "environment": {"VK_DRIVER_FILES": str(prefix / "current.json")},
            }
    raise RuntimeError("A verified v4 driver was not found. Install it with ./install-v4.sh first.")


def verify_driver(selected):
    library = Path(selected["library"])
    if not library.is_file() or driver.digest(library) != selected["sha256"]:
        raise RuntimeError("The selected driver changed. Reinstall the runtime to select it again.")
    if selected["mode"] == "private":
        icd = Path(selected["environment"]["VK_DRIVER_FILES"])
        target = Path(json.loads(icd.read_text())["ICD"]["library_path"])
        if not target.is_absolute():
            target = icd.parent / target
        if target.resolve() != library.resolve():
            raise RuntimeError(
                "Private driver selection changed. Reinstall the runtime to rebind it."
            )


def verify_binding(version, selected):
    required = json.loads((version / "runtime-lock.json").read_text())["driver"]
    expected = driver.normalize_checksum(required["source_manifest_sha256"])
    if selected.get("source_manifest_sha256") != expected:
        raise RuntimeError(
            "This runtime requires a different driver source. Select a matching driver before activating it."
        )
    verify_driver(selected)


def current_version(tool):
    link = tool / "current"
    if not link.is_symlink():
        raise RuntimeError("Managed runtime current is missing or is not a symlink.")
    target = os.readlink(link)
    if not target.startswith("versions/") or target != "versions/" + safe_id(target[9:]):
        raise RuntimeError("Managed runtime current points outside its versions directory.")
    return target[9:]


def verify_tool(tool):
    if tool.is_symlink() or not tool.is_dir():
        raise RuntimeError(
            "BC250-FSR4 already exists but is not a managed directory; preserving it."
        )
    for name, data in STATIC_FILES.items():
        path = tool / name
        if path.is_symlink() or not path.is_file() or path.read_text() != data:
            raise RuntimeError("Managed compatibility-tool file differs; preserving it: " + name)
        if name == "proton" and not path.stat().st_mode & 0o111:
            raise RuntimeError("Managed Proton launcher is no longer executable; preserving it.")
    for name in ("versions", "transactions"):
        path = tool / name
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError("Managed runtime directory differs; preserving it: " + name)
    for name in ("driver.json", ".runtime.lock"):
        path = tool / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Managed runtime file differs; preserving it: " + name)
    return current_version(tool)


@contextlib.contextmanager
def exclusive_lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "BC250 FSR4 is running or another runtime operation is active."
            ) from error
        yield
    finally:
        os.close(descriptor)


def records(tool):
    result = []
    for path in sorted((tool / "transactions").glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Runtime transaction is not a regular file.")
        result.append((path, json.loads(path.read_text())))
    return result


def finish_interrupted(tool):
    """Resolve the only two possible outcomes of an interrupted current switch."""
    for path, record in records(tool):
        if record["state"] != "prepared":
            continue
        current = current_version(tool)
        binding = json.loads((tool / "driver.json").read_text())
        if current == record["target"] and binding == record["driver_after"]:
            record["state"] = "complete"
        elif current == record["previous"] and binding in (
            record["driver_before"],
            record["driver_after"],
        ):
            driver.write_json(tool / "driver.json", record["driver_before"])
            record["state"] = "aborted"
        else:
            raise RuntimeError("Interrupted runtime transaction conflicts with current state.")
        driver.write_json(path, record)


def activate(tool, version, selected, operation):
    previous = current_version(tool)
    before = json.loads((tool / "driver.json").read_text())
    record = {
        "schema": 1,
        "operation": operation,
        "state": "prepared",
        "previous": previous,
        "target": version,
        "driver_before": before,
        "driver_after": selected,
    }
    path = tool / "transactions" / (str(time.time_ns()) + ".json")
    driver.write_json(path, record)
    driver.write_json(tool / "driver.json", selected)
    driver.switch(tool, "versions/" + version)
    record["state"] = "complete"
    driver.write_json(path, record)


def archive_source(args):
    archive = args.archive.expanduser().resolve()
    checksum = args.sha256
    if checksum is None:
        fields = Path(str(archive) + ".sha256").read_text().split()
        if not fields:
            raise RuntimeError("The runtime archive checksum sidecar is empty.")
        checksum = fields[0]
    return archive, driver.normalize_checksum(checksum)


def install(args, root, *, lock_held=False):
    selected = select_driver(args.driver, args.driver_prefix.expanduser().resolve())
    # Probe before downloading a large runtime or creating the Steam entry.
    probe_driver(selected, root)
    tools = root / "compatibilitytools.d"
    if tools.is_symlink():
        raise RuntimeError("Steam compatibilitytools.d is a symlink; preserving it.")
    tools.mkdir(exist_ok=True)
    tool = tools / TOOL
    with (
        contextlib.nullcontext()
        if lock_held
        else exclusive_lock(tools / ".bc250-fsr4-install.lock")
    ):
        exists = tool.exists() or tool.is_symlink()
        if exists:
            verify_tool(tool)
        with (
            exclusive_lock(tool / ".runtime.lock")
            if exists and not lock_held
            else contextlib.nullcontext()
        ):
            if exists:
                finish_interrupted(tool)
                if not args.archive:
                    policy = json.loads((ROOT / "runtime/manifest.json").read_text())
                    version = safe_id(policy["release"]["id"])
                    retained = tool / "versions" / version
                    if retained.exists() or retained.is_symlink():
                        verify_version(retained)
                        if driver.digest(retained / "runtime-lock.json") != driver.digest(
                            ROOT / "runtime/manifest.json"
                        ):
                            raise RuntimeError("Existing immutable runtime policy differs.")
                        verify_binding(retained, selected)
                        if (
                            current_version(tool) != version
                            or json.loads((tool / "driver.json").read_text()) != selected
                        ):
                            activate(tool, version, selected, "install")
                        return {"tool": str(tool), "version": version, "driver": selected}
            with tempfile.TemporaryDirectory(
                prefix=".bc250-runtime-stage-", dir=tools
            ) as temporary:
                stage = Path(temporary)
                unpack = stage / "unpack"
                unpack.mkdir()
                if args.archive:
                    archive, checksum = archive_source(args)
                    version_root, manifest = extract_verified(archive, unpack, checksum)
                else:
                    import runtime_bundle

                    policy = json.loads((ROOT / "runtime/manifest.json").read_text())
                    version_root = runtime_bundle.assemble(
                        unpack, args.cache.expanduser().resolve(), policy, offline=args.offline
                    )
                    manifest = verify_version(version_root)
                    if manifest["id"] != policy["release"]["id"]:
                        raise RuntimeError("Assembled runtime has the wrong release identity.")
                verify_binding(version_root, selected)
                version = manifest["id"]
                if exists:
                    destination = tool / "versions" / version
                    if destination.exists() or destination.is_symlink():
                        if verify_version(destination) != manifest:
                            raise RuntimeError("Existing immutable runtime version differs.")
                    else:
                        promote_runtime(version_root, destination)
                    if (
                        current_version(tool) != version
                        or json.loads((tool / "driver.json").read_text()) != selected
                    ):
                        activate(tool, version, selected, "install")
                else:
                    staged_tool = stage / TOOL
                    (staged_tool / "versions").mkdir(parents=True)
                    (staged_tool / "transactions").mkdir()
                    for name, data in STATIC_FILES.items():
                        path = staged_tool / name
                        path.write_text(data)
                        path.chmod(0o755 if name == "proton" else 0o644)
                    (staged_tool / ".runtime.lock").touch(mode=0o600)
                    driver.write_json(staged_tool / "driver.json", selected)
                    promote_runtime(version_root, staged_tool / "versions" / version)
                    (staged_tool / "current").symlink_to("versions/" + version)
                    promote_runtime(staged_tool, tool)
    return {"tool": str(tool), "version": version, "driver": selected}


def selection(root):
    """Return the owned selection without changing it or requiring its driver to be active."""
    tool = root / "compatibilitytools.d" / TOOL
    if not tool.exists() and not tool.is_symlink():
        return None
    version = verify_tool(tool)
    verify_version(tool / "versions" / version)
    return {"id": version, "driver": json.loads((tool / "driver.json").read_text())}


def restore(root, previous, *, expected, retired, lock_held=False):
    """Undo an owned operation, preserving its payload and any independent changes."""
    tool = root / "compatibilitytools.d" / TOOL
    with (
        exclusive_lock(tool / ".runtime.lock")
        if tool.exists() and not lock_held
        else contextlib.nullcontext()
    ):
        if tool.exists():
            finish_interrupted(tool)
        current = selection(root)
        if current == previous:
            return {"installed": current is not None, "selection": current}
        if current != expected:
            raise RuntimeError("Runtime selection changed independently; preserving it.")
        if previous is not None:
            version = previous["id"]
            safe_id(version)
            verify_version(tool / "versions" / version)
            verify_binding(tool / "versions" / version, previous["driver"])
            activate(tool, version, previous["driver"], "restore")
            return {"installed": True, "selection": previous}
        retired = Path(retired)
        if retired.parent != root / ".bc250-fsr4-retired":
            raise RuntimeError("Retired runtime must stay in this Steam root's recovery directory.")
        if retired.parent.is_symlink():
            raise RuntimeError("Runtime recovery directory is a symlink; preserving it.")
        retired.parent.mkdir(exist_ok=True)
        promote_runtime(tool, retired)
        return {"installed": False, "retired": str(retired)}


def status(root):
    tool = root / "compatibilitytools.d" / TOOL
    if not tool.exists() and not tool.is_symlink():
        return {"installed": False, "tool": str(tool)}
    version = verify_tool(tool)
    manifest = verify_version(tool / "versions" / version)
    selected = json.loads((tool / "driver.json").read_text())
    verify_binding(tool / "versions" / version, selected)
    return {
        "installed": True,
        "tool": str(tool),
        "version": manifest["version"],
        "id": version,
        "driver": selected,
        "retained_versions": sorted(path.name for path in (tool / "versions").iterdir()),
        "interrupted_transactions": [
            str(path) for path, record in records(tool) if record["state"] == "prepared"
        ],
    }


def rollback(args, root):
    tool = root / "compatibilitytools.d" / TOOL
    verify_tool(tool)
    with exclusive_lock(tool / ".runtime.lock"):
        finish_interrupted(tool)
        current = current_version(tool)
        version = args.version
        if version is None:
            version = next(
                (
                    record["previous"]
                    for _, record in reversed(records(tool))
                    if record["state"] == "complete"
                    and record["target"] == current
                    and record["previous"] != current
                ),
                None,
            )
        if version is None:
            raise RuntimeError(
                "No previous runtime version. Select ordinary Proton in Steam to stop using this tool."
            )
        safe_id(version)
        verify_version(tool / "versions" / version)
        selected = json.loads((tool / "driver.json").read_text())
        verify_binding(tool / "versions" / version, selected)
        if version != current:
            activate(tool, version, selected, "rollback")
    return {"tool": str(tool), "id": version, "driver": selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "status", "rollback"):
        command = sub.add_parser(name)
        command.add_argument(
            "--steam-root", type=Path, help="Native Steam root (no game/account scan)"
        )
        if name == "install":
            command.add_argument("--archive", type=Path, help="Complete local runtime bundle")
            command.add_argument(
                "--sha256", help="Expected archive SHA256; default: adjacent .sha256"
            )
            command.add_argument(
                "--cache", type=Path, default=Path.home() / ".cache/bc250-fsr4-runtime"
            )
            command.add_argument(
                "--offline", action="store_true", help="Use only cached pinned components"
            )
            command.add_argument("--driver", choices=("auto", "system", "private"), default="auto")
            command.add_argument(
                "--driver-prefix", type=Path, default=Path.home() / ".local/share/bc250-fsr4"
            )
        elif name == "rollback":
            command.add_argument("--version", help="Retained runtime ID; default: previous version")
    args = parser.parse_args()
    if args.command == "install" and args.sha256 and not args.archive:
        parser.error("--sha256 applies only to --archive")
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    if os.geteuid() == 0:
        raise RuntimeError("Run the runtime installer as your desktop user, without sudo.")
    root = steam_root(args.steam_root)
    result = status(root) if args.command == "status" else globals()[args.command](args, root)
    print(json.dumps(result, indent=2))
    if args.command == "install":
        print(
            "Restart Steam. In the game's Compatibility settings, select BC250 FSR4 (4.1.1 INT8)."
        )


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit("ERROR: " + str(error))
