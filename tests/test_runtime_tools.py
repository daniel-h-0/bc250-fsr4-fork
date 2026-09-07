# SPDX-License-Identifier: MIT
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = load("prove-game")
system = load("system-control")


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        (self.proc / "123").mkdir(parents=True)

    def mapped_library(self, inode=None):
        namespace = self.root / "namespace"
        library = namespace / "bc250-container-only/path with spaces/libvulkan_radeon.so"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"namespace-only driver fixture")
        (self.proc / "123/root").symlink_to(namespace)
        number = library.stat().st_ino if inode is None else inode
        mapped = "/" + str(library.relative_to(namespace))
        # Device mismatch is intentional: Btrfs may report a different maps device.
        row = f"1000-2000 r-xp 00000000 00:00 {number} {mapped}\n"
        (self.proc / "123/maps").write_text(row + row)
        return library

    def test_namespace_only_library_is_hashed_and_duplicate_mappings_deduplicate(self):
        library = self.mapped_library()
        result = proof.mapped(123, "libvulkan_radeon.so", self.proc)
        self.assertEqual(result["sha256"], hashlib.sha256(library.read_bytes()).hexdigest())
        self.assertIsNone(result["host_path"])
        self.assertTrue(result["mapping_verified"])
        self.assertFalse(result["namespace_samefile"])

    def test_replaced_namespace_inode_is_rejected(self):
        self.mapped_library(inode=0)
        with self.assertRaisesRegex(RuntimeError, "namespace inode"):
            proof.mapped(123, "libvulkan_radeon.so", self.proc)

    def test_process_start_handles_spaces_and_parentheses_in_comm(self):
        tail = ["S"] + ["0"] * 18 + ["1234"]
        (self.proc / "123/stat").write_text("123 (Game thread (renderer)) " + " ".join(tail))
        (self.proc / "stat").write_text("cpu 0 0 0 0\nbtime 1700000000\n")
        result = proof.process_identity(123, self.proc)
        self.assertEqual(result["start_ticks"], 1234)
        self.assertEqual(result["started_epoch"], 1700000000 + 1234 / os.sysconf("SC_CLK_TCK"))

    def test_stale_initialization_log_is_rejected(self):
        log = self.root / "previous-game.log"
        log.write_text("Successfully initialized FSR Upscaling provider using version '4.1.1'\n")
        os.utime(log, (100, 100))
        with self.assertRaisesRegex(RuntimeError, "predates this game process"):
            proof.initialization_log(log, {"started_epoch": 110})

    def test_log_snapshot_hash_and_scope_are_reported(self):
        log = self.root / "game.log"
        log.write_text("Successfully initialized FSR Upscaling provider using version '4.1.1'\n")
        os.utime(log, (110, 110))
        result = proof.initialization_log(log, {"started_epoch": 100})
        self.assertEqual(result["engine_log_sha256"], hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertEqual(result["engine_log_mtime_epoch"], 110)
        self.assertIn("may retain earlier launches", result["initialization_scope"])


class SystemStatusTests(unittest.TestCase):
    def test_inactive_status_keeps_stdout_valid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "libvulkan_radeon.so"
            library.write_bytes(b"distribution replacement")
            metadata = root / "system.json"
            metadata.write_text(json.dumps({"library": str(library), "driver_sha256": "0" * 64}))
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(system, "METADATA_PATH", metadata),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = system.main(["status", "--json"])
            self.assertEqual(result, 1)
            self.assertFalse(json.loads(stdout.getvalue())["active"])
            self.assertIn("Rebuild/revalidate", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
