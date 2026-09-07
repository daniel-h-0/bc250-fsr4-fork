#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Guided, reversible FSR 4.1.1 INT8 setup for supported native Steam games."""

import argparse
import configparser
import fcntl
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
import driver
import proton_runtime
import steam_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("game_setup", ROOT / "scripts/game-setup.py")
game_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_setup)
SYSTEM_LIBRARY = Path("/usr/lib/libvulkan_radeon.so")
SYSTEM_METADATA = Path("/usr/share/bc250-fsr4-v4/system.json")


def choose(items, label, render, *, interactive, preferred=None):
    if not items:
        raise RuntimeError("No " + label + " found.")
    if len(items) == 1:
        return items[0]
    if not interactive:
        if preferred is not None:
            return preferred
        raise RuntimeError("More than one " + label + " found; select one explicitly.")
    print("\nChoose " + label + ":")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {render(item)}" + (" (recent account)" if item == preferred else ""))
    default = str(items.index(preferred) + 1) if preferred is not None else ""
    while True:
        answer = input("Number" + (f" [{default}]" if default else "") + ": ").strip() or default
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        print("Enter one of the listed numbers.")


def select_driver(mode, prefix):
    """Prefer the verified system installation; otherwise use the private v4 ICD."""
    expected = json.loads((ROOT / "docs/qualification.json").read_text())["release_driver_sha256"]
    if mode != "private" and SYSTEM_LIBRARY.is_file():
        actual = driver.digest(SYSTEM_LIBRARY)
        known = actual == expected
        if SYSTEM_METADATA.is_file():
            metadata = json.loads(SYSTEM_METADATA.read_text())
            source = json.loads((ROOT / "v4/manifest.json").read_text())
            known = known or (
                metadata.get("driver_sha256") == actual
                and metadata.get("version") == source["version"]
                and metadata.get("mesa") == source["mesa"]
            )
        if known:
            return {"mode": "system", "library": SYSTEM_LIBRARY, "environment": {}}
    if mode != "system":
        report = driver.status(prefix)
        if report["active"]:
            release = driver.verify_release(prefix / driver.current_target(prefix))
            if release["source_manifest_sha256"] != driver.digest(ROOT / "v4/manifest.json"):
                raise RuntimeError("The selected private driver is from another source version.")
            return {
                "mode": "private",
                "library": Path(report["library"]),
                "environment": {"VK_DRIVER_FILES": str(prefix / "current.json")},
            }
    raise RuntimeError(
        "A verified v4 driver was not found. Run ./install-v4.sh first "
        "(add --upgrade-v3 for an existing v3 private install), then rerun setup. "
        "For a custom private location, use --driver-prefix PATH."
    )


def require_game_stopped(profile):
    game_setup.require_stopped()
    executable = Path(profile["executable"]).name.casefold()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            arguments = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
        if any(
            arg.replace("\\", "/").rsplit("/", 1)[-1].casefold() == executable for arg in arguments
        ):
            raise RuntimeError("Close " + profile["title"] + " before setup or rollback.")


