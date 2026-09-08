#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Undo transactions made by the retired per-game setup tool. No new installs."""
import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import driver
import steam_config


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
            command = "./setup-game.sh"
            raise RuntimeError(
                "An interrupted game transaction needs recovery: "
                "run " + command + " recover " + shlex.quote(str(path))
            )


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("rollback", "recover"))
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run recovery as your desktop user, without sudo.")
    path = args.record.expanduser().resolve(strict=True)
    if path.parent.name != "transactions":
        raise RuntimeError("Select the original record in its transactions directory.")
    with (path.parent.parent / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (rollback if args.command == "rollback" else recover)(path)
if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
