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
from statistics import mean
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
    documents = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
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
