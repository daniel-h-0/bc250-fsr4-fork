#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble the pinned runtime from upstream downloads; never touch a game prefix."""

import argparse
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import safe_archive

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, expected, cache, offline=False):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / expected
    if path.exists():
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError("Cached upstream file changed: " + str(path))
        return path
    if offline:
        raise RuntimeError("Offline cache is missing " + expected + " (" + url + ")")
    print("Downloading " + url.rsplit("/", 1)[-1], flush=True)
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
        temporary = Path(output.name)
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                shutil.copyfileobj(response, output)
            output.flush()
            if digest(temporary) != expected:
                raise RuntimeError("Upstream artifact differs from the pin: " + url)
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                raise RuntimeError("Runtime link escapes its root: " + name)
            result[name] = {"target": os.readlink(path)}
        elif path.is_file():
            result[name] = {"sha256": digest(path), "mode": path.stat().st_mode & 0o777}
        elif not path.is_dir():
            raise RuntimeError("Special file in runtime: " + name)
    return result


def tar_tree(root, output, mode="gz"):
    """Deterministic archives, including upstream's relative internal symlinks."""

    def metadata(member):
        member.uid = member.gid = member.mtime = 0
        member.uname = member.gname = ""
        member.pax_headers = {}
        member.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
        return member

    with output.open("wb") as raw:
        compressed = (
            gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0, compresslevel=6)
            if mode == "gz"
            else lzma.LZMAFile(raw, "w", preset=0)
        )
        with compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in sorted(root.rglob("*")):
                archive.add(
                    path, arcname=str(path.relative_to(root)), recursive=False, filter=metadata
                )


