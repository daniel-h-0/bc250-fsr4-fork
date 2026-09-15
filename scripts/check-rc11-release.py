#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check RC11 identities, inherited shaders and fresh synthetic image evidence."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_binary(path, record):
    data = bytearray(path.read_bytes())
    assert sha(data) == record["dll_sha256"] and len(data) == record["dll_bytes"]
    delta = record["binary_delta"]
    for change in delta["changes"]:
        assert data[change["offset"]] == change["after"]
        data[change["offset"]] = change["before"]
    assert sha(data) == delta["previous_sha256"], "RC11 differs outside the recorded label/checksum"


def main(dll=None):
    def load(path):
        return json.loads((ROOT / path).read_text())

    manifest = load("dll/manifest.json")
    previous = load("docs/data/portable-dll-rc10-manifest.json")
    record = load("docs/data/portable-dll-rc11.json")
    assert record["release_version"] == manifest["release_version"] == "4.0.0-rc11"
    assert record["provider_name"] == manifest["provider_name"] == "4.1.1r11"
    assert record["dll_sha256"] == manifest["expected_dll_sha256"]
    assert record["dll_bytes"] == manifest["expected_dll_bytes"]
    assert manifest["replacements"] == previous["replacements"]
    assert len(manifest["replacements"]) == record["validated_shaders"] == 348
    delta = record["binary_delta"]
    assert delta["previous_sha256"] == previous["expected_dll_sha256"]
    assert delta["dll_sha256"] == record["dll_sha256"]
    assert delta["only_provider_label_and_pe_checksum_changed"]
    assert delta["shader_slots_unchanged"] == 348
    assert len(delta["changes"]) == 2
    assert {row["offset"] for row in delta["changes"]} == {
        delta["label_offset"] + 7,
        delta["checksum_offset"] + 1,
    }
    assert next(row for row in delta["changes"] if row["offset"] == delta["label_offset"] + 7) == {
        "offset": delta["label_offset"] + 7,
        "before": ord("0"),
        "after": ord("1"),
    }
    current_driver = load("v4/manifest.json")
    previous_driver = load("v4/legacy/rc10-manifest.json")
    for field in ("sources", "source_inputs", "source_overlays", "patch_order", "base_archive"):
        assert current_driver[field] == previous_driver[field], field
    rc10 = load("docs/data/portable-dll-rc10.json")
    assert record["driver"]["sha256"] == rc10["driver"]["sha256"]
    assert record["driver"]["bytes"] == rc10["driver"]["bytes"]
    assert record["driver"]["source_manifest_sha256"] == sha(
        (ROOT / "v4/manifest.json").read_bytes()
    )
    assert record["driver"]["rebuilt_byte_identical_to_rc10"]
    reference = {row["name"]: row for row in rc10["preflight"]["rows"]}
    assert record["synthetic_only"] and len(record["image_checks"]) == 9
    for row in record["image_checks"]:
        old = reference[row["rc10_reference"]]
        assert row["valid"] and row["finite"] and row["loaded_dll_path_matches"]
        assert row["frames"] == 64 and row["provider"] == record["provider_name"]
        assert row["dll_sha256"] == record["dll_sha256"]
        assert row["pixels_sha256"] == old["pixels_sha256"]
        assert row["driver_sha256"] == old["driver_sha256"]
    watermark = load("docs/data/beginner-watermark-rc11.json")
    assert watermark["provider"] == record["provider_name"]
    assert watermark["dll_sha256"] == record["dll_sha256"]
    assert watermark["all_dispatches_completed"] and watermark["loaded_dll_path_matches"]
    assert watermark["png_sha256"] == sha(
        (ROOT / "docs/assets/rc11-watermark-reference.png").read_bytes()
    )
    if dll is not None:
        verify_binary(dll, record)
    print("PASS: RC11 label-only DLL delta, unchanged shaders/driver and nine fresh image checks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dll", type=Path, help="Also reverse the two byte changes and verify RC10's hash"
    )
    main(parser.parse_args().dll)
