#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the RC12 release record (identity, RC11 comparison, tested-build delta, GPU data) and
the same-day baselines of docs/gpu-cost.md."""

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"candidate", "qualified"}
CHECK_STATUSES = {"pending", "pass"}
DELTA_FIELDS = ("pe-checksum", "appended-section-virtual-size", "provider-label")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fields(data, record):
    """File offsets of the three fields a provider-label change touches, read from the PE."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    optional = pe + 24
    count = struct.unpack_from("<H", data, pe + 6)[0]
    table = optional + struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(count):
        name, vsize, rva, size, offset = struct.unpack_from("<8sIIII", data, table + 40 * i)
        if name == b".bc250\0\0":
            label = offset + vsize - 1 - len(record["provider_name"])  # name + NUL end the section
            return {
                "pe-checksum": range(optional + 64, optional + 68),
                "appended-section-virtual-size": range(table + 40 * i + 8, table + 40 * i + 12),
                "provider-label": range(label, label + 16),
            }
    raise AssertionError("No appended .bc250 section")


def verify_binary(path, record):
    """The release DLL is the tested build except the recorded label, size and checksum bytes."""
    data = bytearray(path.read_bytes())
    assert sha(data) == record["dll_sha256"] and len(data) == record["dll_bytes"], "Not RC12"
    where = fields(data, record)
    for change in record["tested_build"]["delta"]:
        assert change["offset"] in where[change["field"]], change["field"]
        assert data[change["offset"]] == change["release"], change["field"]
        data[change["offset"]] = change["tested"]
    tested = record["tested_build"]
    assert sha(data) == tested["dll_sha256"], "RC12 differs from the tested build elsewhere"
    assert data.find(tested["provider_name"].encode() + b"\0") > 0


def check_baselines(record, rc11_sha256):
    """docs/data/fsr-cost-20261023.json: AMD's original shaders against RC11 on one driver."""
    assert record["schema"] == 1 and record["date"] == "2026-10-23"
    assert record["arms"]["rc11"]["dll_sha256"] == rc11_sha256
    assert record["arms"]["rc11"]["provider"] == "4.1.1r11"
    assert record["arms"]["original"]["provider"] == "4.1.1"
    chart = json.loads((ROOT / "docs/data/fsr-cost-20260911-rc9/configuration.json").read_text())
    original = record["arms"]["original"]["dll_sha256"]
    driver = record["driver"]["libvulkan_radeon_sha256"]
    assert any(
        v.get("dll_sha256") == original and v.get("driver_sha256") == driver
        for v in chart["variants"].values()
    ), "Not the September chart's original-shader DLL and driver"
    assert record["probe"]["sha256"] == chart["probe_sha256"]
    assert record["proton_sha256"] == chart["proton_sha256"]
    assert [b["configuration"] for b in record["blocks"]] == [
        "1080p Quality",
        "1440p Quality",
        "4K Quality",
    ]
    for block in record["blocks"]:
        arms = block["arms"]
        assert set(arms) == {"original", "rc11"} and block["context_max"] == block["output"]
        assert arms["original"]["rounds"] == arms["rc11"]["rounds"]
        assert len(arms["rc11"]["rounds"]) == block["rounds"]
        assert arms["original"]["image"] == arms["rc11"]["image"], block["block"]
        for arm in arms.values():
            values = arm["run_medians_ms"]
            assert len(values) == block["rounds"] and all(v > 0 for v in values)
            assert math.isclose(mean(values), arm["mean_ms"], abs_tol=1e-9)
            assert math.isclose(stdev(values) / math.sqrt(len(values)), arm["se_ms"], abs_tol=1e-9)
        d = [
            a - b
            for a, b in zip(arms["rc11"]["run_medians_ms"], arms["original"]["run_medians_ms"])
        ]
        pair = block["rc11_minus_original_ms"]
        assert math.isclose(mean(d), pair["mean"], abs_tol=1e-9)
        assert math.isclose(stdev(d) / math.sqrt(len(d)), pair["se"], abs_tol=1e-9)
        assert math.isclose(100 * mean(d) / arms["original"]["mean_ms"], pair["percent"])
    return len(record["blocks"])