def apply_upscaler_patch(source, patch):
    """Apply the pinned single-file unified diff, with exact positions and context."""
    original = source.read_text().splitlines(keepends=True)
    lines = patch.read_text().splitlines(keepends=True)
    header = lines.index("--- a/protonfixes/upscalers.py\n")
    if lines[header + 1] != "+++ b/protonfixes/upscalers.py\n":
        raise RuntimeError("Unexpected upscaler patch target.")
    output, cursor, i = [], 0, header + 2
    while i < len(lines):
        match = re.fullmatch(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@.*\n", lines[i])
        if not match:
            raise RuntimeError("Malformed upscaler patch hunk.")
        old_start, old_count, new_start, new_count = map(int, match.groups())
        start = old_start - 1
        if start < cursor or start > len(original):
            raise RuntimeError("Overlapping or out-of-range upscaler patch.")
        output.extend(original[cursor:start])
        cursor = start
        if len(output) != new_start - 1:
            raise RuntimeError("Upscaler patch output position differs.")
        removed = added = 0
        i += 1
        while i < len(lines) and not lines[i].startswith("@@ "):
            line = lines[i]
            if line[:1] not in (" ", "+", "-"):
                raise RuntimeError("Unsupported upscaler patch line.")
            if line[0] in " -":
                if cursor >= len(original) or original[cursor] != line[1:]:
                    raise RuntimeError("Upscaler patch context differs; refusing partial patch.")
                cursor += 1
                removed += 1
            if line[0] in " +":
                output.append(line[1:])
                added += 1
            i += 1
        if (removed, added) != (old_count, new_count):
            raise RuntimeError("Upscaler patch hunk length differs.")
    output.extend(original[cursor:])
    source.write_text("".join(output))


def extract_optiscaler(archive, extracted, staging, policy, cache, offline):
    if executable := shutil.which("bsdtar"):
        command = [
            executable,
            "-xf",
            str(archive),
            "--no-same-owner",
            "--no-same-permissions",
            "-C",
            str(extracted),
        ]
    else:
        # A pinned static upstream extractor works on immutable systems too.
        # It stays in temporary staging; the cache retains its full license archive.
        component = policy["extractor"]
        package = download(component["url"], component["sha256"], cache, offline)
        directory = staging / "extractor"
        directory.mkdir()
        with tarfile.open(package) as bundle:
            safe_archive.extractall(bundle, directory)
        executable = directory / "7zzs"
        if executable.is_symlink() or digest(executable) != component["binary_sha256"]:
            raise RuntimeError("7-Zip extractor differs from its pin.")
        executable.chmod(0o700)
        command = [str(executable), "x", "-y", "-o" + str(extracted), str(archive)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def optiscaler_artifact(archive, patcher, sdk, ngx_signature, staging, policy, cache, offline):
    extracted = staging / "opti"
    extracted.mkdir()
    extract_optiscaler(archive, extracted, staging, policy, cache, offline)
    if any(p.is_symlink() or not (p.is_dir() or p.is_file()) for p in extracted.rglob("*")):
        raise RuntimeError("Unexpected nonregular OptiScaler payload.")
    dll = extracted / "OptiScaler.dll"
    if digest(dll) != policy["optiscaler"]["dll_sha256"]:
        raise RuntimeError("OptiScaler DLL differs from its pin.")
    # WINMM is imported by Vulkan games that never request DXGI. GE redirects
    # this one proxy from its owned prefix; no game-directory files are needed.
    dll.rename(extracted / policy["loader"]["proxy"])
    # Older NGX inputs check for an NVIDIA-signed library before calling the
    # intercepted API. OptiScaler searches beside its proxy for this surrogate.
    # Keep a pinned shared copy here; never discover or copy a game's DLL.
    shutil.copy2(ngx_signature, extracted / "nvngx_dlss.dll")
    shutil.copy2(ROOT / "runtime/licenses/NVIDIA-DLSS.txt", extracted / "Licenses/NVIDIA-DLSS.txt")
    # A bundled 4.1.1 SDK hides the equally versioned driver provider. Use the
    # pinned older SDK bridge so the qualified 4.1.1 driver provider wins.
    shutil.copy2(sdk, extracted / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll")
    shutil.copy2(
        ROOT / "runtime/licenses/FidelityFX-SDK-4.0.2.txt",
        extracted / "Licenses/FidelityFX-SDK-4.0.2.txt",
    )
    plugins = extracted / "OptiScaler/plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copy2(patcher, plugins / "OptiPatcher.asi")
    # These are upstream's interactive deployment helpers, not runtime dependencies.
    for path in extracted.rglob("*"):
        if path.suffix in (".bat", ".sh"):
            path.unlink()
    files = {str(p.relative_to(extracted)): p for p in sorted(extracted.rglob("*")) if p.is_file()}
    for path in files.values():
        path.chmod(0o644)
    output = staging / "optiscaler.tar.xz"
    tar_tree(extracted, output, "xz")
    return output, {
        "version": policy["optiscaler"]["version"],
        "is_dev_file": False,
        "download_url": "artifacts/optiscaler-" + digest(output) + ".tar.xz",
        "zip_sha256_hash": digest(output),
        "sha256_hash": {
            name: digest(path) for name, path in files.items() if not name.endswith(".ini")
        },
        "md5_hash": {
            name: "" if name.endswith(".ini") else hashlib.md5(path.read_bytes()).hexdigest()
            for name, path in files.items()
        },
    }


def assemble(destination, cache, policy=None, offline=False):
    """Return a complete, owned version tree under an empty staging directory."""
    policy = policy or json.loads((ROOT / "runtime/manifest.json").read_text())
    for name, expected in policy["integration"].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError("Runtime integration changed without updating its pin: " + name)
    if any(destination.iterdir()):
        raise RuntimeError("Runtime assembly requires an empty staging directory.")
    ge = policy["proton"]
    ge_archive = download(ge["url"], ge["sha256"], cache, offline)
    opti = download(policy["optiscaler"]["url"], policy["optiscaler"]["sha256"], cache, offline)
    patcher = download(
        policy["optipatcher"]["url"], policy["optipatcher"]["sha256"], cache, offline
    )
    provider = download(
        policy["provider"]["url"], policy["provider"]["archive_sha256"], cache, offline
    )
    sdk = download(policy["sdk"]["url"], policy["sdk"]["sha256"], cache, offline)
    ngx_signature = download(
        policy["ngx_signature"]["url"], policy["ngx_signature"]["sha256"], cache, offline
    )
    if (
        hashlib.sha256(lzma.decompress(provider.read_bytes())).hexdigest()
        != policy["provider"]["sha256"]
    ):
        raise RuntimeError("FSR provider differs from the pinned 4.1.1 DLL.")
    version = destination / policy["release"]["id"]
    version.mkdir()
    with tempfile.TemporaryDirectory(prefix=".assemble-", dir=destination) as temporary:
        staging = Path(temporary)
        # The complete official archive was SHA256-verified above. The Python data
        # filter rejects paths and links leaving this extraction directory.
        with tarfile.open(ge_archive, "r:gz") as archive:
            safe_archive.extractall(archive, staging)
        source = staging / ge["name"]
        for name, expected in ge["files"].items():
            if digest(source / name) != expected["sha256"]:
                raise RuntimeError("GE-Proton input changed: " + name)
        source.rename(version / "ge")
        patch = ROOT / "runtime/patches/0001-pinned-upscaler-manifest.patch"
        apply_upscaler_patch(version / "ge/protonfixes/upscalers.py", patch)
        opti_archive, opti_item = optiscaler_artifact(
            opti, patcher, sdk, ngx_signature, staging, policy, cache, offline
        )
        artifacts = version / "ge/artifacts"
        artifacts.mkdir()
        shutil.copy2(opti_archive, artifacts / Path(opti_item["download_url"]).name)
        provider_name = "provider-" + policy["provider"]["archive_sha256"] + ".dll.xz"
        shutil.copy2(provider, artifacts / provider_name)
        manifest = {
            "optiscaler": [opti_item],
            "fsr_40_drv": [
                {
                    "version": policy["provider"]["version"],
                    "is_dev_file": False,
                    "download_url": "artifacts/" + provider_name,
                    "zip_sha256_hash": policy["provider"]["archive_sha256"],
                    "sha256_hash": policy["provider"]["sha256"],
                    "md5_hash": hashlib.md5(lzma.decompress(provider.read_bytes())).hexdigest(),
                }
            ],
        }
        (version / "ge/upscaler-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy2(ROOT / "runtime/launch.py", version / "launch.py")
    shutil.copy2(ROOT / "runtime/manifest.json", version / "runtime-lock.json")
    shutil.copy2(ROOT / "LICENSE.new-code", version / "LICENSE.new-code")
    shutil.copy2(
        ROOT / "runtime/patches/0001-pinned-upscaler-manifest.patch", version / "upscalers.patch"
    )
    for path in version.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    files = inventory(version)
    critical = [
        name
        for name in files
        if name
        in (
            "launch.py",
            "runtime-lock.json",
            "ge/proton",
            "ge/version",
            "ge/toolmanifest.vdf",
            "ge/upscaler-manifest.json",
            "ge/files/bin/wine",
        )
        or name.startswith("ge/protonfixes/")
        and name.endswith(".py")
        and "sha256" in files[name]
    ]
    (version / "runtime-release.json").write_text(
        json.dumps(
            {
                "schema": 1,
                **{k: policy["release"][k] for k in ("id", "version")},
                "files": files,
                "critical_files": critical,
            },
            indent=2,
        )
        + "\n"
    )
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/bc250-fsr4-runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/runtime")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-", dir=args.output) as temporary:
        parent = Path(temporary)
        tree = assemble(parent, args.cache, offline=args.offline)
        output = args.output / (tree.name + "-x86_64.tar.gz")
        if output.exists():
            raise RuntimeError("Output already exists; choose a new directory.")
        tar_tree(parent, output)
        output.with_suffix(output.suffix + ".sha256").write_text(
            digest(output) + "  " + output.name + "\n"
        )
        print(output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
