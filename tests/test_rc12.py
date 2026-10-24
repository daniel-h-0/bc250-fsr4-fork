# SPDX-License-Identifier: MIT
"""The RC12 release configuration: C10 slots as donor edits, recipe pins, RC11's host edits, label,
release identity, build and DLL compare."""

import collections
import copy
import importlib.util
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dll.port import drop_profile, rc12, rederive
from dll.port.ir import LoweringError

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SPEC = importlib.util.spec_from_file_location("package_dll", ROOT / "scripts/package-dll.py")
package_dll = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_dll)


class LineEdits(unittest.TestCase):
    def test_round_trip_and_refusal(self):
        donor, target = "a\nb\nc\n", "a\nB\nc\n"
        edits = rc12.line_edits(donor, target)
        self.assertEqual(edits, [dict(line=2, before="b", after="B")])
        self.assertEqual(rederive.restore(donor, edits), target)
        with self.assertRaisesRegex(LoweringError, "line count"):
            rc12.line_edits(donor, "a\nb\nc\nd\n")


class CommittedConfiguration(unittest.TestCase):
    def setUp(self):
        self.manifest_path = ROOT / "dll/manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text())
        self.manifest_sha256 = rc12.sha(self.manifest_path.read_bytes())
        self.profile = drop_profile.select(self.manifest["sdk_sha256"])[1]
        self.config = rc12.load_config()
        self.rows = {r["original_offset"]: r for r in self.manifest["replacements"]}

    def check(self, config, **kwargs):
        return rc12.check_config(
            config, self.manifest, self.manifest_sha256, self.profile, **kwargs
        )

    def test_checks_offline(self):
        summary = self.check(self.config)
        self.assertIn("34 C10 slots", summary)
        self.assertIn("eligibility pending", summary)
        self.assertIn("applied unchanged from the drop profile: int8-eligibility", summary)
        self.assertIn("0 proven shifts", summary)
        self.assertEqual(self.config["provider_name"], "4.1.1r12")
        self.assertEqual(self.config["release_version"], "4.0.0-rc12")
        self.assertNotEqual(self.config["provider_name"], self.manifest["provider_name"])

    def test_the_release_keeps_every_rc11_host_edit(self):
        base = rederive.load_config(ROOT / "dll" / self.config["base"]["path"])
        self.assertEqual(self.config["apply_host_patches"], ["int8-eligibility"])
        self.assertEqual(rederive.pending_host_patches(base), ["int8-eligibility"])
        self.assertEqual(rc12.omitted_host_patches(self.config, base), [])

    def test_the_packager_releases_this_configuration(self):
        identity = package_dll.release_identity(ROOT / "dll")
        self.assertEqual(identity["record"], "dll/rc12.json")
        for key in package_dll.IDENTITY:
            self.assertEqual(identity[key], self.config[key], key)
        package_dll.validate_install_guide((ROOT / "dll/INSTALL.md").read_text(), identity)

    def test_committed_format(self):
        self.assertEqual(rc12.dumps(self.config), (ROOT / "dll/rc12.json").read_text())

    def test_moduleid_lines_are_recorded_by_hash(self):
        edits = [e for s in self.config["slots"].values() for e in s["edits"]]
        self.assertFalse(any("ModuleID" in e.get("before", "") for e in edits))
        self.assertEqual(sum("before_sha256" in e for e in edits), 28)
        self.assertNotIn("/home/", (ROOT / "dll/rc12.json").read_text())
        donor = "; ModuleID = '/build/a.ll'\nx\n"
        edits = rc12.line_edits(donor, "; ModuleID = 'b.ll'\ny\n")
        self.assertEqual(
            edits[0],
            dict(line=1, before_sha256=rc12.sha(donor.split("\n")[0]), after="; ModuleID = 'b.ll'"),
        )
        self.assertEqual(rc12.apply_edits(donor, edits), "; ModuleID = 'b.ll'\ny\n")
        with self.assertRaisesRegex(LoweringError, "Line 1 moved"):
            rc12.apply_edits("; ModuleID = '/other/a.ll'\nx\n", edits)

    def test_slots_are_the_recipes_new_programs(self):
        pins = rc12.recipe_pins()
        new = {k for k, (_, _, pin) in pins.items() if pin.get("basis", "new") == "new"}
        self.assertEqual(set(self.config["slots"]), new)
        families = collections.Counter(s["family"] for s in self.config["slots"].values())
        self.assertEqual(families, {"1080p": 17, "8k": 17})
        # Alias slots (one SDK program) get one container.
        by_sdk = collections.defaultdict(set)
        for key, slot in self.config["slots"].items():
            by_sdk[self.rows[int(key, 16)]["original_sha256"]].add(slot["replacement_sha256"])
        self.assertTrue(all(len(v) == 1 for v in by_sdk.values()))
        self.assertEqual(len(by_sdk), 24)

    def test_sources_differ_from_their_donor_in_the_edited_lines_only(self):
        for key, slot in self.config["slots"].items():
            donor = self.rows[int(slot["donor"], 16)]
            ours = rc12.slot_source(self.rows, key, slot).split("\n")
            theirs = (ROOT / "dll" / donor["source"]).read_text().split("\n")
            changed = [n + 1 for n, (a, b) in enumerate(zip(ours, theirs)) if a != b]
            self.assertEqual(len(ours), len(theirs), key)
            self.assertEqual(changed, [e["line"] for e in slot["edits"]], key)

    def test_final_pass_slots_keep_the_sdk_division(self):
        for key in ("0x5e7990", "0x7abdd0"):
            slot = self.config["slots"][key]
            self.assertEqual(slot["donor"], "0x248220")
            self.assertTrue(slot["donor_restored"])
            text = rc12.slot_source(self.rows, key, slot)
            self.assertNotIn("lshr i32 %v14, 1", text)
            self.assertIn("  %v16 = sdiv i32 %v14, 2", text)
            self.assertIn("  %v17 = sdiv i32 %v15, 2", text)

    def test_refuses_tampered_configurations(self):
        def slot(config, key="0x522900"):
            return config["slots"][key]

        def edit(config):
            return slot(config)["edits"][1]

        mutations = {
            "other manifest": lambda c: c.update(base_manifest_sha256="0" * 64),
            "base moved": lambda c: c["base"].update(sha256="0" * 64),
            "other base": lambda c: c["base"].update(path="manifest.json"),
            "rc11 label": lambda c: c.update(provider_name=self.manifest["provider_name"]),
            "empty label": lambda c: c.update(provider_name=""),
            "missing slot": lambda c: c["slots"].pop("0x522900"),
            "extra slot": lambda c: c["slots"].update({"0xdea00": slot(c)}),
            "other container": lambda c: slot(c).update(replacement_sha256="0" * 64),
            "other source": lambda c: slot(c).update(source_sha256="0" * 64),
            "moved edit": lambda c: edit(c).update(line=edit(c)["line"] + 1),
            "other text": lambda c: edit(c).update(after=edit(c)["after"] + " "),
            "dropped edit": lambda c: slot(c)["edits"].pop(),
            "no edits": lambda c: slot(c).update(edits=[]),
            "other donor": lambda c: slot(c).update(donor="0x642b40"),
            "donor in another entry": lambda c: slot(c).update(donor="0x248220"),
            "own donor": lambda c: slot(c).update(donor="0x522900"),
            "donor hash": lambda c: slot(c).update(donor_source_sha256="0" * 64),
            "donor state": lambda c: slot(c, "0x5e7990").update(donor_restored=False),
            "recipe": lambda c: slot(c).update(recipe="c10-pass8"),
            "family": lambda c: slot(c).update(family="4k"),
            "restored slot": lambda c: c["slots"].update({"0x248220": slot(c, "0x5e7990")}),
            "dll pin": lambda c: c.update(expected_dll_sha256="x"),
            "moduleid hash": lambda c: slot(c)["edits"][0].update(before_sha256="0" * 64),
            "moduleid text": lambda c: slot(c)["edits"][0].update(before="; ModuleID = 'x'"),
            "eligibility left out": lambda c: c.update(apply_host_patches=[]),
            "extra host patch": lambda c: c["apply_host_patches"].append("buffer-uav-barrier"),
            "host patch list": lambda c: c.update(apply_host_patches="int8-eligibility"),
            "no host patch list": lambda c: c.pop("apply_host_patches"),
            "rc11 version": lambda c: c.update(release_version=self.manifest["release_version"]),
            "bad version": lambda c: c.update(release_version="4.0.0-rc12/../x"),
            "no version": lambda c: c.pop("release_version"),
        }
        for name, mutate in mutations.items():
            config = copy.deepcopy(self.config)
            mutate(config)
            with self.subTest(name), self.assertRaises((LoweringError, KeyError)):
                self.check(config)

    def test_a_pin_equal_to_rc11_is_refused(self):
        pins = rc12.recipe_pins()
        key = "0x522900"
        recipe, entry, pin = pins[key]
        pins[key] = (recipe, entry, dict(pin, replacement_sha256="0" * 64))
        with self.assertRaisesRegex(LoweringError, "pins differ"):
            self.check(self.config, pins=pins)
        config = copy.deepcopy(self.config)
        release = self.rows[0x522900]["replacement_sha256"]
        config["slots"][key]["replacement_sha256"] = release
        pins[key] = (recipe, entry, dict(pin, replacement_sha256=release))
        with self.assertRaisesRegex(LoweringError, "pins differ"):
            self.check(config, pins=pins)

    def test_verify_sources_checks_the_configuration(self):
        q = subprocess.run(
            [sys.executable, "-B", str(ROOT / "dll/build.py"), "--config", "rc12"]
            + ["--verify-sources"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertIn("34 C10 slots", q.stdout)


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealBuilds(unittest.TestCase):
    """RC11 and RC12 builds; their difference is exactly the C10 slots, 0x248220 and the label.
    The host edits (eligibility, barrier) are RC11's."""

    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        cls.sdk = Path(CACHE) / "sha256" / manifest["sdk_sha256"]
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.dlls = {}
        for config in ("rc11", "rc12"):
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

    def test_compare_with_rc11(self):
        config = rc12.load_config()
        report = rc12.compare(self.dlls["rc11"], self.dlls["rc12"])
        self.assertEqual(report["b"]["sha256"], config["expected_dll_sha256"])
        changed = {r["slot"]: r["b_replacement_sha256"] for r in report["changed_slots"]}
        expected = {k: s["replacement_sha256"] for k, s in config["slots"].items()}
        expected.update(
            (k, s["replacement_sha256"])
            for k, s in rederive.load_config()["elements"]["final-pass-divisions"]["slots"].items()
        )
        self.assertEqual(changed, expected)
        self.assertEqual(report["host_patches_differing"], [])
        self.assertTrue(report["label_changed"])
        self.assertEqual((report["a"]["label"], report["b"]["label"]), ("4.1.1r11", "4.1.1r12"))
        categories = {r["category"] for r in report["prefix_differences"]}
        self.assertEqual(categories, {"pe-header", "provider-label", "shader-pointer"})
        record = json.loads(self.dlls["rc12"].with_suffix(".json").read_text())
        self.assertNotIn("omitted_host_patches", record)
        self.assertEqual(
            record["host_changes"][:3],
            json.loads(self.dlls["rc11"].with_suffix(".json").read_text())["host_changes"][:3],
        )

    def test_the_gpu_tested_build_is_this_dll_with_another_label(self):
        check = runpy.run_path(str(ROOT / "scripts/check-rc12-release.py"))
        record = json.loads((ROOT / "docs/data/portable-dll-rc12.json").read_text())
        check["verify_binary"](self.dlls["rc12"], record)
        with self.assertRaises(AssertionError):
            check["verify_binary"](self.dlls["rc11"], record)

    def test_the_built_dll_packages(self):
        archive = package_dll.create_archive(ROOT, self.dlls["rc12"], self.out / "package")
        self.assertEqual(archive.name, "bc250-fsr4-dll-4.0.0-rc12.zip")
        with self.assertRaisesRegex(ValueError, "qualified manifest"):
            package_dll.release_files(ROOT, self.dlls["rc11"])

    def test_compare_refuses_an_unexplained_byte(self):
        data = bytearray(self.dlls["rc12"].read_bytes())
        data[0x400] ^= 1  # inside the SDK's .text, outside every build region
        forged = self.out / "forged" / "amd_fidelityfx_upscaler_dx12.dll"
        forged.parent.mkdir()
        forged.write_bytes(data)
        forged.with_suffix(".json").write_text(self.dlls["rc12"].with_suffix(".json").read_text())
        with self.assertRaisesRegex(LoweringError, "outside every known region"):
            rc12.compare(self.dlls["rc11"], forged)
        data = bytearray(self.dlls["rc12"].read_bytes())
        data[-1] ^= 1  # the appended section's padding
        forged.write_bytes(data)
        with self.assertRaisesRegex(LoweringError, "unexplained"):
            rc12.compare(self.dlls["rc11"], forged)


if __name__ == "__main__":
    unittest.main()
