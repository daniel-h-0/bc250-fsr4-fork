#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the RC11.1 driver recipe (RC11 plus the one upstream nir_opt_uub fix) and its record."""

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
    archive = record["release_archive"]
    assert archive["name"] == f"bc250-fsr4-v{current['version']}-linux-glibc236-x86_64.tar.gz"
    assert re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
    assert re.fullmatch(r"[0-9a-f]{40}", archive["packaged_from"])
    qualification = record["qualification"]
    assert qualification["status"] in QUALIFICATION
    if qualification["status"] == "qualified":
        # Evidence must name this archive, the unfixed RC11 control and complete passes.
        assert qualification["archive_sha256"] == archive["sha256"]
        assert qualification["control_driver_sha256"] == rc11["sha256"]
        programs = qualification["fsr4_compiled_programs"]["cases"]
        assert len(programs) == 4
        assert all(case["identical_to_control"] and case["blocks"] > 0 for case in programs)
        pixels = qualification["fsr4_pixels"]
        for key in (
            "provider_1080p_rows_identical_to_references",
            "control_rows_identical_to_references",
            "dll_1440p_rows_identical_to_control",
        ):
            passed, total = pixels[key]
            assert 0 < total == passed, key
        helixsr = qualification["helixsr_frame_generation"]
        for key in ("light_clean", "full_clean"):
            passed, total = helixsr["candidate"][key]
            assert 0 < total == passed, key
        for key in ("light_black", "full_lattice"):
            affected, total = helixsr["control"][key]
            assert 0 < total == affected, key
        notes = (ROOT / "docs/release-notes-rc11.1.md").read_text()
        assert archive["name"] in notes and archive["sha256"] in notes
    print(
        "PASS: RC11.1 driver recipe = RC11 + upstream nir_opt_uub fix; qualification "
        + qualification["status"]
    )


if __name__ == "__main__":
    main()
