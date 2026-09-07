#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install, inspect, run or roll back a verified private BC250 FSR4 v4 release."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path


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
    if not shutil.which("vulkaninfo"):
        raise RuntimeError("Install vulkan-tools (vulkaninfo) before activating a release.")
    linked = subprocess.run(["ldd", "-r", str(library)], capture_output=True, text=True, timeout=30)
    if linked.returncode or any(
        message in linked.stdout + linked.stderr for message in ("not found", "undefined symbol")
    ):
        raise RuntimeError(
            "Binary ABI/dependency check failed. Build on this distribution.\n"
            + linked.stdout
            + linked.stderr
        )
    path = directory / "probe.json"
    write_json(path, icd(library))
    env = os.environ.copy()
    for key in ("VK_ICD_FILENAMES", "VK_ADD_DRIVER_FILES"):
        env.pop(key, None)
    env["VK_DRIVER_FILES"] = str(path)
    env["LD_BIND_NOW"] = "1"
    result = subprocess.run(
        ["vulkaninfo", "--summary"], env=env, capture_output=True, text=True, timeout=60
    )
    (directory / "probe.log").write_text(result.stdout + result.stderr)
    if (
        result.returncode
        or "radv" not in result.stdout.lower()
        or "gfx1013" not in result.stdout.lower()
    ):
        raise RuntimeError(
            "BC250 RADV initialization failed. Current release is unchanged.\n"
            + result.stdout
            + result.stderr
        )
    return {
        "loader": "vulkaninfo --summary (LD_BIND_NOW=1)",
        "success": True,
        "dependencies": linked.stdout,
        "vulkaninfo": result.stdout,
    }


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
        bundle.extractall(destination, filter="data")
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


def current_target(prefix):
    current = prefix / "current"
    if current.is_symlink():
        return os.readlink(current)
    if current.exists():
        raise RuntimeError("current exists but is not a managed symlink.")
    return None


def pending(prefix):
    records = [
        (p, json.loads(p.read_text())) for p in sorted((prefix / "transactions").glob("*.json"))
    ]
    return [
        (p, journal)
        for p, journal in records
        if journal.get("state") in ("prepared", "rolling-back")
    ]


def verify_previous(prefix, journal):
    if journal["previous"] is not None:
        # A retained release can be removed or edited between install and rollback.
        # Validate it before restoring migrated launch paths or switching current.
        verify_release(prefix / journal["previous"])


def install(args, prefix):
    if pending(prefix):
        raise RuntimeError(
            "An interrupted transaction needs recovery: run driver.py recover first."
        )
    archive = args.archive.expanduser().resolve()
    checksum = args.sha256
    if checksum is None:
        fields = Path(str(archive) + ".sha256").read_text().split()
        if not fields:
            raise RuntimeError("The adjacent SHA256 file is empty; nothing installed.")
        checksum = fields[0]
    checksum = normalize_checksum(checksum)
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
            "probe": result,
            "archive_sha256": checksum,
            "state": "prepared",
        }
        transactions = prefix / "transactions"
        transactions.mkdir(exist_ok=True)
        record = transactions / (
            time.strftime("%Y%m%dT%H%M%S") + "-" + str(time.time_ns()) + ".json"
        )
        write_json(record, journal)
        changed = []
        try:
            for item in migrations:
                path = Path(item["path"])
                if path.read_bytes().hex() != item["before_hex"]:
                    raise RuntimeError("Legacy ICD changed while installing: " + str(path))
                atomic(path, bytes.fromhex(item["after_hex"]))
                changed.append(item)
            switch(prefix, target)
            journal["state"] = "active"
            write_json(record, journal)
        except BaseException:
            for item in reversed(changed):
                atomic(item["path"], bytes.fromhex(item["before_hex"]))
            if previous is not None:
                switch(prefix, previous)
            elif current_target(prefix) is not None:
                (prefix / "current").unlink()
            journal["state"] = "aborted"
            write_json(record, journal)
            raise
    print("Installed and validated " + release_id)
    print(
        "Steam launch option: VK_DRIVER_FILES="
        + __import__("shlex").quote(str(prefix / "current.json"))
        + " %command%"
    )
    print("Existing explicitly migrated v3 launch paths now select v4. Restart the game.")


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
    journal["state"] = "rolling-back"
    write_json(record, journal)
    for item in journal["migrations"]:
        atomic(item["path"], bytes.fromhex(item["before_hex"]))
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
    for item in journal["migrations"]:
        atomic(item["path"], bytes.fromhex(item["before_hex"]))
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
    return {
        "active": True,
        "prefix": str(prefix),
        "version": manifest["version"],
        "driver_sha256": manifest["driver_sha256"],
        "library": str(prefix / target / "lib/libvulkan_radeon.so"),
        "scope": "Private 64-bit selection; use the wrapper or printed ICD. Does not change system RADV.",
    }


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
    sub.add_parser("rollback")
    sub.add_parser("recover")
    sub.add_parser("status")
    p = sub.add_parser("run")
    p.add_argument("program", nargs=argparse.REMAINDER)
    args = parser.parse_args()
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
    prefix.mkdir(parents=True, exist_ok=True)
    with (prefix / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "install":
            if os.geteuid() == 0:
                raise RuntimeError("Private install must run as your desktop user, without sudo.")
            install(args, prefix)
        elif args.command == "rollback":
            rollback(prefix)
        elif args.command == "recover":
            recover(prefix)
        elif args.command == "status":
            report = status(prefix)
            print(json.dumps(report, indent=2))
            return 0 if report["active"] else 1
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
