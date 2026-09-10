# SPDX-License-Identifier: MIT
"""Exercise immutable exports independently of this repository's Git state."""

import hashlib
import importlib.util
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/source-release.py"
SPEC = importlib.util.spec_from_file_location("source_release", SCRIPT)
source_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_release)


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Source fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / "v4").mkdir()
        (self.root / "v4/manifest.json").write_text('{"version":"4.0.0-test"}\n')
        (self.root / "check.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.root / "check.sh").chmod(0o755)
        (self.root / ".gitignore").write_text(".work/\ndist/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT
        )

    def export(self, directory, ref=None):
        return source_release.create_archive(self.root, Path(self.temporary.name) / directory, ref)

    def test_clean_exports_are_identical_and_complete(self):
        (self.root / ".work").mkdir()
        (self.root / ".work/private-input.bin").write_bytes(b"ignored private fixture")
        first = self.export("one")
        second = self.export("two")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as bundle:
            members = bundle.getmembers()
            self.assertFalse(any("private-input" in member.name for member in members))
            snapshot = json.load(
                bundle.extractfile(
                    next(
                        member
                        for member in members
                        if member.name.endswith("/source-snapshot.json")
                    )
                )
            )
            self.assertEqual(snapshot["commit"], self.commit)
            self.assertEqual(set(snapshot["files"]), {"check.sh", ".gitignore", "v4/manifest.json"})
            for relative, expected in snapshot["files"].items():
                member = next(member for member in members if member.name.endswith("/" + relative))
                self.assertEqual(member.mode, expected["mode"])
                self.assertEqual(
                    hashlib.sha256(bundle.extractfile(member).read()).hexdigest(),
                    expected["sha256"],
                )
        self.assertTrue(
            Path(str(first) + ".sha256")
            .read_text()
            .startswith(hashlib.sha256(first.read_bytes()).hexdigest())
        )

    def test_dirty_default_refuses_but_explicit_commit_uses_committed_bytes(self):
        (self.root / "check.sh").write_text("unreviewed changes\n")
        (self.root / "new-source.txt").write_text("untracked source\n")
        with self.assertRaisesRegex(RuntimeError, "Commit or stash"):
            self.export("dirty")
        archive = self.export("explicit", self.commit)
        with tarfile.open(archive) as bundle:
            member = next(member for member in bundle if member.name.endswith("/check.sh"))
            self.assertEqual(bundle.extractfile(member).read(), b"#!/bin/sh\nexit 0\n")
            self.assertFalse(any(member.name.endswith("new-source.txt") for member in bundle))

    def test_dll_source_identity_does_not_relabel_the_retained_runtime(self):
        for name in source_release.SETUP_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (self.root / "runtime/manifest.json").write_text('{"release":{"version":"4.0.0-rc6"}}')
        (self.root / "dll").mkdir()
        (self.root / "dll/manifest.json").write_text('{"release_version":"4.0.0-rc7"}')
        (self.root / "docs/legacy-rc6.md").write_text(
            "# Retained RC6 guide\n[Games](../docs/games.md)\n"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "Portable DLL alongside retained runtime")
        full = self.export("portable-source")
        self.assertTrue(full.name.startswith("bc250-fsr4-v4.0.0-rc7-source-"))
        setup = source_release.create_archive(
            self.root, Path(self.temporary.name) / "retained-setup", setup=True
        )
        self.assertEqual(setup.name, "bc250-fsr4-setup-4.0.0-rc6.tar.gz")
        with tarfile.open(setup) as bundle:
            readme = bundle.extractfile("bc250-fsr4-setup-4.0.0-rc6/README.md").read().decode()
            self.assertIn("Retained RC6 guide", readme)
            self.assertIn("[Games](docs/games.md)", readme)
            self.assertNotIn("4.0.0-rc7", readme)

    def test_existing_output_is_never_replaced(self):
        archive = self.export("one")
        original = archive.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.export("one")
        self.assertEqual(archive.read_bytes(), original)

    def test_committed_symlink_is_rejected(self):
        (self.root / "external").symlink_to("/etc/passwd")
        self.git("add", "external")
        self.git("commit", "-qm", "Symlink")
        with self.assertRaisesRegex(RuntimeError, "Unsupported source entry"):
            self.export("links")

    def test_subdirectory_does_not_export_parent_repository(self):
        with self.assertRaisesRegex(RuntimeError, "own Git checkout"):
            source_release.create_archive(
                self.root / "v4", Path(self.temporary.name) / "nested", "HEAD"
            )

    def test_setup_export_has_installation_closure_and_commit_evidence_links(self):
        for name in source_release.SETUP_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (self.root / "runtime/manifest.json").write_text('{"release":{"version":"1.0.0-test"}}')
        (self.root / "README.md").write_text(
            "[Use](docs/games.md) [Evidence](docs/qualification.md#proof)\n"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "Setup fixture")
        commit = self.git("rev-parse", "HEAD").decode().strip()
        output = source_release.create_archive(
            self.root, Path(self.temporary.name) / "setup", setup=True
        )
        self.assertEqual(output.name, "bc250-fsr4-setup-1.0.0-test.tar.gz")
        full = self.export("full-distribution")
        self.assertTrue(full.name.startswith("bc250-fsr4-v1.0.0-test-source-"))
        with tarfile.open(output) as bundle:
            names = {name.split("/", 1)[1] for name in bundle.getnames()}
            self.assertEqual(names, source_release.SETUP_FILES | {"source-snapshot.json"})
            readme = bundle.extractfile("bc250-fsr4-setup-1.0.0-test/README.md").read().decode()
            self.assertIn("[Use](docs/games.md)", readme)
            self.assertIn("/blob/" + commit + "/docs/qualification.md#proof", readme)
            snapshot = json.load(
                bundle.extractfile("bc250-fsr4-setup-1.0.0-test/source-snapshot.json")
            )
            self.assertEqual(
                snapshot["files"]["README.md"]["sha256"],
                hashlib.sha256(readme.encode()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
