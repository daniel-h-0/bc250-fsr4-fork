# SPDX-License-Identifier: MIT
"""Keep undoing old journals without retaining their retired installer."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy/game-setup"))
spec = importlib.util.spec_from_file_location(
    "legacy_recovery", ROOT / "legacy/game-setup/recover.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = self.root / "state/transactions/100.json"
        self.record.parent.mkdir(parents=True)
        self.game = self.root / "game.ini"
        self.game.write_bytes(b"installed config")
        self.steam = self.root / "localconfig.vdf"
        self.before = b'"Root" { "LaunchOptions" "old" "Other" "keep" }\n'
        self.after = self.before.replace(b'"old"', b'"managed"')
        self.steam.write_bytes(self.after)
        self.transaction = {
            "schema": 1,
            "guided": True,
            "profile": "retired-title",
            "state": "active",
            "changes": [
                {
                    "path": str(self.game),
                    "before": {"type": "file", "bytes_hex": b"original config".hex()},
                    "after": {"type": "file", "bytes_hex": b"installed config".hex()},
                }
            ],
            "steam_changes": [
                {
                    "path": str(self.steam),
                    "before_hex": self.before.hex(),
                    "after_hex": self.after.hex(),
                    "created_objects": [],
                    "fields": [
                        {"keys": ["Root", "LaunchOptions"], "before": "old", "after": "managed"}
                    ],
                }
            ],
        }
        self.save()
        stopped = patch.object(recovery, "require_stopped")
        stopped.start()
        self.addCleanup(stopped.stop)

    def save(self):
        self.record.write_text(json.dumps(self.transaction))

    def test_original_combined_journal_restores_exact_bytes(self):
        recovery.rollback(self.record)
        self.assertEqual(self.game.read_bytes(), b"original config")
        self.assertEqual(self.steam.read_bytes(), self.before)
        self.assertEqual(json.loads(self.record.read_text())["state"], "rolled-back")

    def test_unrelated_steam_edits_survive(self):
        self.steam.write_bytes(self.after.replace(b'"keep"', b'"later edit"'))
        recovery.rollback(self.record)
        self.assertEqual(self.steam.read_bytes(), self.before.replace(b'"keep"', b'"later edit"'))

    def test_changed_owned_field_blocks_all_writes(self):
        self.steam.write_bytes(self.after.replace(b'"managed"', b'"user choice"'))
        with self.assertRaisesRegex(RuntimeError, "managed Steam field changed"):
            recovery.rollback(self.record)
        self.assertEqual(self.game.read_bytes(), b"installed config")
        self.assertEqual(json.loads(self.record.read_text())["state"], "active")

    def test_partial_interrupted_transaction_recovers(self):
        self.transaction["state"] = "prepared"
        self.save()
        self.steam.write_bytes(self.before)
        recovery.recover(self.record)
        self.assertEqual(self.game.read_bytes(), b"original config")
        self.assertEqual(self.steam.read_bytes(), self.before)

    def test_independent_game_edit_prevents_partial_recovery(self):
        self.transaction["state"] = "rolling-back"
        self.save()
        self.game.write_bytes(b"independent edit")
        with self.assertRaisesRegex(RuntimeError, "changed independently"):
            recovery.recover(self.record)
        self.assertEqual(self.steam.read_bytes(), self.after)

    def test_newer_overlapping_transaction_blocks_older_rollback(self):
        (self.record.parent / "200.json").write_text(json.dumps(self.transaction))
        with self.assertRaisesRegex(RuntimeError, "newer game transaction"):
            recovery.rollback(self.record)
        self.assertEqual(self.game.read_bytes(), b"installed config")

    def test_rollback_error_leaves_recoverable_journal(self):
        with patch.object(recovery, "restore", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                recovery.rollback(self.record)
        self.assertEqual(json.loads(self.record.read_text())["state"], "rolling-back")
        recovery.recover(self.record)
        self.assertEqual(self.game.read_bytes(), b"original config")


if __name__ == "__main__":
    unittest.main()
