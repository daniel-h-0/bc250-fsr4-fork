#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install, inspect, run or roll back a verified private BC250 FSR4 v4 release."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
import safe_archive


def cache_helper():
    path = Path(__file__).with_name("shared-cache.py")
    spec = importlib.util.spec_from_file_location("bc250_shared_cache", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cache_settings(prefix):
    path = prefix / "cache-settings.json"
    if not path.exists():
        return {"schema": 1, "enabled": False, "directory": None}
    if path.is_symlink():
        raise RuntimeError("Cache settings must be a regular file")
    value = json.loads(path.read_text())
    if value.get("schema") != 1 or type(value.get("enabled")) is not bool:
        raise RuntimeError("Invalid shared-cache settings")
    directory = value.get("directory")
    if directory is not None and (
        not isinstance(directory, str) or not Path(directory).is_absolute()
    ):
        raise RuntimeError("Shared-cache storage must be an absolute path")
    return value


def launcher_changes(args, prefix):
    """Install tools outside the selected driver so legacy driver archives work too."""
    helper = cache_helper()
    previous = cache_settings(prefix)
    requested = getattr(args, "shared_cache", False)
    enabled = (
        requested
        if requested is not None
        else (previous["enabled"] if (prefix / "cache-settings.json").exists() else True)
    )
    directory = getattr(args, "cache_dir", None)
    directory = str(directory.expanduser().absolute()) if directory else previous.get("directory")
    launcher = prefix / "bc250-fsr4-run"
    helper.steam_command(getattr(args, "launch_options", "%command%"), [launcher, "run", "--"])
    known = {}
    for record in sorted((prefix / "transactions").glob("*.json")):
        journal = json.loads(record.read_text())
        if journal.get("state") == "active":
            known.update({item["name"]: item for item in journal.get("launcher_files", [])})
    changes = []
    for name in ("bc250-fsr4-run", "cache-settings.json"):
        path = prefix / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("Launcher path contains unrelated data: " + str(path))
        before = path.read_bytes() if path.exists() else None
        if before is not None and (name not in known or before.hex() != known[name]["after_hex"]):
            raise RuntimeError("Launcher/settings changed independently; preserving " + str(path))
        changes.append(
            {
                "name": name,
                "before_hex": before.hex() if before is not None else None,
                "before_mode": path.stat().st_mode & 0o777 if before is not None else None,
            }
        )
    tools = helper.stage_tools(
        prefix,
        ["driver.py", "safe_archive.py", "vulkan_probe.py", "shared-cache.py", "shared-cache.sh"],
    )
    script = (
        "#!/bin/sh\n"
        'if [ "$#" -eq 0 ]; then set -- status --human; fi\n'
        'if [ "$#" -eq 1 ] && [ "$1" = status ]; then set -- status --human; fi\n'
        "exec python3 "
        + shlex.quote(str(tools / "driver.py"))
        + " --prefix "
        + shlex.quote(str(prefix))
        + ' "$@"\n'
    )
    settings = {"schema": 1, "enabled": enabled, "directory": directory}
    payloads = {
        "bc250-fsr4-run": (script.encode(), 0o755),
        "cache-settings.json": ((json.dumps(settings, indent=2) + "\n").encode(), 0o600),
    }
    for item in changes:
        data, mode = payloads[item["name"]]
        item.update(after_hex=data.hex(), after_mode=mode)
    return changes


def launcher_file_state(prefix, item):
    if item["name"] not in {"bc250-fsr4-run", "cache-settings.json"}:
        raise RuntimeError("Unknown managed launcher path")
    path = prefix / item["name"]
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Managed launcher path changed independently")
    return path.read_bytes().hex() if path.exists() else None


def restore_launcher_files(prefix, items):
    for item in items:
        if launcher_file_state(prefix, item) not in (item["before_hex"], item["after_hex"]):
            raise RuntimeError(
                "Launcher/settings changed independently; preserving " + item["name"]
            )
    for item in items:
        path = prefix / item["name"]
        if item["before_hex"] is None:
            if path.exists():
                path.unlink()
        else:
            atomic(path, bytes.fromhex(item["before_hex"]))
            path.chmod(item["before_mode"])


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path, data):
    path = Path(path)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    atomic(path, (json.dumps(value, indent=2) + "\n").encode())


def icd(library):
    return {
        "file_format_version": "1.0.0",
        "ICD": {"library_path": str(library), "api_version": "1.4.0"},
    }


