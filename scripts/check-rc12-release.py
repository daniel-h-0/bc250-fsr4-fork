#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the RC12 release record: identity, RC11 comparison, tested-build delta and GPU data."""

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"candidate", "qualified"}
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
    assert record["dll_sha256"] in notes and tested["dll_sha256"] in notes
    if dll is not None:
        verify_binary(dll, record)
    print(
        f"PASS: RC12 {record['status']}: identity = dll/rc12.json, 35 slots changed from RC11 with "
        "RC11's host edits, tested build = RC12 except label/size/checksum bytes, "
        f"{len(gpu['timing'])} timed GPU blocks with RC11's images"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dll", type=Path, help="Also restore the three label bytes and verify the tested build"
    )
    main(parser.parse_args().dll)
