#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install the pinned GE-Proton dependency without changing any game prefix."""

import ctypes
import errno
import fcntl
import hashlib
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

import driver

MAX_MEMBERS = 100_000
MAX_UNPACKED_BYTES = 4 * 1024**3
AT_FDCWD = -100
RENAME_NOREPLACE = 1


def validate_policy(policy):
    """Require a bounded, explicitly pinned runtime policy."""
    if policy.get("schema") != 1 or not re.fullmatch(r"GE-Proton[A-Za-z0-9._-]+", policy["name"]):
        raise RuntimeError("Unsupported GE-Proton policy.")
    if not re.fullmatch(r"[0-9a-f]{64}", policy["sha256"]):
        raise RuntimeError("GE-Proton archive needs an exact SHA256 pin.")
    if not isinstance(policy["size"], int) or not 0 < policy["size"] <= MAX_UNPACKED_BYTES:
        raise RuntimeError("Invalid GE-Proton archive size.")
    if not policy["url"].startswith("https://"):
        raise RuntimeError("GE-Proton downloads require HTTPS.")
    if not {
        "proton",
        "compatibilitytool.vdf",
        "version",
        "files/bin/wine",
        "protonfixes/upscalers.py",
    }.issubset(policy["files"]):
        raise RuntimeError("GE-Proton policy is missing critical runtime file pins.")
    for name, expected in policy["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise RuntimeError("Unsafe GE-Proton policy path: " + name)
        if not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]):
            raise RuntimeError("Invalid GE-Proton file pin: " + name)