def check_hardware():
    if platform.machine() != "x86_64":
        raise RuntimeError("The v4 release supports x86_64 only.")
    for device in Path("/sys/bus/pci/devices").glob("*"):
        try:
            if (device / "vendor").read_text().strip() == "0x1002" and (
                device / "device"
            ).read_text().strip() == "0x13fe":
                return
        except FileNotFoundError:
            pass
    raise RuntimeError("BC250 PCI device 1002:13fe was not found. No driver was activated.")


def probe(library, directory):
    check_hardware()
    header = library.read_bytes()[:20]
    if header[:5] != b"\x7fELF\x02" or header[18:20] != b"\x3e\x00":
        raise RuntimeError("Driver is not an x86_64 ELF shared library.")
    path = directory / "probe.json"
    write_json(path, icd(library))
    env = os.environ.copy()
    env["LD_BIND_NOW"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("vulkan_probe.py")),
            "--library",
            str(library),
            "--icd",
            str(path),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (directory / "probe.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(
            "BC250 driver ABI/dependency or Vulkan initialization check failed. "
            "Current release is unchanged.\n" + result.stdout + result.stderr
        )
    report = json.loads(result.stdout)
    if report.get("success") is not True:
        raise RuntimeError("BC250 driver probe did not report success.")
    return report


def verify_release(root):
    entries = list(root.rglob("*"))
    if root.is_symlink() or any(p.is_symlink() for p in entries):
        raise RuntimeError("Release payloads must not contain symlinks.")
    if not root.is_dir() or any(not (p.is_file() or p.is_dir()) for p in entries):
        raise RuntimeError("Release payloads may contain only regular files and directories.")
    manifest = json.loads((root / "release.json").read_text())
    if manifest["schema"] != 1 or manifest["architecture"] != "x86_64":
        raise RuntimeError("Unsupported release manifest.")
    actual = {str(p.relative_to(root)) for p in entries if p.is_file()}
    if actual != set(manifest["files"]) | {"release.json"}:
        raise RuntimeError("Unexpected or missing files in release.")
    for rel, expected in manifest["files"].items():
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or digest(root / p) != expected:
            raise RuntimeError("Release file checksum mismatch: " + rel)
    if manifest["files"].get("lib/libvulkan_radeon.so") != manifest["driver_sha256"]:
        raise RuntimeError("Driver digest does not match the release manifest.")
    return manifest


def normalize_checksum(checksum):
    checksum = checksum.strip().lower()
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise RuntimeError("Expected a SHA256 digest containing exactly 64 hexadecimal characters.")
    return checksum


def extract_verified(archive, destination, checksum):
    if digest(archive) != normalize_checksum(checksum):
        raise RuntimeError("Archive SHA256 mismatch; nothing installed.")
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if sum(m.size for m in members) > 512 * 1024 * 1024:
            raise RuntimeError("Oversized release archive.")
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(
                    "Release archives may contain only regular files and directories."
                )
            if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                raise RuntimeError("Unsafe archive member.")
        safe_archive.extractall(bundle, destination)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError("Expected exactly one release directory.")
    return roots[0], verify_release(roots[0])


def switch(prefix, target):
    temporary = prefix / ".current-next"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, prefix / "current")


def managed_target(prefix, target):
    if not isinstance(target, str) or not re.fullmatch(
        r"releases/[A-Za-z0-9][A-Za-z0-9.-]*", target
    ):
        raise RuntimeError("Private driver selection is outside its managed releases directory.")
    path = prefix / target
    if (prefix / "releases").is_symlink() or path.is_symlink():
        raise RuntimeError("Private driver release directory is a symlink; preserving it.")
    return target


def current_target(prefix):
    current = prefix / "current"
    if current.is_symlink():
        return managed_target(prefix, os.readlink(current))
    if current.exists():
        raise RuntimeError("current exists but is not a managed symlink.")
    return None


def pending(prefix):
    for name in ("releases", "icds", "transactions"):
        path = prefix / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError("Managed driver directory was replaced; preserving it: " + str(path))
    paths = sorted((prefix / "transactions").glob("*.json"))
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise RuntimeError("Driver transaction record is not a regular file; preserving it.")
    records = [(p, json.loads(p.read_text())) for p in paths]
    return [
        (p, journal)
        for p, journal in records
        if journal.get("state") in ("prepared", "rolling-back")
    ]


def verify_previous(prefix, journal):
    if journal["previous"] is not None:
        # A retained release can be removed or edited between install and rollback.
        # Validate it before restoring migrated launch paths or switching current.
        verify_release(prefix / managed_target(prefix, journal["previous"]))


def archive_checksum(archive, checksum=None):
    if checksum is None:
        fields = Path(str(archive) + ".sha256").read_text().split()
        if not fields:
            raise RuntimeError("The adjacent SHA256 file is empty; nothing installed.")
        checksum = fields[0]
    return normalize_checksum(checksum)


