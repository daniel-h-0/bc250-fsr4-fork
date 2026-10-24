#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ingest public FidelityFX SDK drops: allowlisted files only, checked by hash and license line.

Only the SDK's signed upscaler DLL and the SDK license file are accepted. A drop is usable only
when allowlist.json records its GPUOpen tag, commit, SHA256 and the license line that puts the
DLL under MIT. Any other file, tag, commit, host or license is refused, whatever its name.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
SOURCE_TREE = ROOT.parents[1]
ALLOWLIST = ROOT / "allowlist.json"
REPOSITORY = "GPUOpen-LibrariesAndSDKs/FidelityFX-SDK"
REMOTE = "https://github.com/" + REPOSITORY
RAW_HOST = "raw.githubusercontent.com"
API_HOST = "api.github.com"
MAX_LISTING = 1 << 20
DLL_PATH = "Kits/FidelityFX/signedbin/amd_fidelityfx_upscaler_dx12.dll"
DLL_NAME = PurePosixPath(DLL_PATH).name
LICENSE_PATH = "docs/license.md"
LICENSE_NAME = "license.md"
MAX_PROPOSED_DLL = 256 << 20
MAX_PROPOSED_LICENSE = 4 << 20
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
GRANTS = ("mit-list", "whole-file-mit")
GRANT_FIELDS = ("grant", "line_number", "line")
LIST_PREAMBLE = "The following license applies to all files except as noted below."
LIST_HEADER = "These source files"
LIST_FOOTER = "are subject to the following license:"
BULLET = re.compile(r"- `([^`]+)`")
PERMISSION = "Permission is hereby granted"
MIT_TERMS = (
    PERMISSION + ", free of charge, to any person obtaining a copy of this software and "
    "associated documentation files",
    "to deal in the Software without restriction, including without limitation the rights to "
    "use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software",
    "The above copyright notice and this permission notice shall be included in all copies or "
    "substantial portions of the Software.",
)
RESTRICTIONS = (
    "reverse engineer",
    "decompil",
    "disassembl",
    "binary form only",
    "except as noted",
    "these source files",
    "subject to the following license",
)


class Refused(ValueError):
    """The input is not an allowlisted public SDK file."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def identity(data):
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
    return {"sha256": sha256(data), "git_blob": blob, "bytes": len(data)}


def squash(text):
    for curly, plain in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(curly, plain)
    return re.sub(r"\s+", "", text)


def has_mit_terms(text):
    return all(squash(term) in squash(text) for term in MIT_TERMS)


def plain(text):
    """ASCII letters and digits only, lower case: spacing, hyphens, punctuation and invisible
    format characters (soft hyphen, zero-width space) cannot split a word the gate looks for."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in text if c.isascii() and c.isalnum())


def restrictions(text):
    words = plain(text)
    return [marker for marker in RESTRICTIONS if plain(marker) in words]


def mentions(text, path):
    """How often the license text names the file of `path` (any spacing or punctuation)."""
    return plain(text).count(plain(PurePosixPath(path).name))


