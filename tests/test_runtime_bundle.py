# SPDX-License-Identifier: MIT
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runtime_bundle as bundle


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_archive_bytes_ignore_cache_permissions_and_mtimes(self):
        tree = self.root / "payload"
        tree.mkdir()
        payload = tree / "OptiPatcher.asi"
        payload.write_bytes(b"same pinned upstream bytes")
        payload.chmod(0o600)
        first, second = self.root / "first.xz", self.root / "second.xz"
        bundle.tar_tree(tree, first, "xz")
        payload.chmod(0o644)
        os.utime(payload, (12345, 12345))
        bundle.tar_tree(tree, second, "xz")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_changed_cached_artifact_is_preserved_and_rejected(self):
        expected = hashlib.sha256(b"original").hexdigest()
        cached = self.root / expected
        cached.write_bytes(b"changed")
        with patch.object(bundle.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "Cached upstream file changed"):
                bundle.download("https://example.invalid/payload", expected, self.root)
        network.assert_not_called()
        self.assertEqual(cached.read_bytes(), b"changed")

    def test_offline_missing_artifact_never_calls_network(self):
        with patch.object(bundle.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "Offline cache is missing"):
                bundle.download("https://example.invalid/payload", "0" * 64, self.root, True)
        network.assert_not_called()

    def test_changed_integration_fails_before_download_or_assembly(self):
        policy = json.loads((bundle.ROOT / "runtime/manifest.json").read_text())
        policy["integration"]["runtime/launch.py"] = "0" * 64
        with patch.object(bundle, "download") as download:
            with self.assertRaisesRegex(RuntimeError, "integration changed"):
                bundle.assemble(self.root, self.root / "cache", policy)
        download.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
