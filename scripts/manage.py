#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install, update, inspect or roll back one BC250 FSR4 distribution."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
import driver
import runtime_bundle

import runtime

ROOT = Path(__file__).resolve().parents[1]


def operations(prefix):
    result = []
    for path in sorted((prefix / "operations").glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Installation operation record is not a regular file.")
        result.append((path, json.loads(path.read_text())))
    return result


@contextlib.contextmanager
def locks(prefix, root):
    tools = root / "compatibilitytools.d"
    for path in (prefix, prefix / "operations", tools):
        if path.is_symlink():
            raise RuntimeError("Managed directory is a symlink; preserving it: " + str(path))
        path.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(runtime.exclusive_lock(prefix / ".lock"))
        stack.enter_context(runtime.exclusive_lock(tools / ".bc250-fsr4-install.lock"))
        tool = tools / runtime.TOOL
        held = tool.exists() or tool.is_symlink()
        if held:
            runtime.verify_tool(tool)
            stack.enter_context(runtime.exclusive_lock(tool / ".runtime.lock"))
            runtime.finish_interrupted(tool)
        yield held


def driver_archive(args, policy):
    if args.driver_archive:
        archive = args.driver_archive.expanduser().resolve()
        return archive, args.driver_sha256
    if "archive_sha256" in policy["driver"]:
        component = policy["driver"]
        checksum = driver.normalize_checksum(component["archive_sha256"])
        return runtime_bundle.download(
            component["archive_url"], checksum, args.cache / "drivers", args.offline
        ), checksum
    if "archive_url" in policy["driver"]:
        url = policy["driver"]["archive_url"]
        asset = url.rsplit("/", 1)[-1]
    else:
        tag = "v" + policy["driver"]["version"]
        asset = "bc250-fsr4-" + tag + "-cachyos-x86_64.tar.gz"
        url = "https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/" + tag + "/" + asset
    cache = args.cache / "drivers"
    if cache.is_symlink():
        raise RuntimeError("Driver download cache is a symlink; preserving it.")
    cache.mkdir(parents=True, exist_ok=True)
    sidecar = cache / (asset + ".sha256")
    if sidecar.is_symlink():
        raise RuntimeError("Driver checksum cache is a symlink; preserving it.")
    if not sidecar.exists():
        if args.offline:
            raise RuntimeError("Offline cache is missing the driver checksum: " + str(sidecar))
        with urllib.request.urlopen(url + ".sha256", timeout=90) as response:
            data = response.read(4097)
        if len(data) > 4096:
            raise RuntimeError("Driver checksum download is oversized.")
        checksum = driver.normalize_checksum(data.decode().split()[0])
        driver.atomic(sidecar, (checksum + "\n").encode())
    if sidecar.is_symlink() or not sidecar.is_file():
        raise RuntimeError("Driver checksum cache is not a regular file.")
    checksum = driver.normalize_checksum(sidecar.read_text().split()[0])
    return runtime_bundle.download(url, checksum, cache, args.offline), checksum


def desired_runtime(args, policy):
    if not args.runtime_archive:
        return policy["release"]["id"]
    with tarfile.open(args.runtime_archive, "r:gz") as archive:
        first = archive.next()
        if first is None or not PurePosixPath(first.name).parts:
            raise RuntimeError("Runtime archive has no version directory.")
        return runtime.safe_id(PurePosixPath(first.name).parts[0])


def owned_driver_record(prefix, operation):
    name = operation["driver_record"]
    if name is None:
        return None, None
    if Path(name).name != name or not name.endswith(".json"):
        raise RuntimeError("Invalid owned driver transaction reference.")
    path = prefix / "transactions" / name
    if path.is_symlink():
        raise RuntimeError("Owned driver transaction is a symlink; preserving it.")
    return path, json.loads(path.read_text()) if path.exists() else None


def restore_driver(prefix, operation):
    """Only the exact transaction reserved by this operation may be undone."""
    path, journal = owned_driver_record(prefix, operation)
    if path is None:
        return
    if journal is None:
        if driver.current_target(prefix) != operation["driver_before"]:
            raise RuntimeError("Driver changed without the owned transaction; preserving it.")
        return
    if journal["state"] in ("prepared", "rolling-back"):
        if [p for p, _ in driver.pending(prefix)] != [path]:
            raise RuntimeError("Another driver transaction needs attention; preserving it.")
        driver.recover(prefix)
    elif journal["state"] == "active":
        active = [
            p
            for p in sorted((prefix / "transactions").glob("*.json"))
            if json.loads(p.read_text())["state"] == "active"
        ]
        if not active or active[-1] != path:
            raise RuntimeError("A later driver installation is active; preserving it.")
        driver.rollback(prefix)
    elif driver.current_target(prefix) != journal["previous"]:
        raise RuntimeError("Driver changed after recovery; preserving it.")


def restore_operation(path, operation, *, runtime_locked=False, outcome="failed"):
    root, prefix = Path(operation["steam_root"]), Path(operation["prefix"])
    tool = root / "compatibilitytools.d" / runtime.TOOL
    with (
        runtime.exclusive_lock(tool / ".runtime.lock")
        if tool.exists() and not runtime_locked
        else contextlib.nullcontext()
    ):
        if tool.exists():
            runtime.finish_interrupted(tool)
        current = runtime.selection(root)
        previous, expected = operation["before_runtime"], operation["expected_runtime"]
        if current not in (previous, expected):
            raise RuntimeError("Runtime changed outside this operation; preserving it.")
        if previous is not None and current != previous:
            runtime.verify_version(tool / "versions" / previous["id"])
        if previous is None and current is not None:
            retired = Path(operation["retired"])
            if retired.exists() or retired.is_symlink() or retired.parent.is_symlink():
                raise RuntimeError("Runtime retirement path changed independently; preserving it.")
        operation["state"] = "restoring"
        operation["restore_outcome"] = operation.get("restore_outcome", outcome)
        driver.write_json(path, operation)
        restore_driver(prefix, operation)
        result = runtime.restore(
            root, previous, expected=expected, retired=Path(operation["retired"]), lock_held=True
        )
        operation["state"] = operation["restore_outcome"]
        driver.write_json(path, operation)
        return result


def recover_pending(prefix, root, held):
    pending = [(p, op) for p, op in operations(prefix) if op["state"] in ("pending", "restoring")]
    if len(pending) > 1:
        raise RuntimeError("More than one unfinished installation operation needs attention.")
    if not pending:
        return False
    path, operation = pending[0]
    if Path(operation["steam_root"]) != root:
        raise RuntimeError(
            "Resume the unfinished operation with --steam-root " + operation["steam_root"]
        )
    restore_operation(path, operation, runtime_locked=held)
    return True


def status(prefix, root):
    pending = [str(p) for p, op in operations(prefix) if op["state"] in ("pending", "restoring")]
    try:
        report = runtime.status(root)
        result = {
            "installed": report["installed"],
            "version": report.get("version"),
            "steam_tool": report["tool"],
            "driver": report.get("driver"),
            "steam_registration": report.get("steam_registration"),
            "unfinished_runtime_transactions": report.get("interrupted_transactions", []),
        }
        result["unfinished_driver_transactions"] = [str(p) for p, _ in driver.pending(prefix)]
        if not report["installed"]:
            try:
                result["driver"] = runtime.select_driver("auto", prefix)
            except RuntimeError:
                pass
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        result = {"installed": None, "error": str(error)}
    result["unfinished_operations"] = pending
    if pending:
        result["recovery"] = "Run bc250-fsr4 rollback with the same --prefix and --steam-root."
    return result


def apply(args, prefix, root):
    policy = json.loads((ROOT / "runtime/manifest.json").read_text())
    migrate_v3 = args.upgrade_v3 or bool(args.upgrade_v3_icd)
    if (args.driver_archive or migrate_v3) and args.driver == "system":
        raise RuntimeError(
            "Driver archives and v3 migration require a private driver; "
            "do not combine them with --driver system."
        )
    with locks(prefix, root) as held:
        recover_pending(prefix, root, held)
        # Recovery can retire a first installation while its old lock remains held.
        held = held and (root / "compatibilitytools.d" / runtime.TOOL).exists()
        if driver.pending(prefix):
            raise RuntimeError("An existing driver transaction needs recovery before installation.")
        before = runtime.selection(root)
        selection_mode = args.driver
        if selection_mode == "auto" and before and before["driver"]["mode"] == "private":
            selection_mode = "private"
        requested_driver = None
        if args.driver_archive:
            archive, checksum = driver_archive(args, policy)
            with tempfile.TemporaryDirectory(prefix=".requested-driver-", dir=prefix) as temporary:
                _, requested_driver = driver.extract_verified(
                    archive, Path(temporary), driver.archive_checksum(archive, checksum)
                )
        try:
            selected = runtime.select_driver(
                "private" if requested_driver else selection_mode, prefix
            )
        except RuntimeError:
            if args.driver == "system":
                raise
            selected = None
        if (
            requested_driver is not None
            and selected is not None
            and driver.verify_release(prefix / driver.current_target(prefix)) != requested_driver
        ):
            # An explicit corrected ABI build must replace an existing build
            # from the same Mesa source; source compatibility is not identity.
            selected = None
        if selected is not None:
            if (
                requested_driver is None
                and selected["mode"] == "private"
                and selected["sha256"] in policy["driver"].get("superseded_private_sha256", [])
            ):
                selected = None
            else:
                try:
                    runtime.probe_driver(selected, root)
                except RuntimeError:
                    if args.driver == "system" or requested_driver is not None:
                        raise
                    # A matching source hash does not establish distro ABI compatibility.
                    # Let the ordinary transaction install and qualify the portable asset.
                    selected = None
        if migrate_v3:
            # Even a reusable driver needs an owned transaction to journal and
            # restore the explicitly requested legacy ICD changes.
            selected = None
        target = desired_runtime(args, policy)
        if (
            selected is not None
            and before == {"id": target, "driver": selected}
            and not args.runtime_archive
            and driver.digest(
                root / "compatibilitytools.d" / runtime.TOOL / "current/runtime-lock.json"
            )
            == driver.digest(ROOT / "runtime/manifest.json")
        ):
            changed = runtime.update_registration(root / "compatibilitytools.d" / runtime.TOOL)
            return {**status(prefix, root), "changed": changed}
        identifier = time.strftime("%Y%m%dT%H%M%S") + "-" + str(time.time_ns())
        path = prefix / "operations" / (identifier + ".json")
        operation = {
            "schema": 1,
            "state": "pending",
            "distribution": target.removeprefix("bc250-fsr4-runtime-"),
            "steam_root": str(root),
            "prefix": str(prefix),
            "before_runtime": before,
            "expected_runtime": None,
            "driver_before": driver.current_target(prefix),
            "driver_record": identifier + ".json" if selected is None else None,
            "retired": str(root / ".bc250-fsr4-retired" / identifier),
        }
        driver.write_json(path, operation)
        try:
            if selected is None:
                print("Installing the compatible BC250 driver…", flush=True)
                archive, checksum = driver_archive(args, policy)
                if not args.driver_archive and "archive_url" in policy["driver"]:
                    with tempfile.TemporaryDirectory(
                        prefix=".default-driver-", dir=prefix
                    ) as temporary:
                        _, release = driver.extract_verified(
                            archive, Path(temporary), driver.archive_checksum(archive, checksum)
                        )
                        if (
                            release["driver_sha256"] != policy["driver"]["sha256"]
                            or release["source_manifest_sha256"]
                            != policy["driver"]["source_manifest_sha256"]
                        ):
                            raise RuntimeError(
                                "Default driver archive differs from the release's binary/source pins."
                            )
                migrations = list(args.upgrade_v3_icd)
                if args.upgrade_v3:
                    migrations.append(prefix / "v3/radv-bc250-fsr4-v3.json")
                driver.install(
                    argparse.Namespace(
                        archive=archive,
                        sha256=checksum,
                        upgrade_v3_icd=migrations,
                        record=prefix / "transactions" / operation["driver_record"],
                        quiet=True,
                    ),
                    prefix,
                )
                selected = runtime.select_driver("private", prefix)
            operation["expected_runtime"] = {"id": target, "driver": selected}
            driver.write_json(path, operation)
            print("Installing BC250 FSR4 " + operation["distribution"] + "…", flush=True)
            runtime.install(
                argparse.Namespace(
                    archive=args.runtime_archive,
                    sha256=args.runtime_sha256,
                    driver=selected["mode"],
                    driver_prefix=prefix,
                    cache=args.cache,
                    offline=args.offline,
                ),
                root,
                lock_held=True,
            )
            if runtime.selection(root) != operation["expected_runtime"]:
                raise RuntimeError("Runtime activation differs from the requested installation.")
            operation["state"] = (
                "active"
                if before != operation["expected_runtime"] or operation["driver_record"]
                else "unchanged"
            )
            driver.write_json(path, operation)
        except BaseException as error:
            operation["error"] = type(error).__name__ + ": " + str(error)
            driver.atomic(
                path.with_suffix(".log"), "".join(traceback.format_exception(error)).encode()
            )
            driver.write_json(path, operation)
            try:
                restore_operation(path, operation, runtime_locked=held)
            except BaseException as recovery_error:
                raise RuntimeError(
                    "Installation failed and recovery needs attention: "
                    + str(recovery_error)
                    + ". Evidence: "
                    + str(path)
                ) from error
            raise RuntimeError(
                "Installation failed; previous selections restored. Evidence: " + str(path)
            ) from error
    return {
        **status(prefix, root),
        "changed": operation["state"] == "active",
        "operation": str(path),
    }


def rollback(prefix, root):
    with locks(prefix, root) as held:
        if recover_pending(prefix, root, held):
            return {**status(prefix, root), "recovered": True}
        candidates = [
            (p, op)
            for p, op in operations(prefix)
            if op["state"] == "active" and Path(op["steam_root"]) == root
        ]
        if not candidates:
            raise RuntimeError("No BC250 FSR4 installation operation to roll back.")
        path, operation = candidates[-1]
        result = restore_operation(path, operation, runtime_locked=held, outcome="rolled-back")
    return {**status(prefix, root), "rolled_back": str(path), **result}


def doctor(prefix, root):
    result = status(prefix, root)
    try:
        driver.check_hardware()
        result["hardware"] = "BC250 1002:13fe"
        if not result.get("driver"):
            raise RuntimeError("No verified driver is selected. Run bc250-fsr4 install first.")
        result["checks"] = runtime.probe_driver(result["driver"], root)
        if result.get("installed") and not result.get("steam_registration", {}).get(
            "save_paths_supported"
        ):
            raise RuntimeError(
                "Steam registration lacks Windows save paths. Run bc250-fsr4 update, then restart Steam."
            )
        result["healthy"] = result.get("error") is None and not any(
            result.get(name)
            for name in (
                "unfinished_operations",
                "unfinished_driver_transactions",
                "unfinished_runtime_transactions",
            )
        )
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        result.update(healthy=False, diagnostic=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(prog="bc250-fsr4", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "update", "status", "doctor", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--steam-root", type=Path)
        command.add_argument(
            "--prefix",
            "--driver-prefix",
            dest="prefix",
            type=Path,
            default=Path(
                os.environ.get("BC250_FSR4_PREFIX", str(Path.home() / ".local/share/bc250-fsr4"))
            ),
        )
        if name in ("install", "update"):
            command.add_argument("--driver", choices=("auto", "private", "system"), default="auto")
            command.add_argument(
                "--driver-archive",
                type=Path,
                help="Install this private driver archive, replacing a different selected build",
            )
            command.add_argument("--driver-sha256")
            command.add_argument("--runtime-archive", type=Path)
            command.add_argument("--runtime-sha256")
            command.add_argument(
                "--cache", type=Path, default=Path.home() / ".cache/bc250-fsr4-runtime"
            )
            command.add_argument("--offline", action="store_true")
            command.add_argument(
                "--upgrade-v3",
                action="store_true",
                help="Install a private driver and reversibly migrate the standard v3 ICD",
            )
            command.add_argument("--upgrade-v3-icd", type=Path, action="append", default=[])
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    if os.geteuid() == 0:
        raise RuntimeError("Run BC250 FSR4 as your desktop user, without sudo.")
    requested = args.prefix.expanduser().absolute()
    prefix = requested.resolve()
    if requested.is_symlink() or prefix in (
        Path("/"),
        Path.home(),
        Path("/usr"),
        Path("/etc"),
        Path("/tmp"),
    ):
        raise RuntimeError(
            "Choose a dedicated --prefix directory, not a system/home root or symlink."
        )
    root = runtime.steam_root(args.steam_root)
    if args.command in ("install", "update"):
        if (args.driver_sha256 and not args.driver_archive) or (
            args.runtime_sha256 and not args.runtime_archive
        ):
            parser.error("An explicit checksum requires its corresponding local archive")
        args.cache = args.cache.expanduser().resolve()
        args.runtime_archive = (
            args.runtime_archive.expanduser().resolve() if args.runtime_archive else None
        )
        result = apply(args, prefix, root)
    else:
        result = globals()[args.command](prefix, root)
    print(json.dumps(result, indent=2))
    if args.command == "doctor" and not result["healthy"]:
        raise SystemExit(1)
    if args.command in ("install", "update"):
        print(
            "Restart Steam and select BC250 FSR4 (4.1.1 INT8) in the game's Compatibility settings."
        )
    elif args.command == "rollback" and not result.get("installed"):
        print(
            "The BC250 FSR4 entry was removed. Restart Steam and select ordinary Proton for those games."
        )


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        RuntimeError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit("ERROR: " + str(error))