def listed_path(entry):
    path = PurePosixPath(entry.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise Refused("Unsafe path in the license's file list: " + entry)
    return str(path)


def license_grant(data, path=DLL_PATH):
    """Return the license line that puts `path` under MIT, or refuse."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Refused("The license file is not UTF-8") from error
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    stripped = [line.strip() for line in lines]
    headers = [i for i, line in enumerate(stripped) if line == LIST_HEADER]
    footers = [i for i, line in enumerate(stripped) if line == LIST_FOOTER]
    if not headers and not footers:
        return whole_file_grant(lines, text, path)
    if len(headers) != 1 or len(footers) != 1 or footers[0] < headers[0]:
        raise Refused("Unrecognized license layout: expected exactly one MIT file list")
    if stripped[0] != LIST_PREAMBLE:
        raise Refused("The license's default terms do not apply 'except as noted below'")
    start, end = headers[0], footers[0]
    named = []
    for index in range(start + 1, end):
        if not stripped[index]:
            continue
        match = BULLET.fullmatch(lines[index].rstrip())
        if not match:
            raise Refused(f"Unrecognized line {index + 1} in the license's MIT file list")
        if listed_path(match.group(1)) == path:
            named.append(index)
    terms = "\n".join(lines[end + 1 :])
    if not has_mit_terms(terms) or restrictions(terms):
        raise Refused("The license's file list is not followed by plain MIT terms")
    if len(named) != 1:
        raise Refused(f"The license's MIT file list does not name {path} exactly once")
    if mentions(text, path) != 1:
        raise Refused(f"The license names {PurePosixPath(path).name} outside its MIT file entry")
    return {"grant": "mit-list", "line_number": named[0] + 1, "line": lines[named[0]]}


def whole_file_grant(lines, text, path):
    found = restrictions(text)
    if found:
        raise Refused("The license restricts files beyond MIT terms: " + ", ".join(found))
    if mentions(text, path):
        raise Refused(f"A whole-file license that names {PurePosixPath(path).name} needs review")
    if not has_mit_terms(text):
        raise Refused("The license is neither an MIT file list nor a whole-file MIT license")
    grants = [i for i, line in enumerate(lines) if PERMISSION in line]
    if len(grants) != 1:
        raise Refused("Expected exactly one MIT permission line")
    return {"grant": "whole-file-mit", "line_number": grants[0] + 1, "line": lines[grants[0]]}


def pe_identity(data):
    """Structural checks of the signed x86-64 DLL. The SHA256 pin remains the identity."""
    try:
        if data[:2] != b"MZ":
            raise Refused("Not a PE image")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 4)[0] != 0x8664:
            raise Refused("Not an x86-64 PE image")
        optional = pe + 24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise Refused("Not a PE32+ image")
        if struct.unpack_from("<I", data, optional + 108)[0] < 5:
            raise Refused("PE image without export and certificate directories")
        count = struct.unpack_from("<H", data, pe + 6)[0]
        table = optional + struct.unpack_from("<H", data, pe + 20)[0]
        sections = [struct.unpack_from("<III", data, table + 40 * i + 12) for i in range(count)]

        def offset(rva):
            for start, size, raw in sections:
                if start <= rva < start + size:
                    return raw + rva - start
            raise Refused("PE directory outside the image")

        export = offset(struct.unpack_from("<I", data, optional + 112)[0])
        name_at = offset(struct.unpack_from("<I", data, export + 12)[0])
        name = data[name_at : data.index(b"\0", name_at)].decode("ascii")
        if name != DLL_NAME:
            raise Refused(f"The PE export name is {name!r}, not {DLL_NAME}")
        certificate, size = struct.unpack_from("<II", data, optional + 144)
        if not size or certificate + size > len(data):
            raise Refused("The DLL carries no Authenticode certificate table")
        length, revision, kind = struct.unpack_from("<IHH", data, certificate)
        if revision != 0x200 or kind != 2 or not 8 < length <= size:
            raise Refused("Unrecognized Authenticode certificate table")
    except Refused:
        raise
    except (struct.error, ValueError) as error:
        raise Refused("Malformed PE image") from error
    return {
        "machine": "x86-64",
        "format": "PE32+",
        "export_name": name,
        "certificate_table_bytes": size,
    }


def check_file_entry(entry, path, extra=()):
    keys = {"path", "sha256", "git_blob", "bytes", *extra}
    if not isinstance(entry, dict) or set(entry) != keys or entry["path"] != path:
        raise Refused("Drop records accept only " + path)
    if not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"]):
        raise Refused("Invalid SHA256 for " + path)
    if not isinstance(entry["git_blob"], str) or not COMMIT.fullmatch(entry["git_blob"]):
        raise Refused("Invalid git blob id for " + path)
    if type(entry["bytes"]) is not int or entry["bytes"] < 1:
        raise Refused("Invalid size for " + path)


def check_record(record):
    """Validate one drop record's format, without reading any file."""
    if not isinstance(record, dict) or set(record) != {"tag", "commit", "dll", "license"}:
        raise Refused("Malformed drop record")
    if not isinstance(record["tag"], str) or not TAG.fullmatch(record["tag"]):
        raise Refused("Invalid GPUOpen tag in drop record")
    if not isinstance(record["commit"], str) or not COMMIT.fullmatch(record["commit"]):
        raise Refused("Invalid commit for " + record["tag"])
    check_file_entry(record["dll"], DLL_PATH)
    check_file_entry(record["license"], LICENSE_PATH, GRANT_FIELDS)
    license = record["license"]
    line = license["line"]
    if type(license["line_number"]) is not int or license["line_number"] < 1:
        raise Refused("Invalid license line number for " + record["tag"])
    if not isinstance(line, str) or "\n" in line or "\r" in line:
        raise Refused("Invalid license line for " + record["tag"])
    if license["grant"] == "mit-list":
        match = BULLET.fullmatch(line.rstrip())
        if not match or listed_path(match.group(1)) != DLL_PATH:
            raise Refused(f"The license line for {record['tag']} does not name {DLL_PATH}")
    elif license["grant"] == "whole-file-mit":
        if PERMISSION not in line:
            raise Refused(f"The license line for {record['tag']} is not the MIT permission")
    else:
        raise Refused("Unknown license grant for " + record["tag"])
    return record


def check_allowlist(allowlist):
    if not isinstance(allowlist, dict) or set(allowlist) != {"schema", "repository", "drops"}:
        raise Refused("Malformed SDK drop allowlist")
    if allowlist["schema"] != 1 or allowlist["repository"] != REPOSITORY:
        raise Refused("The allowlist must be schema 1 for " + REPOSITORY)
    drops = allowlist["drops"]
    if not isinstance(drops, list) or not drops:
        raise Refused("The allowlist has no drops")
    for record in drops:
        check_record(record)
    if len({record["tag"] for record in drops}) != len(drops):
        raise Refused("Duplicate tag in the allowlist")
    return allowlist


def read_allowlist(path=ALLOWLIST):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise Refused("The allowlist must be a regular file")
    raw = path.read_bytes()
    return check_allowlist(json.loads(raw)), sha256(raw)


def lookup(allowlist, tag):
    for record in allowlist["drops"]:
        if record["tag"] == tag:
            return record
    raise Refused(
        f"{tag} is not an allowlisted FidelityFX SDK drop; draft its record with 'propose', "
        "read the license and add the record to dll/port/allowlist.json first"
    )


def verify_bytes(entry, data, label):
    actual = identity(data)
    for key in ("bytes", "sha256", "git_blob"):
        if actual[key] != entry[key]:
            raise Refused(f"{label}: {key} differs from the allowlist")


def verify_drop(record, dll, license):
    """Check both files against a record, re-derive the license line and check the PE."""
    verify_bytes(record["dll"], dll, record["dll"]["path"])
    verify_bytes(record["license"], license, record["license"]["path"])
    grant = license_grant(license, record["dll"]["path"])
    if grant != {key: record["license"][key] for key in GRANT_FIELDS}:
        raise Refused(f"The license line for {record['tag']} differs from the allowlist")
    return {"license_grant": grant, "pe": pe_identity(dll)}


def make_record(tag, commit, dll, license):
    grant = license_grant(license)
    pe_identity(dll)
    return check_record(
        {
            "tag": tag,
            "commit": commit,
            "dll": {"path": DLL_PATH, **identity(dll)},
            "license": {"path": LICENSE_PATH, **identity(license), **grant},
        }
    )


def allowlisted(data, allowlist=None, name="The DLL"):
    """Gate on bytes already read: the allowlist records whose DLL hash they match, or refuse."""
    digest = sha256(data)
    if allowlist is None:
        allowlist = read_allowlist()[0]
    matches = [record for record in allowlist["drops"] if record["dll"]["sha256"] == digest]
    if not matches:
        raise Refused(f"{name} (SHA256 {digest}) is not an allowlisted SDK upscaler DLL")
    pe_identity(data)
    return matches


def require_allowlisted(path, allowlist=None):
    """Gate for later stages (inventory, disassembly): refuse any non-allowlisted DLL bytes."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise Refused("The gate accepts only a regular file")
    return allowlisted(path.read_bytes(), allowlist, path.name)


def raw_url(commit, path):
    if not COMMIT.fullmatch(commit) or path not in (DLL_PATH, LICENSE_PATH):
        raise Refused("Only the SDK upscaler DLL and license are fetched, by commit")
    return f"https://{RAW_HOST}/{REPOSITORY}/{commit}/{urllib.parse.quote(path)}"


def check_url(url, commit):
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != RAW_HOST
        or not parts.path.startswith(f"/{REPOSITORY}/{commit}/")
        or parts.query
        or parts.fragment
    ):
        raise Refused(
            "Refusing a URL outside GPUOpen's FidelityFX SDK at the pinned commit: " + url
        )


def fetch(url, commit, limit):
    check_url(url, commit)
    request = urllib.request.Request(url, headers={"User-Agent": "bc250-fsr4-sdk-intake/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        check_url(response.geturl(), commit)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise Refused("Download exceeds its expected size: " + url)
    return data


def listing_url(commit, path):
    """GitHub's contents API for the directory that holds `path` at `commit` (metadata only)."""
    if not COMMIT.fullmatch(commit) or path not in (DLL_PATH, LICENSE_PATH):
        raise Refused("Only the SDK upscaler DLL and license are looked up, by commit")
    parent = urllib.parse.quote(str(PurePosixPath(path).parent))
    return f"https://{API_HOST}/repos/{REPOSITORY}/contents/{parent}?ref={commit}"


def check_listing_url(url, commit, path):
    expected = urllib.parse.urlsplit(listing_url(commit, path))
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment) != (
        "https",
        API_HOST,
        expected.path,
        expected.query,
        "",
    ):
        raise Refused("Refusing a GitHub API URL other than the pinned listing: " + url)


