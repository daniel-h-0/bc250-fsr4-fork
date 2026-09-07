# SPDX-License-Identifier: MIT
"""Build provenance and source-distribution contracts without a GPU or Mesa build."""

import importlib.util
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build
import driver
import release_common

spec = importlib.util.spec_from_file_location(
    "release_package", Path(build.__file__).with_name("package.py")
)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class BuildFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.work = Path(self.temporary.name) / "work"
        self.root.mkdir()
        self.work.mkdir()
        for name, data in {
            "scripts/build.py": "fixture build recipe",
            "requirements-build.txt": "fixture dependencies",
            "v4/patches/test.patch": "patch",
            "README.md": "readme",
            "CONTRIBUTING.md": "contributing",
            "CHANGELOG.md": "changelog",
            "docs/releases.md": "release instructions",
            "legacy/v3/README.md": "legacy",
            ".github/workflows/v4.yml": "workflow",
            "pyproject.toml": "configuration",
            "requirements-dev.txt": "development dependencies",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        self.manifest = {
            "schema": 1,
            "version": "4.0.0-test",
            "mesa": "27.0.0",
            "architecture": "x86_64",
            "patch_order": ["patches/test.patch"],
            "source_inputs": {
                "patches/test.patch": build.digest(self.root / "v4/patches/test.patch")
            },
            "sources": {},
        }
        build.write_json(self.root / "v4/manifest.json", self.manifest)
        self.source = build.source_directory(self.work, self.manifest)
        for name, data in {
            "src/original.c": "original",
            "docs/license.rst": "license overview",
            "licenses/MIT": "full MIT text",
        }.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        build.write_json(self.work / "source-files.json", build.snapshot_source(self.source))
        info = self.work / "build/meson-info"
        info.mkdir(parents=True)
        build.write_json(
            info / "intro-buildoptions.json",
            [{"machine": "any", "name": "radv-u_trace", "value": False}],
        )
        build.write_json(info / "intro-compilers.json", {})
        build.write_json(
            info / "intro-dependencies.json",
            [{"name": "zlib", "type": "pkgconfig", "version": "1.2.3"}],
        )
        build.write_json(info / "meson-info.json", {"meson_version": {"full": "1.12.0"}})
        self.library = self.work / "build/src/amd/vulkan/libvulkan_radeon.so"
        self.library.parent.mkdir(parents=True)
        self.library.write_bytes(b"fixture library")
        build.write_json(self.work / "build-inputs.json", {"options": build.OPTIONS})
        build.write_json(self.work / "configuration.json", build.configuration(self.work / "build"))
        self.result = {
            "schema": 2,
            "sha256": build.digest(self.library),
            "library": str(self.library),
            "options": build.OPTIONS,
            "manifest_sha256": build.digest(self.root / "v4/manifest.json"),
            "recipe_hashes": build.recipe_hashes(self.root),
            "source_files_sha256": build.digest(self.work / "source-files.json"),
            "build_inputs_sha256": build.digest(self.work / "build-inputs.json"),
            "configuration_sha256": build.digest(self.work / "configuration.json"),
            "configuration": build.configuration(self.work / "build"),
        }
        build.write_json(self.work / "build-result.json", self.result)

    def test_completed_build_accepts_original_inputs(self):
        manifest, result = build.verify_completed_build(self.work, self.root)
        self.assertEqual(manifest["mesa"], "27.0.0")
        self.assertEqual(result["sha256"], build.digest(self.library))

    def test_package_rejects_patch_edited_after_build(self):
        (self.root / "v4/patches/test.patch").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "source input changed"):
            build.verify_completed_build(self.work, self.root)

    def test_package_rejects_recipe_edited_after_build(self):
        (self.root / "scripts/build.py").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "recipe changed"):
            build.verify_completed_build(self.work, self.root)

    def test_package_rejects_unpatched_source_edited_after_build(self):
        (self.source / "src/original.c").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "Materialized source mismatch"):
            build.verify_completed_build(self.work, self.root)

    def test_package_rejects_configuration_edited_after_build(self):
        build.write_json(
            self.work / "build/meson-info/intro-buildoptions.json",
            [{"machine": "any", "name": "radv-u_trace", "value": True}],
        )
        with self.assertRaisesRegex(RuntimeError, "Meson configuration changed"):
            build.verify_completed_build(self.work, self.root)

    def test_package_rejects_binary_edited_after_build(self):
        self.library.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "library changed"):
            build.verify_completed_build(self.work, self.root)

    def test_package_rejects_changed_input_record(self):
        build.write_json(self.work / "build-inputs.json", {"options": ["-Dradv-u_trace=true"]})
        with self.assertRaisesRegex(RuntimeError, "Build input record changed"):
            build.verify_completed_build(self.work, self.root)

    def test_public_provenance_redacts_workspace_and_home_paths(self):
        original = {
            "nested": {
                "flags": [
                    f"-I{self.work}/include",
                    f"-I{self.root}/include",
                    f"-L{Path.home()}/private/lib",
                    "-O2",
                ]
            }
        }
        public = package.public_provenance(original, self.work, self.root)
        self.assertEqual(
            public["nested"]["flags"],
            ["-I<build-work>/include", "-I<source-root>/include", "-L<home>/private/lib", "-O2"],
        )

    def test_source_tree_does_not_invent_git_provenance(self):
        self.assertEqual(
            release_common.source_identity(self.root),
            {"kind": "source-tree", "commit": None, "dirty": None},
        )

    def test_package_rejects_legacy_provenance(self):
        self.result.pop("schema")
        build.write_json(self.work / "build-result.json", self.result)
        with self.assertRaisesRegex(RuntimeError, "new work directory"):
            build.verify_completed_build(self.work, self.root)

    def test_ordered_patches_must_be_hashed(self):
        self.manifest["patch_order"].append("patches/unpinned.patch")
        build.write_json(self.root / "v4/manifest.json", self.manifest)
        with self.assertRaisesRegex(RuntimeError, "Every ordered patch"):
            build.verify_inputs(self.root)

    def test_dependency_upgrade_rejects_resume(self):
        with mock.patch.object(build.subprocess, "check_output", return_value="1.2.4\n"):
            with self.assertRaisesRegex(RuntimeError, "dependency changed: zlib"):
                build.verify_dependency_versions(self.result["configuration"], {})

    def test_github_source_inventory_preserves_development_and_legacy(self):
        (self.root / ".work").mkdir()
        (self.root / ".work" / "private").write_text("excluded")
        files = release_common.source_files(self.root)
        for name in [
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "docs/releases.md",
            "legacy/v3/README.md",
            ".github/workflows/v4.yml",
            "pyproject.toml",
        ]:
            self.assertIn(name, files)
        self.assertNotIn(".work/private", files)

    def test_source_inventory_rejects_unrecognized_content(self):
        (self.root / "private-key").write_text("exclude")
        with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
            release_common.source_files(self.root)

    def test_source_snapshot_works_and_rejects_modified_source(self):
        inventory = release_common.source_files(self.root)
        build.write_json(
            self.root / "source-snapshot.json",
            {
                "schema": 1,
                "commit": "a" * 40,
                "files": {
                    name: {"sha256": build.digest(self.root / name), "mode": 420}
                    for name in inventory
                },
            },
        )
        self.assertIn("source-snapshot.json", release_common.source_files(self.root))
        self.assertEqual(release_common.source_identity(self.root)["commit"], "a" * 40)
        (self.root / "README.md").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "snapshot file changed"):
            release_common.source_files(self.root)

    def test_source_inventory_does_not_read_external_symlink(self):
        (self.root / "README.md").unlink()
        (self.root / "README.md").symlink_to(self.work / "build-result.json")
        with self.assertRaisesRegex(RuntimeError, "unsafe or missing"):
            release_common.source_files(self.root)

    @unittest.skipUnless(shutil.which("git"), "Git is required for checkout inventory")
    def test_checkout_inventory_excludes_untracked_content(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        (self.root / "private-key").write_text("excluded")
        files = release_common.source_files(self.root)
        self.assertIn(".github/workflows/v4.yml", files)
        self.assertNotIn("private-key", files)

    @unittest.skipUnless(
        all(shutil.which(name) for name in ("cc", "strip", "readelf")),
        "C compiler and binutils are required for the archive round trip",
    )
    def test_binary_archive_contains_complete_sources_and_license_text(self):
        subprocess.run(
            ["cc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(self.library)],
            input="int bc250_fixture(void) { return 4; }\n",
            text=True,
            check=True,
        )
        self.result["sha256"] = build.digest(self.library)
        build.write_json(self.work / "build-result.json", self.result)
        original_hash = build.digest(self.library)
        output = Path(self.temporary.name) / "output"
        argv = ["package.py", "--work", str(self.work), "--output", str(output), "--label", "test"]
        with mock.patch.object(package, "ROOT", self.root), mock.patch.object(sys, "argv", argv):
            package.main()
        archive = next(output.glob("*.tar.gz"))
        checksum = Path(str(archive) + ".sha256").read_text().split()[0]
        self.assertEqual(checksum, build.digest(archive))
        self.assertEqual(original_hash, build.digest(self.library))
        destination = Path(self.temporary.name) / "extracted"
        destination.mkdir()
        payload, release = driver.extract_verified(archive, destination, checksum)
        for name in [
            "licenses/Mesa/MIT",
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "legacy/v3/README.md",
            ".github/workflows/v4.yml",
            "docs/releases.md",
        ]:
            self.assertIn(name, release["files"])
        self.assertEqual((payload / "licenses/Mesa/MIT").read_text(), "full MIT text")
        with mock.patch.object(package, "ROOT", self.root), mock.patch.object(sys, "argv", argv):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                package.main()
        self.assertEqual(checksum, build.digest(archive))

    def test_failed_archive_creation_publishes_nothing(self):
        output = Path(self.temporary.name) / "failed-output"
        argv = ["package.py", "--work", str(self.work), "--output", str(output), "--label", "test"]
        with (
            mock.patch.object(package, "ROOT", self.root),
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(package.subprocess, "run"),
            mock.patch.object(package.subprocess, "check_output", return_value="ELF fixture"),
            mock.patch.object(tarfile, "open", side_effect=OSError("disk full")),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                package.main()
        self.assertEqual(list(output.iterdir()), [])

    def test_manifest_changed_during_copy_publishes_nothing(self):
        output = Path(self.temporary.name) / "concurrent-output"
        argv = ["package.py", "--work", str(self.work), "--output", str(output)]
        copy = shutil.copy2

        def concurrent_edit(source, destination, *args, **kwargs):
            if Path(source) == self.root / "v4/manifest.json":
                self.manifest["version"] = "4.0.0-edited"
                build.write_json(source, self.manifest)
            return copy(source, destination, *args, **kwargs)

        with (
            mock.patch.object(package, "ROOT", self.root),
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(package.subprocess, "run"),
            mock.patch.object(package.shutil, "copy2", side_effect=concurrent_edit),
        ):
            with self.assertRaisesRegex(RuntimeError, "Source manifest changed while packaging"):
                package.main()
        self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
