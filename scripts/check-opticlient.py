#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check client source/evidence identities and an optional packaged client archive."""

import argparse
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT, archive=None):
    integration = root / "integrations/optiscaler-client"
    manifest = json.loads((integration / "manifest.json").read_text())
    record = json.loads((root / "docs/data/optiscaler-client-3.json").read_text())
    previous = json.loads((root / "docs/data/optiscaler-client-2.json").read_text())
    dll = json.loads((root / "dll/manifest.json").read_text())
    assert record["client_version"] == manifest["version"]
    assert record["upstream_commit"] == manifest["upstream_commit"]
    assert record["transaction_result"] == record["gui"]["status"] == "pass"
    assert record["transaction_checks"] >= 45 and record["cache_transaction_checks"] >= 26
    assert record["deployment"]["completed_benchmarks"] >= 2
    assert record["deployment"]["cache_restore"] == record["deployment"]["dll_restore"] == "pass"
    assert record["deployment"]["production_preserved"]
    for name, expected in previous["sources"].items():
        if not name.endswith("Bc250Window.cs"):
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    for name, expected in record["sources"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    assert not (integration / "recipes.json").exists()
    assert {row["layout"] for row in previous["real_payload"]} == {"flat", "nested", "unreal"}
    for row in previous["real_payload"]:
        assert row["loaded_expected_path"] and row["ini_preserved"]
        assert row["sha256"] == dll["expected_dll_sha256"]
    for meta in manifest["inputs"].values():
        assert meta["url"].startswith("https://") and re.fullmatch(r"[a-f0-9]{64}", meta["sha256"])
    lock = json.loads((integration / "packages.lock.json").read_text())
    assert all(
        deps.get("Tmds.DBus.Protocol", {}).get("resolved", "0.21.3") == "0.21.3"
        for deps in lock["dependencies"].values()
    )
    if archive:
        archive = Path(archive)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        assert Path(str(archive) + ".sha256").read_text() == actual + "  " + archive.name + "\n"
        base = "bc250-opticlient-" + manifest["version"] + "-linux-x64/"
        with tarfile.open(archive) as tar:
            files = {m.name.removeprefix(base): m for m in tar.getmembers() if m.isfile()}
            assert all(
                m.name.startswith(base) and ".." not in Path(m.name).parts for m in files.values()
            )
            assert "source.tar.gz" in files and "OptiscalerClient" in files
            cache_hashes = json.load(tar.extractfile(files["bc250/cache-tools/SHA256.json"]))
            assert set(cache_hashes) == {
                "client-cache.py",
                "client_cache_vdf.py",
                "shared-cache.py",
                "shared-cache.sh",
                "LICENSE.new-code",
            }
            for name, expected in cache_hashes.items():
                original = root / (
                    "LICENSE.new-code" if name == "LICENSE.new-code" else "scripts/" + name
                )
                bundled = tar.extractfile(files["bc250/cache-tools/" + name]).read()
                assert hashlib.sha256(bundled).hexdigest() == expected
                assert bundled == original.read_bytes()
            assert not any(name.startswith("bc250/payload/") for name in files)
            payload = json.load(tar.extractfile(files["bc250/payload.json"]))
            assert set(payload) == {"ProxyHash", "Files"}
            sums = tar.extractfile(files["SHA256SUMS"]).read().decode().splitlines()
            assert len(sums) == len(files) - 1
            for line in sums:
                expected, name = line.split("  ", 1)
                assert (
                    hashlib.sha256(tar.extractfile(files[name]).read()).hexdigest() == expected
                ), name
            build = json.load(tar.extractfile(files["build.json"]))
            assert build["integration"] == manifest and build["dll"] == dll["expected_dll_sha256"]
            guide = tar.extractfile(files["README.md"]).read().decode()
            assert all(
                "://" in target or target.startswith("#")
                for target in re.findall(r"\]\(([^)]+)\)", guide)
            )
            release_zip = next(
                name
                for name in files
                if name.startswith("bc250/bc250-fsr4-dll-") and name.endswith(".zip")
            )
            with zipfile.ZipFile(io.BytesIO(tar.extractfile(files[release_zip]).read())) as zip:
                assert (
                    hashlib.sha256(zip.read("amd_fidelityfx_upscaler_dx12.dll")).hexdigest()
                    == dll["expected_dll_sha256"]
                )
            with tarfile.open(
                fileobj=io.BytesIO(tar.extractfile(files["source.tar.gz"]).read())
            ) as source:
                assert not any(
                    part in {"bin", "obj", "__pycache__"}
                    for m in source.getmembers()
                    for part in Path(m.name).parts
                )
                for name in cache_hashes:
                    if name != "LICENSE.new-code":
                        assert (
                            source.extractfile("scripts/" + name).read()
                            == (root / "scripts" / name).read_bytes()
                        )
                for name in ("test_client_cache.py", "test_shared_cache.py"):
                    assert (
                        source.extractfile("tests/" + name).read()
                        == (root / "tests" / name).read_bytes()
                    )
                for path in (integration / "src").glob("*.cs"):
                    assert (
                        source.extractfile("bc250-integration/src/" + path.name).read()
                        == path.read_bytes()
                    )
                assert (
                    source.extractfile("OptiscalerClient/LICENSE")
                    .read()
                    .startswith(b"                    GNU GENERAL PUBLIC LICENSE")
                )
    return (
        "OptiScaler Client pins, transaction evidence, cache deployment, general-layout load checks"
        + (" and complete package" if archive else "")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    print("PASS:", check(archive=args.archive))
