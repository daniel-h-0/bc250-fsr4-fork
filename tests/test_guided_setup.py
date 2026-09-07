# SPDX-License-Identifier: MIT
import argparse
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location(
    "guided_setup", Path(__file__).resolve().parents[1] / "scripts/setup-game.py"
)
guided = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guided)


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.steam = self.root / "Steam"
        self.local = self.steam / "userdata/42/config/localconfig.vdf"
        self.config = self.steam / "config/config.vdf"
        for path in (self.local, self.config):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_text(
            '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "123" { "LaunchOptions" "mangohud %command% -keep" } } } } } }\n'
        )
        self.config.write_text(
            '"InstallConfigStore" { "Software" { "Valve" { "Steam" { "Other" "keep" } } } }\n'
        )
        self.original = {path: path.read_bytes() for path in (self.local, self.config)}
        self.game = self.root / "game"
        self.game.mkdir()
        (self.game / "game.exe").write_bytes(b"fixture executable")
        self.state = self.root / "state"
        self.state.mkdir()
        self.runtime = self.state / "payloads/fixture"
        (self.runtime / "OptiScaler").mkdir(parents=True)
        (self.runtime / "OptiScaler.dll").write_bytes(b"proxy")
        (self.runtime / "OptiScaler.ini").write_text("")
        guided.driver.write_json(
            self.runtime / "payload.json",
            {
                "files": {
                    str(path.relative_to(self.runtime)): guided.driver.digest(path)
                    for path in self.runtime.rglob("*")
                    if path.is_file()
                }
            },
        )
        self.policy = {
            "fsr4": "4.1.1",
            "proton": "fixture-proton",
            "optiscaler": {"dll_sha256": guided.driver.digest(self.runtime / "OptiScaler.dll")},
            "profiles": [
                {
                    "id": "fixture",
                    "title": "Fixture",
                    "appid": "123",
                    "executable": "game.exe",
                    "proxy": "dxgi.dll",
                    "config": {},
                    "route": "dlss",
                }
            ],
        }
        self.args = argparse.Namespace(
            driver="auto", driver_prefix=self.root / "private", dry_run=False, yes=True
        )
        self.discovered = {"profile": "fixture", "game": self.game}
        self.account = {"id": "42", "label": "Fixture"}
        for target, name, kwargs in (
            (
                guided,
                "select_driver",
                {
                    "return_value": {
                        "mode": "system",
                        "library": self.root / "driver.so",
                        "environment": {},
                    }
                },
            ),
            (guided, "require_game_stopped", {}),
            (guided.game_setup, "require_stopped", {}),
            (guided.game_setup, "payload", {"return_value": self.runtime}),
            (guided.proton_runtime, "ensure_proton", {}),
            (guided.driver, "probe", {}),
            (guided.shutil, "which", {"return_value": "/fixture/bsdtar"}),
        ):
            mock = patch.object(target, name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def apply(self):
        return guided.execute(
            self.args, self.discovered, self.account, self.steam, self.policy, self.state
        )

    def test_install_and_rollback_restore_game_and_steam_exactly(self):
        record = self.apply()
        self.assertTrue((self.game / "dxgi.dll").is_symlink())
        self.assertIn("PROTON_FSR4_UPGRADE=4.1.1", self.local.read_text())
        self.assertIn("mangohud %command% -keep", self.local.read_text())
        self.assertIn("fixture-proton", self.config.read_text())
        guided.game_setup.rollback(record)
        self.assertFalse((self.game / "dxgi.dll").exists())
        for path, original in self.original.items():
            self.assertEqual(path.read_bytes(), original)

    def test_dry_run_has_no_downloads_or_changes(self):
        self.args.dry_run = True
        before = sorted(str(path) for path in self.root.rglob("*"))
        self.assertIsNone(self.apply())
        self.assertEqual(before, sorted(str(path) for path in self.root.rglob("*")))
        guided.game_setup.payload.assert_not_called()
        guided.proton_runtime.ensure_proton.assert_not_called()
        guided.driver.probe.assert_not_called()
        for path, original in self.original.items():
            self.assertEqual(path.read_bytes(), original)

    def test_rollback_preserves_unrelated_steam_edits(self):
        record = self.apply()
        self.config.write_text(
            self.config.read_text().replace('"Other" "keep"', '"Other" "changed-later"')
        )
        guided.game_setup.rollback(record)
        self.assertIn("changed-later", self.config.read_text())
        self.assertNotIn("fixture-proton", self.config.read_text())
        self.assertEqual(self.local.read_bytes(), self.original[self.local])

    def test_changed_launch_options_block_all_rollback_writes(self):
        record = self.apply()
        self.local.write_text(self.local.read_text().replace("-keep", "-edited"))
        before = self.config.read_bytes()
        with self.assertRaises(RuntimeError):
            guided.game_setup.rollback(record)
        self.assertTrue((self.game / "dxgi.dll").is_symlink())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(json.loads(record.read_text())["state"], "active")

    def test_failure_after_first_steam_write_restores_whole_transaction(self):
        original_atomic = guided.driver.atomic
        failed = False

        def fail_one_write(path, content):
            nonlocal failed
            if Path(path) == self.config and b"fixture-proton" in content and not failed:
                failed = True
                raise OSError("simulated disk failure")
            original_atomic(path, content)

        with patch.object(guided.driver, "atomic", side_effect=fail_one_write):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.apply()
        self.assertFalse((self.game / "dxgi.dll").exists())
        for path, original in self.original.items():
            self.assertEqual(path.read_bytes(), original)
        record = next((self.state / "transactions").glob("*.json"))
        self.assertEqual(json.loads(record.read_text())["state"], "aborted")

    def test_interrupted_combined_transaction_can_recover(self):
        record = self.apply()
        transaction = json.loads(record.read_text())
        transaction["state"] = "prepared"
        guided.driver.write_json(record, transaction)
        # Crash happened after game and account writes, before the global mapping.
        self.config.write_bytes(self.original[self.config])
        guided.game_setup.recover(record)
        self.assertFalse((self.game / "dxgi.dll").exists())
        for path, original in self.original.items():
            self.assertEqual(path.read_bytes(), original)

    def test_ambiguous_unattended_choice_fails_without_guessing(self):
        with self.assertRaisesRegex(RuntimeError, "select one explicitly"):
            guided.choose(["a", "b"], "game", str, interactive=False)

    def test_missing_archive_tool_fails_before_download_or_target_changes(self):
        with patch.object(guided.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install libarchive"):
                self.apply()
        guided.game_setup.payload.assert_not_called()
        guided.proton_runtime.ensure_proton.assert_not_called()
        for path, original in self.original.items():
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
