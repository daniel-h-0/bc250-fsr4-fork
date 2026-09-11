# SPDX-License-Identifier: MIT
"""Meaningful checks for the portable shader lowering and release boundary."""

import ctypes
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dll import build as dll_build
from dll import repack_dll, scalarize_casts

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location("package_dll", ROOT / "scripts/package-dll.py")
package_dll = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_dll)


class CastCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("clang"), "clang is required for executable LLVM equivalence")
    def test_all_16_bit_values_in_both_lanes_have_exact_signed_and_unsigned_results(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for op in ("sext", "zext"):
                original = f"""define i64 @cast(i32 %packed) {{
entry:
  %lanes = bitcast i32 %packed to <2 x i16>
  %wide = {op} <2 x i16> %lanes to <2 x i32>
  %result = bitcast <2 x i32> %wide to i64
  ret i64 %result
}}
"""
                changed, count = scalarize_casts.lower(original)
                self.assertEqual(count, 1)
                functions = []
                libraries = []
                for name, code in (("vector", original), ("lanes", changed)):
                    source = folder / f"{op}-{name}.ll"
                    library = source.with_suffix(".so")
                    source.write_text(code)
                    subprocess.run(
                        [
                            "clang",
                            "-shared",
                            "-fPIC",
                            "-O0",
                            "-Wno-override-module",
                            "-x",
                            "ir",
                            str(source),
                            "-o",
                            str(library),
                        ],
                        capture_output=True,
                        check=True,
                        timeout=30,
                    )
                    libraries.append(ctypes.CDLL(str(library)))
                    function = libraries[-1].cast
                    function.argtypes = [ctypes.c_uint32]
                    function.restype = ctypes.c_uint64
                    functions.append(function)
                for value in range(65536):
                    # Odd multiplier makes the second lane exhaust every 16-bit
                    # value too, with different values and signs in the lanes.
                    other = (value * 40503 + 17) & 65535
                    packed = value | (other << 16)
                    expected = []
                    for lane in (value, other):
                        if op == "sext" and lane & 32768:
                            lane -= 65536
                        expected.append(lane & 0xFFFFFFFF)
                    result = expected[0] | (expected[1] << 32)
                    self.assertEqual(functions[0](packed), result)
                    self.assertEqual(functions[1](packed), result)

    def test_reserved_ssa_names_cannot_be_captured(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            scalarize_casts.lower("  %compatcast1n0 = add i16 1, 2\n")

    def test_nonmatching_arithmetic_and_cast_shapes_are_preserved(self):
        original = "  %a = add <2 x i16> %b, %c\n  %d = zext <4 x i16> %e to <4 x i32>\n"
        self.assertEqual(scalarize_casts.lower(original), (original, 0))


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "recorded.ll").write_text("recorded source\n")
        self.inventory = {
            "recorded.ll": hashlib.sha256((self.root / "recorded.ll").read_bytes()).hexdigest()
        }
        (self.root / "source-inventory.json").write_text(json.dumps(self.inventory))

    def verify(self):
        with patch.object(dll_build, "ROOT", self.root):
            dll_build.verify_sources()

    def test_extra_header_and_changed_source_are_rejected(self):
        self.verify()
        (self.root / "header.h").write_text("unexpected include\n")
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.verify()
        (self.root / "header.h").unlink()
        (self.root / "recorded.ll").write_text("changed source\n")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.verify()

    def test_extra_directory_symlink_is_rejected(self):
        (self.root / "linked-headers").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.verify()

    def test_nested_inventory_filename_is_not_exempt_from_the_inventory(self):
        (self.root / "nested").mkdir()
        (self.root / "nested/source-inventory.json").write_text("unrecorded input")
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.verify()

    def test_repack_refuses_existing_dll_or_record_before_reading_inputs(self):
        output = self.root / "candidate.dll"
        for existing in (output, output.with_suffix(".json")):
            existing.write_text("preserve me")
            with self.assertRaises(FileExistsError):
                repack_dll.build(self.root / "missing-sdk", {}, output)
            self.assertEqual(existing.read_text(), "preserve me")
            existing.unlink()


class DllPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        source = self.root / "dll"
        (source / "notices").mkdir(parents=True)
        (source / "notices/license.txt").write_text("Fixture notice\n")
        self.dll = Path(self.temp.name) / "fixture.dll"
        self.dll.write_bytes(b"MZ fixture bytes for package boundary tests\n")
        (source / "manifest.json").write_text(
            json.dumps(
                {
                    "release_version": "4.0.0-rc7-test",
                    "provider_name": "4.1.1-test",
                    "expected_dll_bytes": self.dll.stat().st_size,
                    "expected_dll_sha256": hashlib.sha256(self.dll.read_bytes()).hexdigest(),
                }
            )
        )
        self.instructions = (
            "Project version **4.0.0-rc7-test**; SDK display name **4.1.1-test**.\n\n"
            f"The DLL is {self.dll.stat().st_size:,} bytes, SHA256:\n\n```text\n"
            + hashlib.sha256(self.dll.read_bytes()).hexdigest()
            + "\n```\n"
        )
        (source / "INSTALL.md").write_text(self.instructions)
        (source / "source-inventory.json").write_text(
            json.dumps(
                {
                    str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source.rglob("*")
                    if p.is_file()
                }
            )
        )

    def test_deterministic_archives_contain_exactly_one_dll_and_verified_checksums(self):
        for kind in ("zip", "tar.xz"):
            one = package_dll.create_archive(
                self.root, self.dll, Path(self.temp.name) / (kind + "-one"), kind
            )
            two = package_dll.create_archive(
                self.root, self.dll, Path(self.temp.name) / (kind + "-two"), kind
            )
            self.assertEqual(one.read_bytes(), two.read_bytes())
            if kind == "zip":
                with zipfile.ZipFile(one) as bundle:
                    self.assertIsNone(bundle.testzip())
                    files = {name: bundle.read(name) for name in bundle.namelist()}
            else:
                with tarfile.open(one) as bundle:
                    files = {member.name: bundle.extractfile(member).read() for member in bundle}
            self.assertEqual(
                set(files), {package_dll.DLL_NAME, "README.md", "notices/license.txt", "SHA256SUMS"}
            )
            self.assertEqual(files[package_dll.DLL_NAME], self.dll.read_bytes())
            for line in files["SHA256SUMS"].decode().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                package_dll.create_archive(self.root, self.dll, one.parent, kind)

    def test_altered_dll_and_instructions_are_rejected(self):
        original = self.dll.read_bytes()
        self.dll.write_bytes(original + b"modified")
        with self.assertRaisesRegex(ValueError, "qualified manifest"):
            package_dll.release_files(self.root, self.dll)
        self.dll.write_bytes(original)
        (self.root / "dll/INSTALL.md").write_text("Unrecorded instructions")
        with self.assertRaisesRegex(ValueError, "source inventory"):
            package_dll.release_files(self.root, self.dll)

    def test_missing_license_cannot_silently_disappear_from_a_release(self):
        (self.root / "dll/notices/license.txt").unlink()
        with self.assertRaisesRegex(ValueError, "notice inventory"):
            package_dll.release_files(self.root, self.dll)

    def test_inventoried_but_stale_checksum_footer_is_rejected(self):
        source = self.root / "dll"
        for stale in (
            self.instructions.replace(hashlib.sha256(self.dll.read_bytes()).hexdigest(), "0" * 64),
            self.instructions.replace(f"{self.dll.stat().st_size:,} bytes", "1 bytes"),
            self.instructions.replace("4.1.1-test", "4.1.1-old"),
        ):
            (source / "INSTALL.md").write_text(stale)
            inventory = json.loads((source / "source-inventory.json").read_text())
            inventory["INSTALL.md"] = hashlib.sha256(stale.encode()).hexdigest()
            (source / "source-inventory.json").write_text(json.dumps(inventory))
            with self.assertRaisesRegex(ValueError, "release identity"):
                package_dll.release_files(self.root, self.dll)

    def test_documentation_revision_keeps_the_original_asset_and_dll(self):
        output = Path(self.temp.name) / "releases"
        original = package_dll.create_archive(self.root, self.dll, output)
        original_bytes = original.read_bytes()
        refresh = package_dll.create_archive(self.root, self.dll, output, documentation_revision=1)
        self.assertTrue(refresh.name.endswith("-docs1.zip"))
        self.assertEqual(original.read_bytes(), original_bytes)
        with zipfile.ZipFile(refresh) as archive:
            self.assertEqual(archive.read(package_dll.DLL_NAME), self.dll.read_bytes())
        with self.assertRaises(FileExistsError):
            package_dll.create_archive(self.root, self.dll, output, documentation_revision=1)
        for invalid in (0, -1, "1", True):
            with self.assertRaisesRegex(ValueError, "positive integer"):
                package_dll.create_archive(
                    self.root, self.dll, output, documentation_revision=invalid
                )


if __name__ == "__main__":
    unittest.main()
