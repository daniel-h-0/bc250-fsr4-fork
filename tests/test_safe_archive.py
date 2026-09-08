# SPDX-License-Identifier: MIT
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import safe_archive


class LegacyExtractionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.destination = self.root / "unpack"
        self.destination.mkdir()

    def extract(self, entries):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name, kind, value in entries:
                item = tarfile.TarInfo(name)
                item.mode = 0o755
                item.type = kind
                if kind == tarfile.REGTYPE:
                    item.size = len(value)
                    archive.addfile(item, io.BytesIO(value))
                else:
                    item.linkname = value
                    archive.addfile(item)
        data.seek(0)
        with tarfile.open(fileobj=data) as archive:
            safe_archive.extract_legacy(archive, self.destination, archive.getmembers())

    def test_relative_symlinks_and_hardlinks_preserve_data(self):
        self.extract(
            [
                ("bin/file", tarfile.REGTYPE, b"payload"),
                ("lib/link", tarfile.SYMTYPE, "../bin/file"),
                ("bin/hard", tarfile.LNKTYPE, "bin/file"),
            ]
        )
        self.assertEqual((self.destination / "lib/link").read_bytes(), b"payload")
        self.assertTrue(
            os.path.samefile(self.destination / "bin/file", self.destination / "bin/hard")
        )
        self.assertEqual((self.destination / "bin/file").stat().st_mode & 0o777, 0o755)

    def test_path_escape_is_rejected_before_extraction(self):
        with self.assertRaisesRegex(RuntimeError, "Unsafe archive"):
            self.extract([("../outside", tarfile.REGTYPE, b"bad")])
        self.assertFalse((self.root / "outside").exists())

    def test_no_file_can_be_written_through_an_archive_symlink(self):
        with self.assertRaisesRegex(RuntimeError, "non-directory"):
            self.extract(
                [("link", tarfile.SYMTYPE, "inside"), ("link/file", tarfile.REGTYPE, b"bad")]
            )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_link_chain_cannot_escape_even_when_each_lexical_target_is_inside(self):
        sentinel = self.root / "outside"
        sentinel.write_bytes(b"preserve")
        with self.assertRaisesRegex(RuntimeError, "link chain escapes"):
            self.extract([("a", tarfile.SYMTYPE, "."), ("b", tarfile.SYMTYPE, "a/../outside")])
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_external_hardlink_and_special_file_are_rejected(self):
        for kind, target in ((tarfile.LNKTYPE, "../outside"), (tarfile.FIFOTYPE, "")):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                self.extract([("bad", kind, target)])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_existing_directory_symlink_is_never_followed(self):
        outside = self.root / "external"
        outside.mkdir()
        (self.destination / "link").symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "existing symlink"):
            self.extract([("link/file", tarfile.REGTYPE, b"bad")])
        self.assertEqual(list(outside.iterdir()), [])

    def test_duplicate_file_cannot_overwrite_previous_payload(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate archive"):
            self.extract([("file", tarfile.REGTYPE, b"one"), ("file", tarfile.REGTYPE, b"two")])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_hardlink_cannot_import_external_file_through_existing_directory_link(self):
        outside = self.root / "external"
        outside.mkdir()
        sentinel = outside / "file"
        sentinel.write_bytes(b"preserve")
        (self.destination / "link").symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "hard link target escapes"):
            self.extract([("hard", tarfile.LNKTYPE, "link/file")])
        self.assertFalse((self.destination / "hard").exists())
        self.assertEqual(sentinel.stat().st_nlink, 1)
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_existing_private_directory_permissions_are_preserved(self):
        directory = self.destination / "private"
        directory.mkdir(mode=0o700)
        self.extract([("private/new/file", tarfile.REGTYPE, b"data")])
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / "new").stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
