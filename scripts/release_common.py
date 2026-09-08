#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inventory source files consistently in checkouts and extracted releases."""

import json
import subprocess
from pathlib import Path

from build import digest

SOURCE_DIRECTORIES = {".github", "scripts", "v4", "docs", "tests", "legacy", "runtime"}
SOURCE_FILES = {
    ".dockerignore",
    ".gitignore",
    ".editorconfig",
    "Dockerfile",
    "LICENSE.new-code",
    "README.md",
    "THIRD_PARTY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "requirements-build.txt",
    "requirements-dev.txt",
    "build-anywhere.sh",
    "build-bc250.sh",
    "check.sh",
    "install-v4.sh",
    "run-bc250-fsr4.sh",
    "setup.sh",
    "setup-game.sh",
    "install-runtime.sh",
    "bc250-fsr4",
}
GENERATED_ROOTS = {
    ".git",
    ".venv",
    ".work",
    ".build",
    ".cts-out",
    ".cts-icd",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "lib",
    "licenses",
    "build-provenance.json",
    "release.json",
}


def validate_paths(root, names):
    result = sorted(set(names))
    for name in result:
        relative = Path(name)
        path = root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise RuntimeError("Source inventory contains an unsafe or missing file: " + name)
    return result


def source_files(root):
    """Return regular source files relative to an exact repository/archive root."""
    root = Path(root).resolve()
    if (root / ".git").exists():
        top = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True
        ).strip()
        if Path(top).resolve() != root:
            raise RuntimeError("Source root does not match the Git worktree root.")
        output = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
        return validate_paths(root, [name.decode() for name in output.split(b"\0") if name])
    snapshot = root / "source-snapshot.json"
    if snapshot.exists():
        value = json.loads(snapshot.read_text())
        if value["schema"] != 1:
            raise RuntimeError("Unsupported source snapshot schema.")
        paths = validate_paths(root, [*value["files"], "source-snapshot.json"])
        for name, metadata in value["files"].items():
            if digest(root / name) != metadata["sha256"]:
                raise RuntimeError("Source snapshot file changed: " + name)
            executable = bool((root / name).stat().st_mode & 0o111)
            if executable != (str(metadata["mode"]) in ("100755", "755", "493")):
                raise RuntimeError("Source snapshot file mode changed: " + name)
        return paths
    # GitHub's automatic source downloads have no .git or project snapshot.
    # Limit inventory to project sources, refusing unknown roots rather than
    # accidentally adding local output, credentials, or unrelated files.
    names = []
    for child in root.iterdir():
        if child.name in SOURCE_FILES:
            names.append(child.name)
        elif child.name in SOURCE_DIRECTORIES:
            for path in child.rglob("*"):
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                if path.is_file() or path.is_symlink():
                    names.append(str(path.relative_to(root)))
        elif child.name not in GENERATED_ROOTS:
            raise RuntimeError("Unrecognized source-tree entry: " + child.name)
    return validate_paths(root, names)


def source_identity(root):
    """Describe the source revision without inventing Git history for archives."""
    root = Path(root).resolve()
    if (root / ".git").exists():
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        changed = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"], text=True
        )
        return {"kind": "git", "commit": commit, "dirty": bool(changed)}
    if (root / "source-snapshot.json").exists():
        snapshot = json.loads((root / "source-snapshot.json").read_text())
        return {"kind": "source-snapshot", "commit": snapshot["commit"], "dirty": False}
    return {"kind": "source-tree", "commit": None, "dirty": None}