def install(args, prefix):
    if pending(prefix):
        raise RuntimeError(
            "An interrupted transaction needs recovery: run driver.py recover first."
        )
    archive = args.archive.expanduser().resolve()
    checksum = archive_checksum(archive, args.sha256)
    previous = current_target(prefix)
    stable_icd = prefix / "current.json"
    if stable_icd.exists() and json.loads(stable_icd.read_text()) != icd(
        prefix / "current/lib/libvulkan_radeon.so"
    ):
        raise RuntimeError("Stable ICD was edited independently; preserving it.")
    # Validate every old launch manifest before writing anything to it.
    migrations = []
    for raw in args.upgrade_v3_icd:
        path = raw.expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Legacy ICD must be an existing regular file: " + str(path))
        original = path.read_bytes()
        old = json.loads(original)
        library = Path(old["ICD"]["library_path"])
        if not library.is_absolute():
            library = path.parent / library
        if (
            path.name not in ("radv-bc250-fsr4-v3.json", "radv-bc250-fsr4.json")
            or library.name != "libvulkan_radeon.so"
            or not library.is_file()
        ):
            raise RuntimeError("Not a recognized v3 installation: " + str(path))
        migrations.append({"path": str(path), "before_hex": original.hex()})
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=prefix) as temporary:
        stage = Path(temporary)
        unpack = stage / "unpack"
        unpack.mkdir()
        root, manifest = extract_verified(archive, unpack, checksum)
        result = probe(root / "lib/libvulkan_radeon.so", stage)
        launch_files = launcher_changes(args, prefix)
        release_id = (
            manifest["version"]
            + "-"
            + manifest["driver_sha256"][:12]
            + "-"
            + digest(root / "release.json")[:8]
        )
        if any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for c in release_id
        ):
            raise RuntimeError("Unsafe version identifier.")
        target = "releases/" + release_id
        destination = prefix / target
        if destination.exists():
            if verify_release(destination) != manifest:
                raise RuntimeError("Existing immutable release differs.")
        else:
            (prefix / "releases").mkdir(exist_ok=True)
            shutil.move(root, destination)
        # ICD is outside the immutable payload because it is install-location dependent.
        driver_icd = prefix / "icds" / f"{release_id}.json"
        driver_icd.parent.mkdir(exist_ok=True)
        write_json(driver_icd, icd(destination / "lib/libvulkan_radeon.so"))
        write_json(prefix / "current.json", icd(prefix / "current/lib/libvulkan_radeon.so"))
        for item in migrations:
            after = (
                json.dumps(icd(prefix / "current/lib/libvulkan_radeon.so"), indent=2) + "\n"
            ).encode()
            item["after_sha256"] = hashlib.sha256(after).hexdigest()
            item["after_hex"] = after.hex()
        journal = {
            "schema": 1,
            "previous": previous,
            "target": target,
            "icd": str(driver_icd),
            "migrations": migrations,
            "launcher_files": launch_files,
            "probe": result,
            "archive_sha256": checksum,
            "state": "prepared",
        }
        transactions = prefix / "transactions"
        transactions.mkdir(exist_ok=True)
        record = getattr(args, "record", None) or transactions / (
            time.strftime("%Y%m%dT%H%M%S") + "-" + str(time.time_ns()) + ".json"
        )
        record = Path(record)
        if record.parent != transactions or record.exists() or record.is_symlink():
            raise RuntimeError("Driver transaction path is not an unused managed record.")
        write_json(record, journal)
        changed = []
        try:
            for item in migrations:
                path = Path(item["path"])
                if path.read_bytes().hex() != item["before_hex"]:
                    raise RuntimeError("Legacy ICD changed while installing: " + str(path))
                atomic(path, bytes.fromhex(item["after_hex"]))
                changed.append(item)
            for item in launch_files:
                if launcher_file_state(prefix, item) != item["before_hex"]:
                    raise RuntimeError("Launcher/settings changed while installing")
                path = prefix / item["name"]
                atomic(path, bytes.fromhex(item["after_hex"]))
                path.chmod(item["after_mode"])
            switch(prefix, target)
            journal["state"] = "active"
            write_json(record, journal)
        except BaseException:
            for item in reversed(changed):
                atomic(item["path"], bytes.fromhex(item["before_hex"]))
            restore_launcher_files(prefix, launch_files)
            if previous is not None:
                switch(prefix, previous)
            elif current_target(prefix) is not None:
                (prefix / "current").unlink()
            journal["state"] = "aborted"
            write_json(record, journal)
            raise
    if not getattr(args, "quiet", False):
        print("Installed and validated " + release_id)
        print(
            "Steam launch options:\n"
            + cache_helper().steam_command(
                getattr(args, "launch_options", "%command%"),
                [prefix / "bc250-fsr4-run", "run", "--"],
            )
        )
        print("Shared cache: " + ("enabled" if cache_settings(prefix)["enabled"] else "disabled"))
        print("Status: " + shlex.quote(str(prefix / "bc250-fsr4-run")) + " status")
        print("The launcher is installed permanently. First cache use may compile shaders.")
        if migrations:
            print("The explicitly migrated v3 launch paths now select v4. Restart the game.")
    return record


