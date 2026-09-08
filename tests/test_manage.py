# SPDX-License-Identifier: MIT
"""Coordinated driver/runtime ownership and interruption recovery in temporary roots."""

import argparse
import contextlib
import fcntl
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import driver
import manage

import runtime


class PowerLoss(BaseException):
    pass


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.steam = self.root / "Steam"
        self.steam.mkdir()
        self.prefix = self.root / "private"
        self.prefix.mkdir()
        self.tool = self.steam / "compatibilitytools.d" / runtime.TOOL
        self.system = None
        self.source = "a" * 64
        self.policy = {
            "release": {"id": "bc250-fsr4-runtime-4.0.0-rc2", "version": "4.0.0-rc2"},
            "driver": {"source_manifest_sha256": self.source, "version": "4.0.0-rc1"},
        }
        self.project = self.root / "project"
        (self.project / "runtime").mkdir(parents=True)
        (self.project / "runtime/manifest.json").write_text(json.dumps(self.policy))
        for patcher in (
            mock.patch.object(manage, "ROOT", self.project),
            mock.patch.object(runtime, "select_driver", side_effect=self.select_driver),
            mock.patch.object(driver, "probe", return_value={"success": True}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.args = argparse.Namespace(
            driver="auto",
            driver_archive=self.driver_bundle("new", self.source),
            driver_sha256=None,
            runtime_archive=self.runtime_bundle("4.0.0-rc2"),
            runtime_sha256=None,
            cache=self.root / "cache",
            offline=True,
            upgrade_v3=False,
            upgrade_v3_icd=[],
        )

    def archive(self, root):
        path = self.root / (root.name + ".tar.gz")
        with tarfile.open(path, "w:gz") as bundle:
            bundle.add(root, arcname=root.name)
        Path(str(path) + ".sha256").write_text(driver.digest(path) + "\n")
        return path

    def driver_bundle(self, name, source):
        root = self.root / ("driver-" + name)
        (root / "lib").mkdir(parents=True)
        library = root / "lib/libvulkan_radeon.so"
        library.write_bytes(("driver fixture " + name).encode())
        (root / "release.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "architecture": "x86_64",
                    "version": "4.0.0-" + name,
                    "driver_sha256": driver.digest(library),
                    "source_manifest_sha256": source,
                    "files": {"lib/libvulkan_radeon.so": driver.digest(library)},
                }
            )
        )
        return self.archive(root)

    def runtime_bundle(self, version):
        root = self.root / ("bc250-fsr4-runtime-" + version)
        root.mkdir()
        wine = bytearray(32)
        wine[:5], wine[18:20] = b"\x7fELF\x02", b"\x3e\x00"
        for name in runtime.REQUIRED_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(wine) if name.endswith("/wine") else (version + name).encode())
            path.chmod(0o755 if name in ("ge/proton", "ge/files/bin/wine") else 0o644)
        lock = {**self.policy, "release": {"id": root.name, "version": version}}
        (root / "runtime-lock.json").write_text(json.dumps(lock))
        files = {
            str(p.relative_to(root)): {"sha256": driver.digest(p), "mode": p.stat().st_mode & 0o777}
            for p in root.rglob("*")
            if p.is_file()
        }
        (root / "runtime-release.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "id": root.name,
                    "version": version,
                    "files": files,
                    "critical_files": sorted(runtime.REQUIRED_FILES),
                }
            )
        )
        return self.archive(root)

    def select_driver(self, mode, prefix):
        if mode != "private" and self.system is not None:
            library, selected_mode = self.system, "system"
            source = self.source
        elif mode != "system" and driver.current_target(prefix):
            release = driver.verify_release(prefix / driver.current_target(prefix))
            source = release["source_manifest_sha256"]
            if source != self.source:
                raise RuntimeError("No compatible driver")
            library = prefix / driver.current_target(prefix) / "lib/libvulkan_radeon.so"
            selected_mode = "private"
        else:
            raise RuntimeError("No compatible driver")
        return {
            "mode": selected_mode,
            "library": str(library),
            "sha256": driver.digest(library),
            "source_manifest_sha256": source,
            "environment": {"VK_DRIVER_FILES": str(prefix / "current.json")}
            if selected_mode == "private"
            else {},
        }

    def existing_driver(self, archive=None):
        return driver.install(
            argparse.Namespace(
                archive=archive or self.args.driver_archive,
                sha256=None,
                upgrade_v3_icd=[],
                quiet=True,
            ),
            self.prefix,
        )

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return manage.apply(self.args, self.prefix, self.steam)

    def rollback(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return manage.rollback(self.prefix, self.steam)

    def interrupt_after_driver_commit(self):
        original = driver.write_json

        def power_loss(path, value):
            if Path(path).parent == self.prefix / "operations" and value.get("expected_runtime"):
                raise PowerLoss("power lost before runtime preparation was recorded")
            return original(path, value)

        with mock.patch.object(driver, "write_json", side_effect=power_loss):
            with self.assertRaises(PowerLoss):
                self.apply()
        operation = manage.operations(self.prefix)[-1][1]
        self.assertEqual(operation["state"], "pending")
        self.assertIsNone(operation["expected_runtime"])
        self.assertTrue((self.prefix / "transactions" / operation["driver_record"]).is_file())
        return operation

    def test_system_driver_reuse_and_rollback_never_modify_driver(self):
        self.args.driver_archive = None
        self.system = self.root / "system-driver.so"
        self.system.write_bytes(b"externally installed system driver")
        original = self.system.read_bytes()
        with mock.patch.object(driver, "install", wraps=driver.install) as install:
            report = self.apply()
            self.assertEqual(report["version"], "4.0.0-rc2")
            install.assert_not_called()
        operation = manage.operations(self.prefix)[-1][1]
        self.assertIsNone(operation["driver_record"])
        with mock.patch.object(driver, "rollback", wraps=driver.rollback) as rollback:
            report = self.rollback()
            rollback.assert_not_called()
        self.assertFalse(report["installed"])
        self.assertFalse(self.tool.exists())
        self.assertTrue(Path(operation["retired"]).is_dir())
        self.assertEqual(self.system.read_bytes(), original)

    def test_explicit_v3_migration_is_not_skipped_for_reusable_private_driver(self):
        self.apply()
        previous = runtime.selection(self.steam)
        legacy = self.prefix / "v3/radv-bc250-fsr4-v3.json"
        legacy.parent.mkdir()
        library = legacy.parent / "libvulkan_radeon.so"
        library.write_bytes(b"retained v3 payload")
        original = json.dumps(driver.icd(library)).encode()
        legacy.write_bytes(original)
        self.args.upgrade_v3 = True
        self.apply()
        operation = manage.operations(self.prefix)[-1][1]
        self.assertIsNotNone(operation["driver_record"])
        self.assertNotEqual(legacy.read_bytes(), original)
        self.assertEqual(
            json.loads(legacy.read_text()),
            driver.icd(self.prefix / "current/lib/libvulkan_radeon.so"),
        )
        self.rollback()
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(library.read_bytes(), b"retained v3 payload")
        self.assertEqual(runtime.selection(self.steam), previous)

    def test_doctor_reports_interrupted_component_transaction(self):
        self.apply()
        selected = runtime.selection(self.steam)
        record = self.tool / "transactions/unfinished.json"
        driver.write_json(
            record,
            {
                "state": "prepared",
                "previous": selected["id"],
                "target": selected["id"],
                "driver_before": selected["driver"],
                "driver_after": selected["driver"],
            },
        )
        with mock.patch.object(driver, "check_hardware"):
            report = manage.doctor(self.prefix, self.steam)
        self.assertFalse(report["healthy"])
        self.assertEqual(report["unfinished_runtime_transactions"], [str(record)])
        self.assertEqual(json.loads(record.read_text())["state"], "prepared")

    def test_explicit_system_mode_rejects_v3_migration_before_mutation(self):
        self.args.driver_archive = None
        self.args.driver = "system"
        self.args.upgrade_v3_icd = [self.root / "custom/radv-bc250-fsr4.json"]
        with self.assertRaisesRegex(RuntimeError, "v3 migration require a private driver"):
            self.apply()
        self.assertEqual(list(self.prefix.iterdir()), [])
        self.assertFalse(self.tool.exists())

    def test_doctor_reports_legacy_registration_without_editing_it(self):
        self.apply()
        manifest = self.tool / "compatibilitytool.vdf"
        manifest.write_text(runtime.LEGACY_REGISTRATION)
        with mock.patch.object(driver, "check_hardware"):
            report = manage.doctor(self.prefix, self.steam)
        self.assertFalse(report["healthy"])
        self.assertIn("Windows save paths", report["diagnostic"])
        self.assertEqual(manifest.read_text(), runtime.LEGACY_REGISTRATION)

    def test_unchanged_runtime_update_still_repairs_legacy_registration(self):
        self.apply()
        manifest = self.tool / "compatibilitytool.vdf"
        manifest.write_text(runtime.LEGACY_REGISTRATION)
        self.args.driver_archive = self.args.runtime_archive = None
        with mock.patch.object(runtime, "ROOT", self.project):
            self.assertTrue(self.apply()["changed"])
            self.assertFalse(self.apply()["changed"])
        self.assertTrue(runtime.registration(self.tool)["save_paths_supported"])

    def test_explicit_abi_replacement_rebinds_same_runtime_and_rolls_back(self):
        self.apply()
        previous = manage.runtime.selection(self.steam)
        previous_target = driver.current_target(self.prefix)
        self.args.driver_archive = self.driver_bundle("corrected-abi", self.source)
        self.apply()
        selected = manage.runtime.selection(self.steam)
        self.assertEqual(selected["id"], previous["id"])
        self.assertNotEqual(selected["driver"]["sha256"], previous["driver"]["sha256"])
        self.assertNotEqual(driver.current_target(self.prefix), previous_target)
        self.rollback()
        self.assertEqual(manage.runtime.selection(self.steam), previous)
        self.assertEqual(driver.current_target(self.prefix), previous_target)

    def test_failed_explicit_abi_replacement_restores_both_selections(self):
        self.apply()
        previous = manage.runtime.selection(self.steam)
        previous_target = driver.current_target(self.prefix)
        self.args.driver_archive = self.driver_bundle("corrected-abi", self.source)
        with mock.patch.object(
            manage.runtime, "install", side_effect=RuntimeError("injected failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "previous selections restored"):
                self.apply()
        self.assertEqual(manage.runtime.selection(self.steam), previous)
        self.assertEqual(driver.current_target(self.prefix), previous_target)

    def test_automatic_update_replaces_incompatible_private_driver_and_rolls_back(self):
        self.apply()
        previous = runtime.selection(self.steam)
        old_target = driver.current_target(self.prefix)
        corrected = self.driver_bundle("portable", self.source)
        self.args.driver_archive = None
        original_probe = runtime.probe_driver

        def check(selected, root):
            if selected["sha256"] == previous["driver"]["sha256"]:
                raise RuntimeError("Old distribution ABI")
            return original_probe(selected, root)

        with (
            mock.patch.object(runtime, "probe_driver", side_effect=check),
            mock.patch.object(manage, "driver_archive", return_value=(corrected, None)),
        ):
            self.apply()
        self.assertNotEqual(runtime.selection(self.steam)["driver"], previous["driver"])
        self.rollback()
        self.assertEqual(runtime.selection(self.steam), previous)
        self.assertEqual(driver.current_target(self.prefix), old_target)

    def test_default_driver_download_uses_pinned_archive_without_sidecar_lookup(self):
        self.args.driver_archive = None
        self.policy["driver"].update(
            archive_url="https://example.invalid/portable.tar.gz", archive_sha256="f" * 64
        )
        with mock.patch.object(
            manage.runtime_bundle, "download", return_value=Path("verified")
        ) as download:
            self.assertEqual(
                manage.driver_archive(self.args, self.policy), (Path("verified"), "f" * 64)
            )
        download.assert_called_once_with(
            "https://example.invalid/portable.tar.gz", "f" * 64, self.args.cache / "drivers", True
        )

    def test_automatic_update_preserves_an_existing_private_driver_choice(self):
        self.apply()
        previous = runtime.selection(self.steam)
        self.system = self.root / "also-available-system-driver.so"
        self.system.write_bytes(b"verified system driver")
        self.args.driver_archive = None
        self.apply()
        self.assertEqual(runtime.selection(self.steam), previous)

    def test_corrupt_explicit_archive_cannot_hide_behind_existing_driver(self):
        self.apply()
        previous = manage.runtime.selection(self.steam)
        self.args.driver_archive.write_bytes(b"corrupt replacement")
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.apply()
        self.assertEqual(manage.runtime.selection(self.steam), previous)

    def test_reused_private_driver_remains_active_after_rollback(self):
        existing = self.existing_driver()
        target = driver.current_target(self.prefix)
        self.apply()
        self.rollback()
        self.assertEqual(driver.current_target(self.prefix), target)
        self.assertEqual(json.loads(existing.read_text())["state"], "active")
        self.assertFalse(self.tool.exists())

    def test_first_install_owns_only_its_exact_driver_transaction(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            manage.apply(self.args, self.prefix, self.steam)
        self.assertNotIn("VK_DRIVER_FILES=", output.getvalue())
        operation = manage.operations(self.prefix)[-1][1]
        record = self.prefix / "transactions" / operation["driver_record"]
        self.assertEqual(json.loads(record.read_text())["state"], "active")
        self.rollback()
        self.assertIsNone(driver.current_target(self.prefix))
        self.assertEqual(json.loads(record.read_text())["state"], "rolled-back")

    def test_v3_upgrade_and_rollback_restore_legacy_icd_bytes(self):
        old_library = self.prefix / "v3/lib/libvulkan_radeon.so"
        old_library.parent.mkdir(parents=True)
        old_library.write_bytes(b"old v3 driver")
        legacy = self.prefix / "v3/radv-bc250-fsr4-v3.json"
        original = (json.dumps(driver.icd(old_library), separators=(",", ":")) + "\n").encode()
        legacy.write_bytes(original)
        self.args.upgrade_v3 = True
        self.apply()
        self.assertNotEqual(legacy.read_bytes(), original)
        self.rollback()
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(old_library.read_bytes(), b"old v3 driver")

    def test_runtime_failure_restores_prior_private_driver_and_keeps_evidence(self):
        old = self.existing_driver(self.driver_bundle("old", "b" * 64))
        previous = driver.current_target(self.prefix)
        with mock.patch.object(
            runtime, "install", side_effect=RuntimeError("missing pinned provider")
        ):
            with self.assertRaisesRegex(RuntimeError, "previous selections restored"):
                self.apply()
        path, operation = manage.operations(self.prefix)[-1]
        self.assertEqual(driver.current_target(self.prefix), previous)
        self.assertEqual(json.loads(old.read_text())["state"], "active")
        self.assertEqual(operation["state"], "failed")
        self.assertIn("missing pinned provider", path.with_suffix(".log").read_text())

    def test_failure_after_first_runtime_promotion_retires_payload_and_restores_driver(self):
        original = runtime.install

        def fail_after_promotion(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("interrupted after runtime promotion")

        with mock.patch.object(runtime, "install", side_effect=fail_after_promotion):
            with self.assertRaisesRegex(RuntimeError, "previous selections restored"):
                self.apply()
        operation = manage.operations(self.prefix)[-1][1]
        self.assertIsNone(driver.current_target(self.prefix))
        self.assertFalse(self.tool.exists())
        self.assertTrue(Path(operation["retired"]).is_dir())
        self.assertEqual(operation["state"], "failed")

    def test_interrupted_first_promotion_recovers_before_a_fresh_install(self):
        original_install = runtime.install
        original_write = driver.write_json

        def power_loss(path, value):
            if Path(path).parent == self.prefix / "operations" and value.get("state") == "active":
                raise PowerLoss("power lost after runtime promotion")
            return original_write(path, value)

        with mock.patch.object(driver, "write_json", side_effect=power_loss):
            with self.assertRaises(PowerLoss):
                self.apply()
        pending = manage.operations(self.prefix)[-1][1]
        self.assertTrue(self.tool.exists())

        def fail_after_next_promotion(*args, **kwargs):
            original_install(*args, **kwargs)
            # The original root was retired by recovery. The new root must have
            # its own lock held before rolling its driver back.
            with (self.tool / ".runtime.lock").open("r") as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                with mock.patch.object(driver, "rollback", wraps=driver.rollback) as rollback:
                    operation_path, operation = manage.operations(self.prefix)[-1]
                    with self.assertRaisesRegex(RuntimeError, "running"):
                        manage.restore_operation(operation_path, operation, runtime_locked=False)
                    rollback.assert_not_called()
            raise RuntimeError("second promotion failed")

        with mock.patch.object(runtime, "install", side_effect=fail_after_next_promotion):
            with self.assertRaisesRegex(RuntimeError, "previous selections restored"):
                self.apply()
        self.assertTrue(Path(pending["retired"]).is_dir())
        self.assertFalse(self.tool.exists())
        self.assertIsNone(driver.current_target(self.prefix))

    def test_failure_after_runtime_upgrade_restores_exact_prior_binding(self):
        self.existing_driver()
        self.apply()
        before = runtime.selection(self.steam)
        self.args.runtime_archive = self.runtime_bundle("4.0.0-rc3")
        original = runtime.install

        def fail_after_promotion(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("failed after runtime update")

        with mock.patch.object(runtime, "install", side_effect=fail_after_promotion):
            with self.assertRaisesRegex(RuntimeError, "previous selections restored"):
                self.apply()
        self.assertEqual(runtime.selection(self.steam), before)
        self.assertTrue((self.tool / "versions/bc250-fsr4-runtime-4.0.0-rc3").is_dir())

    def test_interruption_after_driver_commit_uses_reserved_record_for_recovery(self):
        operation = self.interrupt_after_driver_commit()
        record = self.prefix / "transactions" / operation["driver_record"]
        self.assertEqual(json.loads(record.read_text())["state"], "active")
        result = self.rollback()
        self.assertTrue(result["recovered"])
        self.assertIsNone(driver.current_target(self.prefix))
        self.assertEqual(json.loads(record.read_text())["state"], "rolled-back")

    def test_recovery_preserves_a_later_external_driver_install(self):
        self.interrupt_after_driver_commit()
        external = self.existing_driver(self.driver_bundle("external", self.source))
        target = driver.current_target(self.prefix)
        with self.assertRaisesRegex(RuntimeError, "later driver installation"):
            self.rollback()
        self.assertEqual(driver.current_target(self.prefix), target)
        self.assertEqual(json.loads(external.read_text())["state"], "active")

    def test_runtime_binding_conflict_is_checked_before_driver_rollback(self):
        self.apply()
        target = driver.current_target(self.prefix)
        binding = json.loads((self.tool / "driver.json").read_text())
        binding["independent_change"] = True
        (self.tool / "driver.json").write_text(json.dumps(binding))
        with mock.patch.object(driver, "rollback", wraps=driver.rollback) as rollback:
            with self.assertRaisesRegex(RuntimeError, "Runtime changed outside"):
                self.rollback()
            rollback.assert_not_called()
        self.assertEqual(driver.current_target(self.prefix), target)
        self.assertTrue(self.tool.exists())

    def test_retirement_path_conflict_is_checked_before_driver_rollback(self):
        self.apply()
        operation = manage.operations(self.prefix)[-1][1]
        retired = Path(operation["retired"])
        retired.mkdir(parents=True)
        (retired / "independent-file").write_text("preserve")
        target = driver.current_target(self.prefix)
        with mock.patch.object(driver, "rollback", wraps=driver.rollback) as rollback:
            with self.assertRaisesRegex(RuntimeError, "retirement path changed"):
                self.rollback()
            rollback.assert_not_called()
        self.assertEqual(driver.current_target(self.prefix), target)
        self.assertTrue(self.tool.exists())
        self.assertEqual((retired / "independent-file").read_text(), "preserve")

    def test_running_runtime_blocks_driver_mutation(self):
        self.apply()
        with (self.tool / ".runtime.lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            with mock.patch.object(driver, "install", wraps=driver.install) as install:
                with self.assertRaisesRegex(RuntimeError, "running"):
                    self.apply()
                install.assert_not_called()

    def test_status_without_install_is_read_only(self):
        result = manage.status(self.prefix, self.steam)
        self.assertFalse(result["installed"])
        self.assertFalse((self.prefix / "operations").exists())
        self.assertFalse((self.steam / "compatibilitytools.d").exists())


if __name__ == "__main__":
    unittest.main()
