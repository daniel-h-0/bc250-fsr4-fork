#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export an immutable Git commit as a reproducible, complete source archive."""

import argparse
import gzip
import hashlib
import io
import json
import posixpath
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SETUP_FILES = {
    "README.md",
    "THIRD_PARTY.md",
    "LICENSE.new-code",
    "install-v4.sh",
    "install-runtime.sh",
    "run-bc250-fsr4.sh",
    "setup-game.sh",
    "scripts/driver.py",
    "scripts/runtime.py",
    "scripts/runtime_bundle.py",
    "runtime/manifest.json",
    "runtime/launch.py",
    "runtime/patches/0001-pinned-upscaler-manifest.patch",
    "legacy/game-setup/recover.py",
    "legacy/game-setup/steam_config.py",
    "docs/games.md",
    "docs/game-troubleshooting.md",
    "docs/upgrading-v3.md",
}


def setup_files(files, commit):
    """Keep only installation/recovery inputs; link omitted evidence to the commit."""
    selected = {name: files[name] for name in sorted(SETUP_FILES)}
    base = "https://github.com/daniel-h-0/bc250-fsr4-fork/blob/" + commit + "/"
    for name, (data, mode) in selected.items():
        if not name.endswith(".md"):
            continue

        def link(match):
            label, target = match.groups()
            if "://" in target or target.startswith("#"):
                return match[0]
            path, _, anchor = target.partition("#")
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(name), path))
            if relative in selected:
                return match[0]
            return "[" + label + "](" + base + relative + ("#" + anchor if anchor else "") + ")"

        selected[name] = (
            re.sub(r"\[([^\]\n]*)\]\(([^)\n]+)\)", link, data.decode()).encode(),
            mode,
        )
    return selected


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def snapshot(root, ref):
    """Read committed bytes, never working-tree files or ignored build output."""
    top = Path(git(root, "rev-parse", "--show-toplevel").decode().strip())
    if top.resolve() != root.resolve():
        raise RuntimeError("Run source-release.py from its own Git checkout.")
    commit = (
        git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    )
    records = git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    files = {}
    for record in filter(None, records):
        header, raw_path = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        path = raw_path.decode("utf-8")
        parts = PurePosixPath(path)
        if (
            parts.is_absolute()
            or ".." in parts.parts
            or kind != "blob"
            or mode not in {"100644", "100755"}
        ):
            raise RuntimeError("Unsupported source entry: " + path)
        if path == "source-snapshot.json":
            raise RuntimeError("source-snapshot.json is generated and must not be committed.")
        files[path] = (git(root, "cat-file", "blob", oid), int(mode[-3:], 8))
    return commit, files


def create_archive(root, output, ref=None, *, setup=False):
    if ref is None:
        if git(root, "status", "--porcelain", "--untracked-files=normal").strip():
            raise RuntimeError(
                "Commit or stash source changes first; --ref exports an explicitly selected commit."
            )
        ref = "HEAD"
    commit, files = snapshot(root, ref)
    manifest = json.loads(files["v4/manifest.json"][0])
    version = manifest["version"]
    if not version or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in version
    ):
        raise RuntimeError("Invalid source version in manifest.")
    name = f"bc250-fsr4-v{version}-source-{commit[:12]}"
    if setup:
        version = json.loads(files["runtime/manifest.json"][0])["release"]["version"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+", version):
            raise RuntimeError("Invalid runtime version.")
        name = "bc250-fsr4-setup-" + version
        files = setup_files(files, commit)
    metadata = {
        "schema": 1,
        "commit": commit,
        "version": version,
        "files": {
            path: {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
            for path, (data, mode) in sorted(files.items())
        },
    }
    files["source-snapshot.json"] = ((json.dumps(metadata, indent=2) + "\n").encode(), 0o644)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (name + ".tar.gz")
    checksum = output / (archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise RuntimeError("Release output already exists; use another output directory.")
    # Both tar metadata and the gzip header are deterministic for a given commit.
    with tempfile.TemporaryDirectory(prefix=".source-release-", dir=output) as tmp:
        staged = Path(tmp) / archive.name
        with staged.open("wb") as raw:
            with gzip.GzipFile(
                fileobj=raw, filename="", mode="wb", mtime=0, compresslevel=9
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as bundle:
                    for path, (data, mode) in sorted(files.items()):
                        member = tarfile.TarInfo(f"{name}/{path}")
                        member.size = len(data)
                        member.mode = mode
                        member.mtime = 0
                        bundle.addfile(member, io.BytesIO(data))
        with staged.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        staged_checksum = Path(tmp) / checksum.name
        staged_checksum.write_text(digest + "  " + archive.name + "\n")
        # Exclusive publication refuses concurrent writers rather than replacing assets.
        try:
            archive.hardlink_to(staged)
            try:
                checksum.hardlink_to(staged_checksum)
            except OSError:
                archive.unlink()
                raise
        except FileExistsError as error:
            raise RuntimeError(
                "Release output was created concurrently; nothing replaced."
            ) from error
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref", help="Exact commit/tag to export; otherwise requires a clean checkout of HEAD"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "dist/source")
    parser.add_argument(
        "--setup", action="store_true", help="Export the small end-user setup bundle"
    )
    args = parser.parse_args()
    print(create_archive(ROOT, args.output.expanduser().resolve(), args.ref, setup=args.setup))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
