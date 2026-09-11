#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check source inputs, documentation and published data without a GPU or network."""

import argparse
import csv
import hashlib
import json
import math
import re
import runpy
import subprocess
import sys
from pathlib import Path, PurePosixPath
from statistics import mean, median
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_text())


def relative_file(root, relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Unsafe source path: " + relative)
    result = root / path
    require(result.is_file() and not result.is_symlink(), "Missing/nonregular source: " + relative)
    return result


def check_snapshot(root):
    path = root / "source-snapshot.json"
    if not path.exists():
        return "checkout/source tree (no exported snapshot metadata)"
    snapshot = load(path)
    require(snapshot["schema"] == 1, "Unsupported source snapshot schema")
    require(
        bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", snapshot["commit"])),
        "Invalid snapshot commit",
    )
    for relative, metadata in snapshot["files"].items():
        source = relative_file(root, relative)
        require(digest(source) == metadata["sha256"], "Source snapshot file changed: " + relative)
        mode = metadata["mode"]
        if isinstance(mode, str):
            mode = int(mode, 8) & 0o777
        require(mode in (0o644, 0o755), "Unsupported snapshot file mode: " + relative)
        require(
            bool(source.stat().st_mode & 0o111) == bool(mode & 0o111),
            "Source snapshot executable mode changed: " + relative,
        )
    return f"{len(snapshot['files'])} source snapshot hashes/modes at {snapshot['commit']}"


def check_inputs(root):
    manifest_path = root / "v4/manifest.json"
    manifest = load(manifest_path)
    require(manifest["schema"] == 1, "Unsupported source manifest schema")
    require(manifest["id"] == "bc250-fsr4-v" + manifest["version"], "Source id/version mismatch")
    require(manifest["architecture"] == "x86_64", "Only x86_64 is currently supported")
    archive = manifest["base_archive"]
    require(archive["name"] == "mesa-" + manifest["mesa"] + ".tar.xz", "Mesa/archive mismatch")
    require(bool(SHA256.fullmatch(archive["sha256"])), "Invalid Mesa archive SHA256")
    require(archive["url"].startswith("https://"), "Mesa archive URL must use HTTPS")
    inputs = manifest["source_inputs"]
    for relative, expected in inputs.items():
        require(bool(SHA256.fullmatch(expected)), "Invalid SHA256: " + relative)
        require(
            digest(relative_file(root / "v4", relative)) == expected, "Input changed: " + relative
        )
    order = manifest["patch_order"]
    require(len(order) == len(set(order)), "Duplicate patch in patch_order")
    require(
        set(order) == {path for path in inputs if path.endswith(".patch")},
        "Patch order/input mismatch",
    )
    actual = {str(path.relative_to(root / "v4")) for path in (root / "v4/patches").glob("*.patch")}
    require(set(order) == actual, "Unrecorded or missing active patch")
    touched = set()
    for relative in order:
        touched.update(
            re.findall(r"^\+\+\+ b/(\S+)", (root / "v4" / relative).read_text(), re.MULTILINE)
        )
    require(touched == set(manifest["sources"]), "Patched files/final source hashes mismatch")
    for relative, expected in manifest["sources"].items():
        require(bool(SHA256.fullmatch(expected)), "Invalid final source hash: " + relative)
    runtime = load(root / "runtime/manifest.json")
    require(runtime["schema"] == 1, "Unsupported runtime schema")
    require(
        runtime["release"]["id"] == "bc250-fsr4-runtime-" + runtime["release"]["version"],
        "Runtime id/version mismatch",
    )
    for name, expected in runtime["integration"].items():
        require(
            digest(relative_file(root, name)) == expected,
            "Runtime integration pin changed: " + name,
        )
    require(
        runtime["provider"]["sha256"] == manifest["provider_sha256"],
        "Driver/runtime provider mismatch",
    )
    require(
        runtime["driver"]["source_manifest_sha256"] == digest(manifest_path),
        "Runtime targets another driver source",
    )
    require(runtime["preset"]["FSR.Fsr4ForceModel"] == "2", "Runtime must select INT8 model 2")
    require(runtime["preset"]["FrameGen.Enabled"] == "false", "Frame generation is not qualified")
    proton = runtime["proton"]
    for component in (
        proton,
        runtime["optiscaler"],
        runtime["optipatcher"],
        runtime["provider"],
        runtime["sdk"],
        runtime["ngx_signature"],
    ):
        require(bool(SHA256.fullmatch(component["sha256"])), "Invalid runtime artifact SHA256")
        require(component["url"].startswith("https://"), "Runtime downloads require HTTPS")
    for relative, metadata in proton["files"].items():
        require(bool(SHA256.fullmatch(metadata["sha256"])), "Invalid Proton file pin: " + relative)
    qualification = load(root / "docs/qualification.json")
    if qualification["version"] == manifest["version"]:
        require(
            qualification["source_manifest_sha256"] == digest(manifest_path),
            "Qualified source manifest changed without a new version",
        )
    target = load(root / "v4/build-targets/steamos-3.8.json")
    require(target["schema"] == 1 and target["id"] == "steamos-3.8-x86_64", "Invalid ABI target")
    names = set()
    for package in target["packages"]:
        require(package["name"] not in names, "Duplicate target package")
        names.add(package["name"])
        require(bool(SHA256.fullmatch(package["sha256"])), "Invalid target package hash")
        require(
            package["url"].startswith("https://steamdeck-packages.steamos.cloud/archlinux-mirror/"),
            "Target packages must come from Valve's mirror",
        )
    portable = load(root / "v4/build-targets/linux-glibc236.json")
    require(
        portable["schema"] == 1 and portable["id"] == "linux-glibc236-x86_64",
        "Invalid portable ABI target",
    )
    require(
        len({p["name"] for p in portable["packages"]}) == len(portable["packages"]),
        "Duplicate portable package",
    )
    for package in portable["packages"]:
        require(bool(SHA256.fullmatch(package["sha256"])), "Invalid portable package hash")
        require(
            package["url"].startswith("https://deb.debian.org/debian/pool/"),
            "Portable packages must come from Debian",
        )
    require(portable["libdrm"] == target["libdrm"], "Static DRM source differs between ABI targets")
    return f"{len(inputs)} pinned inputs, {len(touched)} modified Mesa files, runtime/qualification metadata"


def markdown_anchors(text):
    anchors = set()
    counts = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        counts[slug] = count + 1
    return anchors


def check_docs(root):
    # Archived upstream prose is historical; only its orientation index is maintained.
    documents = [*root.glob("*.md"), *(root / "docs").rglob("*.md"), *(root / "dll").rglob("*.md")]
    if (root / "legacy/README.md").exists():
        documents.append(root / "legacy/README.md")
    if (root / "runtime/manifest.json").is_file():
        version = load(root / "runtime/manifest.json")["release"]["version"]
        for name in (
            "README.md",
            "docs/games.md",
            "docs/releases.md",
            "docs/upgrading-rc1.md",
            "docs/upgrading-rc2.md",
        ):
            for actual in re.findall(
                r"bc250-fsr4-setup-([A-Za-z0-9.-]+)\.tar\.gz", (root / name).read_text()
            ):
                require(actual == version, "Stale setup version in " + name)
    for document in documents:
        prose = re.sub(r"```.*?```", "", document.read_text(), flags=re.DOTALL)
        for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", prose):
            target = target.split(' "', 1)[0].strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            resolved = (document.parent / unquote(url.path)).resolve() if url.path else document
            label = f"{document.relative_to(root)}: {target}"
            require(resolved.is_relative_to(root.resolve()), "Link leaves source tree: " + label)
            require(resolved.exists(), "Broken documentation link: " + label)
            if url.fragment and resolved.suffix == ".md":
                require(
                    unquote(url.fragment) in markdown_anchors(resolved.read_text()),
                    "Missing Markdown heading: " + label,
                )
    return f"local links in {len(documents)} maintained Markdown files"


def close(actual, expected, label):
    require(
        math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10),
        "Published data mismatch: " + label,
    )


