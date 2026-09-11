# SPDX-License-Identifier: MIT
"""Reject incorrect release identities and misleading measured RC8 claims."""

import copy
import json
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = runpy.run_path(str(ROOT / "scripts/check-repo.py"))["check_rc8_record"]


class RC8RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads((ROOT / "docs/data/portable-dll-rc8.json").read_text())
        cls.manifest = json.loads((ROOT / "dll/manifest.json").read_text())

    def test_published_raw_data_reproduces_claims(self):
        CHECK(self.record, self.manifest)

    def test_reject_wrong_dll_even_with_valid_timestamps(self):
        record = copy.deepcopy(self.record)
        record["performance"]["rows"][1]["dll_sha256"] = record["reference_rc7_sha256"]
        with self.assertRaisesRegex(ValueError, "DLL/provider"):
            CHECK(record, self.manifest)

    def test_reject_unmatched_clock_and_reordered_run(self):
        for field in ("clock", "order"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                if field == "clock":
                    record["performance"]["rows"][1]["scoring_telemetry"][0]["gpu_clock_hz"] = (
                        1500000000
                    )
                else:
                    record["performance"]["rows"].reverse()
                    # ABBA is symmetric; swap unlike neighboring arms instead.
                    rows = record["performance"]["rows"]
                    rows[0], rows[1] = rows[1], rows[0]
                with self.assertRaises(ValueError):
                    CHECK(record, self.manifest)

    def test_reject_summary_not_derived_from_raw_samples(self):
        record = copy.deepcopy(self.record)
        record["performance"]["summary"]["saving_percent"] += 1
        with self.assertRaisesRegex(ValueError, "percentage"):
            CHECK(record, self.manifest)

    def test_reject_missing_shader_or_different_image(self):
        for field in ("shader", "image"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                row = record["quality"]["cases"][0]["rows"][1]
                if field == "shader":
                    removed = record["changed_shader_slots"][0]["shader_sha256"]
                    row["observed_shaders"] = [
                        r for r in row["observed_shaders"] if r["sha256"] != removed
                    ]
                else:
                    row["pixels_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    CHECK(record, self.manifest)

    def test_reject_missing_balanced_or_incorrect_changed_slot(self):
        for field in ("balanced", "slot"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                if field == "balanced":
                    record["quality"]["cases"].pop()
                else:
                    record["changed_shader_slots"][0]["shader_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    CHECK(record, self.manifest)
