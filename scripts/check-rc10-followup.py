#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Recompute RC10 follow-up results from the retained, separate campaign records."""

import hashlib
import json
import math
import tarfile
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(name):
    return json.loads((ROOT / "docs/data" / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10)


def main():
    portability = load("rc10-cache-portability-20260914.json")
    require(portability["complete"], "Incomplete cache userspace record")
    require(
        portability["helper_sha256"] == digest(ROOT / "scripts/shared-cache.py")
        and portability["bootstrap_sha256"] == digest(ROOT / "scripts/shared-cache.sh"),
        "Cache launcher changed since portability qualification",
    )
    require(
        len(portability["rows"]) == 4
        and all(row["tests_pass"] and row["readonly_fallback"] for row in portability["rows"]),
        "A userspace or read-only fallback failed",
    )
    cache_gpu = load("rc10-cache-gpu-followup-20260914.json")
    require(cache_gpu["complete"] and len(cache_gpu["rows"]) == 4, "GPU cache checks incomplete")
    require(cache_gpu["helper_sha256"] == portability["helper_sha256"], "GPU cache helper differs")
    require(cache_gpu["original_foz_files_unchanged"], "Original Fossilize cache changed")
    require(all(row["valid"] for row in cache_gpu["rows"]), "GPU cache check failed")
    require(len({row["pixels_sha256"] for row in cache_gpu["rows"]}) == 1, "Cache images differ")
    compiler = load("rc10-compiler-followup-20260914.json")
    require(compiler["complete"], "Incomplete compiler screen")
    runs = compiler["runs"]
    require(len(runs) == 12 and len({run["name"] for run in runs}) == 12, "Run inventory differs")
    for run in runs:
        require(run["valid"], "Invalid compiler run")
        require(run["frames"] == len(run["gpu_ms"]), "GPU samples missing")
        require(
            all(math.isfinite(value) and value > 0 for value in run["gpu_ms"]), "Invalid GPU time"
        )
        startup = run["startup"]
        marks = startup["marks"]
        for phase in ("context", "first_dispatch"):
            begin, freq = marks[phase + "_begin"]
            end, end_freq = marks[phase + "_end"]
            require(freq == end_freq and freq > 0 and end >= begin, "Invalid CPU counters")
            require(close(startup["timing"][phase], (end - begin) / freq), "CPU duration differs")
        if run["frames"] == 4:
            require(
                all(value == 0 for value in startup["cache_bytes_before"].values()),
                "Cold cache populated",
            )
        else:
            require(run["frames"] == 600, "Unexpected steady-state workload")
            require(
                close(run["scored_median_ms"], median(run["gpu_ms"][300:])), "GPU median differs"
            )
    for frames, count in ((4, 8), (600, 4)):
        selected = [run for run in runs if run["frames"] == frames]
        require(len(selected) == count, "Repeat count differs")
        require(len({run["pixels_sha256"] for run in selected}) == 1, "Images differ")
    cold = {}
    for variant in ("a", "cse"):
        selected = [run for run in runs if run["variant"] == variant]
        cold[variant] = median(
            sum(run["startup"]["timing"].values()) for run in selected if run["frames"] == 4
        )
        warm = median(run["scored_median_ms"] for run in selected if run["frames"] == 600)
        require(
            close(cold[variant], compiler["cold_cpu_medians_seconds"][variant]),
            "Cold median differs",
        )
        require(close(warm, compiler["warm_gpu_medians_ms"][variant]), "Warm median differs")
    require(
        close((1 - cold["cse"] / cold["a"]) * 100, compiler["cold_reduction_percent"]),
        "Compilation improvement differs",
    )
    native = compiler["native_comparison"]
    require(native["complete"] and len(native["pairs"]) == 36, "Native comparison incomplete")
    for pair in native["pairs"]:
        before, after = pair["rows"]
        require(before["native_sha256"] == after["native_sha256"], "Native machine code changed")
        require(before["stats"] == after["stats"], "Native resources changed")
        require(pair["same_code"] and pair["same_stats"], "Native result flags disagree")
    driver = load("rc10-driver-prototype-20260914.json")
    require(len(driver["runs"]) == 12, "Driver prototype run inventory differs")
    for size in ("1080p", "1440p", "4k"):
        selected = [run for run in driver["runs"] if run["size"] == size]
        require(
            len(selected) == 4 and all(run["valid"] for run in selected), "Driver route missing"
        )
        require(len({run["pixels_sha256"] for run in selected}) == 1, "Driver images differ")
        require(all(run["frames"] == 64 for run in selected), "Driver workload differs")
    final_driver = load("rc10-driver-followup-20260914.json")
    require(
        final_driver["complete"] and not final_driver["release_qualified"], "Driver scope differs"
    )
    require(len(final_driver["runs"]) == 46, "Driver follow-up run inventory differs")
    identity = final_driver["cache_identity"]
    require(
        identity["complete"] and identity["opt_out_distinct"] and identity["stable"],
        "Driver cache identity failed",
    )
    require(
        identity["rows"][0]["uuid"] == identity["rows"][2]["uuid"], "Driver cache UUID is unstable"
    )
    require(
        identity["rows"][0]["uuid"] != identity["rows"][1]["uuid"],
        "Driver opt-out shares a cache UUID",
    )
    isolation = final_driver["resize_isolation"]
    require(
        isolation["complete"] and isolation["old_pixels"] == isolation["new_pixels"],
        "Provider resize behavior changed",
    )
    for scenario in ("sdr", "hdr", "motion", "reset", "resize", "rcas"):
        selected = {
            run["variant"]: run
            for run in final_driver["runs"]
            if run["name"].startswith("mode-" + scenario + "-")
        }
        require(set(selected) == {"a", "cse", "provider"}, "Driver mode route missing")
        require(
            all(run["valid"] and run["frames"] == 64 for run in selected.values()),
            "Invalid driver mode check",
        )
        require(
            selected["a"]["pixels_sha256"] == selected["cse"]["pixels_sha256"],
            "Compiler mode image changed",
        )
        expected = (
            isolation["old_pixels"] if scenario == "resize" else selected["a"]["pixels_sha256"]
        )
        require(selected["provider"]["pixels_sha256"] == expected, "Provider mode image changed")
    for size in ("1080p", "1440p", "4k"):
        for variant in ("a", "provider"):
            selected = [
                run
                for run in final_driver["runs"]
                if run["frames"] == 600 and run["size"] == size and run["variant"] == variant
            ]
            require(
                len(selected) == (6 if size == "1080p" else 2), "Driver repeat inventory differs"
            )
            values = []
            for run in selected:
                require(run["valid"] and len(run["gpu_ms"]) == 600, "Invalid driver scoring run")
                value = median(run["gpu_ms"][300:])
                require(close(value, run["median_ms"]), "Driver scoring median differs")
                values.append(value)
            require(
                close(median(values), final_driver["performance"][size]["medians_ms"][variant]),
                "Driver summary differs",
            )
    selected = load("rc10-selected-compiler-20260914.json")
    require(
        selected["complete"] and not selected["release_qualified"],
        "Selected candidate scope differs",
    )
    proposal = selected["candidate"]
    require(
        proposal["changed_slots"] == 48 and proposal["unchanged_slots"] == 300,
        "Selected slot boundary changed",
    )
    proven = {pair["rows"][1]["shader_sha256"] for pair in native["pairs"]}
    require(
        {change["replacement_shader_sha256"] for change in proposal["changes"]} == proven,
        "Selected candidate includes an unproven shader",
    )
    require(
        len(selected["runs"]) == 22 and all(run["valid"] for run in selected["runs"]),
        "Selected candidate runs incomplete",
    )
    cold = {}
    for variant in ("a", "selected"):
        group = [
            run
            for run in selected["runs"]
            if run["category"] == "cold" and run["variant"] == variant
        ]
        require(len(group) == 2, "Selected cold repeat count differs")
        values = []
        for run in group:
            startup = run["startup"]
            total = 0
            for phase in ("context", "first_dispatch"):
                begin, freq = startup["marks"][phase + "_begin"]
                end, end_freq = startup["marks"][phase + "_end"]
                require(
                    freq == end_freq and freq > 0 and end >= begin, "Selected CPU counters invalid"
                )
                total += (end - begin) / freq
            require(
                all(value == 0 for value in startup["cache_bytes_before"].values()),
                "Selected cold cache populated",
            )
            values.append(total)
        cold[variant] = median(values)
        require(
            close(cold[variant], selected["cold_medians_seconds"][variant]),
            "Selected cold median differs",
        )
    require(
        close((1 - cold["selected"] / cold["a"]) * 100, selected["cold_reduction_percent"]),
        "Selected compiler improvement differs",
    )
    warm = [run for run in selected["runs"] if run["frames"] == 600]
    require(
        len(warm) == 12 and len({run["pixels_sha256"] for run in warm}) == 1,
        "Selected sustained images differ",
    )
    for run in warm:
        require(
            len(run["gpu_ms"]) == 600 and close(median(run["gpu_ms"][300:]), run["median_ms"]),
            "Selected GPU samples differ",
        )
    capsule = ROOT / "v4/experimental/compile-cse"
    source = json.loads((capsule / "manifest.json").read_text())
    archive = capsule / "source-proposal.tar.xz"
    require(digest(archive) == source["archive_sha256"], "CSE source capsule changed")
    expected = {
        f"dll/shaders/{change['original_offset']:08x}.ll": change["replacement_source_sha256"]
        for change in proposal["changes"]
    }
    require(set(source["files"]) == set(expected), "CSE source proposal inventory differs")
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        require(
            len(members) == 48 and {member.name for member in members} == set(expected),
            "CSE archive members differ",
        )
        for member in members:
            require(
                member.isfile() and member.size == source["files"][member.name]["bytes"],
                "CSE archive member type/size differs",
            )
            with bundle.extractfile(member) as stream:
                require(
                    hashlib.file_digest(stream, "sha256").hexdigest() == expected[member.name],
                    "CSE source member changed",
                )
    custom_code = load("rc10-custom-driver-code-20260914.json")
    require(set(custom_code) == {"legacy", "port"}, "Custom driver code coverage differs")
    for flavor, evidence in custom_code.items():
        require(
            evidence["complete"] and len(evidence["rows"]) == 36,
            "Custom driver code check incomplete",
        )
        require(
            evidence["all_machine_code_identical"] and evidence["all_hardware_config_identical"],
            "Custom driver code changed",
        )
        require(
            {row["shader_pairs"][1]["shader_sha256"] for row in evidence["rows"]} == proven,
            "Custom driver misses a changed shader",
        )
        for row in evidence["rows"]:
            for key in (
                "code_sha256",
                "exec_sha256",
                "config_sha256",
                "info_sha256",
                "stats_sha256",
                "sizes",
            ):
                require(
                    row["original"][key] == row["candidate"][key],
                    "Custom driver comparison differs: " + flavor + "/" + key,
                )
    print("PASS: RC10 cache, compiler, native-code and driver follow-up evidence recomputes")


if __name__ == "__main__":
    main()
