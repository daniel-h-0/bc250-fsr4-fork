#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Recompute the September 14 development results from recorded CPU/GPU inputs."""

import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def durations(row):
    marks = row["cpu_markers"]
    values = {}
    for phase in ["context", "first_dispatch"]:
        begin, end = marks[phase + "_begin"], marks[phase + "_end"]
        assert begin["frequency"] == end["frequency"] > 0
        elapsed = (end["counter"] - begin["counter"]) / begin["frequency"]
        assert math.isfinite(elapsed) and elapsed > 0
        values[phase] = elapsed
    return values


def summarize(data):
    assert data["schema"] == 1 and data["date"] == "2026-09-14"
    rows = data["cache_runs"]
    assert len(rows) == 8 and all(row["valid"] for row in rows)
    assert len({row["pixels_sha256"] for row in rows}) == 1
    cold, shared = [], []
    for row in rows:
        timing = durations(row)
        for phase, value in timing.items():
            assert value == row["timing"][phase + "_seconds"]
        total = sum(timing.values())
        assert total == row["timing"]["context_and_first_dispatch_seconds"]
        if "cold" in row["name"] or "independent" in row["name"]:
            assert row["cache_bytes_before"] == {"mesa": 0, "vkd3d": 0}
            cold.append(total)
        elif "mesa-shared" in row["name"]:
            assert row["cache_bytes_before"]["mesa"] > 0
            assert row["cache_bytes_before"]["vkd3d"] == 0
            shared.append(total)
    assert len(cold) == 4 and len(shared) == 2
    helper = data["helper_runs"]
    assert len(helper) == 4 and data["original_fossilize_bytes_unchanged"]
    assert all(row["valid"] for row in helper)
    assert len({row["pixels_sha256"] for row in helper}) == 1
    for row in helper:
        assert durations(row)["first_dispatch"] == row["first_dispatch_seconds"]
    cse = data["cse"]
    assert len(cse["cold_runs"]) == len(cse["warm_runs"]) == 4
    before, after = [], []
    for row in cse["cold_runs"]:
        assert row["valid"] and durations(row) == row["timing"]
        assert row["cache_bytes_before"] == {"mesa": 0, "vkd3d": 0}
        (after if row["name"].endswith("-cse") else before).append(sum(durations(row).values()))
    assert len(before) == len(after) == 2
    for row in cse["warm_runs"]:
        assert row["valid"] and len(row["gpu_ms"]) == 600 and row["discard_first_frames"] == 300
        assert all(math.isfinite(value) and value > 0 for value in row["gpu_ms"])
        assert statistics.median(row["gpu_ms"][300:]) == row["median_ms"]
    native = cse["native_code_comparison"]
    assert native["complete"] and native["same_native_machine_code"]
    assert native["same_resource_statistics"]
    assert native["rows"][0]["native_sha256"] == native["rows"][1]["native_sha256"]
    assert native["rows"][0]["stats"] == native["rows"][1]["stats"]
    port = data["driver_port"]
    assert port["audited_pairs"] == 42 and port["unique_original_spirv"] == 28
    assert not port["missing"] and not port["incompatible_pairs"] and not port["qualified_driver"]
    return {
        "cold_cache_median_seconds": statistics.median(cold),
        "shared_mesa_median_seconds": statistics.median(shared),
        "cse_control_median_seconds": statistics.median(before),
        "cse_candidate_median_seconds": statistics.median(after),
        "cse_startup_reduction_percent": (1 - statistics.median(after) / statistics.median(before))
        * 100,
        "native_machine_code_identical": True,
        "driver_qualified": False,
    }


if __name__ == "__main__":
    path = ROOT / "docs/data/rc10-startup-study-20260914.json"
    print(json.dumps(summarize(json.loads(path.read_text())), indent=2))
