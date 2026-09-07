# SPDX-License-Identifier: MIT
"""Prevent locally working but incomplete binary source distributions."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("package_checkout", SCRIPTS / "package.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class CleanPackagingTests(unittest.TestCase):
    def test_untracked_helper_or_uncommitted_script_prevents_packaging(self):
        for tracked in (False, True):
            with self.subTest(tracked_edit=tracked), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "source"
                root.mkdir()
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                for key, value in (
                    ("user.name", "Fixture"),
                    ("user.email", "fixture@example.invalid"),
                ):
                    subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
                (root / "README.md").write_text("Fixture source\n")
                subprocess.run(["git", "-C", str(root), "add", "."], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", "Fixture"], check=True)
                if tracked:
                    (root / "README.md").write_text("Changed source\n")
                else:
                    (root / "scripts").mkdir()
                    (root / "scripts/new_helper.py").write_text("def helper(): pass\n")
                output = Path(temporary) / "output"
                argv = [
                    "package.py",
                    "--work",
                    str(Path(temporary) / "work"),
                    "--output",
                    str(output),
                ]
                with (
                    mock.patch.object(package, "ROOT", root),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(
                        package.build,
                        "verify_completed_build",
                        return_value=({"version": "4.0.0-test"}, {}),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "clean source checkout"):
                        package.main()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
