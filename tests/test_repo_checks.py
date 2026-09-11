# SPDX-License-Identifier: MIT
"""Regressions for the publication consistency checks."""

import copy
import importlib.util
import json
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repo_checks", ROOT / "scripts/check-repo.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class RepositoryCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_exported_snapshot_detects_changed_file_and_executable_mode(self):
        source = self.root / "example.py"
        source.write_text("print('fixture')\n")
        source.chmod(0o755)
        snapshot = {
            "schema": 1,
            "commit": "a" * 40,
            "files": {"example.py": {"sha256": checks.digest(source), "mode": 0o755}},
        }
        (self.root / "source-snapshot.json").write_text(json.dumps(snapshot))
        checks.check_snapshot(self.root)
        source.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "executable mode changed"):
            checks.check_snapshot(self.root)
        source.chmod(0o755)
        source.write_text("print('changed')\n")
        with self.assertRaisesRegex(ValueError, "Source snapshot file changed"):
            checks.check_snapshot(self.root)

    def test_changed_patch_cannot_pass_unchanged_manifest(self):
        shutil.copytree(ROOT / "v4", self.root / "v4")
        patch = next((self.root / "v4/patches").glob("*.patch"))
        with patch.open("a") as output:
            output.write("\nchanged input\n")
        with self.assertRaisesRegex(ValueError, "Input changed"):
            checks.check_inputs(self.root)

    def test_links_resolve_relative_paths_and_headings(self):
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text("[guide](docs/guide.md#supported-builds)\n")
        guide = self.root / "docs/guide.md"
        guide.write_text("# Supported builds\n[home](../README.md)\n")
        checks.check_docs(self.root)
        guide.write_text("# Renamed heading\n")
        with self.assertRaisesRegex(ValueError, "Missing Markdown heading"):
            checks.check_docs(self.root)

    def test_changed_scored_row_is_detected(self):
        folder = self.root / "docs/data/performance-20260907"
        shutil.copytree(ROOT / "docs/data/performance-20260907", folder)
        shutil.copy2(ROOT / "docs/qualification.json", self.root / "docs/qualification.json")
        checks.check_performance(self.root)
        samples = folder / "samples.csv"
        rows = samples.read_text().splitlines()
        columns = rows[0].split(",")
        fields = rows[1].split(",")
        fields[columns.index("FrameTime")] = "1000"
        rows[1] = ",".join(fields)
        samples.write_text("\n".join(rows) + "\n")
        with self.assertRaisesRegex(ValueError, "Published data mismatch"):
            checks.check_performance(self.root)

    def test_changed_cost_estimate_is_detected_against_original_timestamps(self):
        for name in ("fsr-cost-20260908", "performance-20260907"):
            shutil.copytree(ROOT / "docs/data" / name, self.root / "docs/data" / name)
        checks.check_fsr_cost(self.root)
        path = self.root / "docs/data/fsr-cost-20260908/estimates.json"
        values = json.loads(path.read_text())
        values["estimates"][0]["v4_estimated_cost_ms"] = 0
        path.write_text(json.dumps(values))
        with self.assertRaisesRegex(ValueError, "FSR cost reconstruction changed"):
            checks.check_fsr_cost(self.root)


class RC9PublicationTests(unittest.TestCase):
    def setUp(self):
        self.record = copy.deepcopy(checks.load(ROOT / "docs/data/portable-dll-rc9.json"))
        self.manifest = checks.load(ROOT / "dll/manifest.json")

    def test_release_run_cannot_use_the_development_provider_label(self):
        self.record["performance"]["rows"][0]["provider"] = "4.1.1d1"
        with self.assertRaisesRegex(ValueError, "DLL/provider mismatch"):
            checks.check_rc9_record(self.record, self.manifest)

    def test_changed_scored_samples_cannot_keep_the_published_median(self):
        row = self.record["performance"]["rows"][0]
        row["gpu_ms"][300:] = [100.0] * 300
        with self.assertRaisesRegex(ValueError, "per-run median"):
            checks.check_rc9_record(self.record, self.manifest)

    def test_complete_model_identity_is_required(self):
        self.record["quality"]["preflights"][1]["row"]["observed_shaders"] = []
        with self.assertRaisesRegex(ValueError, "shader coverage missing"):
            checks.check_rc9_record(self.record, self.manifest)

    def test_stale_component_evidence_cannot_be_relabelled_as_current(self):
        old = checks.load(ROOT / "docs/data/portable-dll-rc8.json")
        self.record["development_component_evidence"]["modified_model_weights"][0][
            "component_shader_sha256"
        ] = old["development_component_evidence"]["modified_model_weights"][0][
            "component_shader_sha256"
        ]
        with self.assertRaisesRegex(ValueError, "fallback component identity"):
            checks.check_rc9_record(self.record, self.manifest)

    def test_inherited_timing_cannot_be_presented_as_final_release_bytes(self):
        row = self.record["inherited_checkpoint_comparison"]["rows"][1]
        row["dll_sha256"] = self.record["dll_sha256"]
        row["provider"] = self.record["provider_name"]
        with self.assertRaisesRegex(ValueError, "DLL/provider mismatch"):
            checks.check_rc9_record(self.record, self.manifest)

    def chart_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        for name in ("fsr-cost-20260910", "fsr-cost-20260911-rc9"):
            shutil.copytree(ROOT / "docs/data" / name, directory / name)
        current = directory / "fsr-cost-20260911-rc9"
        summarize = runpy.run_path(str(current / "summarize.py"))["summarize"]
        return current, summarize

    def test_refresh_must_preserve_every_baseline_timestamp(self):
        current, summarize = self.chart_fixture()
        samples = current / "samples.csv"
        lines = samples.read_text().splitlines()
        fields = lines[1].split(",")
        fields[-1] = "1000.000000000"
        lines[1] = ",".join(fields)
        samples.write_text("\n".join(lines) + "\n")
        with self.assertRaisesRegex(ValueError, "Baseline timestamps must remain unchanged"):
            summarize()

    def test_refresh_must_preserve_baseline_run_metadata(self):
        current, summarize = self.chart_fixture()
        path = current / "runs.json"
        runs = checks.load(path)
        next(iter(runs.values()))["clock_min_mhz"] = 1800
        path.write_text(json.dumps(runs))
        with self.assertRaisesRegex(ValueError, "Baseline run metadata must remain unchanged"):
            summarize()


if __name__ == "__main__":
    unittest.main()