def check_release_dll(record, validation):
    """The release DLL's own GPU start: this DLL and label, every new slot dispatched, the tested
    build's timed images (= RC11's), and a watermark capture of this DLL."""
    check = record["release_dll_check"]
    assert check["status"] in CHECK_STATUSES
    if record["status"] == "qualified":
        assert check["status"] == "pass", "A qualified RC12 needs the release DLL's own GPU start"
    if check["status"] == "pending":
        assert "**Pending:**" in validation
        return check
    assert "**Pending:**" not in validation, "The validation page still shows a pending check"
    assert check["dll_sha256"] == record["dll_sha256"]
    assert check["provider"] == record["provider_name"]
    assert check["census"]["runs"] == record["gpu"]["census"]["runs"]
    assert check["census"]["c10_slots_dispatched"] == record["rc11_comparison"]["c10_slots"]
    timed = {block["block"]: block["arms"]["rc11"]["images"] for block in record["gpu"]["timing"]}
    assert 2 * len(check["image_runs"]) == record["gpu"]["image_runs"]
    for run in check["image_runs"]:
        assert run["images"] == timed[run["block"]], run["block"]
    files = check["watermark"]
    assert files["record"].startswith("docs/data/") and files["image"].startswith("docs/assets/")
    watermark = json.loads((ROOT / files["record"]).read_text())
    assert watermark["provider"] == record["provider_name"]
    assert watermark["dll_sha256"] == record["dll_sha256"]
    assert watermark["all_dispatches_completed"] and watermark["loaded_dll_path_matches"]
    assert watermark["png_sha256"] == sha((ROOT / files["image"]).read_bytes())
    guide = (ROOT / "docs/beginner-guide.md").read_text()
    assert files["image"].removeprefix("docs/") in guide
    assert files["record"].removeprefix("docs/") in guide
    return check


def main(dll=None):
    def load(path):
        return json.loads((ROOT / path).read_text())

    record = load("docs/data/portable-dll-rc12.json")
    config = load(record["configuration"])
    manifest = load("dll/manifest.json")
    rederived = load("dll/" + config["base"]["path"])
    assert record["schema"] == 1 and record["status"] in STATUSES
    for key, field in (
        ("release_version", "release_version"),
        ("provider_name", "provider_name"),
        ("dll_sha256", "expected_dll_sha256"),
        ("dll_bytes", "expected_dll_bytes"),
    ):
        assert record[key] == config[field], key
    previous = record["previous_release"]
    assert previous == {
        "version": manifest["release_version"],
        "provider_name": manifest["provider_name"],
        "dll_sha256": manifest["expected_dll_sha256"],
        "dll_bytes": manifest["expected_dll_bytes"],
    }
    restored = rederived["elements"]["final-pass-divisions"]["slots"]
    comparison = record["rc11_comparison"]
    assert comparison["c10_slots"] == len(config["slots"]) == 34
    assert comparison["changed_slots"] == len(config["slots"]) + len(restored) == 35
    assert list(restored) == [comparison["restored_slot"]]
    assert comparison["host_patches_differing"] == [] and comparison["unexplained_bytes"] == 0
    assert comparison["changed_slots"] + comparison["moved_only_slots"] + comparison[
        "unchanged_slots"
    ] == len(manifest["replacements"])
    assert config["apply_host_patches"] == ["int8-eligibility"]
    tested = record["tested_build"]
    assert tested["dll_bytes"] == record["dll_bytes"]
    assert tested["dll_sha256"] not in (record["dll_sha256"], previous["dll_sha256"])
    assert tested["provider_name"] == record["provider_name"] + "t"
    assert [c["field"] for c in tested["delta"]] == list(DELTA_FIELDS)
    gpu = record["gpu"]
    assert gpu["census"]["provider"] == tested["provider_name"]
    assert gpu["negative_control"]["int8_programs_dispatched"] == 0
    images = set()
    for block in gpu["timing"]:
        arms = block["arms"]
        assert {"rc11", "rc12"} <= set(arms), block["block"]
        for arm in arms.values():
            assert len(arm["run_medians_ms"]) == block["rounds"]
            assert math.isclose(mean(arm["run_medians_ms"]), arm["mean_ms"], abs_tol=1e-6)
            assert arm["dll"] in ("rc11", "tested_build")
        assert len({tuple(arm["images"]) for arm in arms.values()}) == 1, block["block"]
        images.add(tuple(arms["rc11"]["images"]))
    assert len(images) == len(gpu["timing"])
    notes = (ROOT / "docs/release-notes-rc12.md").read_text()
    validation = (ROOT / "docs/portable-dll-rc12.md").read_text()
    assert record["dll_sha256"] in notes
    assert record["dll_sha256"] in validation and tested["dll_sha256"] in validation
    for change in tested["delta"]:
        assert f"`{change['offset']:#x}`" in validation, change["field"]
    check = check_release_dll(record, validation)
    baselines = check_baselines(load("docs/data/fsr-cost-20261023.json"), previous["dll_sha256"])
    if dll is not None:
        verify_binary(dll, record)
    print(
        f"PASS: RC12 {record['status']}: identity = dll/rc12.json, 35 slots changed from RC11 with "
        "RC11's host edits, tested build = RC12 except label/size/checksum bytes, "
        f"{len(gpu['timing'])} timed GPU blocks with RC11's images, release DLL GPU start "
        f"{check['status']}; {baselines} same-day original-shader baselines"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dll", type=Path, help="Also restore the three label bytes and verify the tested build"
    )
    main(parser.parse_args().dll)