def listed_blob(data, path):
    """The git blob id and size GitHub lists for `path` in a contents-API directory listing."""
    try:
        entries = json.loads(data)
    except ValueError as error:
        raise Refused("GitHub's directory listing is not JSON") from error
    if not isinstance(entries, list):
        raise Refused("GitHub's answer is not a directory listing")
    found = [e for e in entries if isinstance(e, dict) and e.get("path") == path]
    if len(found) != 1:
        raise Refused(f"GitHub lists {len(found)} entries for {path} at the commit, expected one")
    entry = found[0]
    sha, size = entry.get("sha"), entry.get("size")
    if entry.get("type") != "file" or not isinstance(sha, str) or not COMMIT.fullmatch(sha):
        raise Refused(f"GitHub's entry for {path} is not a regular file blob")
    if type(size) is not int or size < 1:
        raise Refused(f"GitHub's entry for {path} has no valid size")
    return {"git_blob": sha, "bytes": size}


def remote_blob(commit, path):
    """GitHub's own blob id and size of `path` at `commit`. Only the parent directory's listing
    is requested (a few kilobytes); the file itself is not downloaded."""
    url = listing_url(commit, path)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bc250-fsr4-sdk-intake/1",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        check_listing_url(response.geturl(), commit, path)
        data = response.read(MAX_LISTING + 1)
    if len(data) > MAX_LISTING:
        raise Refused("GitHub's directory listing exceeds its expected size")
    return listed_blob(data, path)


