# SPDX-License-Identifier: MIT
"""Build provenance and source-distribution contracts without a GPU or Mesa build."""

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import chdir
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


class ToolIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wrapper = self.root / "wrapper"
        self.wrapper.write_text('#!/bin/sh\nexec "$@"\n')
        self.wrapper.chmod(0o755)
        self.compiler = self.root / "fixturecc"
        self.compiler.write_text("#!/bin/sh\n# first implementation\nprintf 'compiler 1.0\\n'\n")
        self.compiler.chmod(0o755)

    def test_wrapped_compiler_bytes_are_checked_even_with_same_version(self):
        command = shlex.join([str(self.wrapper), "fixturecc"])
        with mock.patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.defpath}):
            before = build.tool_identity(command)
            self.compiler.write_text(
                "#!/bin/sh\n# changed implementation\nprintf 'compiler 1.0\\n'\n"
            )
            after = build.tool_identity(command)
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["executable_sha256"], after["executable_sha256"])
        self.assertNotEqual(before["command_files"], after["command_files"])
        self.assertEqual(after["command_files"][1]["path"], str(self.compiler.resolve()))

    def test_nonexecutable_compiler_script_is_fingerprinted(self):
        script = self.root / "compiler script.py"
        script.write_text("# first implementation\nprint('compiler 1.0')\n")
        command = shlex.join([sys.executable, str(script)])
        before = build.tool_identity(command)
        script.write_text("# changed implementation\nprint('compiler 1.0')\n")
        after = build.tool_identity(command)
        self.assertFalse(os.access(script, os.X_OK))
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["executable_sha256"], after["executable_sha256"])
        self.assertNotEqual(before["command_files"], after["command_files"])
        self.assertEqual(after["command_files"][1]["path"], str(script.resolve()))

    def test_relative_interpreter_script_is_not_shadowed_by_path(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        script = scripts / self.compiler.name
        script.write_text("print('compiler 1.0')\n")
        with (
            chdir(scripts),
            mock.patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.defpath}),
        ):
            identity = build.tool_identity(shlex.join([sys.executable, script.name]))
        recorded = {item["path"] for item in identity["command_files"]}
        self.assertIn(str(script.resolve()), recorded)

    def test_configured_compiler_exelist_detects_same_version_replacement(self):
        info = self.root / "build/meson-info"
        info.mkdir(parents=True)
        build.write_json(info / "intro-buildoptions.json", [])
        build.write_json(info / "intro-dependencies.json", [])
        build.write_json(info / "meson-info.json", {"meson_version": {"full": "1.12.0"}})
        build.write_json(
            info / "intro-compilers.json",
            {
                "host": {
                    "c": {
                        "id": "fixture",
                        "version": "1.0",
                        "full_version": "compiler 1.0",
                        "exelist": [str(self.wrapper), str(self.compiler)],
                        "linker_exelist": [str(self.compiler)],
                        "linker_id": "fixture",
                    }
                }
            },
        )
        before = build.configuration(info.parent)
        self.compiler.write_text("#!/bin/sh\n# changed implementation\nprintf 'compiler 1.0\\n'\n")
        with self.assertRaisesRegex(RuntimeError, "Meson configuration changed"):
            build.verify_configuration(info.parent, before)
        # The package command may run outside the original compiler container.
        # Compiler hashes remain provenance; the static Meson settings still match.
        self.compiler.unlink()
        build.verify_configuration(info.parent, before, check_tools=False)


class PortableTlsTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("c++") and shutil.which("readelf"),
        "C/C++ compilers and readelf are required for the ABI regression check",
    )
    def test_default_c_and_cpp_tls_load_on_pre_gnu2_libc(self):
        # Exercise emitted ELF relocations, not just the spelling of the flags.
        # This fails with the former defaults on a GNU2-default toolchain.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tls.c"
            source.write_text("__thread int value; int *address(void) { return &value; }\n")
            for compiler in ("cc", "c++"):
                with self.subTest(compiler=compiler):
                    library = root / "tls.so"
                    subprocess.run(
                        [
                            compiler,
                            *shlex.split(build.DEFAULT_FLAGS),
                            "-shared",
                            "-fPIC",
                            str(source),
                            "-o",
                            str(library),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    relocations = subprocess.check_output(
                        ["readelf", "--relocs", "--wide", str(library)], text=True
                    )
                    versions = subprocess.check_output(
                        ["readelf", "--version-info", str(library)], text=True
                    )
                    self.assertNotIn("TLSDESC", relocations)
                    self.assertNotIn("GLIBC_ABI_GNU2_TLS", versions)
                    self.assertIn("__tls_get_addr", relocations)


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

    @unittest.skipUnless(shutil.which("cc"), "C preprocessor required")
    def test_unrecorded_header_can_change_compiler_input_but_cannot_resume(self):
        include = self.source / "include"
        include.mkdir()
        source = self.source / "src/original.c"
        source.write_text('#include "answer.h"\nint answer = VALUE;\n')
        (include / "answer.h").write_text("#define VALUE 1\n")
        recorded = build.snapshot_source(self.source)
        command = ["cc", "-E", "-P", "-I", str(include), str(source)]
        before = subprocess.check_output(command, text=True)
        (source.parent / "answer.h").write_text("#define VALUE 2\n")
        after = subprocess.check_output(command, text=True)
        self.assertNotEqual(before, after)
        with self.assertRaisesRegex(RuntimeError, "source inventory changed"):
            build.verify_source(self.source, recorded, complete=True)

    def test_package_rejects_unrecorded_source_file(self):
        (self.source / "src/override.h").write_text("unrecorded compiler input")
        with self.assertRaisesRegex(RuntimeError, "source inventory changed"):
            build.verify_completed_build(self.work, self.root)

    def test_internal_source_links_are_pinned_and_external_links_rejected(self):
        alias = self.source / "include"
        alias.symlink_to("src", target_is_directory=True)
        recorded = build.snapshot_source(self.source)
        build.verify_source(self.source, recorded, complete=True)
        alias.unlink()
        alias.symlink_to("licenses", target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "source mismatch"):
            build.verify_source(self.source, recorded, complete=True)
        alias.unlink()
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "Unsafe materialized source"):
            build.snapshot_source(self.source)

    def test_source_script_modes_and_special_files_are_checked(self):
        original = self.source / "src/original.c"
        recorded = build.snapshot_source(self.source)
        original.chmod(original.stat().st_mode ^ 0o100)
        with self.assertRaisesRegex(RuntimeError, "source mismatch"):
            build.verify_source(self.source, recorded, complete=True)
        os.mkfifo(self.source / "unrecorded-pipe")
        with self.assertRaisesRegex(RuntimeError, "Nonregular materialized source"):
            build.snapshot_source(self.source)

    def test_package_rejects_patch_edited_after_build(self):
        (self.root / "v4/patches/test.patch").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "source input changed"):
            build.verify_completed_build(self.work, self.root)

    def test_portable_build_rejects_edited_imported_target_recipe(self):
        for name in (
            "build-compat.py",
            "build-steamos.py",
            "runtime_bundle.py",
            "driver.py",
            "safe_archive.py",
        ):
            (self.root / "scripts" / name).write_text("original " + name)
        definition = self.root / "v4/build-targets/linux-glibc236.json"
        definition.parent.mkdir()
        source = self.work / "target-sources/libdrm-test.tar.xz"
        source.parent.mkdir()
        source.write_bytes(b"pinned source")
        build.write_json(
            definition,
            {"packages": [], "libdrm": {"version": "test", "sha256": build.digest(source)}},
        )
        self.result.update(display_info="disabled", options=build.build_options("disabled"))
        self.result["target"] = {
            "id": "linux-glibc236-x86_64",
            "definition_sha256": build.digest(definition),
            "builder_sha256": build.digest(self.root / "scripts/build-compat.py"),
            "recipe_hashes": build.target_recipe_hashes(self.root, portable=True),
            "packages": [],
            "source_archives": {source.name: build.digest(source)},
        }
        build.write_json(self.work / "build-result.json", self.result)
        build.verify_completed_build(self.work, self.root)
        (self.root / "scripts/build-steamos.py").write_text("changed ABI/link environment")
        with self.assertRaisesRegex(RuntimeError, "Target build recipe changed"):
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
