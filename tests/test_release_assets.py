# SPDX-License-Identifier: MIT
"""Release staging preserves source availability and rejects changed archives."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_assets", ROOT / "scripts/release-assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


class ReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dll = self.archive("dll.zip", b"dll archive fixture")
        self.source = self.archive("source.tar.gz", b"complete source fixture")
        self.driver = self.archive("driver.tar.gz", b"driver archive fixture")
        self.output = self.root / "upload"

    def archive(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        Path(str(path) + ".sha256").write_text(
            hashlib.sha256(data).hexdigest() + "  " + name + "\n"
        )
        return path

    def test_three_roles_produce_four_assets_and_preserve_originals(self):
        names = assets.stage(self.output, self.dll, self.source, self.driver)
        self.assertEqual(names, ["SHA256SUMS", "dll.zip", "driver.tar.gz", "source.tar.gz"])
        for original in [self.dll, self.source, self.driver]:
            self.assertEqual((self.output / original.name).read_bytes(), original.read_bytes())
            self.assertTrue(Path(str(original) + ".sha256").is_file())
        sums = (self.output / "SHA256SUMS").read_text()
        for original in [self.dll, self.source, self.driver]:
            self.assertIn(assets.digest(original) + "  " + original.name + "\n", sums)

    def test_driver_is_optional_but_complete_source_is_always_present(self):
        self.assertEqual(
            assets.stage(self.output, self.dll, self.source),
            ["SHA256SUMS", "dll.zip", "source.tar.gz"],
        )

    def test_client_is_staged_with_source_and_verified_before_any_output(self):
        client = self.archive("client.tar.gz", b"client with corresponding source")
        client.write_bytes(b"corrupt client")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            assets.stage(self.output, self.dll, self.source, client=client)
        self.assertFalse(self.output.exists())
        client = self.archive("client.tar.gz", b"client with corresponding source")
        self.assertEqual(
            assets.stage(self.output, self.dll, self.source, client=client),
            ["SHA256SUMS", "client.tar.gz", "dll.zip", "source.tar.gz"],
        )

    def test_corruption_never_creates_an_upload_directory(self):
        self.driver.write_bytes(b"changed after packaging")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            assets.stage(self.output, self.dll, self.source, self.driver)
        self.assertFalse(self.output.exists())

    def test_existing_assets_are_not_replaced(self):
        self.output.mkdir()
        marker = self.output / "existing"
        marker.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            assets.stage(self.output, self.dll, self.source)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_duplicate_names_and_symlinks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assets.stage(self.output, self.dll, self.source, self.source)
        link = self.root / "link.zip"
        link.symlink_to(self.dll)
        with self.assertRaises(ValueError):
            assets.stage(self.output, link, self.source)

    def test_checksum_names_are_bound_to_the_selected_archive(self):
        Path(str(self.dll) + ".sha256").write_text(assets.digest(self.dll) + "  wrong.zip\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            assets.stage(self.output, self.dll, self.source)
