# SPDX-License-Identifier: MIT
import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location(
    "game_setup", Path(__file__).resolve().parents[1] / "scripts/game-setup.py"
)
game_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_setup)


class GameSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "game with spaces"
        self.game.mkdir()
        (self.game / "game.exe").write_bytes(b"fixture executable")
        self.sdk = self.game / "fsr.dll"
        self.sdk.write_bytes(b"original SDK")
        self.ini = self.game / "OptiScaler.ini"
        self.original = b"; preserve exact original\n[User]\nSetting=keep-me\n"
        self.ini.write_bytes(self.original)
        self.state = self.root / "state"
        self.state.mkdir()
        self.runtime = self.state / "payloads/runtime-v1"
        self.make_runtime(b"known proxy")
        self.policy = {
            "proton": "fixture",
            "fsr4": "4.1.1",
            "optiscaler": {"dll_sha256": game_setup.driver.digest(self.runtime / "OptiScaler.dll")},
            "profiles": [
                {
                    "id": "deadzone",
                    "title": "Fixture",
                    "executable": "game.exe",
                    "proxy": "dxgi.dll",
                    "config": {"Inputs.EnableFfxInputs": "false"},
                    "native_fsr4": {
                        "path": "fsr.dll",
                        "md5": hashlib.md5(self.sdk.read_bytes()).hexdigest(),
                    },
                }
            ],
        }
        self.args = argparse.Namespace(profile="deadzone", game=self.game, watermark=False)
        self.stopped = patch.object(game_setup, "require_stopped")
        self.fetch = patch.object(game_setup, "payload", side_effect=lambda *_: self.runtime)
        self.stopped.start()
        self.fetch.start()
        self.addCleanup(self.stopped.stop)
        self.addCleanup(self.fetch.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_runtime(self, proxy):
        (self.runtime / "OptiScaler").mkdir(parents=True)
        (self.runtime / "OptiScaler.dll").write_bytes(proxy)
        (self.runtime / "OptiScaler.ini").write_bytes(b"")
        (self.runtime / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll").write_bytes(
            b"provider " + proxy
        )
        game_setup.driver.write_json(
            self.runtime / "payload.json",
            {
                "files": {
                    str(p.relative_to(self.runtime)): game_setup.driver.digest(p)
                    for p in self.runtime.rglob("*")
                    if p.is_file()
                }
            },
        )

    def test_game_setup_and_exact_rollback(self):
        game_setup.install(self.args, self.state, self.policy)
        self.assertIn("Fsr4ForceModel=2", self.ini.read_text())
        self.assertIn("FsrNonLinearSRGB=auto", self.ini.read_text())
        self.assertIn("Setting=keep-me", self.ini.read_text())
        self.assertTrue((self.game / "dxgi.dll").is_symlink())
        record = next((self.state / "transactions").glob("*.json"))
        game_setup.rollback(record)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertFalse((self.game / "dxgi.dll").exists())
        self.assertEqual(self.sdk.read_bytes(), b"original SDK")

    def test_changed_sdk_fails_before_writes(self):
        self.sdk.write_bytes(b"game updated")
        with self.assertRaisesRegex(RuntimeError, "SDK changed"):
            game_setup.install(self.args, self.state, self.policy)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertFalse((self.state / "transactions").exists())

    def test_unknown_profile_has_actionable_error_before_payload(self):
        self.args.profile = "typo"
        with self.assertRaisesRegex(RuntimeError, "Available profiles: deadzone"):
            game_setup.install(self.args, self.state, self.policy)
        self.assertFalse((self.state / "transactions").exists())

    def test_rollback_cli_infers_custom_state_without_current_policy(self):
        game_setup.install(self.args, self.state, self.policy)
        record = next((self.state / "transactions").glob("*.json"))
        with (
            patch.object(game_setup, "ROOT", self.root / "missing-source"),
            patch.object(game_setup.os, "geteuid", return_value=1000),
            patch.object(sys, "argv", ["game-setup.py", "rollback", str(record)]),
        ):
            game_setup.main()
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertTrue((self.state / ".lock").exists())
        self.assertFalse((self.game / "dxgi.dll").exists())

    def test_unrelated_proxy_is_preserved(self):
        (self.game / "dxgi.dll").write_bytes(b"other mod")
        with self.assertRaisesRegex(RuntimeError, "unrelated proxy"):
            game_setup.install(self.args, self.state, self.policy)
        self.assertEqual((self.game / "dxgi.dll").read_bytes(), b"other mod")
        self.assertEqual(self.ini.read_bytes(), self.original)

    def test_later_user_edit_blocks_rollback(self):
        game_setup.install(self.args, self.state, self.policy)
        self.ini.write_text("user changed settings")
        record = next((self.state / "transactions").glob("*.json"))
        with self.assertRaisesRegex(RuntimeError, "changed after setup"):
            game_setup.rollback(record)
        self.assertEqual(self.ini.read_text(), "user changed settings")

    def test_managed_runtime_upgrade_and_two_exact_rollbacks(self):
        game_setup.install(self.args, self.state, self.policy)
        old_runtime = self.runtime
        old_ini = self.ini.read_bytes()
        first = next((self.state / "transactions").glob("*.json"))
        self.runtime = self.state / "payloads/runtime-v2"
        self.make_runtime(b"new pinned proxy")
        self.policy["optiscaler"]["dll_sha256"] = game_setup.driver.digest(
            self.runtime / "OptiScaler.dll"
        )
        game_setup.install(self.args, self.state, self.policy)
        second = sorted((self.state / "transactions").glob("*.json"))[-1]
        self.assertEqual((self.game / "dxgi.dll").resolve(), self.runtime / "OptiScaler.dll")
        with self.assertRaisesRegex(RuntimeError, "newer game transaction"):
            game_setup.rollback(first)
        game_setup.rollback(second)
        self.assertEqual((self.game / "dxgi.dll").resolve(), old_runtime / "OptiScaler.dll")
        self.assertEqual(self.ini.read_bytes(), old_ini)
        game_setup.rollback(first)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertFalse((self.game / "dxgi.dll").exists())

    def test_repeated_identical_install_must_rollback_in_order(self):
        game_setup.install(self.args, self.state, self.policy)
        first = next((self.state / "transactions").glob("*.json"))
        game_setup.install(self.args, self.state, self.policy)
        with self.assertRaisesRegex(RuntimeError, "newer game transaction"):
            game_setup.rollback(first)
        self.assertTrue((self.game / "dxgi.dll").is_symlink())

    def test_modified_retained_runtime_blocks_upgrade(self):
        game_setup.install(self.args, self.state, self.policy)
        before = self.ini.read_bytes()
        (self.runtime / "OptiScaler/plugins").mkdir()
        (self.runtime / "OptiScaler/plugins/extra.asi").write_bytes(b"untracked plugin")
        with self.assertRaisesRegex(RuntimeError, "runtime payload was modified"):
            game_setup.install(self.args, self.state, self.policy)
        self.assertEqual(self.ini.read_bytes(), before)

    def test_interrupted_install_blocks_new_work_and_recovers(self):
        game_setup.install(self.args, self.state, self.policy)
        record = next((self.state / "transactions").glob("*.json"))
        transaction = json.loads(record.read_text())
        transaction["state"] = "prepared"
        # Simulate termination after the first link, before the remaining writes.
        for change in transaction["changes"][1:]:
            game_setup.restore(Path(change["path"]), change["before"])
        game_setup.driver.write_json(record, transaction)
        with self.assertRaisesRegex(RuntimeError, "needs recovery"):
            game_setup.install(self.args, self.state, self.policy)
        game_setup.recover(record)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertFalse((self.game / "dxgi.dll").exists())
        self.assertEqual(json.loads(record.read_text())["state"], "recovered")

    def test_rollback_failure_is_recoverable(self):
        game_setup.install(self.args, self.state, self.policy)
        record = next((self.state / "transactions").glob("*.json"))
        real_restore = game_setup.restore
        calls = 0

        def interrupted_restore(path, item):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated disk error")
            return real_restore(path, item)

        with patch.object(game_setup, "restore", side_effect=interrupted_restore):
            with self.assertRaisesRegex(OSError, "disk error"):
                game_setup.rollback(record)
        self.assertEqual(json.loads(record.read_text())["state"], "rolling-back")
        game_setup.recover(record)
        self.assertEqual(self.ini.read_bytes(), self.original)
        self.assertFalse((self.game / "dxgi.dll").exists())

    def test_recovery_preserves_independent_edits_without_partial_restore(self):
        game_setup.install(self.args, self.state, self.policy)
        record = next((self.state / "transactions").glob("*.json"))
        transaction = json.loads(record.read_text())
        transaction["state"] = "prepared"
        game_setup.driver.write_json(record, transaction)
        self.ini.write_text("user edit after interruption")
        with self.assertRaisesRegex(RuntimeError, "changed independently"):
            game_setup.recover(record)
        self.assertEqual(self.ini.read_text(), "user edit after interruption")
        self.assertTrue((self.game / "dxgi.dll").is_symlink())


if __name__ == "__main__":
    unittest.main()
