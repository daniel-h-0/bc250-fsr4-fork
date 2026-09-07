# SPDX-License-Identifier: MIT
"""Pinned GE-Proton installation with synthetic archives and no live Steam access."""

import fcntl
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import proton_runtime


class ProtonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.steam = self.root / "Steam"
        self.steam.mkdir()
        self.state = self.root / "state"
        self.name = "GE-Proton11-6-x86_64"
        self.target = self.steam / "compatibilitytools.d" / self.name
        wine = bytearray(32)
        wine[:5] = b"\x7fELF\x02"
        wine[18:20] = b"\x3e\x00"
        self.files = {
            "proton": (b"#!/usr/bin/env python3\n# fixture\n", 0o755),
            "compatibilitytool.vdf": (
                (
                    '"compatibilitytools" { "compat_tools" { "'
                    + self.name
                    + '" { "install_path" "." } } }\n'
                ).encode(),
                0o644,
            ),
            "version": (b"1787951532 GE-Proton11-6\n", 0o644),
            "files/bin/wine": (bytes(wine), 0o755),
            "protonfixes/upscalers.py": (b"# GE manages provider downloads\n", 0o644),
        }
        self.policy = {
            "schema": 1,
            "name": self.name,
            "version": "GE-Proton11-6",
            "url": "https://example.invalid/pinned-proton.tar.gz",
            "sha256": "",
            "size": 0,
            "files": {
                name: {"sha256": hashlib.sha256(data).hexdigest(), "executable": bool(mode & 0o111)}
                for name, (data, mode) in self.files.items()
            },
        }
        self.archive = self.make_archive()

    def make_archive(self, extras=()):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            root = tarfile.TarInfo(self.name)
            root.type = tarfile.DIRTYPE
            root.mode = 0o755
            bundle.addfile(root)
            for name, (data, mode) in self.files.items():
                item = tarfile.TarInfo(self.name + "/" + name)
                item.size = len(data)
                item.mode = mode
                bundle.addfile(item, io.BytesIO(data))
            for item, data in extras:
                bundle.addfile(item, io.BytesIO(data) if data is not None else None)
        data = output.getvalue()
        self.policy["sha256"] = hashlib.sha256(data).hexdigest()
        self.policy["size"] = len(data)
        return data

    def install(self):
        with mock.patch.object(
            proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
        ) as download:
            target = proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(target, self.target)
        return target

    def test_install_and_reuse_exact_runtime_without_network(self):
        self.install()
        with mock.patch.object(
            proton_runtime.urllib.request,
            "urlopen",
            side_effect=AssertionError("unexpected download"),
        ):
            self.assertEqual(
                proton_runtime.ensure_proton(self.steam, self.root / "unused-state", self.policy),
                self.target,
            )
        self.assertFalse((self.root / "unused-state").exists())
        self.assertFalse(any(self.target.glob("**/amdxcffx64.dll")))

    def test_internal_symbolic_and_hard_links_are_preserved(self):
        symlink = tarfile.TarInfo(self.name + "/files/bin/wine-link")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "wine"
        hardlink = tarfile.TarInfo(self.name + "/files/bin/wine-hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = self.name + "/files/bin/wine"
        hardlink.mode = 0o755
        self.archive = self.make_archive([(symlink, None), (hardlink, None)])
        self.install()
        self.assertTrue((self.target / "files/bin/wine-link").is_symlink())
        self.assertEqual(
            (self.target / "files/bin/wine-hardlink").stat().st_ino,
            (self.target / "files/bin/wine").stat().st_ino,
        )

    def test_existing_modified_runtime_is_preserved(self):
        self.install()
        modified = self.target / "proton"
        modified.write_text("user modification\n")
        with mock.patch.object(
            proton_runtime.urllib.request,
            "urlopen",
            side_effect=AssertionError("unexpected download"),
        ):
            with self.assertRaisesRegex(RuntimeError, "preserving it: proton"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertEqual(modified.read_text(), "user modification\n")

    def test_unknown_existing_directory_is_not_replaced(self):
        self.target.mkdir(parents=True)
        (self.target / "user-note").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "preserving it"):
            proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertEqual((self.target / "user-note").read_text(), "keep")
        self.assertFalse(self.state.exists())

    def test_existing_runtime_symlink_is_not_followed(self):
        other = self.root / "unmanaged"
        other.mkdir()
        self.target.parent.mkdir()
        self.target.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "preserving it"):
            proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(list(other.iterdir()), [])

    def test_archive_traversal_is_rejected_before_extraction(self):
        item = tarfile.TarInfo(self.name + "/../../outside")
        item.size = 4
        self.archive = self.make_archive([(item, b"nope")])
        with mock.patch.object(
            proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsafe or duplicate"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "outside").exists())
        self.assertEqual(list(self.target.parent.glob(".bc250-proton-stage-*")), [])

    def test_external_symlink_is_rejected(self):
        item = tarfile.TarInfo(self.name + "/external")
        item.type = tarfile.SYMTYPE
        item.linkname = "../other-runtime"
        self.archive = self.make_archive([(item, None)])
        with mock.patch.object(
            proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
        ):
            with self.assertRaisesRegex(RuntimeError, "link escapes"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.target.exists())

    def test_absolute_hardlink_is_rejected(self):
        item = tarfile.TarInfo(self.name + "/external")
        item.type = tarfile.LNKTYPE
        item.linkname = "/etc/passwd"
        self.archive = self.make_archive([(item, None)])
        with mock.patch.object(
            proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
        ):
            with self.assertRaisesRegex(RuntimeError, "link escapes"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.target.exists())

    def test_wrong_archive_hash_leaves_no_partial_download(self):
        self.policy["sha256"] = "f" * 64
        with mock.patch.object(
            proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
        ):
            with self.assertRaisesRegex(RuntimeError, "pinned size and SHA256"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.target.exists())
        self.assertEqual(list((self.state / "downloads").iterdir()), [])

    def test_interrupted_staging_never_activates_a_partial_runtime(self):
        def interrupt(archive, destination, policy):
            root = destination / policy["name"]
            root.mkdir()
            (root / "partial-file").write_text("interrupted")
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                proton_runtime.urllib.request, "urlopen", return_value=io.BytesIO(self.archive)
            ),
            mock.patch.object(proton_runtime, "extract_archive", side_effect=interrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob(".bc250-proton-stage-*")), [])
        with mock.patch.object(
            proton_runtime.urllib.request,
            "urlopen",
            side_effect=AssertionError("cache should be reused"),
        ):
            self.assertEqual(
                proton_runtime.ensure_proton(self.steam, self.state, self.policy), self.target
            )

    def test_atomic_promotion_preserves_a_concurrently_created_directory(self):
        source = self.root / "staging"
        source.mkdir()
        (source / "owned").write_text("new")
        destination = self.root / "concurrent"
        destination.mkdir()
        with self.assertRaisesRegex(RuntimeError, "preserving it"):
            proton_runtime.promote_runtime(source, destination)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual((source / "owned").read_text(), "new")

    def test_concurrent_setup_fails_without_waiting_or_downloading(self):
        self.target.parent.mkdir()
        with (self.target.parent / ".bc250-fsr4-proton.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                proton_runtime.ensure_proton(self.steam, self.state, self.policy)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.target.exists())

    def test_published_metadata_matches_existing_game_provider_pins(self):
        root = Path(__file__).resolve().parents[1]
        proton = json.loads((root / "v4/proton.json").read_text())
        games = json.loads((root / "v4/games.json").read_text())
        proton_runtime.validate_policy(proton)
        self.assertEqual(proton["name"], games["proton"])
        self.assertEqual(proton["provider"]["version"], games["fsr4"])
        self.assertEqual(proton["provider"]["sha256"], games["provider_sha256"])


if __name__ == "__main__":
    unittest.main()
