# SPDX-License-Identifier: MIT
import importlib.util
import io
import json
import os
import shlex
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("steamos_build", ROOT / "scripts/build-steamos.py")
steamos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(steamos)


class TargetTests(unittest.TestCase):
    def test_target_environment_replaces_host_build_paths_and_quotes_spaces(self):
        root = Path("/temporary root/sysroot")
        env = steamos.target_environment(
            root,
            {
                "CC": "host-cc",
                "CFLAGS": "-march=native",
                "CPATH": "/host/include",
                "LIBRARY_PATH": "/host/lib",
                "GCC_EXEC_PREFIX": "/host/compiler",
                "PKG_CONFIG_PATH": "/host/pkgconfig",
                "DISPLAY": ":9",
                "PATH": "/build/tools",
            },
        )
        self.assertEqual(shlex.split(env["CC"]), [str(root / "usr/bin/gcc")])
        self.assertIn("--sysroot=" + str(root), shlex.split(env["CFLAGS"]))
        self.assertNotIn("-march=native", env["CFLAGS"])
        self.assertNotIn("CPATH", env)
        self.assertNotIn("LIBRARY_PATH", env)
        self.assertNotIn("GCC_EXEC_PREFIX", env)
        self.assertEqual(env["PKG_CONFIG_PATH"], "")
        self.assertEqual(env["DISPLAY"], ":9")
        self.assertEqual(env["PATH"], "/build/tools")

    @unittest.skipUnless(shutil.which("bsdtar"), "libarchive is required")
    def test_offline_package_hash_and_absolute_usr_alias_relocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            archive = root / "package.tar.gz"
            with tarfile.open(archive, "w:gz") as out:
                regular = tarfile.TarInfo("usr/bin/gcc")
                regular.size = 7
                out.addfile(regular, io.BytesIO(b"fixture"))
                alias = tarfile.TarInfo("usr/bin/target-gcc")
                alias.type = tarfile.SYMTYPE
                alias.linkname = "/usr/bin/gcc"
                out.addfile(alias)
            checksum = steamos.build.digest(archive)
            archive.rename(cache / checksum)
            policy = {"packages": [{"url": "https://example.invalid/package", "sha256": checksum}]}
            destination = root / "sysroot"
            steamos.prepare_sysroot(destination, policy, cache, True)
            alias = destination / "usr/bin/target-gcc"
            self.assertEqual(os.readlink(alias), "gcc")
            self.assertEqual(alias.read_bytes(), b"fixture")
            self.assertEqual(json.loads((destination / "target.json").read_text()), policy)
            (cache / checksum).write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "Cached upstream file changed"):
                steamos.prepare_sysroot(root / "rejected", policy, cache, True)
            self.assertEqual(alias.read_bytes(), b"fixture")

    def test_existing_sysroot_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "existing"
            marker.write_text("keep")
            with self.assertRaises(FileExistsError):
                steamos.prepare_sysroot(root, {"packages": []}, root / "cache", True)
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
