# SPDX-License-Identifier: MIT
"""The client packager bundles only the pinned release ZIP and its pinned DLL."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/package-opticlient.py"
SPEC = importlib.util.spec_from_file_location("package_opticlient", SCRIPT)
package_opticlient = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_opticlient)
DLL = "amd_fidelityfx_upscaler_dx12.dll"


class BundlePinTests(unittest.TestCase):
    def test_manifest_pins_the_release_record(self):
        pin = json.loads((ROOT / "integrations/optiscaler-client/manifest.json").read_text())[
            "dll_zip"
        ]
        release = json.loads((ROOT / pin["release"]).read_text())
        self.assertEqual(pin["file"], "bc250-fsr4-dll-" + release["release_version"] + ".zip")
        self.assertEqual(pin["dll_sha256"], release["expected_dll_sha256"])
        self.assertTrue(pin["url"].endswith("/v" + release["release_version"] + "/" + pin["file"]))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "dll").mkdir()
        self.dll = b"MZ" + b"x" * 2048
        (self.root / "dll/test.json").write_text(
            json.dumps(
                {
                    "release_version": "4.0.0-test",
                    "expected_dll_sha256": hashlib.sha256(self.dll).hexdigest(),
                }
            )
        )
        self.zip = self.make("bc250-fsr4-dll-4.0.0-test.zip", self.dll)
        self.pin = {
            "release": "dll/test.json",
            "file": self.zip.name,
            "sha256": hashlib.sha256(self.zip.read_bytes()).hexdigest(),
            "dll_sha256": hashlib.sha256(self.dll).hexdigest(),
        }

    def make(self, name, dll):
        path = self.root / "inputs" / name
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(DLL, dll)
        return path

    def check(self, path, **changes):
        package_opticlient.check_dll_zip(path, {**self.pin, **changes}, self.root)

    def test_pinned_zip_passes(self):
        self.check(self.zip)

    def test_other_bytes_are_refused(self):
        with self.assertRaisesRegex(ValueError, "pinned checksum"):
            self.check(self.make(self.zip.name, self.dll + b"y"))

    def test_other_filename_is_refused(self):
        renamed = self.zip.rename(self.zip.with_name("bc250-fsr4-dll-4.0.0-rc11-docs2.zip"))
        with self.assertRaisesRegex(ValueError, "must be"):
            self.check(renamed)
        with self.assertRaisesRegex(ValueError, "must be"):
            self.check(renamed, file=renamed.name)

    def test_dll_must_match_the_release_record(self):
        other = self.make("bc250-fsr4-dll-4.0.0-test.zip", b"MZ" + b"z" * 2048)
        digest = hashlib.sha256(other.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "does not match dll/test.json"):
            self.check(other, sha256=digest)
        with self.assertRaisesRegex(ValueError, "does not match dll/test.json"):
            self.check(
                other,
                sha256=digest,
                dll_sha256=hashlib.sha256(b"MZ" + b"z" * 2048).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
