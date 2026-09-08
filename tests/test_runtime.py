# SPDX-License-Identifier: MIT
"""Owned runtime installation and recovery using small local fixture bundles."""

import argparse
import fcntl
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runtime


class RuntimeInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.steam = self.root / "Steam"
        self.stock = self.steam / "compatibilitytools.d/GE-Proton11-6-x86_64"
        self.stock.mkdir(parents=True)
        (self.stock / "proton").write_bytes(b"shared stock runtime")
        (self.steam / "config").mkdir()
        (self.steam / "config/config.vdf").write_bytes(b"unrelated mappings\n")
        (self.steam / "userdata").mkdir()
        (self.steam / "userdata/sentinel").write_bytes(b"unrelated accounts\n")
        self.tool = self.steam / "compatibilitytools.d" / runtime.TOOL
        self.library = self.root / "libvulkan_radeon.so"
        self.library.write_bytes(b"fixture BC250 driver")
        self.selected = {
            "mode": "system",
            "library": str(self.library),
            "sha256": runtime.driver.digest(self.library),
            "source_manifest_sha256": "a" * 64,
            "environment": {},
        }
        self.driver_patch = mock.patch.object(runtime, "select_driver", return_value=self.selected)
        self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.probe = mock.patch.object(
            runtime.driver, "probe", return_value={"success": True}
        ).start()
        self.addCleanup(mock.patch.stopall)
        self.args = argparse.Namespace(
            archive=None,
            sha256=None,
            driver="auto",
            driver_prefix=self.root / "driver",
            cache=self.root / "cache",
            offline=True,
            version=None,
        )
        self.archive = self.bundle("1.0.0-rc1")
        self.args.archive = self.archive

    def tree(self, version, driver_source=None):
        name = "bc250-fsr4-runtime-" + version
        root = self.root / "fixtures" / name
        root.mkdir(parents=True)
        wine = bytearray(32)
        wine[:5] = b"\x7fELF\x02"
        wine[18:20] = b"\x3e\x00"
        for name in runtime.REQUIRED_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(wine) if name.endswith("/wine") else (version + name).encode())
            path.chmod(0o755 if name in ("ge/proton", "ge/files/bin/wine") else 0o644)
        (root / "runtime-lock.json").write_text(
            json.dumps(
                {
                    "driver": {
                        "source_manifest_sha256": driver_source
                        or self.selected["source_manifest_sha256"]
                    }
                }
            )
        )
        (root / "ge/internal-link").symlink_to("proton")
        self.inventory(root, version)
        return root

    def inventory(self, root, version):
        files = {}
        for path in root.rglob("*"):
            if path.name == "runtime-release.json" or (path.is_dir() and not path.is_symlink()):
                continue
            files[str(path.relative_to(root))] = (
                {"target": os.readlink(path)}
                if path.is_symlink()
                else {"sha256": runtime.driver.digest(path), "mode": path.stat().st_mode & 0o7777}
            )
        manifest = {
            "schema": 1,
            "id": root.name,
            "version": version,
            "files": files,
            "critical_files": sorted(runtime.REQUIRED_FILES),
        }
        (root / "runtime-release.json").write_text(json.dumps(manifest))

    def bundle(self, version, driver_source=None):
        root = self.tree(version, driver_source)
        archive = self.root / (root.name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(root, arcname=root.name)
        Path(str(archive) + ".sha256").write_text(
            runtime.driver.digest(archive) + "  " + archive.name + "\n"
        )
        return archive

    def install(self, archive=None):
        self.args.archive = archive or self.archive
        return runtime.install(self.args, self.steam)

    def assert_external_unchanged(self):
        self.assertEqual((self.stock / "proton").read_bytes(), b"shared stock runtime")
        self.assertEqual((self.steam / "config/config.vdf").read_bytes(), b"unrelated mappings\n")
        self.assertEqual((self.steam / "userdata/sentinel").read_bytes(), b"unrelated accounts\n")

    def test_additive_install_and_inventory_status(self):
        result = self.install()
        self.assertEqual(result["version"], "bc250-fsr4-runtime-1.0.0-rc1")
        report = runtime.status(self.steam)
        self.assertTrue(report["installed"])
        self.assertEqual(report["version"], "1.0.0-rc1")
        self.assertEqual(report["driver"], self.selected)
        self.assertEqual(report["interrupted_transactions"], [])
        self.assertTrue((self.tool / "current").is_symlink())
        self.assertEqual((self.tool / "current/ge/internal-link").readlink(), Path("proton"))
        self.probe.assert_called_once()
        self.assert_external_unchanged()

    def test_repeat_install_reuses_owned_version(self):
        self.install()
        inode = (self.tool / "current/ge/proton").stat().st_ino
        self.install()
        self.assertEqual((self.tool / "current/ge/proton").stat().st_ino, inode)
        self.assertEqual(runtime.records(self.tool), [])

    def test_registration_satisfies_steam_name_detection_and_preserves_old_selection(self):
        self.install()
        # Steam checks the internal key, separately from the display/layer names.
        text = (self.tool / "compatibilitytool.vdf").read_text()
        self.assertIn('    "proton-bc250-fsr4"\n', text)
        self.assertIn('"aliases" "BC250-FSR4"', text)
        self.assertTrue(runtime.status(self.steam)["steam_registration"]["save_paths_supported"])
        self.assert_external_unchanged()

    def legacy_registration(self):
        path = self.tool / "compatibilitytool.vdf"
        path.write_text(runtime.LEGACY_REGISTRATION)
        return path

    def test_registration_upgrade_preserves_old_bytes_accounts_and_runtime_rollback(self):
        self.install()
        old = runtime.current_version(self.tool)
        path = self.legacy_registration()
        self.assertFalse(runtime.status(self.steam)["steam_registration"]["save_paths_supported"])
        self.install(self.bundle("1.0.0-rc2"))
        self.assertEqual(path.read_text(), runtime.STATIC_FILES[path.name])
        self.assertEqual(
            (self.tool / "compatibilitytool.rc5.vdf.backup").read_text(),
            runtime.LEGACY_REGISTRATION,
        )
        runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), old)
        self.assertTrue(runtime.status(self.steam)["steam_registration"]["save_paths_supported"])
        self.assert_external_unchanged()

    def test_registration_update_resumes_after_interrupted_publication(self):
        self.install()
        path = self.legacy_registration()
        with mock.patch.object(runtime.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                runtime.update_registration(self.tool)
        self.assertEqual(path.read_text(), runtime.LEGACY_REGISTRATION)
        self.assertTrue(runtime.update_registration(self.tool))
        self.assertFalse(runtime.update_registration(self.tool))
        self.assert_external_unchanged()

    def test_registration_repair_preserves_foreign_edits_and_backup_links(self):
        self.install()
        path = self.legacy_registration()
        path.write_text(runtime.LEGACY_REGISTRATION.replace("BC250 FSR4 (4.1.1 INT8)", "Custom"))
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "preserving"):
            runtime.update_registration(self.tool)
        self.assertEqual(path.read_bytes(), before)
        self.legacy_registration()
        backup = self.tool / "compatibilitytool.rc5.vdf.backup"
        backup.symlink_to(self.steam / "config/config.vdf")
        with self.assertRaisesRegex(RuntimeError, "backup differs"):
            runtime.update_registration(self.tool)
        self.assertTrue(backup.is_symlink())
        self.assertEqual(path.read_text(), runtime.LEGACY_REGISTRATION)
        previous = runtime.current_version(self.tool)
        with self.assertRaisesRegex(RuntimeError, "backup differs"):
            self.install(self.bundle("foreign-backup"))
        self.assertEqual(runtime.current_version(self.tool), previous)
        self.assert_external_unchanged()

    def test_offline_rebind_reuses_verified_runtime_without_upstream_cache(self):
        self.install()
        retained = self.tool / "current"
        inode = (retained / "ge/proton").stat().st_ino
        policy = json.loads((retained / "runtime-lock.json").read_text())
        policy["release"] = {"id": retained.resolve().name, "version": "1.0.0-rc1"}
        (retained / "runtime-lock.json").write_text(json.dumps(policy))
        self.inventory(retained.resolve(), "1.0.0-rc1")
        project = self.root / "project"
        (project / "runtime").mkdir(parents=True)
        shutil.copy2(retained / "runtime-lock.json", project / "runtime/manifest.json")
        replacement = self.root / "replacement.so"
        replacement.write_bytes(b"corrected driver ABI")
        self.selected.update(library=str(replacement), sha256=runtime.driver.digest(replacement))
        self.args.archive = None
        import runtime_bundle

        with (
            mock.patch.object(runtime, "ROOT", project),
            mock.patch.object(
                runtime_bundle, "assemble", side_effect=AssertionError("must stay offline")
            ),
        ):
            runtime.install(self.args, self.steam)
        self.assertEqual((retained / "ge/proton").stat().st_ino, inode)
        self.assertEqual(runtime.selection(self.steam)["driver"], self.selected)
        self.assertFalse(self.args.cache.exists())

    def test_runtime_discovery_reads_secondary_library_with_spaces_and_escapes(self):
        library = self.root / 'secondary "disk"'
        container = library / "steamapps/common/SteamLinuxRuntime_4"
        container.mkdir(parents=True)
        (container / "run").touch()
        escaped = str(library).replace('"', '\\"')
        (self.steam / "config/libraryfolders.vdf").write_text('"path" "' + escaped + '"\n')
        self.assertEqual(runtime.steam_runtime(self.steam), container)

    def test_missing_steam_runtime_is_reported_as_pending(self):
        result = runtime.probe_driver(self.selected, self.steam)
        self.assertTrue(result["host"]["success"])
        self.assertEqual(result["steam_runtime"]["state"], "pending")

    def test_upgrade_and_rollback_preserve_both_versions_and_stock(self):
        self.install()
        first = runtime.current_version(self.tool)
        self.install(self.bundle("1.0.0-rc2"))
        second = runtime.current_version(self.tool)
        self.assertNotEqual(first, second)
        runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), first)
        self.args.version = second
        runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), second)
        self.assertEqual(len(list((self.tool / "versions").iterdir())), 2)
        self.assert_external_unchanged()

    def test_rollback_checks_retained_bytes_before_switch(self):
        self.install()
        first = runtime.current_version(self.tool)
        self.install(self.bundle("1.0.0-rc2"))
        second = runtime.current_version(self.tool)
        (self.tool / "versions" / first / "launch.py").write_text("edited")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), second)

    def test_first_install_has_no_rollback_version(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, "No previous runtime version"):
            runtime.rollback(self.args, self.steam)

    def test_runtime_shared_lock_prevents_upgrade_and_rollback(self):
        self.install()
        with (self.tool / ".runtime.lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            with self.assertRaisesRegex(RuntimeError, "running"):
                self.install()
            with self.assertRaisesRegex(RuntimeError, "running"):
                runtime.rollback(self.args, self.steam)

    def test_interrupted_switch_recovers_before_retry(self):
        self.install()
        first = runtime.current_version(self.tool)
        archive = self.bundle("1.0.0-rc2")
        with mock.patch.object(runtime.driver, "switch", side_effect=OSError("interruption")):
            with self.assertRaisesRegex(OSError, "interruption"):
                self.install(archive)
        self.assertEqual(runtime.current_version(self.tool), first)
        self.assertEqual(runtime.records(self.tool)[0][1]["state"], "prepared")
        self.install(archive)
        self.assertEqual(
            [r["state"] for _, r in runtime.records(self.tool)], ["aborted", "complete"]
        )

    def test_interrupted_commit_is_completed_after_current_switched(self):
        self.install()
        original = runtime.driver.write_json

        def interrupt_commit(path, value):
            if path.parent.name == "transactions" and value["state"] == "complete":
                raise OSError("interruption")
            return original(path, value)

        with mock.patch.object(runtime.driver, "write_json", side_effect=interrupt_commit):
            with self.assertRaisesRegex(OSError, "interruption"):
                self.install(self.bundle("1.0.0-rc2"))
        runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), "bc250-fsr4-runtime-1.0.0-rc1")
        self.assertEqual(runtime.records(self.tool)[0][1]["state"], "complete")

    def test_edited_static_entry_is_preserved(self):
        self.install()
        path = self.tool / "proton"
        path.write_text("user wrapper")
        with self.assertRaisesRegex(RuntimeError, "preserving"):
            self.install()
        self.assertEqual(path.read_text(), "user wrapper")

    def test_existing_unmanaged_tool_is_preserved(self):
        self.tool.mkdir()
        (self.tool / "user-file").write_text("owned elsewhere")
        with self.assertRaisesRegex(RuntimeError, "preserving"):
            self.install()
        self.assertEqual((self.tool / "user-file").read_text(), "owned elsewhere")

    def test_current_cannot_escape_owned_versions(self):
        self.install()
        (self.tool / "current").unlink()
        (self.tool / "current").symlink_to("../GE-Proton11-6-x86_64")
        with self.assertRaisesRegex(RuntimeError, "outside"):
            runtime.status(self.steam)

    def test_driver_change_requires_rebinding(self):
        self.install()
        self.library.write_text("another driver")
        with self.assertRaisesRegex(RuntimeError, "driver changed"):
            runtime.status(self.steam)
        with self.assertRaisesRegex(RuntimeError, "driver changed"):
            self.install()

    def test_archive_driver_contract_mismatch_does_not_create_entry(self):
        archive = self.bundle("different-driver", "b" * 64)
        with self.assertRaisesRegex(RuntimeError, "different driver source"):
            self.install(archive)
        self.assertFalse(self.tool.exists())
        self.assert_external_unchanged()

    def test_rollback_requires_the_retained_versions_driver_source(self):
        self.install()
        self.selected["source_manifest_sha256"] = "b" * 64
        self.install(self.bundle("new-driver"))
        current = runtime.current_version(self.tool)
        with self.assertRaisesRegex(RuntimeError, "different driver source"):
            runtime.rollback(self.args, self.steam)
        self.assertEqual(runtime.current_version(self.tool), current)
        self.assertEqual(runtime.status(self.steam)["id"], current)

    def test_status_rejects_an_unrecorded_driver_source(self):
        self.install()
        selected = json.loads((self.tool / "driver.json").read_text())
        del selected["source_manifest_sha256"]
        (self.tool / "driver.json").write_text(json.dumps(selected))
        with self.assertRaisesRegex(RuntimeError, "different driver source"):
            runtime.status(self.steam)

    def test_private_driver_current_icd_must_still_select_the_bound_library(self):
        icd = self.root / "current.json"
        icd.write_text(json.dumps(runtime.driver.icd(self.library)))
        selected = {
            **self.selected,
            "mode": "private",
            "environment": {"VK_DRIVER_FILES": str(icd)},
        }
        runtime.verify_driver(selected)
        replacement = self.root / "other-driver.so"
        replacement.write_bytes(self.library.read_bytes())
        icd.write_text(json.dumps(runtime.driver.icd(replacement)))
        with self.assertRaisesRegex(RuntimeError, "selection changed"):
            runtime.verify_driver(selected)

    def test_failed_driver_probe_does_not_create_entry(self):
        self.probe.side_effect = RuntimeError("dependency missing")
        with self.assertRaisesRegex(RuntimeError, "dependency"):
            self.install()
        self.assertFalse(self.tool.exists())

    def test_wrong_archive_hash_does_not_create_entry(self):
        self.args.sha256 = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.install()
        self.assertFalse(self.tool.exists())

    def test_no_archive_uses_pinned_assembler_and_offline_cache(self):
        built = self.tree("2.0.0")

        def assemble(destination, cache, policy, *, offline):
            self.assertEqual(cache, self.args.cache)
            self.assertTrue(offline)
            self.assertEqual(policy["release"]["id"], built.name)
            return Path(shutil.copytree(built, destination / built.name, symlinks=True))

        policy_root = self.root / "policy"
        (policy_root / "runtime").mkdir(parents=True)
        (policy_root / "runtime/manifest.json").write_text(
            json.dumps({"release": {"id": built.name}})
        )
        self.args.archive = None
        with mock.patch.dict(
            sys.modules, {"runtime_bundle": types.SimpleNamespace(assemble=assemble)}
        ):
            with mock.patch.object(runtime, "ROOT", policy_root):
                result = runtime.install(self.args, self.steam)
        self.assertEqual(result["version"], built.name)
        self.assert_external_unchanged()

    def test_missing_modified_or_extra_payload_is_rejected(self):
        for operation in ("missing", "changed", "extra", "mode"):
            with self.subTest(operation=operation):
                tree = self.tree("bad-" + operation)
                if operation == "missing":
                    (tree / "launch.py").unlink()
                elif operation == "changed":
                    (tree / "launch.py").write_text("different")
                elif operation == "mode":
                    (tree / "ge/proton").chmod(0o644)
                else:
                    (tree / "unexpected").write_text("extra")
                with self.assertRaises(RuntimeError):
                    runtime.verify_version(tree)

    def test_version_cannot_link_to_shared_stock(self):
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                tree = self.tree("stock-link-" + str(hardlink))
                (tree / "ge/proton").unlink()
                if hardlink:
                    os.link(self.stock / "proton", tree / "ge/proton")
                else:
                    (tree / "ge/proton").symlink_to(self.stock / "proton")
                self.inventory(tree, "stock-link-" + str(hardlink))
                with self.assertRaises(RuntimeError):
                    runtime.verify_version(tree)
        self.assert_external_unchanged()

    def test_only_empty_regular_generated_ge_lock_is_allowed(self):
        tree = self.tree("ge-lock")
        lock = tree / "ge/dist.lock"
        lock.touch()
        runtime.verify_version(tree)
        lock.write_text("unexpected payload")
        with self.assertRaisesRegex(RuntimeError, "runtime lock"):
            runtime.verify_version(tree)
        lock.unlink()
        lock.symlink_to("proton")
        with self.assertRaisesRegex(RuntimeError, "runtime lock"):
            runtime.verify_version(tree)

    def test_unsafe_archive_links_and_paths_are_rejected(self):
        for entry, target in (
            ("../outside", None),
            ("link", "/etc/passwd"),
            ("link", "../../outside"),
        ):
            with self.subTest(entry=entry, target=target):
                archive = self.root / "unsafe.tar.gz"
                name = "bc250-fsr4-runtime-unsafe"
                with tarfile.open(archive, "w:gz") as bundle:
                    first = tarfile.TarInfo(name)
                    first.type = tarfile.DIRTYPE
                    bundle.addfile(first)
                    item = tarfile.TarInfo(name + "/" + entry)
                    if target:
                        item.type = tarfile.SYMTYPE
                        item.linkname = target
                    bundle.addfile(item, io.BytesIO(b""))
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    with self.assertRaises(RuntimeError):
                        runtime.extract_verified(
                            archive, Path(temporary), runtime.driver.digest(archive)
                        )
        self.assertFalse((self.root / "outside").exists())

    def test_status_absent_is_read_only(self):
        self.assertFalse(runtime.status(self.steam)["installed"])
        self.assertFalse(self.tool.exists())


class DriverSelectionTests(unittest.TestCase):
    def test_rebuilt_system_driver_requires_matching_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "libvulkan_radeon.so"
            library.write_bytes(b"independently rebuilt system driver")
            metadata = root / "system.json"
            policy = json.loads((runtime.ROOT / "runtime/manifest.json").read_text())["driver"]
            record = {
                "driver_sha256": runtime.driver.digest(library),
                "version": policy["version"],
                "mesa": policy["mesa"],
            }
            with (
                mock.patch.object(runtime, "SYSTEM_LIBRARY", library),
                mock.patch.object(runtime, "SYSTEM_METADATA", metadata),
            ):
                for source in (None, "0" * 64):
                    metadata.write_text(json.dumps({**record, "source_manifest_sha256": source}))
                    with self.assertRaisesRegex(RuntimeError, "verified v4 driver was not found"):
                        runtime.select_driver("system", root)
                metadata.write_text(
                    json.dumps(
                        {**record, "source_manifest_sha256": policy["source_manifest_sha256"]}
                    )
                )
                self.assertEqual(
                    runtime.select_driver("system", root)["sha256"], record["driver_sha256"]
                )


if __name__ == "__main__":
    unittest.main()
