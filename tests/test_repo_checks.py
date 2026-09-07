# SPDX-License-Identifier: MIT
"""Regressions for the publication consistency checks."""

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