def verify_remote_blobs(record, lookup=remote_blob):
    """Require GitHub's blob id and size of both files at the pinned commit to equal the record.

    This ties the record, and so every cache hit checked against it, to GitHub's own tree."""
    for key in ("dll", "license"):
        entry = record[key]
        remote = lookup(record["commit"], entry["path"])
        for field in ("git_blob", "bytes"):
            if remote[field] != entry[field]:
                raise Refused(
                    f"{record['tag']} {entry['path']}: GitHub's {field} at {record['commit']} "
                    "differs from the allowlist"
                )


def resolve_tag(tag):
    if not TAG.fullmatch(tag):
        raise Refused("Invalid GPUOpen tag: " + tag)
    ref = "refs/tags/" + tag
    result = subprocess.run(
        ["git", "-c", "credential.helper=", "ls-remote", "--tags", REMOTE, ref, ref + "^{}"],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    )
    if result.returncode:
        raise RuntimeError("git ls-remote failed: " + result.stderr.strip())
    refs = {}
    for line in result.stdout.splitlines():
        commit, _, name = line.partition("\t")
        refs[name] = commit
    commit = refs.get(ref + "^{}", refs.get(ref))
    if commit is None:
        raise Refused(f"{REPOSITORY} has no tag {tag}")
    if not COMMIT.fullmatch(commit):
        raise Refused(f"Unexpected object id for {tag}: {commit}")
    return commit


def outside_source(path):
    resolved = Path(path).resolve()
    if resolved == SOURCE_TREE or SOURCE_TREE in resolved.parents:
        raise ValueError("SDK caches and drop outputs must be outside the source tree")
    return resolved


