# SPDX-License-Identifier: MIT
"""Generate package recipes safely without invoking pacman or requiring a GPU."""

import argparse
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import driver

spec = importlib.util.spec_from_file_location(
    "system_package", Path(driver.__file__).with_name("system-package.py")
)
system_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system_package)


class SystemPackageTests(unittest.TestCase):
    def test_makepkg_configuration_cannot_redirect_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts/system-control.py").write_text("fixture control")
            (root / "LICENSE.new-code").write_text("fixture license")
            base = root / "base-original.pkg.tar.zst"
            base.write_bytes(b"base package fixture")
            archive = root / "release.tar.gz"
            archive.write_bytes(b"release fixture")
            payload = root / "payload"
            (payload / "lib").mkdir(parents=True)
            library = payload / "lib/libvulkan_radeon.so"
            library.write_bytes(b"new driver fixture")
            release = {
                "version": "4.0.0-test",
                "mesa": "26.2.2",
                "driver_sha256": driver.digest(library),
            }
            info = {
                "pkgname": ["vulkan-radeon"],
                "pkgver": ["3:26.2.2-2.82"],
                "arch": ["x86_64"],
                "depend": ["glibc"],
                "license": ["MIT"],
                "group": ["mesa"],
                "backup": ["etc/example.conf"],
            }
            output = root / "packages"
            args = argparse.Namespace(
                archive=archive, base_package=base, output=output, sha256="a" * 64
            )

            def makepkg(command, **kwargs):
                self.assertEqual(command[0], "makepkg")
                env = kwargs["env"]
                self.assertEqual(env["PKGDEST"], str(output))
                self.assertEqual(env["BUILDDIR"], str(output))
                self.assertEqual(env["PKGEXT"], ".pkg.tar.zst")
                for name in ("vulkan-radeon", "bc250-fsr4-v4"):
                    (Path(env["PKGDEST"]) / (name + env["PKGEXT"])).write_bytes(b"package fixture")
                return subprocess.CompletedProcess(command, 0)

            with (
                mock.patch.object(system_package, "ROOT", root),
                mock.patch.object(system_package.os, "geteuid", return_value=1000),
                mock.patch.dict(
                    system_package.os.environ, {"PKGDEST": "/unrelated", "PKGEXT": ".pkg.tar.xz"}
                ),
                mock.patch.object(
                    system_package, "call", return_value=".PKGINFO\nusr/lib/libvulkan_radeon.so\n"
                ),
                mock.patch.object(system_package, "package_info", return_value=info),
                mock.patch.object(driver, "extract_verified", return_value=(payload, release)),
                mock.patch.object(driver, "probe"),
                mock.patch.object(
                    system_package.subprocess, "check_output", return_value=b"original driver"
                ),
                mock.patch.object(system_package.subprocess, "run", side_effect=makepkg),
            ):
                system_package.build(args)
            recipe = (output / "PKGBUILD").read_text()
            for field in [
                "depends=('glibc')",
                "license=('MIT')",
                "groups=('mesa')",
                "backup=('etc/example.conf')",
            ]:
                self.assertIn(field, recipe)
            subprocess.run(["bash", "-n", str(output / "PKGBUILD")], check=True)
            self.assertTrue((output / "packages.json").is_file())
            self.assertEqual((output / "base.pkg.tar.zst").read_bytes(), base.read_bytes())


if __name__ == "__main__":
    unittest.main()
