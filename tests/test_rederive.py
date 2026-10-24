# SPDX-License-Identifier: MIT
"""The re-derived DLL configuration: division audit, pinned restorations, build and DLL compare."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dll.port import drop_profile, rederive
from dll.port.ir import LoweringError, rename_ssa

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK = """%dx.types.Handle = type { i8* }
%dx.types.ResBind = type { i32, i32, i32, i8 }
%dx.types.CBufRet.i32 = type { i32, i32, i32, i32 }

define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind { i32 1, i32 1, i32 0, i8 2 }, i32 1, i1 false)
  %2 = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %1, i32 4)
  %3 = extractvalue %dx.types.CBufRet.i32 %2, 0
  %4 = sdiv i32 %3, 2
  %5 = and i32 %3, 255
  %6 = sdiv i32 %5, 4
  %7 = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)
  %8 = icmp ult i32 %7, %4
  %9 = icmp ult i32 %7, %6
  ret void
}

!dx.entryPoints = !{!1}
!1 = !{void ()* @main, !"main", null, null, !2}
!2 = !{i32 4, !3}
!3 = !{i32 64, i32 1, i32 1}
"""


def rc(*replacements):
    """The SDK program as an RC source (renamed values), with whole-line replacements."""
    text = rename_ssa(SDK)
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def verdicts(entries):
    return {e["value"]: e["verdict"] for e in entries}


class AuditSlot(unittest.TestCase):
    def test_kept_divisions(self):
        entries = rederive.audit_slot(SDK, rc())
        self.assertEqual(verdicts(entries), {"%v4": "kept", "%v6": "kept"})
        self.assertEqual([e["proven"] for e in entries], [False, True])

    def test_unproven_shift_is_restored_and_proven_shift_kept(self):
        source = rc(
            ("  %v4 = sdiv i32 %v3, 2", "  %v4 = lshr i32 %v3, 1  ; note"),
            ("  %v6 = sdiv i32 %v5, 4", "  %v6 = lshr i32 %v5, 2"),
        )
        entries = rederive.audit_slot(SDK, source)
        self.assertEqual(verdicts(entries), {"%v4": "restore", "%v6": "shift-proven"})
        restore = entries[0]
        self.assertEqual(restore["before"], "  %v4 = lshr i32 %v3, 1  ; note")
        self.assertEqual(restore["after"], "  %v4 = sdiv i32 %v3, 2  ; note")
        self.assertEqual(restore["witness"], {"x": -1, "sdiv": 0, "lshr": 0x7FFFFFFF})
        fixed = rederive.restore(source, [restore])
        self.assertEqual(verdicts(rederive.audit_slot(SDK, fixed))["%v4"], "kept")

    def test_assumed_range_makes_the_shift_exact(self):
        source = rc(("  %v4 = sdiv i32 %v3, 2", "  %v4 = lshr i32 %v3, 1"))
        entries = rederive.audit_slot(SDK, source, {"b1[4].x": (0, 4096)})
        self.assertEqual(verdicts(entries)["%v4"], "shift-proven")

    def test_refuses_sources_it_cannot_align(self):
        for name, replacement in (
            ("other form", ("  %v4 = sdiv i32 %v3, 2", "  %v4 = ashr i32 %v3, 1")),
            (
                "other dividend",
                (
                    "  %v3 = extractvalue %dx.types.CBufRet.i32 %v2, 0",
                    "  %v3 = extractvalue %dx.types.CBufRet.i32 %v2, 1",
                ),
            ),
            (
                "other cbuffer row",
                ("i32 59, %dx.types.Handle %v1, i32 4)", "i32 59, %dx.types.Handle %v1, i32 5)"),
            ),
            ("renamed quotient", ("  %v4 = sdiv i32 %v3, 2", "  %v40 = sdiv i32 %v3, 2")),
        ):
            with self.subTest(name), self.assertRaises(LoweringError):
                rederive.audit_slot(SDK, rc(replacement))

    def test_restore_refuses_a_moved_line(self):
        edit = dict(line=9, before="  %v4 = lshr i32 %v3, 1", after="  %v4 = sdiv i32 %v3, 2")
        with self.assertRaisesRegex(LoweringError, "moved"):
            rederive.restore(rc(), [edit])


class CommittedConfiguration(unittest.TestCase):
    def setUp(self):
        self.manifest_path = ROOT / "dll/manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text())
        self.manifest_sha256 = rederive.sha(self.manifest_path.read_bytes())
        self.profile = drop_profile.select(self.manifest["sdk_sha256"])[1]
        self.config = rederive.load_config()

    def check(self, config):
        return rederive.check_config(config, self.manifest, self.manifest_sha256, self.profile)

    def test_checks_offline(self):
        summary = self.check(self.config)
        self.assertIn("192 SDK divisions in 348 slots, 190 kept, 2 restored", summary)
        self.assertEqual(rederive.pending_host_patches(self.config), ["int8-eligibility"])

    def test_restored_source_differs_from_rc11_only_in_the_restored_lines(self):
        rows = {r["original_offset"]: r for r in self.manifest["replacements"]}
        for offset, slot in rederive.restorations(self.config).items():
            ours = rederive.restored_source(rows[offset], slot).split("\n")
            theirs = (ROOT / "dll" / rows[offset]["source"]).read_text().split("\n")
            changed = [n + 1 for n, (a, b) in enumerate(zip(ours, theirs)) if a != b]
            self.assertEqual(len(ours), len(theirs))
            self.assertEqual(changed, [e["line"] for e in slot["edits"]])
            for edit in slot["edits"]:
                self.assertRegex(edit["after"], r"^  %v\d+ = sdiv i32 %v\d+, 2$")

    def test_refuses_tampered_configurations(self):
        def edit(config):
            return config["elements"]["final-pass-divisions"]["slots"]["0x248220"]["edits"]

        def repinned_shift(config):
            slot = config["elements"]["final-pass-divisions"]["slots"]["0x248220"]
            for e in slot["edits"]:
                e["after"] = e["before"]
            slot["source_sha256"] = slot["rc11_source_sha256"]

        mutations = {
            "shift re-pinned as a restoration": repinned_shift,
            "other manifest": lambda c: c.update(base_manifest_sha256="0" * 64),
            "shift kept": lambda c: edit(c)[0].update(after=edit(c)[0]["before"]),
            "moved line": lambda c: edit(c)[0].update(line=edit(c)[0]["line"] + 1),
            "other text": lambda c: edit(c)[1].update(after="  %v17 = sdiv i32 %v15, 4"),
            "count": lambda c: c["elements"]["final-pass-divisions"]["totals"].update(restore=3),
            "assume": lambda c: c["elements"]["final-pass-divisions"].update(assume={"x": 1}),
            "eligibility": lambda c: c["elements"]["int8-eligibility"].update(status="derived"),
            "patch name": lambda c: c["elements"]["int8-eligibility"].update(host_patch="x"),
            "order": lambda c: c.update(elements=dict(reversed(c["elements"].items()))),
            "dll bytes": lambda c: c.update(expected_dll_bytes=c["expected_dll_bytes"] + 1),
        }
        for name, mutate in mutations.items():
            config = copy.deepcopy(self.config)
            mutate(config)
            with self.subTest(name), self.assertRaises(LoweringError):
                self.check(config)


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealBuilds(unittest.TestCase):
    """The audit reproduces the pinned element; both builds and their byte comparison."""

    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        cls.sdk = Path(CACHE) / "sha256" / manifest["sdk_sha256"]
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.audit = rederive.audit(cls.sdk, DXC, cls.out / "audit", jobs=3)
        cls.dlls = {}
        for config in ("rc11", "rederived"):
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "dll/build.py"),
                    "--config",
                    config,
                    "--sdk",
                    str(cls.sdk),
                    "--dxcompiler",
                    str(Path(DXC) / "lib/libdxcompiler.so"),
                    "--output",
                    str(cls.out / config),
                    "--jobs",
                    "3",
                ],
                check=True,
                capture_output=True,
            )
            cls.dlls[config] = cls.out / config / "amd_fidelityfx_upscaler_dx12.dll"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_audit_equals_the_pinned_element(self):
        pinned = rederive.load_config()["elements"]["final-pass-divisions"]
        self.assertEqual(self.audit["element"], pinned)

    def test_builds_differ_only_in_the_rederived_elements(self):
        report = rederive.compare(self.dlls["rc11"], self.dlls["rederived"])
        totals = {}
        for run in report["runs"]:
            key = (run["category"], str(run["detail"]))
            totals[key] = totals.get(key, 0) + run["bytes"]
        self.assertEqual(
            set(totals),
            {
                ("pe-header", "CheckSum (covers every byte)"),
                ("host-patch", "int8-eligibility"),
                ("replacement", "0x248220"),
            },
        )
        self.assertEqual(totals[("host-patch", "int8-eligibility")], 18)
        parts = {r["part"] for r in report["runs"] if r["category"] == "replacement"}
        self.assertEqual(parts, {"(container digest)", "STAT", "HASH", "DXIL"})


if __name__ == "__main__":
    unittest.main()
