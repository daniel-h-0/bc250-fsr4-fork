#!/usr/bin/env python3
"""Recompute the four-way GPU-cost chart from every recorded timestamp."""

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def summarize():
    config = json.loads((ROOT / "configuration.json").read_text())
    runs = json.loads((ROOT / "runs.json").read_text())
    old = ROOT / config["baseline_source"]["directory"]
    for name, expected in config["baseline_source"]["files"].items():
        if hashlib.sha256((old / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Historical baseline source changed: " + name)
    old_runs = json.loads((old / "runs.json").read_text())
    preserved = {k: r for k, r in old_runs.items() if r["variant"] != "v4r7"}
    if {k: r for k, r in runs.items() if r["variant"] != "v4r9"} != preserved:
        raise ValueError("Baseline run metadata must remain unchanged")
    old_lines = (old / "samples.csv").read_text().splitlines()
    new_lines = (ROOT / "samples.csv").read_text().splitlines()
    if [s for s in old_lines[1:] if s.split(",")[1] != "v4r7"] != [
        s for s in new_lines[1:] if s.split(",")[1] != "v4r9"
    ]:
        raise ValueError("Baseline timestamps must remain unchanged")
    samples = {}
    with (ROOT / "samples.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            key = row["run"]
            values = samples.setdefault(key, [])
            frame, value = int(row["frame"]), float(row["gpu_ms"])
            if frame != len(values) or not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid or reordered GPU timestamp: " + key)
            if (
                row["variant"] != runs[key]["variant"]
                or row["resolution"] != runs[key]["resolution"]
            ):
                raise ValueError("Timestamp/run identity mismatch: " + key)
            values.append(value)
    if set(samples) != set(runs) or len(runs) != 48:
        raise ValueError("The complete campaign requires 48 independent runs")
    cells = []
    for resolution, dimensions in config["sizes"].items():
        image_hashes = set()
        for variant in config["variants"]:
            selected = [
                key
                for key, run in runs.items()
                if run["resolution"] == resolution and run["variant"] == variant
            ]
            if len(selected) != config["runs_per_cell"]:
                raise ValueError("Incomplete technique/resolution cell")
            if {runs[key]["round"] for key in selected} != set(
                range(1, config["runs_per_cell"] + 1)
            ):
                raise ValueError("Missing or repeated launch round")
            medians = []
            for key in selected:
                run = runs[key]
                if not run["valid"] or len(samples[key]) != config["frames_per_run"]:
                    raise ValueError("Failed or incomplete run: " + key)
                if run["dll_sha256"] != config["variants"][variant]["dll_sha256"]:
                    raise ValueError("DLL identity differs: " + key)
                if run["driver_sha256"] != config["variants"][variant]["driver_sha256"]:
                    raise ValueError("Driver identity differs: " + key)
                if run["shader_dumping"] or run["trace"]:
                    raise ValueError("Diagnostic work cannot enter a scored run")
                if variant == "v4r9":
                    actual = config["rc9_resolution_orders"][run["round"] - 1][run["position"] - 1]
                    if actual != resolution:
                        raise ValueError("The RC9 resolution order differs: " + key)
                elif config["historical_orders"][run["round"] - 1][run["position"] - 1] != variant:
                    raise ValueError("The historical run order differs: " + key)
                if run["clock_min_mhz"] != 1850 or run["clock_max_mhz"] != 1850:
                    raise ValueError("Scoring clock differs: " + key)
                provider = {
                    "fsr411": "4.1.1",
                    "fsr411b": "4.1.1b",
                    "v3": "4.1.1",
                    "v4r9": "4.1.1r9",
                }
                if run["provider"].lower() != provider[variant]:
                    raise ValueError("Unexpected active provider: " + key)
                values = samples[key][config["discard_first_frames"] :]
                medians.append(statistics.median(values))
                image_hashes.add(run["pixels_sha256"])
            estimate = statistics.median(medians)
            cells.append(
                dict(
                    resolution=resolution,
                    output=dimensions["output"],
                    render=dimensions["render"],
                    variant=variant,
                    gpu_ms=estimate,
                    run_min_ms=min(medians),
                    run_max_ms=max(medians),
                    run_medians_ms=medians,
                    runs=selected,
                    run_range_percent=(max(medians) - min(medians)) / estimate * 100,
                )
            )
        if len(image_hashes) != 1:
            raise ValueError("Final images differ within a resolution; review before charting")
    comparisons = {}
    for resolution in config["sizes"]:
        values = {c["variant"]: c["gpu_ms"] for c in cells if c["resolution"] == resolution}
        comparisons[resolution] = {
            variant: dict(
                saved_ms=value - values["v4r9"],
                reduction_percent=(1 - values["v4r9"] / value) * 100,
            )
            for variant, value in values.items()
            if variant != "v4r9"
        }
    return dict(
        schema=2,
        complete=True,
        date=config["date"],
        measurement_dates=config["measurement_dates"],
        preserved_baseline_runs=36,
        fresh_rc9_runs=12,
        metric="Whole-upscaler GPU milliseconds",
        statistic="Median of four independent run medians",
        frames_total=48 * config["frames_per_run"],
        frames_scored=48 * (config["frames_per_run"] - config["discard_first_frames"]),
        full_images_byte_identical_per_resolution=True,
        cells=cells,
        rc9_savings=comparisons,
    )


if __name__ == "__main__":
    result = summarize()
    (ROOT / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    for cell in result["cells"]:
        print(
            f"{cell['resolution']:>5} {cell['variant']:>8}  {cell['gpu_ms']:.5f} ms"
            f"  range {cell['run_min_ms']:.5f}–{cell['run_max_ms']:.5f}"
        )