def rollback(prefix):
    if pending(prefix):
        raise RuntimeError(
            "An interrupted transaction needs recovery: run driver.py recover first."
        )
    records = sorted((prefix / "transactions").glob("*.json"), reverse=True)
    active = [(p, json.loads(p.read_text())) for p in records]
    active = [(p, j) for p, j in active if j["state"] == "active"]
    if not active:
        raise RuntimeError("No active install transaction to roll back.")
    record, journal = active[0]
    if current_target(prefix) != journal["target"]:
        raise RuntimeError("Current release changed outside this transaction; refusing rollback.")
    verify_previous(prefix, journal)
    for item in journal["migrations"]:
        if digest(item["path"]) != item["after_sha256"]:
            raise RuntimeError(
                "Legacy ICD was modified after installation; preserving it: " + item["path"]
            )
    for item in journal.get("launcher_files", []):
        if launcher_file_state(prefix, item) != item["after_hex"]:
            raise RuntimeError(
                "Launcher/settings changed independently; preserving " + item["name"]
            )
    journal["state"] = "rolling-back"
    write_json(record, journal)
    for item in journal["migrations"]:
        atomic(item["path"], bytes.fromhex(item["before_hex"]))
    restore_launcher_files(prefix, journal.get("launcher_files", []))
    if journal["previous"] is not None:
        switch(prefix, journal["previous"])
    else:
        (prefix / "current").unlink()
    journal["state"] = "rolled-back"
    write_json(record, journal)
    print(
        "Previous selection and legacy ICD files restored. Release payloads retained. Restart games."
    )


def recover(prefix):
    records = pending(prefix)
    if len(records) != 1:
        raise RuntimeError(
            "Recovery expects exactly one interrupted transaction, found " + str(len(records))
        )
    record, journal = records[0]
    if current_target(prefix) not in (journal["target"], journal["previous"]):
        raise RuntimeError("Current release was changed independently; preserving it.")
    verify_previous(prefix, journal)
    for item in journal["migrations"]:
        current = Path(item["path"]).read_bytes().hex()
        if current not in (item["before_hex"], item["after_hex"]):
            raise RuntimeError("Legacy ICD changed independently; preserving it: " + item["path"])
    for item in journal.get("launcher_files", []):
        if launcher_file_state(prefix, item) not in (item["before_hex"], item["after_hex"]):
            raise RuntimeError(
                "Launcher/settings changed independently; preserving " + item["name"]
            )
    for item in journal["migrations"]:
        atomic(item["path"], bytes.fromhex(item["before_hex"]))
    restore_launcher_files(prefix, journal.get("launcher_files", []))
    if journal["previous"] is not None:
        switch(prefix, journal["previous"])
    elif current_target(prefix) is not None:
        (prefix / "current").unlink()
    journal["state"] = "recovered"
    write_json(record, journal)
    print("Interrupted transaction restored to its previous selection. Payloads retained.")


def status(prefix):
    if pending(prefix):
        raise RuntimeError("An interrupted transaction needs recovery: run driver.py recover.")
    target = current_target(prefix)
    if target is None:
        return {"active": False, "prefix": str(prefix), "reason": "No private release selected"}
    manifest = verify_release(prefix / target)
    if json.loads((prefix / "current.json").read_text()) != icd(
        prefix / "current/lib/libvulkan_radeon.so"
    ):
        raise RuntimeError("Stable ICD differs from the managed selection.")
    report = {
        "active": True,
        "prefix": str(prefix),
        "version": manifest["version"],
        "driver_sha256": manifest["driver_sha256"],
        "library": str(prefix / target / "lib/libvulkan_radeon.so"),
        "scope": "Private 64-bit selection; use the wrapper or printed ICD. Does not change system RADV.",
    }
    try:
        settings = cache_settings(prefix)
        report["shared_cache"] = {
            "enabled": settings["enabled"],
            **cache_helper().inspect(os.environ, settings["directory"]),
        }
    except (OSError, ValueError, RuntimeError) as error:
        report["shared_cache"] = {"available": False, "reason": str(error)}
    return report


