#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inspect or restore the packaged BC250 FSR4 v4 driver."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

METADATA_PATH = Path("/usr/share/bc250-fsr4-v4/system.json")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["status", "rollback", "check-update"], nargs="?", default="status"
    )
    parser.add_argument(
        "--json", action="store_true", help="Compatibility option; status is always JSON"
    )
    args = parser.parse_args(argv)
    metadata = json.loads(METADATA_PATH.read_text())
    active = (
        Path(metadata["library"]).is_file()
        and sha(metadata["library"]) == metadata["driver_sha256"]
    )
    if args.action == "rollback":
        if os.geteuid() != 0:
            raise RuntimeError("Run sudo bc250-fsr4 rollback to restore the recorded package.")
        if subprocess.run(["pgrep", "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError("Exit Steam and games before rollback.")
        if not active:
            raise RuntimeError(
                "Driver changed since v4 installation; refusing to downgrade a newer or locally modified package."
            )
        package = Path(metadata["rollback_package"])
        if sha(package) != metadata["base_package_sha256"]:
            raise RuntimeError("Rollback package SHA256 mismatch.")
        with tempfile.TemporaryDirectory(prefix="bc250-fsr4-rollback-", dir="/var/tmp") as tmp:
            target = Path(tmp) / "base.pkg.tar.zst"
            shutil.copy2(package, target)
            subprocess.run(["pacman", "-U", str(target)], check=True)
        print("Previous RADV restored; lib32 remains untouched. Restart Steam/games.")
        print("Optional: sudo pacman -R bc250-fsr4-v4 to remove the now-inactive status package.")
        return 0
    print(
        json.dumps(
            {"active": active, "drivers": {metadata["library"]: active}, **metadata}, indent=2
        )
    )
    if not active:
        print(
            "The distribution or another install replaced v4. Rebuild/revalidate for the current Mesa version; do not copy an old driver over an updated package.",
            file=sys.stderr,
        )
    return 0 if active or args.action == "check-update" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
