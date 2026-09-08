# SPDX-License-Identifier: MIT
"""Safe data-only tar extraction, including distribution Python 3.11 builds."""

import os
import posixpath
import shutil
import tarfile
from pathlib import Path, PurePosixPath


def extractall(archive, destination, members=None):
    members = list(archive.getmembers() if members is None else members)
    if hasattr(tarfile, "data_filter"):
        archive.extractall(destination, members=members, filter="data")
    else:
        extract_legacy(archive, destination, members)


def extract_legacy(archive, destination, members):
    """Create data first and links last; never write through archive-created links."""
    root = Path(destination).resolve(strict=True)
    entries = {}
    for member in members:
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts:
            raise RuntimeError("Unsafe archive member: " + member.name)
        if str(name) == "." and member.isdir():
            continue
        if str(name) == "." or not (
            member.isfile() or member.isdir() or member.issym() or member.islnk()
        ):
            raise RuntimeError("Unsupported archive member: " + member.name)
        if name in entries and not (member.isdir() and entries[name].isdir()):
            raise RuntimeError("Duplicate archive member: " + member.name)
        entries[name] = member
        if member.issym() or member.islnk():
            target = PurePosixPath(member.linkname)
            combined = str(name.parent / target) if member.issym() else str(target)
            normalized = PurePosixPath(posixpath.normpath(combined))
            if target.is_absolute() or ".." in normalized.parts:
                raise RuntimeError("Archive link escapes extraction: " + member.name)
    for name, member in entries.items():
        for parent in name.parents:
            if parent in entries and not entries[parent].isdir():
                raise RuntimeError("Archive writes through a non-directory: " + str(parent))
        path = root / name
        for existing in (path, *path.parents):
            if existing == root:
                break
            if existing.is_symlink():
                raise RuntimeError("Extraction would follow an existing symlink: " + str(existing))
        if path.exists() and not (member.isdir() and path.is_dir()):
            raise RuntimeError("Extraction would replace an existing path: " + str(path))
    # New directories are private until the entire extraction succeeds.
    directories = set()
    for name, member in entries.items():
        path = root / name
        directories.update(
            parent for parent in path.parents if parent != root and parent.is_relative_to(root)
        )
        if member.isdir():
            directories.add(path)
    new_directories = {directory for directory in directories if not directory.exists()}
    for directory in sorted(new_directories, key=lambda p: len(p.parts)):
        directory.mkdir(mode=0o700, exist_ok=True)
    for name, member in entries.items():
        if member.isfile():
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output, archive.extractfile(member) as source:
                shutil.copyfileobj(source, output)
                os.fchmod(output.fileno(), member.mode & 0o755)
    pending = {name: member for name, member in entries.items() if member.islnk()}
    while pending:
        progress = False
        for name, member in list(pending.items()):
            target = root / posixpath.normpath(member.linkname)
            if not target.resolve().is_relative_to(root):
                raise RuntimeError("Archive hard link target escapes extraction: " + str(name))
            if target.is_file() and not target.is_symlink():
                os.link(target, root / name, follow_symlinks=False)
                del pending[name]
                progress = True
        if not progress:
            raise RuntimeError("Archive hard link has no regular-file target.")
    for name, member in entries.items():
        if member.issym():
            (root / name).symlink_to(member.linkname)
    for name, member in entries.items():
        if member.issym() and not (root / name).resolve().is_relative_to(root):
            raise RuntimeError("Archive link chain escapes extraction: " + str(name))
    for directory in new_directories:
        directory.chmod(0o755)