def print_driver_status(report):
    if not report["active"]:
        print("Driver: no private release selected")
        return
    print("Driver: " + report["version"])
    print("Library: " + report["library"])
    shared = report["shared_cache"]
    print(
        "Shared caching for this launcher: " + ("enabled" if shared.get("enabled") else "disabled")
    )
    cache_helper().print_status(shared)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path(
            os.environ.get("BC250_FSR4_PREFIX", str(Path.home() / ".local/share/bc250-fsr4"))
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("archive", type=Path)
    p.add_argument("--sha256", help="Expected archive SHA256 (default: adjacent .sha256 file)")
    p.add_argument("--upgrade-v3-icd", type=Path, action="append", default=[])
    cache_group = p.add_mutually_exclusive_group()
    cache_group.add_argument("--shared-cache", dest="shared_cache", action="store_true")
    cache_group.add_argument("--no-shared-cache", dest="shared_cache", action="store_false")
    p.set_defaults(shared_cache=None)
    p.add_argument("--cache-dir", type=Path, help="Advanced: shared cache storage")
    p.add_argument(
        "--launch-options",
        default=None,
        help="Existing Steam options to preserve in the printed command",
    )
    sub.add_parser("rollback")
    sub.add_parser("recover")
    p = sub.add_parser("status")
    p.add_argument(
        "--human",
        action="store_true",
        help="Readable status (default through the installed launcher)",
    )
    p.add_argument("--json", action="store_true", help="Machine-readable status")
    p = sub.add_parser("steam", help="Generate launch options without editing Steam")
    p.add_argument("--launch-options", default=None)
    p = sub.add_parser("run")
    p.add_argument(
        "--no-shared-cache",
        action="store_true",
        help="Use the original cache settings for this launch",
    )
    p.add_argument(
        "--cache-dir", type=Path, help="Advanced: override shared cache storage for this launch"
    )
    p.add_argument("program", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command in {"install", "steam"}:
        args.launch_options = cache_helper().existing_steam_options(args.launch_options)
    requested_prefix = args.prefix.expanduser().absolute()
    prefix = requested_prefix.resolve()
    if requested_prefix.is_symlink() or prefix in [
        Path("/"),
        Path.home(),
        Path("/usr"),
        Path("/etc"),
        Path("/tmp"),
    ]:
        raise RuntimeError(
            "Choose a dedicated installation directory, not a system/home root or symlink."
        )
    if args.command == "status":
        report = status(prefix)
        if args.human and not args.json:
            print_driver_status(report)
        else:
            print(json.dumps(report, indent=2))
        return 0 if report["active"] else 1
    if args.command == "steam":
        if not status(prefix)["active"] or not (prefix / "bc250-fsr4-run").is_file():
            raise RuntimeError("Install the driver with the updated tools first")
        print(
            cache_helper().steam_command(
                args.launch_options, [prefix / "bc250-fsr4-run", "run", "--"]
            )
        )
        return 0
    prefix.mkdir(parents=True, exist_ok=True)
    fd = os.open(prefix / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "install":
            if os.geteuid() == 0:
                raise RuntimeError("Private install must run as your desktop user, without sudo.")
            install(args, prefix)
        elif args.command == "rollback":
            rollback(prefix)
        elif args.command == "recover":
            recover(prefix)
        elif args.command == "run":
            report = status(prefix)
            if args.program[:1] == ["--"]:
                args.program = args.program[1:]
            if not report["active"] or not args.program:
                raise RuntimeError("Install a release and supply a command to run.")
            path = prefix / "icds" / (Path(current_target(prefix)).name + ".json")
            if json.loads(path.read_text()) != icd(report["library"]):
                raise RuntimeError("Immutable release ICD was modified.")
            env = os.environ.copy()
            env.pop("VK_ICD_FILENAMES", None)
            env.pop("VK_ADD_DRIVER_FILES", None)
            env["VK_DRIVER_FILES"] = str(path)
            try:
                settings = cache_settings(prefix)
                env = cache_helper().launch_environment(
                    env,
                    args.cache_dir or settings["directory"],
                    enabled=settings["enabled"] and not args.no_shared_cache,
                )
            except (OSError, ValueError, RuntimeError) as error:
                print(
                    "Shared cache unavailable; keeping the selected driver and original cache settings: "
                    + str(error),
                    file=sys.stderr,
                    flush=True,
                )
            # Close lock before exec: applications must not hold the installer lease.
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.execvpe(args.program[0], args.program, env)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        RuntimeError,
        ValueError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
