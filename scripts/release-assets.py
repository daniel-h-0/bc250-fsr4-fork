#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stage selected release archives and one checksum list, without publishing."""

import argparse
import hashlib
import re
import shutil
import tempfile
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(output, dll, source, driver=None, client=None):
    """Keep original build artifacts/sidecars; expose one archive per role."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Staging destination already exists: " + str(output))
    selections = {"dll": Path(dll), "source": Path(source)}
    if driver is not None:
        selections["driver"] = Path(driver)
    if client is not None:
        selections["client"] = Path(client)
    expected_suffixes = {
        "dll": ".zip",
        "source": ".tar.gz",
        "driver": ".tar.gz",
        "client": ".tar.gz",
    }
    names = set()
    records = []
    for role, path in selections.items():
        if (
            path.is_symlink()
            or not path.is_file()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path.name)
            or not path.name.endswith(expected_suffixes[role])
            or path.name in names
        ):
            raise ValueError("Invalid or duplicate " + role + " archive: " + str(path))
        names.add(path.name)
        checksum = Path(str(path) + ".sha256")
        if checksum.is_symlink() or not checksum.is_file():
            raise ValueError("Missing regular archive checksum: " + str(checksum))
        actual = digest(path)
        if checksum.read_text() != actual + "  " + path.name + "\n":
            raise ValueError("Archive checksum mismatch: " + str(path))
        records.append((path, actual))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-assets-", dir=output.parent) as temp:
        prepared = Path(temp) / "assets"
        prepared.mkdir()
        for path, expected in records:
            destination = prepared / path.name
            shutil.copyfile(path, destination)
            if digest(destination) != expected:
                raise ValueError("Archive changed while staging: " + str(path))
        (prepared / "SHA256SUMS").write_text(
            "".join(
                f"{checksum}  {path.name}\n"
                for path, checksum in sorted(records, key=lambda item: item[0].name)
            )
        )
        # mkdir claims the destination without replacing an existing directory.
        output.mkdir()
        try:
            for path in prepared.iterdir():
                path.rename(output / path.name)
        except BaseException:
            shutil.rmtree(output)
            raise
    return sorted(path.name for path in output.iterdir())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True, help="One packaged DLL ZIP")
    parser.add_argument(
        "--source", type=Path, required=True, help="Complete source snapshot tar.gz"
    )
    parser.add_argument("--driver", type=Path, help="Optional separately qualified driver tar.gz")
    parser.add_argument("--client", type=Path, help="Optional project OptiScaler Client tar.gz")
    parser.add_argument("--output", type=Path, required=True, help="New upload staging directory")
    args = parser.parse_args()
    for name in stage(args.output, args.dll, args.source, args.driver, args.client):
        print(args.output / name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit("ERROR: " + str(error)) from error
