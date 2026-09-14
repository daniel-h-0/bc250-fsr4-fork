# SPDX-License-Identifier: MIT
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import driver


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "install"
        self.prefix.mkdir()
        self.payload = self.root / "release"
        (self.payload / "lib").mkdir(parents=True)
        self.library = self.payload / "lib/libvulkan_radeon.so"
        self.library.write_bytes(b"test fixture only")
        self.make_archive()

    def tearDown(self):
        self.tmp.cleanup()

    def make_archive(self, launcher_tools_api=None):
        release = dict(
            schema=1,
            version="4.0.0-test",
            architecture="x86_64",
            driver_sha256=driver.digest(self.library),
            files={
                str(path.relative_to(self.payload)): driver.digest(path)
                for path in self.payload.rglob("*")
                if path.is_file() and path != self.payload / "release.json"
            },
        )
        if launcher_tools_api is not None:
            release["launcher_tools_api"] = launcher_tools_api
        (self.payload / "release.json").write_text(json.dumps(release))
        self.archive = self.root / "fixture.tar.gz"
        with tarfile.open(self.archive, "w:gz") as t:
            t.add(self.payload, arcname="release")
        self.args = argparse.Namespace(
            archive=self.archive, sha256=driver.digest(self.archive), upgrade_v3_icd=[]
        )

    def install(self):
        with patch.object(driver, "probe", return_value={"success": True}):
            driver.install(self.args, self.prefix)

    def test_checksum_failure_preserves_old_current(self):
        (self.prefix / "current").symlink_to("releases/old")
        self.args.sha256 = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.install()
        self.assertEqual(driver.current_target(self.prefix), "releases/old")

    def test_external_current_is_not_adopted_or_replaced(self):
        current = self.prefix / "current"
        current.symlink_to(self.payload)
        with self.assertRaisesRegex(RuntimeError, "outside its managed releases"):
            self.install()
        self.assertEqual(os.readlink(current), str(self.payload))
        self.assertEqual(self.library.read_bytes(), b"test fixture only")

    def test_replaced_managed_directories_are_preserved(self):
        for name in ("releases", "icds", "transactions"):
            with self.subTest(name=name):
                path = self.prefix / name
                path.symlink_to(self.payload)
                with self.assertRaisesRegex(RuntimeError, "Managed driver directory was replaced"):
                    self.install()
                self.assertEqual(
                    set(self.payload.iterdir()),
                    {self.payload / "lib", self.payload / "release.json"},
                )
                path.unlink()

    def test_symlink_transaction_is_not_used_for_rollback(self):
        self.install()
        record = next((self.prefix / "transactions").glob("*.json"))
        retained = self.root / "retained.json"
        record.rename(retained)
        record.symlink_to(retained)
        before = retained.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "transaction record is not a regular file"):
            driver.rollback(self.prefix)
        self.assertEqual(retained.read_bytes(), before)
        self.assertTrue((self.prefix / "current").is_symlink())

    def test_uppercase_checksum_is_accepted(self):
        self.args.sha256 = self.args.sha256.upper()
        self.install()
        self.assertTrue(driver.status(self.prefix)["active"])

    def test_wrapper_accepts_separator_and_pins_immutable_release(self):
        self.install()
        env = os.environ.copy()
        env.update(
            HOME=str(self.root / "home"),
            XDG_STATE_HOME=str(self.root / "state"),
            BC250_FSR4_PREFIX=str(self.prefix),
            VK_ICD_FILENAMES="stale.json",
            VK_ADD_DRIVER_FILES="extra.json",
        )
        wrapper = Path(__file__).resolve().parents[1] / "run-bc250-fsr4.sh"
        result = subprocess.run(
            [
                str(wrapper),
                "--",
                sys.executable,
                "-c",
                "import json,os; print(json.dumps(dict(os.environ)))",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        launched = json.loads(result.stdout)
        expected = self.prefix / "icds" / (Path(driver.current_target(self.prefix)).name + ".json")
        self.assertEqual(launched["VK_DRIVER_FILES"], str(expected))
        self.assertNotIn("VK_ICD_FILENAMES", launched)
        self.assertNotIn("VK_ADD_DRIVER_FILES", launched)

    def test_empty_adjacent_checksum_is_actionable_and_preserves_selection(self):
        (self.prefix / "current").symlink_to("releases/old")
        Path(str(self.archive) + ".sha256").write_text("")
        self.args.sha256 = None
        with self.assertRaisesRegex(RuntimeError, "SHA256 file is empty"):
            self.install()
        self.assertEqual(driver.current_target(self.prefix), "releases/old")

    def test_bad_abi_probe_preserves_old_current(self):
        (self.prefix / "current").symlink_to("releases/old")
        with patch.object(driver, "probe", side_effect=RuntimeError("bad ABI")):
            with self.assertRaisesRegex(RuntimeError, "bad ABI"):
                driver.install(self.args, self.prefix)
        self.assertEqual(driver.current_target(self.prefix), "releases/old")
        self.assertFalse((self.prefix / "releases").exists())

    def test_missing_file_rejected(self):
        self.library.unlink()
        with self.assertRaisesRegex(RuntimeError, "missing files"):
            driver.verify_release(self.payload)

    def test_modified_file_rejected(self):
        self.library.write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            driver.verify_release(self.payload)

    def test_retained_library_cannot_be_replaced_with_external_symlink(self):
        external = self.root / "external-library"
        external.write_bytes(self.library.read_bytes())
        self.library.unlink()
        self.library.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "must not contain symlinks"):
            driver.verify_release(self.payload)

    def test_untracked_special_file_is_rejected_without_opening_it(self):
        os.mkfifo(self.payload / "extra.pipe")
        with self.assertRaisesRegex(RuntimeError, "only regular files and directories"):
            driver.verify_release(self.payload)

    def test_archive_links_rejected(self):
        with tarfile.open(self.archive, "w:gz") as t:
            member = tarfile.TarInfo("release/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            t.addfile(member)
        with self.assertRaisesRegex(RuntimeError, "regular files"):
            driver.extract_verified(
                self.archive, self.root / "extract", driver.digest(self.archive)
            )

    def test_archive_path_traversal_rejected(self):
        with tarfile.open(self.archive, "w:gz") as t:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            t.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            driver.extract_verified(
                self.archive, self.root / "extract", driver.digest(self.archive)
            )
        self.assertFalse((self.root.parent / "escape").exists())

    def legacy(self):
        old = self.root / "v3 with spaces"
        old.mkdir()
        (old / "libvulkan_radeon.so").write_bytes(b"old driver retained")
        path = old / "radv-bc250-fsr4-v3.json"
        # Deliberately unusual formatting: rollback must be byte exact.
        before = json.dumps(driver.icd(old / "libvulkan_radeon.so"), separators=(",", ":")).encode()
        path.write_bytes(before)
        self.args.upgrade_v3_icd = [path]
        return path, before

    def test_v3_migration_and_exact_rollback(self):
        path, before = self.legacy()
        self.install()
        target = json.loads(path.read_text())["ICD"]["library_path"]
        self.assertEqual(target, str(self.prefix / "current/lib/libvulkan_radeon.so"))
        self.assertTrue(Path(target).is_file())
        self.assertTrue(driver.status(self.prefix)["active"])
        driver.rollback(self.prefix)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(driver.current_target(self.prefix), None)
        self.assertEqual((path.parent / "libvulkan_radeon.so").read_bytes(), b"old driver retained")

    def test_rollback_refuses_later_user_edit(self):
        path, _ = self.legacy()
        self.install()
        path.write_text("user edit")
        with self.assertRaisesRegex(RuntimeError, "modified after"):
            driver.rollback(self.prefix)
        self.assertEqual(path.read_text(), "user edit")
        self.assertTrue(driver.status(self.prefix)["active"])

    def test_rollback_restores_previous_v4(self):
        self.install()
        first = driver.current_target(self.prefix)
        self.library.write_bytes(b"new fixture driver")
        self.make_archive()
        self.install()
        self.assertNotEqual(driver.current_target(self.prefix), first)
        driver.rollback(self.prefix)
        self.assertEqual(driver.current_target(self.prefix), first)
        self.assertTrue(driver.status(self.prefix)["active"])

    def test_reinstall_immutable_payload_verifies_existing_files(self):
        self.install()
        (self.prefix / "current/lib/libvulkan_radeon.so").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            self.install()

    def test_reinstall_rejects_same_bytes_via_external_symlink(self):
        self.install()
        installed = self.prefix / "current/lib/libvulkan_radeon.so"
        external = self.root / "external-installed-driver"
        external.write_bytes(installed.read_bytes())
        installed.unlink()
        installed.symlink_to(external)
        target = driver.current_target(self.prefix)
        with self.assertRaisesRegex(RuntimeError, "must not contain symlinks"):
            self.install()
        self.assertEqual(driver.current_target(self.prefix), target)

    def test_rollback_rejects_modified_previous_release_before_changes(self):
        self.install()
        first = driver.current_target(self.prefix)
        self.library.write_bytes(b"new fixture driver")
        self.make_archive()
        self.install()
        second = driver.current_target(self.prefix)
        (self.prefix / first / "lib/libvulkan_radeon.so").write_bytes(b"modified previous release")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            driver.rollback(self.prefix)
        self.assertEqual(driver.current_target(self.prefix), second)
        self.assertTrue(driver.status(self.prefix)["active"])

    def test_failure_during_selection_restores_v3(self):
        path, before = self.legacy()
        with patch.object(driver, "switch", side_effect=OSError("simulated failed rename")):
            with self.assertRaisesRegex(OSError, "failed rename"):
                self.install()
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(driver.current_target(self.prefix))
        records = list((self.prefix / "transactions").glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["state"], "aborted")

    def test_integrated_launcher_defaults_on_and_can_opt_out_per_launch(self):
        self.args.shared_cache = None
        self.install()
        self.assertTrue(driver.cache_settings(self.prefix)["enabled"])
        launcher = self.prefix / "bc250-fsr4-run"
        env = dict(
            os.environ,
            HOME=str(self.root / "home"),
            XDG_CACHE_HOME=str(self.root / "cache"),
            XDG_STATE_HOME=str(self.root / "state"),
            MESA_SHADER_CACHE_DIR=str(self.root / "original-cache"),
        )
        for flags, shared in [([], True), (["--no-shared-cache"], False)]:
            child = subprocess.run(
                [
                    str(launcher),
                    "run",
                    *flags,
                    "--",
                    sys.executable,
                    "-c",
                    "import json,os;print(json.dumps({k:os.environ[k] for k in ['VK_DRIVER_FILES','MESA_SHADER_CACHE_DIR']}))",
                ],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            effective = json.loads(child.stdout)
            self.assertTrue(Path(effective["VK_DRIVER_FILES"]).is_file())
            self.assertEqual(
                effective["MESA_SHADER_CACHE_DIR"] != env["MESA_SHADER_CACHE_DIR"], shared
            )
        status = subprocess.run(
            [str(launcher), "status"], env=env, text=True, capture_output=True, check=True
        )
        self.assertIn("Shared caching for this launcher: enabled", status.stdout)

    def test_cache_preference_and_launcher_roll_back_with_driver(self):
        self.args.shared_cache = True
        self.install()
        before = (self.prefix / "bc250-fsr4-run").read_bytes()
        self.library.write_bytes(b"updated driver fixture")
        self.make_archive()
        self.args.shared_cache = False
        self.install()
        self.assertFalse(driver.cache_settings(self.prefix)["enabled"])
        driver.rollback(self.prefix)
        self.assertTrue(driver.cache_settings(self.prefix)["enabled"])
        self.assertEqual((self.prefix / "bc250-fsr4-run").read_bytes(), before)
        driver.rollback(self.prefix)
        self.assertFalse((self.prefix / "bc250-fsr4-run").exists())
        self.assertFalse((self.prefix / "cache-settings.json").exists())

    def test_launcher_edit_is_preserved_before_rollback_changes_selection(self):
        self.install()
        target = driver.current_target(self.prefix)
        (self.prefix / "bc250-fsr4-run").write_text("user change")
        with self.assertRaisesRegex(RuntimeError, "changed independently"):
            driver.rollback(self.prefix)
        self.assertEqual(driver.current_target(self.prefix), target)
        self.assertEqual((self.prefix / "bc250-fsr4-run").read_text(), "user change")

    def test_optional_cache_metadata_cannot_block_the_driver_launch(self):
        self.args.shared_cache = True
        self.install()
        launcher = self.prefix / "bc250-fsr4-run"
        env = dict(
            os.environ,
            HOME=str(self.root / "home"),
            XDG_STATE_HOME=str(self.root / "state"),
            XDG_CACHE_HOME=str(self.root / "cache"),
            MESA_SHADER_CACHE_DIR=str(self.root / "original"),
        )
        settings = self.prefix / "cache-settings.json"
        for text in ("[]", "null", "{", '{"schema":1,"enabled":"yes"}'):
            with self.subTest(settings=text):
                settings.write_text(text)
                child = subprocess.run(
                    [
                        str(launcher),
                        "run",
                        "--",
                        sys.executable,
                        "-c",
                        "import json,os;print(json.dumps(dict(os.environ)))",
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                launched = json.loads(child.stdout)
                self.assertEqual(launched["MESA_SHADER_CACHE_DIR"], env["MESA_SHADER_CACHE_DIR"])
                self.assertTrue(Path(launched["VK_DRIVER_FILES"]).is_file())
                self.assertIn("keeping the selected driver", child.stderr)
                self.assertFalse(driver.status(self.prefix)["shared_cache"]["available"])
        settings.write_text('{"schema":1,"enabled":true}')
        self.assertIsNone(driver.cache_settings(self.prefix)["directory"])
        settings.unlink()
        settings.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            driver.cache_settings(self.prefix)

    def tool_archive(self, api=1):
        scripts = self.payload / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in driver.LAUNCHER_TOOLS:
            data = Path(driver.__file__).with_name(name).read_bytes()
            (scripts / name).write_bytes(data + b"\n# verified update fixture\n")
        (self.payload / "LICENSE.new-code").write_text("License from the verified fixture bundle\n")
        self.make_archive(launcher_tools_api=api)
        self.args.shared_cache = None

    def test_installed_updater_adopts_verified_bundle_tools_and_rolls_them_back(self):
        self.args.shared_cache = True
        self.install()
        launcher = self.prefix / "bc250-fsr4-run"
        before = launcher.read_bytes()
        first_tools = next((self.prefix / "launcher-tools").iterdir())
        self.tool_archive()
        with patch.object(driver, "__file__", str(first_tools / "driver.py")):
            self.install()
        self.assertNotEqual(launcher.read_bytes(), before)
        selected = next(p for p in (self.prefix / "launcher-tools").iterdir() if p != first_tools)
        for name in driver.LAUNCHER_TOOLS:
            self.assertEqual(
                (selected / name).read_bytes(), (self.payload / "scripts" / name).read_bytes()
            )
        self.assertEqual(
            (selected / "LICENSE.new-code").read_bytes(),
            (self.payload / "LICENSE.new-code").read_bytes(),
        )
        self.assertTrue(driver.cache_settings(self.prefix)["enabled"])
        driver.rollback(self.prefix)
        self.assertEqual(launcher.read_bytes(), before)

    def test_fresh_source_installer_retains_its_tools_for_older_archives(self):
        self.tool_archive()
        self.install()
        selected = next((self.prefix / "launcher-tools").iterdir())
        self.assertEqual((selected / "driver.py").read_bytes(), Path(driver.__file__).read_bytes())

    def test_rollback_and_recovery_check_retained_launcher_before_changing_selection(self):
        spaced = self.root / "installed driver with spaces"
        self.prefix.rename(spaced)
        self.prefix = spaced
        self.install()
        previous_tools = next((self.prefix / "launcher-tools").iterdir())
        old_script = previous_tools / "driver.py"
        original = old_script.read_bytes()
        self.tool_archive()
        with patch.object(driver, "__file__", str(old_script)):
            self.install()
        target = driver.current_target(self.prefix)
        launcher = self.prefix / "bc250-fsr4-run"
        selected_launcher = launcher.read_bytes()
        record = sorted((self.prefix / "transactions").glob("*.json"))[-1]
        journal = json.loads(record.read_text())
        for operation in (driver.rollback, driver.recover):
            for damage in ("missing", "modified"):
                with self.subTest(operation=operation.__name__, damage=damage):
                    journal["state"] = "active" if operation == driver.rollback else "prepared"
                    driver.write_json(record, journal)
                    if damage == "missing":
                        old_script.unlink()
                    else:
                        old_script.write_bytes(b"independent edit")
                    with self.assertRaisesRegex(
                        RuntimeError, "Restore the retained launcher tools"
                    ):
                        operation(self.prefix)
                    self.assertEqual(driver.current_target(self.prefix), target)
                    self.assertEqual(launcher.read_bytes(), selected_launcher)
                    self.assertEqual(json.loads(record.read_text())["state"], journal["state"])
                    old_script.write_bytes(original)
        journal["state"] = "active"
        driver.write_json(record, journal)
        driver.rollback(self.prefix)
        child = subprocess.run(
            [str(launcher), "status", "--json"], text=True, capture_output=True, check=True
        )
        self.assertTrue(json.loads(child.stdout)["active"])

    def test_installed_update_refuses_unknown_or_incomplete_tool_contract(self):
        self.install()
        target = driver.current_target(self.prefix)
        first_tools = next((self.prefix / "launcher-tools").iterdir())
        for api, missing in ((2, False), (1, True)):
            with self.subTest(api=api, missing=missing):
                self.tool_archive(api)
                if missing:
                    (self.payload / "scripts/shared-cache.sh").unlink()
                    self.make_archive(launcher_tools_api=api)
                with patch.object(driver, "__file__", str(first_tools / "driver.py")):
                    with self.assertRaisesRegex(RuntimeError, "new download|missing its declared"):
                        self.install()
                self.assertEqual(driver.current_target(self.prefix), target)

    def test_status_does_not_create_an_installation(self):
        absent = self.root / "absent"
        child = subprocess.run(
            [sys.executable, str(Path(driver.__file__)), "--prefix", str(absent), "status"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(child.returncode, 1)
        self.assertFalse(json.loads(child.stdout)["active"])
        self.assertFalse(absent.exists())


class RecoveryAndAbiTests(unittest.TestCase):
    setUp = InstallerTests.setUp
    tearDown = InstallerTests.tearDown
    make_archive = InstallerTests.make_archive
    install = InstallerTests.install
    legacy = InstallerTests.legacy

    def test_undefined_lazy_symbol_is_rejected(self):
        if not shutil.which("cc"):
            self.skipTest("A C compiler is needed for the broken-library fixture")
        source = self.root / "broken.c"
        source.write_text(
            "extern int missing_symbol(void); int probe(void) { return missing_symbol(); }\n"
        )
        subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(self.library)], check=True)
        with patch.object(driver, "check_hardware"):
            with self.assertRaisesRegex(RuntimeError, "ABI/dependency"):
                driver.probe(self.library, self.root)
        self.assertIn("missing_symbol", (self.root / "probe.log").read_text())

    def test_interrupted_install_can_be_recovered(self):
        path, before = self.legacy()
        self.install()
        record = next((self.prefix / "transactions").glob("*.json"))
        journal = json.loads(record.read_text())
        journal["state"] = "prepared"
        driver.write_json(record, journal)
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            driver.status(self.prefix)
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            self.install()
        driver.recover(self.prefix)
        self.assertIsNone(driver.current_target(self.prefix))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(record.read_text())["state"], "recovered")

    def test_stable_icd_edit_is_not_overwritten(self):
        self.install()
        (self.prefix / "current.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "edited independently"):
            self.install()
        self.assertEqual((self.prefix / "current.json").read_text(), "{}")

    def test_interrupted_first_rollback_recovers_using_retained_tools(self):
        self.install()
        tools = next((self.prefix / "launcher-tools").iterdir())
        write_json = driver.write_json

        def interrupt_commit(path, value):
            if value.get("state") == "rolled-back":
                raise OSError("simulated interruption after launcher removal")
            write_json(path, value)

        with patch.object(driver, "write_json", side_effect=interrupt_commit):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                driver.rollback(self.prefix)
        self.assertFalse((self.prefix / "bc250-fsr4-run").exists())
        self.assertEqual(len(driver.pending(self.prefix)), 1)
        child = subprocess.run(
            [sys.executable, str(tools / "driver.py"), "--prefix", str(self.prefix), "recover"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("restored to its previous selection", child.stdout)
        self.assertFalse(driver.pending(self.prefix))
        self.assertIsNone(driver.current_target(self.prefix))


if __name__ == "__main__":
    unittest.main()
