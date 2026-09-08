# SPDX-License-Identifier: MIT
import hashlib
import json
import os
import subprocess
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

    def test_interrupted_packaging_does_not_publish_partial_archive(self):
        tree = self.root / "payload"
        tree.mkdir()
        output = self.root / "release.tar.gz"

        def fail(tree, path):
            path.write_bytes(b"partial archive")
            raise OSError("interrupted write")

        with patch.object(bundle, "tar_tree", side_effect=fail):
            with self.assertRaisesRegex(OSError, "interrupted write"):
                bundle.publish_tree(tree, output)
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".sha256").exists())

    def test_packaging_preserves_existing_sidecar_and_concurrent_archive(self):
        tree = self.root / "payload"
        tree.mkdir()
        output = self.root / "release.tar.gz"
        checksum = Path(str(output) + ".sha256")
        checksum.write_bytes(b"preserve checksum")
        with self.assertRaisesRegex(RuntimeError, "Output already exists"):
            bundle.publish_tree(tree, output)
        self.assertEqual(checksum.read_bytes(), b"preserve checksum")
        checksum.unlink()
        original = bundle.tar_tree

        def race(tree, staged):
            original(tree, staged)
            output.write_bytes(b"concurrent writer")

        with patch.object(bundle, "tar_tree", side_effect=race):
            with self.assertRaises(FileExistsError):
                bundle.publish_tree(tree, output)
        self.assertEqual(output.read_bytes(), b"concurrent writer")
        self.assertFalse(checksum.exists())

    def test_published_runtime_checksum_matches_complete_archive(self):
        tree = self.root / "payload"
        tree.mkdir()
        (tree / "file").write_bytes(b"complete payload")
        output = self.root / "release.tar.gz"
        bundle.publish_tree(tree, output)
        self.assertEqual(
            Path(str(output) + ".sha256").read_text(),
            bundle.digest(output) + "  " + output.name + "\n",
        )

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

    def test_python_patch_matches_gnu_patch_on_complete_upstream_source(self):
        source = self.root / "protonfixes/upscalers.py"
        source.parent.mkdir()
        original = (bundle.ROOT / "tests/fixtures/ge-proton11-6-upscalers.py").read_bytes()
        source.write_bytes(original)
        patch = bundle.ROOT / "runtime/patches/0001-pinned-upscaler-manifest.patch"
        bundle.apply_upscaler_patch(source, patch)
        actual = source.read_bytes()
        source.write_bytes(original)
        subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
            cwd=self.root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        self.assertEqual(actual, source.read_bytes())

    def test_context_drift_does_not_partially_patch_upstream_source(self):
        source = self.root / "upscalers.py"
        original = (bundle.ROOT / "tests/fixtures/ge-proton11-6-upscalers.py").read_text()
        source.write_text(
            original.replace("enabled = check_optiscaler(", "changed = check_optiscaler(")
        )
        before = source.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "context differs"):
            bundle.apply_upscaler_patch(
                source, bundle.ROOT / "runtime/patches/0001-pinned-upscaler-manifest.patch"
            )
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