def execute(args, game, account, steam_root, policy, state):
    profile = next(p for p in policy["profiles"] if p["id"] == game["profile"])
    setup_args = argparse.Namespace(profile=profile["id"], game=Path(game["game"]), watermark=False)
    game_setup.validate_game(setup_args, policy)
    selected = select_driver(args.driver, args.driver_prefix.expanduser().resolve())
    environment = {
        "PROTON_FSR4_UPGRADE": policy["fsr4"],
        "WINEDLLOVERRIDES": profile["proxy"].removesuffix(".dll") + "=n,b",
        **selected["environment"],
    }
    changes = steam_config.plan_settings(
        steam_root,
        [account["id"]],
        profile["appid"],
        environment,
        policy["proton"],
        profile.get("launch_args", []),
        remove_environment=("VK_ICD_FILENAMES", "VK_ADD_DRIVER_FILES", "PROTON_USE_OPTISCALER")
        + (() if selected["mode"] == "private" else ("VK_DRIVER_FILES",)),
    )
    print("\nGame: " + profile["title"])
    print("Folder: " + str(game["game"]))
    print("Steam account: " + account["label"] + " (" + str(account["id"]) + ")")
    print("Driver: verified " + selected["mode"] + " v4 installation")
    print("Runtime: pinned FSR 4.1.1 INT8, OptiScaler and OptiPatcher; GE-Proton11-6")
    proton_policy = json.loads((ROOT / "v4/proton.json").read_text())
    proton_directory = steam_root / "compatibilitytools.d" / proton_policy["name"]
    if proton_directory.exists() or proton_directory.is_symlink():
        proton_runtime.validate_install(proton_directory, proton_policy)
    else:
        print("GE-Proton needs a 534 MB download and about 1.5 GB of installed space.")
    print("Setup will back up game files, merge launch options, and select Proton for this game.")
    print("Proton selection is shared by Steam accounts; launch options apply to this account.")
    if args.dry_run:
        print(
            "Preview only: no downloads or files changed. Steam may remain open for this preview."
        )
        return None
    if not shutil.which("bsdtar"):
        raise RuntimeError("Install libarchive (bsdtar) for the runtime archive, then rerun setup.")
    require_game_stopped(profile)
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("Use --yes with an explicit --profile for unattended setup.")
        if input("Apply this setup? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Cancelled. No files changed.")
            return None
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        game_setup.require_recovered(state)
        print("Checking driver loading and runtime downloads…", flush=True)
        with tempfile.TemporaryDirectory(prefix=".driver-probe-", dir=state) as temporary:
            driver.probe(selected["library"], Path(temporary))
        proton_runtime.ensure_proton(steam_root, state, proton_policy)
        # Downloads may take minutes. Recheck Steam and the selected game before mutation.
        game_setup.payload(state, policy)
        require_game_stopped(profile)
        record = game_setup.install(setup_args, state, policy, steam_changes=changes, quiet=True)
    print("\nSetup complete. Restart Steam and launch " + profile["title"] + ".")
    print(
        "In the game's graphics menu, select "
        + ("DLSS" if profile["route"] == "dlss" else "FSR")
        + " and your preferred quality level. Frame generation stays off."
    )
    print("GE-Proton downloads its FSR 4.1.1 provider on the first launch; allow it to finish.")
    print("Undo: ./setup-game.sh rollback " + shlex.quote(str(record)))
    print(
        "This completes configuration. Optional visual/engagement checks: docs/game-troubleshooting.md"
    )
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("setup", "rollback", "recover"), nargs="?", default="setup"
    )
    parser.add_argument("record", nargs="?", type=Path)
    parser.add_argument(
        "--list", action="store_true", help="List supported installed games, without changing files"
    )
    parser.add_argument("--profile", help="Game profile ID; otherwise choose interactively")
    parser.add_argument(
        "--steam-root", type=Path, help="Native Steam root, if it cannot be detected"
    )
    parser.add_argument(
        "--account", help="Steam userdata account ID, when more than one is present"
    )
    parser.add_argument("--driver", choices=("auto", "private", "system"), default="auto")
    parser.add_argument(
        "--driver-prefix", type=Path, default=Path.home() / ".local/share/bc250-fsr4"
    )
    parser.add_argument("--state", type=Path, help="Runtime/backup location; keep it after setup")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview setup without downloads or writes"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Apply without the final interactive confirmation"
    )
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12 or newer is required.")
    if os.geteuid() == 0:
        raise RuntimeError("Run setup as your desktop user, without sudo.")
    if args.command in ("rollback", "recover"):
        if args.record is None or args.list or args.dry_run:
            parser.error(
                "rollback/recover require a transaction record and do not accept --list/--dry-run"
            )
        record = args.record.expanduser().resolve()
        if record.parent.name != "transactions":
            parser.error("Use the original record inside its runtime transactions directory")
        state = record.parent.parent
        if args.state is not None and args.state.expanduser().resolve() != state:
            parser.error("--state does not match the record")
        policy = json.loads((ROOT / "v4/games.json").read_text())
        transaction = json.loads(record.read_text())
        profile = next((p for p in policy["profiles"] if p["id"] == transaction["profile"]), None)
        if profile:
            require_game_stopped(profile)
        with (state / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            getattr(game_setup, args.command)(record)
        return
    if args.record:
        parser.error("A transaction record is only used with rollback/recover")
    interactive = sys.stdin.isatty() and not args.yes
    roots = (
        [args.steam_root.expanduser().resolve()]
        if args.steam_root
        else steam_config.discover_roots()
    )
    if not roots:
        raise RuntimeError(
            "Native Steam was not found. Use --steam-root PATH for a custom installation. Steam Flatpak is not supported by this helper."
        )
    steam_root = choose(roots, "Steam installation", str, interactive=interactive)
    policy = json.loads((ROOT / "v4/games.json").read_text())
    games = steam_config.discover_games(steam_root, policy["profiles"])
    if args.list:
        for game in games:
            print(f"{game['profile']}: {game['title']} — {game['game']}")
        if not games:
            print(
                "No supported games installed. Current profiles: "
                + ", ".join(p["id"] for p in policy["profiles"])
            )
        return
    if args.profile:
        games = [game for game in games if game["profile"] == args.profile]
    game = choose(
        games, "supported installed game (--profile)", lambda g: g["title"], interactive=interactive
    )
    accounts = steam_config.discover_accounts(steam_root)
    if args.account:
        accounts = [account for account in accounts if str(account["id"]) == args.account]
    preferred = [account for account in accounts if account["preferred"]]
    account = choose(
        accounts,
        "Steam account (--account)",
        lambda a: a["label"] + " (" + str(a["id"]) + ")",
        interactive=interactive,
        preferred=preferred[0] if len(preferred) == 1 else None,
    )
    state = (
        (args.state or Path.home() / ".local/share/bc250-fsr4/game-runtime").expanduser().resolve()
    )
    execute(args, game, account, steam_root, policy, state)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(
            "Setup cancelled. If interrupted during a write, use the printed recovery record."
        )
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        configparser.Error,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit("ERROR: " + str(error))
