#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check RC10's release identity and its relationship to qualified shader inputs."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    def load(name):
        return json.loads((ROOT / name).read_text())

    manifest = load("dll/manifest.json")
    previous = load("docs/data/portable-dll-rc9-manifest.json")
    proposal = load("v4/experimental/compile-cse/manifest.json")["candidate"]
    record = load("docs/data/portable-dll-rc10.json")
    assert record["release_version"] == manifest["release_version"] == "4.0.0-rc10"
    assert record["provider_name"] == manifest["provider_name"] == "4.1.1r10"
    assert record["dll_sha256"] == manifest["expected_dll_sha256"] == record["rebuild"]["sha256"]
    assert record["dll_bytes"] == manifest["expected_dll_bytes"] == record["rebuild"]["bytes"]
    assert record["rebuild"]["validated_shaders"] == 348
    before = {r["original_offset"]: r for r in previous["replacements"]}
    selected = {r["original_offset"]: r for r in proposal["changes"]}
    assert len(selected) == 48
    for row in manifest["replacements"]:
        offset = row["original_offset"]
        if offset in selected:
            change = selected[offset]
            assert row["replacement_sha256"] == change["replacement_shader_sha256"]
            assert row["source_sha256"] == change["replacement_source_sha256"]
            assert before[offset]["replacement_sha256"] == change["original_shader_sha256"]
        else:
            assert row == before[offset]
    assert (
        record["driver"]["source_manifest_sha256"]
        == hashlib.sha256((ROOT / "v4/manifest.json").read_bytes()).hexdigest()
    )
    assert record["driver"]["same_stripped_output_from_final_recipe"]
    assert record["preflight"]["complete"] and len(record["preflight"]["rows"]) == 21
    for row in record["preflight"]["rows"]:
        assert row["valid"] and row["loaded_dll_path_matches"] and row["frames"] == 64
        assert row["pixels_sha256"] == row["expected_pixels_sha256"]
        if row["provider"] == "4.1.1r10":
            assert row["dll_sha256"] == record["dll_sha256"]
        if "paired" in row["name"] or "original_port" in row["name"] or "provider" in row["name"]:
            assert row["driver_sha256"] == record["driver"]["sha256"]
    installer = record["installer"]
    assert installer["complete"] and installer["rc10_driver_sha256"] == record["driver"]["sha256"]
    assert installer["old_driver_restored"] and installer["first_install_rolled_back"]
    assert installer["bad_checksum_preserved_selection"] and not installer["system_driver_changed"]
    assert (
        installer["helper_sha256"]
        == hashlib.sha256((ROOT / "scripts/driver.py").read_bytes()).hexdigest()
    )
    assert installer["cache_uuid"]["enabled"] != installer["cache_uuid"]["disabled"]
    assert installer["cache_uuid"]["enabled_repeats"]
    translators = record["additional_translators"]
    assert translators["complete"] and len(translators["rows"]) == 2
    assert all(r["dll_valid"] for r in translators["rows"])
    assert translators["rows"][0]["provider_selected"] == "3.1.5"
    assert not translators["rows"][0]["provider_valid"]
    assert translators["rows"][1]["provider_valid"]
    assert len({r[0] for r in translators["rows"][1]["provider_matches"]}) == 14
    watermark = load("docs/data/beginner-watermark-rc10.json")
    assert watermark["provider"] == record["provider_name"]
    assert watermark["dll_sha256"] == record["dll_sha256"]
    assert (
        watermark["png_sha256"]
        == hashlib.sha256(
            (ROOT / "docs/assets/rc10-watermark-reference.png").read_bytes()
        ).hexdigest()
    )
    print("PASS: RC10 identity, 48 selected/300 retained slots and 21 final-artifact image checks")


if __name__ == "__main__":
    main()
