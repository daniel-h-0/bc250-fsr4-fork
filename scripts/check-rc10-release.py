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

    manifest = load("docs/data/portable-dll-rc10-manifest.json")
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
        == hashlib.sha256((ROOT / "v4/legacy/rc10-manifest.json").read_bytes()).hexdigest()
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
        == hashlib.sha256((ROOT / "legacy/rc10-tools/driver.py").read_bytes()).hexdigest()
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
    gameplay = load(record["driver_gameplay_record"])
    assert gameplay["qualification_complete"]
    assert gameplay["driver_sha256"] == record["driver"]["sha256"]
    assert [(g["appid"], g["gameplay_qualified"]) for g in gameplay["games"]] == [
        (870780, True),
        (482400, False),
    ]
    pairs = load("v4/experimental/rc9-port/manifest.json")["shader_entries"]
    for game in gameplay["games"]:
        assert game["normal_steam_launch"] and game["original_files_and_settings_restored"]
        assert game["save_payloads_rechecked_after_cloud_restore"]
        assert game["provider_name"] == "4.1.1" and game["provider_source"] == "DRIVER"
        assert game["logged_shader_count"] == len(game["exact_shader_matches"])
        assert game["logged_shader_count"] == (14 if game["appid"] == 870780 else 13)
        for process in game["processes"]:
            assert process["all_in_same_process"] and process["lsfg_layer_absent"]
            assert process["driver_sha256"] == gameplay["driver_sha256"]
            assert process["provider_sha256"] == gameplay["provider_sha256"]
            assert process["bridge_sha256"] == gameplay["sdk_bridge_sha256"]
        for match in game["exact_shader_matches"]:
            pair = pairs[match["index"]]
            assert match["entry"] == pair["entry"]
            assert match["original_spirv_sha256"] == pair["original_spirv_sha256"]
            assert match["rc9_spirv_sha256"] == pair["target_spirv_sha256"]
        for screenshot in game["screenshots"]:
            assert screenshot["pixels_unedited"]
            assert (
                hashlib.sha256((ROOT / screenshot["path"]).read_bytes()).hexdigest()
                == screenshot["sha256"]
            )
    print(
        "PASS: driver Control gameplay evidence, separate DX11 menu scope and restoration identities"
    )
    print("PASS: RC10 identity, 48 selected/300 retained slots and 21 final-artifact image checks")


if __name__ == "__main__":
    main()
