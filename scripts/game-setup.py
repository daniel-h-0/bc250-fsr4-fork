#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare known game integrations with reversible file and optional Steam changes."""

import argparse
import configparser
import fcntl
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
import driver
import steam_config

ROOT = Path(__file__).resolve().parents[1]
COMMON = {
    "Upscalers.Dx11Upscaler": "ffx_12",
    "Upscalers.Dx12Upscaler": "ffx",
    "Upscalers.VulkanUpscaler": "ffx_12",
    "FrameGen.Enabled": "false",
    "FSR.UpscalerIndex": "0",
    "FSR.Fsr4ForceModel": "2",
    "FSR.Fsr4EnableWatermark": "auto",
    "FSR.FsrNonLinearColorSpace": "false",
    "FSR.FsrNonLinearSRGB": "auto",
    "FSR.FsrNonLinearPQ": "auto",
    "Hotfix.RestoreComputeSignature": "false",
    "Hotfix.RestoreGraphicSignature": "false",
    "Hotfix.ExtendedStateRestore": "false",
    "Menu.DisableSplash": "true",
    "Log.LogToFile": "false",
}


def download(url, path, expected):
    if path.is_file() and driver.digest(path) == expected:
        return path
    temporary = path.with_suffix(".partial")
    with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    if driver.digest(temporary) != expected:
        temporary.unlink()
        raise RuntimeError(
            "Upstream download checksum changed; update/requalify the pin, do not bypass it: " + url
        )
    temporary.replace(path)
    return path


def payload(state, policy):
    release = (
        state
        / "payloads"
        / (policy["optiscaler"]["sha256"][:16] + "-" + policy["optipatcher"]["sha256"][:12])
    )

    def valid(root):
        return (
            (root / "OptiScaler.dll").is_file()
            and driver.digest(root / "OptiScaler.dll") == policy["optiscaler"]["dll_sha256"]
            and (root / "OptiScaler/plugins/OptiPatcher.asi").is_file()
            and driver.digest(root / "OptiScaler/plugins/OptiPatcher.asi")
            == policy["optipatcher"]["sha256"]
        )

    if release.exists():
        verify_payload(release)
        if not valid(release):
            raise RuntimeError("Existing runtime payload differs from the selected pins.")
        return release
    cache = state / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(
        policy["optiscaler"]["url"], cache / "OptiScaler.7z", policy["optiscaler"]["sha256"]
    )
    patcher = download(
        policy["optipatcher"]["url"], cache / "OptiPatcher.asi", policy["optipatcher"]["sha256"]
    )
    release.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=release.parent) as tmp:
        staged = Path(tmp)
        subprocess.run(
            [
                "bsdtar",
                "-xf",
                str(archive),
                "--no-same-owner",
                "--no-same-permissions",
                "-C",
                str(staged),
            ],
            check=True,
        )
        if any(
            p.is_symlink() or not p.resolve().is_relative_to(staged.resolve())
            for p in staged.rglob("*")
        ):
            raise RuntimeError("Unexpected link in runtime archive.")
        (staged / "OptiScaler/plugins").mkdir(parents=True, exist_ok=True)
        shutil.copy2(patcher, staged / "OptiScaler/plugins/OptiPatcher.asi")
        if not valid(staged):
            raise RuntimeError("Extracted runtime does not match the pinned binaries.")
        driver.write_json(
            staged / "payload.json",
            {
                "files": {
                    str(p.relative_to(staged)): driver.digest(p)
                    for p in staged.rglob("*")
                    if p.is_file()
                }
            },
        )
        staged.rename(release)
    return release


def verify_payload(root):
    """Check retained versions too, before using them as an upgrade baseline."""
    entries = list(root.rglob("*"))
    manifest = json.loads((root / "payload.json").read_text())
    files = {str(p.relative_to(root)) for p in entries if p.is_file()}
    if (
        root.is_symlink()
        or not root.is_dir()
        or any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in entries)
        or files != set(manifest["files"]) | {"payload.json"}
        or any(driver.digest(root / p) != h for p, h in manifest["files"].items())
    ):
        raise RuntimeError("Existing runtime payload was modified; preserving it for inspection.")


def capture(path):
    if path.is_symlink():
        return {"type": "symlink", "target": os.readlink(path)}
    if path.is_file():
        return {"type": "file", "bytes_hex": path.read_bytes().hex()}
    if path.exists():
        raise RuntimeError("Refusing to replace a real directory or special file: " + str(path))
    return {"type": "absent"}


def restore(path, item):
    if item["type"] == "file":
        driver.atomic(path, bytes.fromhex(item["bytes_hex"]))
    elif item["type"] == "absent":
        if path.exists() or path.is_symlink():
            path.unlink()
    elif item["type"] == "symlink":
        temporary = path.with_name("." + path.name + ".bc250-next")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(item["target"])
        os.replace(temporary, path)


