#!/usr/bin/env python3
"""Recompute the historical FFX anchor and explicitly model the README estimates."""

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def reconstruct():
    provenance = json.loads((HERE / "provenance.json").read_text())
    samples = HERE / "ffx-samples.csv"
    assert hashlib.sha256(samples.read_bytes()).hexdigest() == provenance["export_sha256"]
    with samples.open() as stream:
        rows = list(csv.DictReader(stream))
    means = {}
    for run in provenance["runs"]:
        selected = [row for row in rows if row["run"] == run["run"]]
        assert len(selected) == run["summary"]["n"]
        for row in selected:
            assert row["driver"] == run["driver"]
            assert row["gpu_completion_fence_verified"] == "True"
            measured = (int(row["end_ticks"]) - int(row["start_ticks"])) * 1000 / int(row["frequency"])
            assert math.isclose(measured, float(row["gpu_ms"]), abs_tol=1e-10)
        mean = statistics.mean(float(row["gpu_ms"]) for row in selected)
        assert math.isclose(mean, run["summary"]["mean"], abs_tol=1e-10)
        means.setdefault(run["driver"], []).append(mean)
    anchor = statistics.mean(means["original-v3-2.82"])
    arithmetic = statistics.mean(means["installed-2.84"])
    campaign_path = HERE.parent / "performance-20260907/results.json"
    campaign = json.loads(campaign_path.read_text())
    estimates = []
    for row in campaign["summary"]:
        width, height = row["output"]
        ratio = width * height / (2560 * 1440)
        v3 = anchor * ratio
        saving = row["v3"]["gpu_time_ms"] - row["v4"]["gpu_time_ms"]
        assert 0 < saving < v3
        estimates.append({
            "output": row["output"],
            "pixel_ratio": ratio,
            "v3_cost_ms": v3,
            "v3_basis": "historical measured Balanced anchor" if ratio == 1 else "pixel-scaled estimate",
            "measured_whole_frame_gpu_saving_ms": saving,
            "v4_estimated_cost_ms": v3 - saving,
        })
    return {
        "schema": 1,
        "scope": "Illustrative FSR4 cost reconstruction; not a three-resolution isolated-pass benchmark",
        "historical_measured_ffx_ms": {"v3": anchor, "arithmetic_2_84": arithmetic, "saving": anchor - arithmetic},
        "campaign_results_sha256": hashlib.sha256(campaign_path.read_bytes()).hexdigest(),
        "assumptions": [
            "Historical packaged v3 Balanced timing approximates the later source-v3 Quality baseline.",
            "Total FSR4 GPU cost scales with output pixel count, with no fixed-cost term.",
            "The entire matched whole-frame GPU reduction is attributed to the FSR4 interval.",
        ],
        "estimates": estimates,
    }


if __name__ == "__main__":
    print(json.dumps(reconstruct(), indent=2))
