# SPDX-License-Identifier: MIT
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import driver


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root/'install'
        self.prefix.mkdir()
        self.payload = self.root/'release'
        (self.payload/'lib').mkdir(parents=True)
        self.library = self.payload/'lib/libvulkan_radeon.so'
        self.library.write_bytes(b'test fixture only')
        self.make_archive()

    def tearDown(self):
        self.tmp.cleanup()

    def make_archive(self):
        release = dict(schema=1, version='4.0.0-test', architecture='x86_64',
                       driver_sha256=driver.digest(self.library),
                       files={'lib/libvulkan_radeon.so': driver.digest(self.library)})
        (self.payload/'release.json').write_text(json.dumps(release))
        self.archive = self.root/'fixture.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as t:
            t.add(self.payload, arcname='release')
        self.args = argparse.Namespace(archive=self.archive, sha256=driver.digest(self.archive), upgrade_v3_icd=[])

    def install(self):
        with patch.object(driver, 'probe', return_value={'success': True}):
            driver.install(self.args, self.prefix)

    def test_checksum_failure_preserves_old_current(self):
        (self.prefix/'current').symlink_to('old')
        self.args.sha256 = '0'*64
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            self.install()
        self.assertEqual(driver.current_target(self.prefix), 'old')

    def test_bad_abi_probe_preserves_old_current(self):
        (self.prefix/'current').symlink_to('old')
        with patch.object(driver, 'probe', side_effect=RuntimeError('bad ABI')):
            with self.assertRaisesRegex(RuntimeError, 'bad ABI'):
                driver.install(self.args, self.prefix)
        self.assertEqual(driver.current_target(self.prefix), 'old')
        self.assertFalse((self.prefix/'releases').exists())

    def test_missing_file_rejected(self):
        self.library.unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing files'):
            driver.verify_release(self.payload)

    def test_modified_file_rejected(self):
        self.library.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            driver.verify_release(self.payload)

    def test_archive_links_rejected(self):
        with tarfile.open(self.archive, 'w:gz') as t:
            member = tarfile.TarInfo('release/link')
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
            t.addfile(member)
        with self.assertRaisesRegex(RuntimeError, 'regular files'):
            driver.extract_verified(self.archive, self.root/'extract', driver.digest(self.archive))

    def test_archive_path_traversal_rejected(self):
        with tarfile.open(self.archive, 'w:gz') as t:
            member = tarfile.TarInfo('../escape')
            member.size = 1
            t.addfile(member, io.BytesIO(b'x'))
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
            driver.extract_verified(self.archive, self.root/'extract', driver.digest(self.archive))
        self.assertFalse((self.root.parent/'escape').exists())

    def legacy(self):
        old = self.root/'v3 with spaces'
        old.mkdir()
        (old/'libvulkan_radeon.so').write_bytes(b'old driver retained')
        path = old/'radv-bc250-fsr4-v3.json'
        # Deliberately unusual formatting: rollback must be byte exact.
        before = json.dumps(driver.icd(old/'libvulkan_radeon.so'), separators=(',', ':')).encode()
        path.write_bytes(before)
        self.args.upgrade_v3_icd = [path]
        return path, before

    def test_v3_migration_and_exact_rollback(self):
        path, before = self.legacy()
        self.install()
        target = json.loads(path.read_text())['ICD']['library_path']
        self.assertEqual(target, str(self.prefix/'current/lib/libvulkan_radeon.so'))
        self.assertTrue(Path(target).is_file())
        self.assertTrue(driver.status(self.prefix)['active'])
        driver.rollback(self.prefix)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(driver.current_target(self.prefix), None)
        self.assertEqual((path.parent/'libvulkan_radeon.so').read_bytes(), b'old driver retained')

    def test_rollback_refuses_later_user_edit(self):
        path, _ = self.legacy()
        self.install()
        path.write_text('user edit')
        with self.assertRaisesRegex(RuntimeError, 'modified after'):
            driver.rollback(self.prefix)
        self.assertEqual(path.read_text(), 'user edit')
        self.assertTrue(driver.status(self.prefix)['active'])

    def test_rollback_restores_previous_v4(self):
        self.install()
        first = driver.current_target(self.prefix)
        self.library.write_bytes(b'new fixture driver')
        self.make_archive()
        self.install()
        self.assertNotEqual(driver.current_target(self.prefix), first)
        driver.rollback(self.prefix)
        self.assertEqual(driver.current_target(self.prefix), first)
        self.assertTrue(driver.status(self.prefix)['active'])

    def test_reinstall_immutable_payload_verifies_existing_files(self):
        self.install()
        (self.prefix/'current/lib/libvulkan_radeon.so').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            self.install()

    def test_failure_during_selection_restores_v3(self):
        path, before = self.legacy()
        with patch.object(driver, 'switch', side_effect=OSError('simulated failed rename')):
            with self.assertRaisesRegex(OSError, 'failed rename'):
                self.install()
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(driver.current_target(self.prefix))
        records = list((self.prefix/'transactions').glob('*.json'))
        self.assertEqual(json.loads(records[0].read_text())['state'], 'aborted')


if __name__ == '__main__':
    unittest.main()

class RecoveryAndAbiTests(unittest.TestCase):
    setUp = InstallerTests.setUp
    tearDown = InstallerTests.tearDown
    make_archive = InstallerTests.make_archive
    install = InstallerTests.install
    legacy = InstallerTests.legacy
    def test_undefined_lazy_symbol_is_rejected(self):
        from types import SimpleNamespace
        self.library.write_bytes(b'\x7fELF\x02' + b'\0'*13 + b'\x3e\x00')
        result = SimpleNamespace(returncode=0,stdout='undefined symbol: old_llvm_symbol',stderr='')
        with patch.object(driver,'check_hardware'), patch.object(driver.shutil,'which',return_value='/usr/bin/vulkaninfo'), patch.object(driver.subprocess,'run',return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError,'ABI/dependency'):
                driver.probe(self.library,self.root)
        self.assertEqual(run.call_args[0][0][:2],['ldd','-r'])

    def test_interrupted_install_can_be_recovered(self):
        path,before = self.legacy()
        self.install()
        record = next((self.prefix/'transactions').glob('*.json'))
        journal = json.loads(record.read_text())
        journal['state'] = 'prepared'
        driver.write_json(record,journal)
        with self.assertRaisesRegex(RuntimeError,'recovery'):
            driver.status(self.prefix)
        with self.assertRaisesRegex(RuntimeError,'recovery'):
            self.install()
        driver.recover(self.prefix)
        self.assertIsNone(driver.current_target(self.prefix))
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(json.loads(record.read_text())['state'],'recovered')

    def test_stable_icd_edit_is_not_overwritten(self):
        self.install()
        (self.prefix/'current.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'edited independently'):
            self.install()
        self.assertEqual((self.prefix/'current.json').read_text(),'{}')