def validate_install(root, policy):
    """Verify the pinned critical files of an existing or newly extracted runtime.

    This is deliberately not a full-tree attestation of a pre-existing runtime.
    The complete official archive is checked before a new installation. GE owns
    subsequent provider downloads into game prefixes; no provider is bundled here.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(
            "Existing GE-Proton path is not a regular runtime directory; preserving it."
        )
    for name, expected in policy["files"].items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or driver.digest(path) != expected["sha256"]
            or bool(path.stat().st_mode & 0o111) != expected["executable"]
        ):
            raise RuntimeError(
                "Existing GE-Proton runtime differs from the pinned release; preserving it: " + name
            )
    version = (root / "version").read_text().split()
    if len(version) != 2 or not version[0].isdigit() or version[1] != policy["version"]:
        raise RuntimeError("GE-Proton version does not match the selected runtime.")
    text = re.sub(r"//[^\n]*", "", (root / "compatibilitytool.vdf").read_text())
    if not re.search(
        r'"compat_tools"\s*\{\s*"' + re.escape(policy["name"]) + r'"\s*\{', text
    ) or not re.search(r'"install_path"\s*"\."', text):
        raise RuntimeError("GE-Proton manifest does not register the expected native Steam tool.")
    with (root / "files/bin/wine").open("rb") as stream:
        header = stream.read(20)
    if header[:5] != b"\x7fELF\x02" or header[18:20] != b"\x3e\x00":
        raise RuntimeError("GE-Proton Wine loader is not an x86_64 ELF binary.")
    return root


def download_archive(state, policy):
    cache = state / "downloads"
    if cache.is_symlink():
        raise RuntimeError("GE-Proton download cache is a symlink; preserving it.")
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (policy["name"] + "-" + policy["sha256"][:16] + ".tar.gz")
    if archive.exists() or archive.is_symlink():
        if (
            archive.is_symlink()
            or not archive.is_file()
            or archive.stat().st_size != policy["size"]
            or driver.digest(archive) != policy["sha256"]
        ):
            raise RuntimeError("Existing GE-Proton download differs from the pin; preserving it.")
        return archive
    descriptor, temporary_name = tempfile.mkstemp(prefix=".proton-", suffix=".partial", dir=cache)
    temporary = Path(temporary_name)
    try:
        checksum = hashlib.sha256()
        length = 0
        with os.fdopen(descriptor, "wb") as output:
            with urllib.request.urlopen(policy["url"], timeout=90) as response:
                while block := response.read(1024 * 1024):
                    length += len(block)
                    if length > policy["size"]:
                        raise RuntimeError("GE-Proton download exceeds the pinned size.")
                    output.write(block)
                    checksum.update(block)
            output.flush()
            os.fsync(output.fileno())
        if length != policy["size"] or checksum.hexdigest() != policy["sha256"]:
            raise RuntimeError("GE-Proton download does not match its pinned size and SHA256.")
        # Refuse a concurrently created cache entry rather than replacing it.
        os.link(temporary, archive)
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def archive_members(bundle, name):
    """Preflight paths and links within exactly one expected runtime root."""
    members = []
    names = set()
    expanded = 0
    for member in bundle:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != name
            or str(path) in names
        ):
            raise RuntimeError("Unsafe or duplicate GE-Proton archive path: " + member.name)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise RuntimeError("Unsupported GE-Proton archive entry: " + member.name)
        if len(path.parts) == 1 and not member.isdir():
            raise RuntimeError("GE-Proton archive root must be a directory.")
        if member.issym() or member.islnk():
            link = PurePosixPath(member.linkname)
            target = link if member.islnk() else path.parent / link
            target = PurePosixPath(posixpath.normpath(str(target)))
            if (
                link.is_absolute()
                or not target.parts
                or target.parts[0] != name
                or ".." in target.parts
            ):
                raise RuntimeError(
                    "GE-Proton archive link escapes its runtime root: " + member.name
                )
        names.add(str(path))
        expanded += member.size
        members.append(member)
        if member.size < 0 or expanded > MAX_UNPACKED_BYTES or len(members) > MAX_MEMBERS:
            raise RuntimeError("GE-Proton archive exceeds extraction limits.")
    return members, expanded


def extract_archive(archive, destination, policy):
    with tarfile.open(archive, "r:gz") as bundle:
        members, expanded = archive_members(bundle, policy["name"])
        if shutil.disk_usage(destination).free < expanded:
            raise RuntimeError("Not enough space to extract the pinned GE-Proton runtime.")
        bundle.extractall(destination, members=members, filter="data")
    root = destination / policy["name"]
    if set(destination.iterdir()) != {root}:
        raise RuntimeError("Expected one GE-Proton runtime directory.")
    for path in root.rglob("*"):
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Extracted GE-Proton link escapes its runtime root.")
    return validate_install(root, policy)


def promote_runtime(source, destination):
    """Atomically promote on Linux without replacing even an empty directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise RuntimeError("This Linux runtime lacks atomic no-replace directory promotion.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if (
        rename(AT_FDCWD, os.fsencode(source), AT_FDCWD, os.fsencode(destination), RENAME_NOREPLACE)
        != 0
    ):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise RuntimeError("GE-Proton destination appeared during setup; preserving it.")
        raise OSError(error, os.strerror(error), str(destination))


def ensure_proton(steam_root: Path, state: Path, policy: dict) -> Path:
    """Reuse or install the v4/proton.json runtime; never replace an existing tree.

    The caller owns Steam shutdown and the wider setup transaction. Installation
    only adds this compatibility tool; it never launches Steam, runs Proton, or
    changes provider caches or game prefixes.
    """
    validate_policy(policy)
    steam_root = Path(steam_root).expanduser().resolve()
    if not steam_root.is_dir():
        raise RuntimeError("The selected native Steam root does not exist.")
    tools = steam_root / "compatibilitytools.d"
    if tools.is_symlink():
        raise RuntimeError("Steam compatibilitytools.d is a symlink; preserving it.")
    target = tools / policy["name"]
    if target.exists() or target.is_symlink():
        return validate_install(target, policy)
    tools.mkdir(exist_ok=True)
    lock = os.open(tools / ".bc250-fsr4-proton.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Another GE-Proton setup is already running for this Steam root."
            ) from error
        if target.exists() or target.is_symlink():
            return validate_install(target, policy)
        archive = download_archive(Path(state).expanduser().resolve(), policy)
        with tempfile.TemporaryDirectory(prefix=".bc250-proton-stage-", dir=tools) as temporary:
            root = extract_archive(archive, Path(temporary), policy)
            promote_runtime(root, target)
        return target
    finally:
        os.close(lock)
