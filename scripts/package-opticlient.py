#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the pinned Linux OptiScaler Client integration and package its source."""

import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def build(work, output, dotnet, dll_zip):
    integration = ROOT / "integrations/optiscaler-client"
    manifest = json.loads((integration / "manifest.json").read_text())
    work, output = work.resolve(), output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    downloads = work / "downloads"
    downloads.mkdir(exist_ok=True)
    inputs = {}
    for key, meta in manifest["inputs"].items():
        target = downloads / meta["file"]
        if not target.exists():
            request = urllib.request.Request(
                meta["url"], headers={"User-Agent": "bc250-fsr4-build"}
            )
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                target.open("wb") as file,
            ):
                shutil.copyfileobj(response, file)
        if digest(target) != meta["sha256"]:
            raise ValueError("Input checksum mismatch: " + key)
        inputs[key] = target
    source = work / "source"
    if source.exists():
        raise FileExistsError(
            "Use a fresh work directory or remove only its generated source directory"
        )
    source.mkdir()
    with tarfile.open(inputs["client"]) as archive:
        archive.extractall(source, filter="data")
    upstream = next(source.iterdir())
    runpy.run_path(str(integration / "prepare.py"))["prepare"](upstream, integration)
    stage = work / ("bc250-opticlient-" + manifest["version"] + "-linux-x64")
    if stage.exists():
        raise FileExistsError(stage)
    env = os.environ.copy()
    env.update(
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_GENERATE_ASPNET_CERTIFICATE="false",
        DOTNET_CLI_HOME=str(work / "dotnet-home"),
    )
    subprocess.run(
        [
            str(dotnet),
            "publish",
            str(upstream / "OptiscalerClient.csproj"),
            "-c",
            "Release",
            "-r",
            "linux-x64",
            "--self-contained",
            "true",
            "-p:RestoreLockedMode=true",
            "-o",
            str(stage),
        ],
        env=env,
        check=True,
    )
    (stage / "bc250").mkdir()
    cache_tools = stage / "bc250/cache-tools"
    cache_tools.mkdir()
    cache_sources = ["client-cache.py", "client_cache_vdf.py", "shared-cache.py", "shared-cache.sh"]
    for name in cache_sources:
        shutil.copy2(ROOT / "scripts" / name, cache_tools / name)
    shutil.copy2(ROOT / "LICENSE.new-code", cache_tools / "LICENSE.new-code")
    (cache_tools / "SHA256.json").write_text(
        json.dumps(
            {p.name: digest(p) for p in sorted(cache_tools.iterdir()) if p.is_file()}, indent=2
        )
        + "\n"
    )
    payload = work / "qualification-payload"
    payload.mkdir(parents=True)
    subprocess.run(["bsdtar", "-xf", str(inputs["opti"]), "-C", str(payload)], check=True)
    for path in payload.rglob("*"):
        if path.is_symlink():
            raise ValueError("Unexpected symlink in OptiScaler payload")
    for name in ("setup_linux.sh", "setup_windows.bat"):
        (payload / name).unlink(missing_ok=True)
    shutil.copy2(inputs["dlss"], payload / "nvngx_dlss.dll")
    (payload / "OptiScaler/plugins").mkdir(exist_ok=True)
    shutil.copy2(inputs["patcher"], payload / "OptiScaler/plugins/OptiPatcher.asi")
    shutil.copy2(inputs["dlss_license"], payload / "Licenses/NVIDIA-DLSS-LICENSE.txt")
    payload_manifest = {
        "ProxyHash": digest(payload / "OptiScaler.dll"),
        "Files": {
            str(p.relative_to(payload)): digest(p)
            for p in sorted(payload.rglob("*"))
            if p.is_file()
        },
    }
    (payload / "payload.json").write_text(json.dumps(payload_manifest, indent=2) + "\n")
    shutil.copy2(payload / "payload.json", stage / "bc250/payload.json")
    (stage / "bc250/inputs.json").write_text(json.dumps(manifest["inputs"], indent=2) + "\n")
    dll_manifest = json.loads((ROOT / "dll/manifest.json").read_text())
    with zipfile.ZipFile(dll_zip) as archive:
        data = archive.read("amd_fidelityfx_upscaler_dx12.dll")
        if hashlib.sha256(data).hexdigest() != dll_manifest["expected_dll_sha256"]:
            raise ValueError("The bundled release ZIP does not match the DLL manifest")
    shutil.copy2(dll_zip, stage / "bc250" / dll_zip.name)
    notices = stage / "notices"
    notices.mkdir()
    shutil.copy2(upstream / "LICENSE", notices / "OptiScaler-Client-GPL-3.0.txt")
    shutil.copy2(integration / "NOTICE.md", notices / "BC250-INTEGRATION.md")
    shutil.copy2(ROOT / "LICENSE.new-code", notices / "BC250-MIT-tools.txt")
    # Preserve dependency license texts/metadata with the self-contained runtime.
    assets = json.loads((upstream / "obj/project.assets.json").read_text())
    for package in assets["libraries"].values():
        if package.get("type") != "package":
            continue
        for package_root in assets["packageFolders"]:
            package_dir = Path(package_root) / package["path"]
            if not package_dir.exists():
                continue
            for path in package_dir.rglob("*"):
                if path.is_file() and (
                    path.suffix == ".nuspec"
                    or any(word in path.name.lower() for word in ("license", "notice", "copying"))
                ):
                    destination = (
                        notices / "nuget" / package["path"] / path.relative_to(package_dir)
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
    for name in ("LICENSE.txt", "ThirdPartyNotices.txt"):
        path = dotnet.parent / name
        if path.exists():
            shutil.copy2(path, notices / ("dotnet-" + name))
    # Complete modified client source accompanies every binary archive.
    with tarfile.open(stage / "source.tar.gz", "w:gz") as archive:
        for path in sorted(upstream.rglob("*")):
            if any(part in {"bin", "obj", ".git"} for part in path.relative_to(upstream).parts):
                continue
            if path.is_file():
                archive.add(path, arcname="OptiscalerClient/" + str(path.relative_to(upstream)))
        for path in sorted(integration.rglob("*")):
            if path.is_file() and not any(
                part in {"bin", "obj", "__pycache__"}
                for part in path.relative_to(integration).parts
            ):
                archive.add(path, arcname="bc250-integration/" + str(path.relative_to(integration)))
        archive.add(Path(__file__), arcname="package-opticlient.py")
        for name in cache_sources:
            archive.add(ROOT / "scripts" / name, arcname="scripts/" + name)
        archive.add(ROOT / "LICENSE.new-code", arcname="LICENSE.new-code")
        for name in ("test_client_cache.py", "test_shared_cache.py"):
            archive.add(ROOT / "tests" / name, arcname="tests/" + name)
    guide = (ROOT / "docs/optiscaler-client.md").read_text()

    def online_link(match):
        target = match.group(1)
        if "://" in target or target.startswith("#"):
            return match.group(0)
        path, separator, anchor = target.partition("#")
        relative = (ROOT / "docs" / path).resolve().relative_to(ROOT)
        return (
            "](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/"
            + str(relative)
            + separator
            + anchor
            + ")"
        )

    (stage / "README.md").write_text(re.sub(r"\]\(([^)]+)\)", online_link, guide))
    launcher = stage / "Start-BC250-OptiClient.sh"
    launcher.write_text(
        '#!/bin/sh\nset -eu\ncd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec ./OptiscalerClient "$@"\n'
    )
    launcher.chmod(0o755)
    (stage / "build.json").write_text(
        json.dumps({"integration": manifest, "dll": dll_manifest["expected_dll_sha256"]}, indent=2)
        + "\n"
    )
    (stage / "SHA256SUMS").write_text(
        "".join(
            f"{digest(p)}  {p.relative_to(stage)}\n"
            for p in sorted(stage.rglob("*"))
            if p.is_file()
        )
    )
    target = output / (stage.name + ".tar.gz")
    if target.exists():
        raise FileExistsError(target)
    with tarfile.open(target, "w:gz") as archive:
        archive.add(stage, arcname=stage.name)
    Path(str(target) + ".sha256").write_text(digest(target) + "  " + target.name + "\n")
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".work/opticlient")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/opticlient")
    parser.add_argument("--dotnet", type=Path, required=True, help=".NET 10 SDK executable")
    parser.add_argument("--dll-zip", type=Path, required=True)
    args = parser.parse_args()
    build(args.work, args.output, args.dotnet.resolve(), args.dll_zip.resolve())
