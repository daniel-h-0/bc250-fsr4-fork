#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check that the RC11.1 driver recipe is RC11's plus the one upstream nir_opt_uub fix."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = "patches/0004-nir-opt_uub-fix-imax-optimization.patch"
FIXED_SOURCE = "src/compiler/nir/nir_opt_uub.c"
UPSTREAM = "a13dfbea0f898dde77382ccba8f699673885952a"
QUALIFICATION = {"pending", "qualified"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    def load(path):
        return json.loads((ROOT / path).read_text())

    current = load("v4/manifest.json")
    previous = load("v4/legacy/rc11-manifest.json")
    assert previous["version"] == "4.0.0-rc11"
    assert current["version"] == "4.0.0-rc11.1"
    for field in ("mesa", "architecture", "provider_sha256", "base_archive", "lineage"):
        assert current[field] == previous[field], field
    assert current["source_overlays"] == previous["source_overlays"]
    assert current["patch_order"] == [*previous["patch_order"], PATCH]
    patch = (ROOT / "v4" / PATCH).read_bytes()
    assert current["source_inputs"] == {**previous["source_inputs"], PATCH: sha(patch)}
    assert FIXED_SOURCE not in previous["sources"]
    assert set(current["sources"]) == {*previous["sources"], FIXED_SOURCE}
    for name, expected in previous["sources"].items():
        assert current["sources"][name] == expected, name

    text = patch.decode()
    assert text.startswith("From " + UPSTREAM + " ")
    assert re.findall(r"^\+\+\+ b/(\S+)", text, re.MULTILINE) == [FIXED_SOURCE]
    diff = text.split("\ndiff --git ", 1)[1].split("\n--\n", 1)[0]
    changed = [
        line
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert changed == [
        "-   nir_scalar replacement = alu->op == nir_op_umax ? const_src : src;",
        "+   nir_scalar replacement = op == nir_op_umax ? const_src : src;",
    ]

    record = load("docs/data/driver-rc11.1.json")
    rc11 = load("docs/data/portable-dll-rc11.json")["driver"]
    assert record["schema"] == 1 and record["version"] == current["version"]
    assert record["source_manifest_sha256"] == sha((ROOT / "v4/manifest.json").read_bytes())
    assert record["previous"]["source_manifest_sha256"] == rc11["source_manifest_sha256"]
    assert record["previous"]["source_manifest_sha256"] == sha(
        (ROOT / "v4/legacy/rc11-manifest.json").read_bytes()
    )
    assert record["previous"]["driver_sha256"] == rc11["sha256"]
    assert record["previous"]["rebuilt_byte_identical"]
    assert record["fix"]["patch_sha256"] == sha(patch)
    assert record["fix"]["upstream_commit"] == UPSTREAM
    assert record["fix"]["fixed_source_sha256"] == current["sources"][FIXED_SOURCE]
    driver = record["driver"]
    for key in ("sha256", "unstripped_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", driver[key]), key
    assert driver["sha256"] != rc11["sha256"]
    assert record["elf_comparison"]["changed_functions_outside_fix"] == []
    assert record["qualification"]["status"] in QUALIFICATION
    print(
        "PASS: RC11.1 driver recipe = RC11 + upstream nir_opt_uub fix; qualification "
        + record["qualification"]["status"]
    )


if __name__ == "__main__":
    main()