class Cache:
    """Content-addressed store <root>/sha256/<digest>; every read is re-hashed."""

    def __init__(self, root):
        self.root = outside_source(root)
        self.objects = self.root / "sha256"

    def path(self, digest):
        if not SHA256.fullmatch(digest):
            raise Refused("Invalid cache digest")
        return self.objects / digest

    def get(self, digest):
        path = self.path(digest)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise Refused("Cache objects must be regular files: " + str(path))
        if not path.exists():
            return None
        data = path.read_bytes()
        if sha256(data) != digest:
            raise Refused("Corrupt cache object (remove it): " + str(path))
        return data

    def put(self, data):
        digest = sha256(data)
        if self.get(digest) is None:
            self.objects.mkdir(parents=True, exist_ok=True)
            stream = tempfile.NamedTemporaryFile(dir=self.objects, prefix=".part-", delete=False)
            try:
                with stream:
                    stream.write(data)
                os.chmod(stream.name, 0o444)
                os.replace(stream.name, self.path(digest))
            except BaseException:
                Path(stream.name).unlink(missing_ok=True)
                raise
        return self.path(digest)


def obtain(cache, record, key, offline, fetcher):
    entry = record[key]
    data = cache.get(entry["sha256"])
    source = "cache"
    if data is None:
        if offline:
            raise Refused(f"{entry['path']} of {record['tag']} is not in the cache (offline)")
        data = fetcher(raw_url(record["commit"], entry["path"]), record["commit"], entry["bytes"])
        source = "network"
    verify_bytes(entry, data, f"{record['tag']} {entry['path']}")
    if source == "network":
        cache.put(data)
    return data, source


def ingest(
    tag,
    cache_dir,
    output_dir,
    *,
    offline=False,
    fetcher=fetch,
    resolver=resolve_tag,
    blob_lookup=remote_blob,
    allowlist_path=ALLOWLIST,
):
    """Verify an allowlisted drop and write <output>/<tag>/ with the DLL, license and intake.

    Online, the tag must still resolve to the recorded commit and GitHub's blob id and size of
    both files at that commit must equal the record, before any file is read or fetched."""
    allowlist, allowlist_sha256 = read_allowlist(allowlist_path)
    record = lookup(allowlist, tag)
    cache = Cache(cache_dir)
    output = outside_source(output_dir)
    destination = output / tag
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not offline:
        remote = resolver(tag)
        if remote != record["commit"]:
            raise Refused(f"{tag} now resolves to {remote}; the allowlist pins {record['commit']}")
        verify_remote_blobs(record, blob_lookup)
    dll, dll_source = obtain(cache, record, "dll", offline, fetcher)
    license, license_source = obtain(cache, record, "license", offline, fetcher)
    intake = {
        "schema": 1,
        "repository": REPOSITORY,
        "record": record,
        "allowlist_sha256": allowlist_sha256,
        "tag_verified_online": not offline,
        "blobs_verified_online": not offline,
        **verify_drop(record, dll, license),
        "files": {DLL_NAME: sha256(dll), LICENSE_NAME: sha256(license)},
    }
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{tag}-", dir=output))
    try:
        (staging / DLL_NAME).write_bytes(dll)
        (staging / LICENSE_NAME).write_bytes(license)
        (staging / "intake.json").write_text(json.dumps(intake, indent=2) + "\n")
        staging.chmod(0o755)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return intake, {"dll": dll_source, "license": license_source}


def propose(tag, cache_dir, *, fetcher=fetch, resolver=resolve_tag):
    """Draft a record for a GPUOpen tag. It is printed for review, never added to the allowlist."""
    if not TAG.fullmatch(tag):
        raise Refused("Invalid GPUOpen tag: " + tag)
    cache = Cache(cache_dir)
    commit = resolver(tag)
    license = fetcher(raw_url(commit, LICENSE_PATH), commit, MAX_PROPOSED_LICENSE)
    license_grant(license)
    dll = fetcher(raw_url(commit, DLL_PATH), commit, MAX_PROPOSED_DLL)
    record = make_record(tag, commit, dll, license)
    cache.put(license)
    cache.put(dll)
    return record