def check_performance(root):
    folder = root / "docs/data/performance-20260907"
    result = load(folder / "results.json")
    qualification = load(root / "docs/qualification.json")
    with (folder / "samples.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected_runs = {run["run"]: run for run in result["runs"]}
    require(len(expected_runs) == 12 and len(rows) == 360, "Performance campaign size changed")
    require({row["run"] for row in rows} == set(expected_runs), "Missing or extra scored run")
    for name, run in expected_runs.items():
        selected = [row for row in rows if row["run"] == name]
        require(len(selected) == run["sample_count"] == 30, "Scored run size: " + name)
        for row in selected:
            require(
                [int(row["width"]), int(row["height"])] == run["output"]
                and row["arm"] == run["arm"],
                "Run identity mismatch: " + name,
            )
            require(
                float(row["PawnCount"]) == 1 and float(row["IgnoredForSummary"]) == 0,
                "Invalid scoring row: " + name,
            )
        for field, expected in run["means_ms"].items():
            close(mean(float(row[field]) for row in selected), expected, name + "/" + field)
        close(1000 / run["means_ms"]["FrameTime"], run["fps"], name + "/fps")
    for summary in result["summary"]:
        output = summary["output"]
        for arm in ("v3", "v4"):
            selected = [
                row
                for row in rows
                if [int(row["width"]), int(row["height"])] == output and row["arm"] == arm
            ]
            require(len(selected) == 60, "Performance resolution/arm size mismatch")
            frame_time = mean(float(row["FrameTime"]) for row in selected)
            close(frame_time, summary[arm]["frame_time_ms"], f"{output}/{arm}/frame_time")
            close(1000 / frame_time, summary[arm]["fps"], f"{output}/{arm}/fps")
            close(
                mean(float(row["GPUTime"]) for row in selected),
                summary[arm]["gpu_time_ms"],
                f"{output}/{arm}/gpu",
            )
        for field, key in (
            ("fps", "fps_change_percent"),
            ("gpu_time_ms", "gpu_time_change_percent"),
        ):
            close((summary["v4"][field] / summary["v3"][field] - 1) * 100, summary[key], key)
    performance = qualification["performance"]
    require(
        performance["summary"] == result["summary"], "Qualification/performance summaries diverged"
    )
    require(
        performance["result_sha256"] == digest(folder / "results.json"),
        "Performance result digest changed",
    )
    return "all 360 scoring rows reproduce the 12 runs and published three-resolution summary"


def check_syntax(root):
    python_files = [
        *(root / "scripts").glob("*.py"),
        *(root / "tests").glob("*.py"),
        *(root / "runtime").glob("*.py"),
        *(root / "dll").glob("*.py"),
        *(root / "legacy/game-setup").glob("*.py"),
    ]
    for path in python_files:
        compile(path.read_bytes(), str(path), "exec")
    shell_files = [*root.glob("*.sh"), *(root / "scripts").glob("*.sh")]
    for path in shell_files:
        subprocess.run(["bash", "-n", str(path)], check=True)
    for path in [
        *(root / "v4").glob("*.json"),
        *(root / "runtime").glob("*.json"),
        *(root / "docs").rglob("*.json"),
    ]:
        load(path)
    return f"{len(python_files)} Python and {len(shell_files)} shell entry points, JSON documents"


def check_fsr_cost(root):
    directory = root / "docs/data/fsr-cost-20260908"
    reconstructed = runpy.run_path(str(directory / "reconstruct.py"))["reconstruct"]()
    require(reconstructed == load(directory / "estimates.json"), "FSR cost reconstruction changed")
    return "historical FFX timestamps and matched GPU deltas reproduce the published cost estimates"


def check_rc8_record(record, manifest):
    require(record["schema"] == 1, "RC8 record schema")
    for actual, expected in (
        (record["release_version"], manifest["release_version"]),
        (record["provider_name"], manifest["provider_name"]),
        (record["dll_sha256"], manifest["expected_dll_sha256"]),
        (record["dll_bytes"], manifest["expected_dll_bytes"]),
        (record["reference_rc7_sha256"], manifest["previous_release"]["dll_sha256"]),
    ):
        require(actual == expected, "RC8 qualification identity mismatch")
    binding = record["checkpoint_binding"]
    require(
        binding["release_sha256"] == record["dll_sha256"]
        and binding["all_348_shaders_identical_to_checkpoint"]
        and binding["only_binary_changes_from_checkpoint_are_provider_label_and_pe_checksum"]
        and binding["changed_byte_offsets"] == [352, 353, 819973, 819974]
        and binding["sdk_synchronization_preserved"],
        "RC8 checkpoint binding mismatch",
    )
    slots = {r["original_offset"]: r for r in manifest["replacements"]}
    shader_set = [[offset, row["replacement_sha256"]] for offset, row in sorted(slots.items())]
    require(
        hashlib.sha256(json.dumps(shader_set, separators=(",", ":")).encode()).hexdigest()
        == record["shader_set_sha256"],
        "RC8 shader-set fingerprint mismatch",
    )
    previous = dict(record["reference_rc7_shader_set"])
    changed = {int(row["offset"], 16): row for row in record["changed_shader_slots"]}
    require(len(previous) == len(slots) == 348 and set(previous) == set(slots), "RC7 shader set")
    require(
        len(changed) == len(record["changed_shader_slots"]) == 16
        and set(changed) == {o for o, r in slots.items() if r["replacement_sha256"] != previous[o]},
        "RC8 changed shader slots",
    )
    for offset, row in changed.items():
        source = slots[offset]
        require(
            row["source"] == source["source"]
            and row["source_sha256"] == source["source_sha256"]
            and row["shader_sha256"] == source["replacement_sha256"],
            "RC8 changed shader identity mismatch",
        )
    changed_hashes = {row["shader_sha256"] for row in changed.values()}

    def check_run(row, frames):
        require(row["valid"] and row["finite"] and row["returncode"] == 0, "Invalid RC8 run")
        require(row["frames"] == frames and row["loaded_dll_path_matches"], "RC8 run identity")
        require(row["variant"] in ("v4r7", "v4r8"), "RC8 run variant")
        expected = (
            (record["dll_sha256"], record["provider_name"])
            if row["variant"] == "v4r8"
            else (record["reference_rc7_sha256"], "4.1.1r7")
        )
        require((row["dll_sha256"], row["provider"]) == expected, "RC8 run DLL/provider mismatch")
        require(
            row["driver_sha256"] == record["environment"]["driver_sha256"], "RC8 driver mismatch"
        )
        require(
            math.isfinite(row["pixel_min"])
            and math.isfinite(row["pixel_max"])
            and row["pixel_min"] <= row["pixel_max"],
            "RC8 non-finite image bounds",
        )

    campaign = record["performance"]
    order = ["v4r7", "v4r8", "v4r8", "v4r7"] * 2
    require(campaign["complete"] and not campaign["trace"], "Incomplete/traced RC8 campaign")
    require(
        campaign["frames_per_run"] == 600
        and campaign["discard_first_frames"] == 300
        and campaign["output"] == [2560, 1440]
        and campaign["render"] == [1706, 960]
        and campaign["preset"] == "Quality",
        "RC8 performance workload mismatch",
    )
    rows = campaign["rows"]
    require(campaign["order"] == [r["variant"] for r in rows] == order, "RC8 timing order")
    require(len({r["name"] for r in rows}) == 8, "Duplicate RC8 timing run")
    summary = campaign["summary"]
    for row in rows:
        check_run(row, 600)
        values = row["gpu_ms"]
        require(
            not row["shader_dumping"]
            and len(values) == 600
            and all(math.isfinite(v) and v > 0 for v in values),
            "Invalid RC8 GPU samples",
        )
        close(median(values[300:]), row["median_ms"], "RC8 per-run median")
        clocks = row["scoring_telemetry"]
        require(
            clocks
            and all(
                300 <= t["completed_frames"] < 600 and t["gpu_clock_hz"] == 1850000000
                for t in clocks
            ),
            "RC8 scoring clock mismatch",
        )
        require(row["pixels_sha256"] == summary["pixels_sha256"], "RC8 scored image mismatch")
    medians = {}
    for variant in ("v4r7", "v4r8"):
        values = [r["median_ms"] for r in rows if r["variant"] == variant]
        result = summary["summaries"][variant]
        require(values == result["run_medians_ms"], "RC8 run medians")
        for field, actual in (
            ("median_ms", median(values)),
            ("min_ms", min(values)),
            ("max_ms", max(values)),
        ):
            close(actual, result[field], "RC8 " + field)
        medians[variant] = median(values)
    close(medians["v4r7"] - medians["v4r8"], summary["saving_ms"], "RC8 saving")
    close(
        100 * (1 - medians["v4r8"] / medians["v4r7"]), summary["saving_percent"], "RC8 percentage"
    )
    require(
        summary["all_images_identical"]
        and summary["sampled_scoring_clock_mhz"] == 1850
        and summary["all_candidate_runs_below_all_controls"]
        and summary["summaries"]["v4r8"]["max_ms"] < summary["summaries"]["v4r7"]["min_ms"],
        "RC8 summary classification mismatch",
    )
    quality = record["quality"]
    require(
        quality["complete"] and quality["output"] == [2560, 1440], "Incomplete RC8 quality matrix"
    )
    require(
        [c["scenario"] for c in quality["cases"]]
        == ["hdr", "sdr", "motion", "reset", "resize", "rcas", "static"],
        "RC8 quality scenario coverage",
    )
    require(quality["preflight"]["scenario"] == "static", "RC8 preflight scenario")
    for index, case in enumerate([quality["preflight"], *quality["cases"]]):
        require(
            case["render"] == ([1506, 848] if index == 7 else [1706, 960]), "RC8 image input size"
        )
        require([r["variant"] for r in case["rows"]] == ["v4r7", "v4r8"], "RC8 image pair")
        for row in case["rows"]:
            check_run(row, 64)
            require(row["shader_dumping"], "RC8 image shader identification missing")
            require(row["pixels_sha256"] == case["pixels_sha256"], "RC8 quality image mismatch")
            if row["variant"] == "v4r8":
                require(
                    changed_hashes <= {r["sha256"] for r in row["observed_shaders"]},
                    "RC8 changed shader coverage missing",
                )
    evidence = record["development_component_evidence"]
    require(
        [r["pass"] for r in evidence["modified_model_weights"]] == [7, 8, 9, 11],
        "RC8 fallback coverage",
    )
    for row in evidence["modified_model_weights"]:
        require(row["component_shader_sha256"] in changed_hashes, "RC8 fallback component identity")
        require(
            row["fast_path_witness_changes_image"] and len(row["mutations"]) >= 2,
            "RC8 fallback witnesses",
        )
        require(
            all(
                m["exact_fallback_image"] and m["mutation_changes_reference_image"]
                for m in row["mutations"]
            ),
            "RC8 fallback mismatch",
        )
    for row in evidence["cpu_arithmetic"]:
        require(
            row["component_shader_sha256"] in changed_hashes and row["results"]["valid"],
            "RC8 CPU component identity",
        )


def check_dll(root):
    if not (root / "dll/manifest.json").exists():
        return "historical source without a portable DLL component"
    output = subprocess.check_output(
        [sys.executable, "-B", str(root / "dll/build.py"), "--verify-sources"], text=True
    )
    manifest = load(root / "dll/manifest.json")
    record = load(root / "docs/data/portable-dll-rc7.json")
    require(
        record["dll_sha256"] == manifest["previous_release"]["dll_sha256"],
        "Historical RC7 DLL qualification identity mismatch",
    )
    campaign = record["performance"]
    require(campaign["complete"] and not campaign["trace"], "Incomplete/traced DLL timing campaign")
    require(campaign["frames"] == 240 and len(campaign["rows"]) == 12, "DLL timing campaign size")
    require(set(campaign["summaries"]) == {"1080", "1440", "2160"}, "DLL timing output sizes")
    for size, summary in campaign["summaries"].items():
        rows = [row for row in campaign["rows"] if row["size"] == size]
        require([r["variant"] for r in rows] == ["v4", "dll", "dll", "v4"], "DLL timing ABBA order")
        for row in rows:
            values = row["gpu_ms"]
            require(
                len(values) == 240 and all(math.isfinite(v) and v > 0 for v in values),
                "Invalid DLL GPU samples",
            )
            close(median(values[120:]), row["median_ms"], "DLL per-run median")
            require(row["pixels_sha256"] == summary["pixels_sha256"], "DLL output image mismatch")
            if row["variant"] == "dll":
                require(row["tested_dll_sha256"] == record["dll_sha256"], "Timing used another DLL")
        control = mean(r["median_ms"] for r in rows if r["variant"] == "v4")
        candidate = mean(r["median_ms"] for r in rows if r["variant"] == "dll")
        close(control, summary["v4_mean_of_medians_ms"], "DLL control summary")
        close(candidate, summary["dll_mean_of_medians_ms"], "DLL candidate summary")
        close(candidate / control, summary["dll_relative_time"], "DLL relative GPU time")
        close(
            (candidate / control - 1) * 100,
            summary["dll_time_change_percent"],
            "DLL GPU percentage",
        )
        legacy = [row for row in campaign["legacy_rows"] if row["size"] == size]
        require(len(legacy) == 2, "Missing existing-v4 timing controls")
        for row in legacy:
            values = row["gpu_ms"]
            require(
                len(values) == 240 and all(math.isfinite(v) and v > 0 for v in values),
                "Invalid existing-v4 GPU samples",
            )
            close(median(values[120:]), row["median_ms"], "Existing-v4 per-run median")
            require(row["pixels_sha256"] == summary["pixels_sha256"], "Control image mismatch")
        legacy_mean = mean(row["median_ms"] for row in legacy)
        comparison = campaign["legacy_comparison"][size]
        close(legacy_mean, comparison["legacy_v4_mean_of_medians_ms"], "Existing-v4 summary")
        close(
            (candidate / legacy_mean - 1) * 100,
            comparison["rc7_time_change_from_legacy_percent"],
            "DLL/existing-v4 percentage",
        )
    require(len(campaign["legacy_rows"]) == 6, "Existing-v4 timing campaign size")
    quality = record["quality"]
    require(quality["complete"] and len(quality["rows"]) == 20, "Incomplete DLL image matrix")
    for case in quality["rows"]:
        variants = case["variants"]
        require(len(variants) in (2, 4), "Incomplete DLL image comparison")
        require(all(v["finite"] for v in variants), "Non-finite DLL image")
        images = {v["pixels_sha256"] for v in variants}
        references = {v["pixels_sha256"] for v in variants if v["arm"] == "reference"}
        require(case["qualified"] == (len(images) == 1), "Incorrect image qualification")
        require(case["reference_stable"] == (len(references) == 1), "Incorrect reference stability")
        for variant in variants:
            if variant["arm"] == "dll":
                require(variant["dll_sha256"] == record["dll_sha256"], "Image used another DLL")
        if not case["qualified"]:
            require(not case["reference_stable"], "Unresolved stable-reference image mismatch")
    check_rc8_record(load(root / manifest["qualification_record"]), manifest)
    return (
        output.strip() + "; RC7's 4,320 samples/20 cases and RC8's 4,800 samples/7 cases verified"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="Source tree or extracted release to check"
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="Only static consistency/data checks"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    for check in (
        check_snapshot,
        check_inputs,
        check_dll,
        check_docs,
        check_performance,
        check_fsr_cost,
        check_syntax,
    ):
        print("PASS:", check(root), flush=True)
    if not args.skip_tests:
        subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root,
            check=True,
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