def require_stopped():
    if subprocess.run(["pgrep", "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("Exit Steam and all games before changing runtime files.")


def records(state):
    return [(p, json.loads(p.read_text())) for p in sorted((state / "transactions").glob("*.json"))]


def require_recovered(state):
    for path, transaction in records(state):
        if transaction["state"] in ("prepared", "rolling-back"):
            command = (
                "./setup-game.sh"
                if transaction.get("guided") or transaction.get("steam_changes")
                else "python3 scripts/game-setup.py"
            )
            raise RuntimeError(
                "An interrupted game transaction needs recovery: "
                "run " + command + " recover " + shlex.quote(str(path))
            )


def managed_runtime(state, paths):
    """Only a matching journal may authorize replacing an older managed runtime."""
    current = {str(p): capture(p) for p in paths[:2]}
    for _, transaction in reversed(records(state)):
        if transaction["state"] != "active":
            continue
        after = {item["path"]: item["after"] for item in transaction["changes"]}
        if all(after.get(path) == snapshot for path, snapshot in current.items()):
            proxy, directory = (current[str(p)] for p in paths[:2])
            if proxy["type"] != "symlink" or directory["type"] != "symlink":
                return False
            runtime = Path(proxy["target"]).parent
            if runtime.parent != state / "payloads" or directory["target"] != str(
                runtime / "OptiScaler"
            ):
                return False
            verify_payload(runtime)
            return True
    return False


def require_latest(path, transaction):
    owned = {item["path"] for item in transaction["changes"]}
    for later in sorted(path.parent.glob("*.json")):
        if later.name <= path.name:
            continue
        other = json.loads(later.read_text())
        if other["state"] == "active" and owned.intersection(
            item["path"] for item in other["changes"]
        ):
            raise RuntimeError("Roll back the newer game transaction first: " + str(later))


def restore_transaction(path, transaction, state):
    # All files must still be at one of this transaction's two known states.
    # A crash can happen between any two atomic file replacements.
    for item in transaction["changes"]:
        current = capture(Path(item["path"]))
        if current not in (item["before"], item["after"]):
            raise RuntimeError("A game file changed independently; preserving it: " + item["path"])
    steam_restores = [
        (
            Path(change["path"]),
            steam_config.restore_settings(
                steam_config.read_config(Path(change["path"])), change, recover=True
            ),
        )
        for change in transaction.get("steam_changes", [])
    ]
    for steam_path, content in steam_restores:
        driver.atomic(steam_path, content)
    for item in reversed(transaction["changes"]):
        if capture(Path(item["path"])) != item["before"]:
            restore(Path(item["path"]), item["before"])
    transaction["state"] = state
    driver.write_json(path, transaction)


def validate_game(args, policy):
    profile = next((p for p in policy["profiles"] if p["id"] == args.profile), None)
    if profile is None:
        raise RuntimeError(
            "Unknown game profile. Available profiles: "
            + ", ".join(p["id"] for p in policy["profiles"])
        )
    game = args.game.expanduser().resolve()
    executable = game / profile["executable"]
    if not executable.is_file():
        raise RuntimeError("Expected game executable was not found: " + str(executable))
    target = executable.parent
    if "native_fsr4" in profile:
        native = profile["native_fsr4"]
        sdk = target / native["path"]
        if not sdk.is_file() or hashlib.md5(sdk.read_bytes()).hexdigest() != native["md5"]:
            raise RuntimeError(
                "The game-native FSR SDK changed. Requalify this profile before injection."
            )
    return profile, target


def install(args, state, policy, *, steam_changes=None, quiet=False):
    require_stopped()
    require_recovered(state)
    profile, target = validate_game(args, policy)
    runtime = payload(state, policy)
    paths = [target / profile["proxy"], target / "OptiScaler", target / "OptiScaler.ini"]
    managed = managed_runtime(state, paths)
    if (
        not managed
        and paths[0].is_file()
        and driver.digest(paths[0]) != policy["optiscaler"]["dll_sha256"]
    ):
        raise RuntimeError("An unrelated proxy DLL already exists; preserving it: " + str(paths[0]))
    if paths[1].exists() and not paths[1].is_symlink():
        raise RuntimeError(
            "An existing real OptiScaler directory needs manual reconciliation: " + str(paths[1])
        )
    if not managed and paths[1].is_symlink():
        helper = paths[1] / "amd_fidelityfx_upscaler_dx12.dll"
        expected = runtime / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll"
        if not helper.is_file() or driver.digest(helper) != driver.digest(expected):
            raise RuntimeError(
                "Existing runtime directory is from a different version; preserving it."
            )
    before = [capture(p) for p in paths]
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(paths[2] if paths[2].is_file() else runtime / "OptiScaler.ini")
    values = {**COMMON, **profile["config"]}
    if args.watermark:
        values["FSR.Fsr4EnableWatermark"] = "true"
    for dotted, value in values.items():
        section, key = dotted.split(".", 1)
        if not ini.has_section(section):
            ini.add_section(section)
        ini.set(section, key, value)
    stream = io.StringIO()
    ini.write(stream, space_around_delimiters=False)
    after = [
        {"type": "symlink", "target": str(runtime / "OptiScaler.dll")},
        {"type": "symlink", "target": str(runtime / "OptiScaler")},
        {"type": "file", "bytes_hex": stream.getvalue().encode().hex()},
    ]
    changes = [{"path": str(p), "before": b, "after": a} for p, b, a in zip(paths, before, after)]
    transaction = {"schema": 1, "profile": profile["id"], "state": "prepared", "changes": changes}
    if steam_changes is not None:
        transaction["guided"] = True
    if steam_changes:
        transaction["steam_changes"] = steam_changes
        # Stage all Steam edits before touching any game files.
        for change in steam_changes:
            if steam_config.read_config(Path(change["path"])).hex() != change["before_hex"]:
                raise RuntimeError("Steam configuration changed while staging; run setup again.")
    directory = state / "transactions"
    directory.mkdir(exist_ok=True)
    record = directory / (str(time.time_ns()) + ".json")
    driver.write_json(record, transaction)
    print("Recovery record: " + str(record), flush=True)
    try:
        for item in changes:
            path = Path(item["path"])
            if capture(path) != item["before"]:
                raise RuntimeError("Game file changed while staging: " + str(path))
            restore(path, item["after"])
        for change in steam_changes or []:
            require_stopped()
            steam_path = Path(change["path"])
            if steam_config.read_config(steam_path).hex() != change["before_hex"]:
                raise RuntimeError("Steam configuration changed during setup; restoring changes.")
            driver.atomic(steam_path, bytes.fromhex(change["after_hex"]))
        transaction["state"] = "active"
        driver.write_json(record, transaction)
    except BaseException:
        require_stopped()
        restore_transaction(record, transaction, "aborted")
        raise
    if quiet:
        return record
    print(
        "Configured "
        + profile["title"]
        + ". Select "
        + policy["proton"]
        + " in Steam Compatibility."
    )
    print("Merge with existing Steam launch options:")
    print(
        "PROTON_FSR4_UPGRADE="
        + shlex.quote(policy["fsr4"])
        + " WINEDLLOVERRIDES="
        + profile["proxy"].removesuffix(".dll")
        + "=n,b %command% "
        + " ".join(shlex.quote(a) for a in profile.get("launch_args", []))
    )
    print("Rollback record: " + str(record))
    print("Game-side upscaler selection is still required; see docs/games.md.")
    return record


def rollback(path):
    require_stopped()
    require_recovered(path.parent.parent)
    transaction = json.loads(path.read_text())
    if transaction["state"] != "active":
        raise RuntimeError("This game transaction is not active.")
    require_latest(path, transaction)
    for item in transaction["changes"]:
        if capture(Path(item["path"])) != item["after"]:
            raise RuntimeError("A game file changed after setup; preserving it: " + item["path"])
    for change in transaction.get("steam_changes", []):
        steam_config.restore_settings(steam_config.read_config(Path(change["path"])), change)
    transaction["state"] = "rolling-back"
    driver.write_json(path, transaction)
    restore_transaction(path, transaction, "rolled-back")
    print(
        "Original game runtime files restored exactly."
        + (" Managed Steam settings restored." if transaction.get("steam_changes") else "")
    )


def recover(path):
    require_stopped()
    transaction = json.loads(path.read_text())
    if transaction["state"] not in ("prepared", "rolling-back"):
        raise RuntimeError("This game transaction does not need recovery.")
    require_latest(path, transaction)
    restore_transaction(path, transaction, "recovered")
    print("Interrupted game transaction restored to its previous files.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--state",
        type=Path,
        help="Runtime state directory; rollback/recover infer it from the record",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    i = sub.add_parser("install")
    i.add_argument("--game", type=Path, required=True)
    i.add_argument("--profile", required=True, help="Profile ID from v4/games.json")
    i.add_argument(
        "--watermark",
        action="store_true",
        help="Temporary visual proof; normal installs leave it off",
    )
    r = sub.add_parser("rollback")
    r.add_argument("record", type=Path)
    r = sub.add_parser("recover")
    r.add_argument("record", type=Path)
    args = p.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run game setup as your desktop user, without sudo.")
    if args.command in ("rollback", "recover"):
        args.record = args.record.expanduser().resolve()
        if args.record.parent.name != "transactions":
            raise RuntimeError(
                "Select the original record inside its runtime transactions directory."
            )
        state = args.record.parent.parent
        if args.state is not None and args.state.expanduser().resolve() != state:
            raise RuntimeError("--state does not match the supplied transaction record.")
    else:
        state = (
            (args.state or Path.home() / ".local/share/bc250-fsr4/game-runtime")
            .expanduser()
            .resolve()
        )
    state.mkdir(parents=True, exist_ok=True)
    with (state / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "fetch":
            policy = json.loads((ROOT / "v4/games.json").read_text())
            print(payload(state, policy))
        elif args.command == "install":
            policy = json.loads((ROOT / "v4/games.json").read_text())
            install(args, state, policy)
        elif args.command == "rollback":
            rollback(args.record)
        elif args.command == "recover":
            recover(args.record)


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        configparser.Error,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit("ERROR: " + str(error))