def seed(cache_dir, files, allowlist_path=ALLOWLIST):
    """Copy local files into the cache. Only bytes recorded in the allowlist are accepted."""
    allowlist = read_allowlist(allowlist_path)[0]
    allowed = {}
    for record in allowlist["drops"]:
        for key in ("dll", "license"):
            allowed.setdefault(record[key]["sha256"], []).append(f"{record['tag']} {key}")
    cache = Cache(cache_dir)
    accepted = []
    for path in map(Path, files):
        if path.is_symlink() or not path.is_file():
            raise Refused("Seed files must be regular files: " + str(path))
        data = path.read_bytes()
        digest = sha256(data)
        if digest not in allowed:
            raise Refused(f"{path} (SHA256 {digest}) is not an allowlisted SDK drop file")
        accepted.append((path, data, digest))
    for _, data, _ in accepted:
        cache.put(data)
    return [(str(path), digest, allowed[digest]) for path, _, digest in accepted]


def check_tree(cache_dir=None):
    """Offline checks: allowlist format, the RC11 build pin and notice, optional cached drops."""
    allowlist = read_allowlist()[0]
    drops = allowlist["drops"]
    manifest_path = SOURCE_TREE / "dll/manifest.json"
    summary = f"{len(drops)} allowlisted SDK drops ({drops[0]['tag']}..{drops[-1]['tag']})"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        record = next((r for r in drops if r["dll"]["sha256"] == manifest["sdk_sha256"]), None)
        if record is None:
            raise Refused("The DLL manifest's SDK input is not an allowlisted drop")
        if manifest["sdk_url"] != raw_url(record["commit"], DLL_PATH):
            raise Refused("The DLL manifest's SDK URL differs from its allowlist record")
        notice = (SOURCE_TREE / "dll/notices/AMD-SDK-LICENSE.md").read_bytes()
        verify_bytes(record["license"], notice, "dll/notices/AMD-SDK-LICENSE.md")
        if license_grant(notice) != {key: record["license"][key] for key in GRANT_FIELDS}:
            raise Refused("The shipped AMD SDK notice no longer lists the DLL under MIT")
        summary += (
            f"; RC11 input and notice match {record['tag']} line {record['license']['line_number']}"
        )
    if cache_dir is not None:
        cache = Cache(cache_dir)
        for record in drops:
            dll = obtain(cache, record, "dll", True, None)[0]
            verify_drop(record, dll, obtain(cache, record, "license", True, None)[0])
        summary += "; every cached drop re-verified"
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("check", help="validate the allowlist (and cached drops)")
    command.add_argument("--cache", type=Path)
    command = commands.add_parser("seed", help="add local copies whose hashes are allowlisted")
    command.add_argument("--cache", type=Path, required=True)
    command.add_argument("files", type=Path, nargs="+")
    command = commands.add_parser("ingest", help="verify an allowlisted drop into OUTPUT/TAG")
    command.add_argument("--tag", required=True)
    command.add_argument("--cache", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument(
        "--offline", action="store_true", help="cache only; no tag or GitHub blob check"
    )
    command = commands.add_parser("propose", help="print a draft record for a new tag")
    command.add_argument("--tag", required=True)
    command.add_argument("--cache", type=Path, required=True)
    command = commands.add_parser("gate", help="exit 0 only for an allowlisted SDK DLL")
    command.add_argument("dll", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check_tree(args.cache))
        elif args.command == "seed":
            for path, digest, labels in seed(args.cache, args.files):
                print(f"{digest}  {path}  ({', '.join(labels)})")
        elif args.command == "ingest":
            intake, sources = ingest(args.tag, args.cache, args.output, offline=args.offline)
            grant = intake["license_grant"]
            print(
                json.dumps(
                    {
                        "tag": args.tag,
                        "commit": intake["record"]["commit"],
                        "output": str(outside_source(args.output) / args.tag),
                        "sources": sources,
                        "tag_verified_online": intake["tag_verified_online"],
                        "blobs_verified_online": intake["blobs_verified_online"],
                        "license": f"{grant['grant']} line {grant['line_number']}",
                    },
                    indent=2,
                )
            )
        elif args.command == "propose":
            print(json.dumps(propose(args.tag, args.cache), indent=2))
            print(
                "Draft only: read the whole license, then add this record to "
                "dll/port/allowlist.json in a reviewed commit.",
                file=sys.stderr,
            )
        else:
            records = require_allowlisted(args.dll)
            print(f"allowlisted: {', '.join(r['tag'] for r in records)} ({records[0]['commit']})")
    except Refused as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
