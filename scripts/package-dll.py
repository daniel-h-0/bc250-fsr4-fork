#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package the exact release DLL with its instructions, notices and checksums."""

import argparse
import hashlib
import io
import json
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DLL_NAME = "amd_fidelityfx_upscaler_dx12.dll"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_install_guide(text, manifest):
    heading = (
        f"Project version **{manifest['release_version']}**; "
        f"SDK display name **{manifest['provider_name']}**."
    )
    footers = re.findall(
        r"The DLL is ([\d,]+) bytes, SHA256:\s+```text\s+([a-f0-9]{64})\s+```", text
    )
    expected = (f"{manifest['expected_dll_bytes']:,}", manifest["expected_dll_sha256"])
    if heading not in text or footers != [expected]:
        raise ValueError("Installation guide release identity differs from the DLL manifest")


def release_files(root, dll):
    source = root / "dll"
    manifest = json.loads((source / "manifest.json").read_text())
    version = manifest["release_version"]
    if not re.fullmatch(r"[A-Za-z0-9.-]+", version):
        raise ValueError("Invalid DLL release version")
    if dll.is_symlink() or not dll.is_file():
        raise ValueError("The release DLL must be a regular file")
    content = dll.read_bytes()
    if (
        len(content) != manifest["expected_dll_bytes"]
        or digest(content) != manifest["expected_dll_sha256"]
    ):
        raise ValueError("The release DLL does not match the qualified manifest")
    inventory = json.loads((source / "source-inventory.json").read_text())
    paths = {"README.md": source / "INSTALL.md"}
    notices = {"notices/" + p.name: p for p in sorted((source / "notices").iterdir())}
    expected_notices = {name for name in inventory if name.startswith("notices/")}
    if not expected_notices or set(notices) != expected_notices:
        raise ValueError("Release notice inventory mismatch")
    paths.update(notices)
    files = {DLL_NAME: content}
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Invalid release documentation: " + str(path))
        content = path.read_bytes()
        if digest(content) != inventory.get(str(path.relative_to(source))):
            raise ValueError("Release documentation differs from the source inventory")
        files[name] = content
    validate_install_guide(files["README.md"].decode(), manifest)
    files["SHA256SUMS"] = "".join(
        f"{digest(data)}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    return version, files


def create_archive(root, dll, output, archive_format="zip", documentation_revision=None):
    if archive_format not in ("zip", "tar.xz"):
        raise ValueError("Unsupported archive format")
    if documentation_revision is not None and (
        type(documentation_revision) is not int or documentation_revision < 1
    ):
        raise ValueError("Documentation revision must be a positive integer")
    version, files = release_files(root, dll)
    output.mkdir(parents=True, exist_ok=True)
    suffix = "" if documentation_revision is None else f"-docs{documentation_revision}"
    archive = output / f"bc250-fsr4-dll-{version}{suffix}.{archive_format}"
    checksum = Path(str(archive) + ".sha256")
    for path in (archive, checksum):
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    with tempfile.TemporaryDirectory(prefix=".dll-release-", dir=output) as temp:
        staged = Path(temp) / archive.name
        if archive_format == "zip":
            with zipfile.ZipFile(
                staged, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as bundle:
                for name, data in sorted(files.items()):
                    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.create_system = 3
                    entry.external_attr = 0o100644 << 16
                    bundle.writestr(
                        entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
                    )
        else:
            with tarfile.open(staged, "x:xz", preset=9, format=tarfile.PAX_FORMAT) as bundle:
                for name, data in sorted(files.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = 0o644
                    entry.mtime = 0
                    bundle.addfile(entry, io.BytesIO(data))
        with staged.open("rb") as stream:
            checksum_text = (
                hashlib.file_digest(stream, "sha256").hexdigest() + "  " + archive.name + "\n"
            )
        staged_checksum = Path(temp) / checksum.name
        staged_checksum.write_text(checksum_text)
        archive.hardlink_to(staged)
        try:
            checksum.hardlink_to(staged_checksum)
        except OSError:
            archive.unlink()
            raise
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/dll")
    parser.add_argument("--format", choices=("zip", "tar.xz"), default="zip")
    parser.add_argument(
        "--documentation-revision",
        type=int,
        help="Name a documentation-only refresh without replacing original assets",
    )
    args = parser.parse_args()
    print(
        create_archive(
            ROOT,
            args.dll.expanduser().absolute(),
            args.output.expanduser().resolve(),
            args.format,
            args.documentation_revision,
        )
    )


if __name__ == "__main__":
    main()
