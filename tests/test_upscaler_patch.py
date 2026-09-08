# SPDX-License-Identifier: MIT
"""Exercise the patch against the complete, unmodified GE-Proton11-6 module.

fixtures/ge-proton11-6-upscalers.py is copied from the pinned official release,
not a rewritten model of the implementation. Its SHA256 is checked before patch
application. It contains umu-protonfixes d13333be729b3018f9f9bc6790944901319b425b
plus GE's patches/protonfixes/0001-upscalers-add-optiscaler-downloader.patch:
https://github.com/GloriousEggroll/proton-ge-custom/tree/GE-Proton11-6

The fixture is covered by the upstream umu-protonfixes license:
https://github.com/Open-Wine-Components/umu-protonfixes/blob/d13333be729b3018f9f9bc6790944901319b425b/LICENSE

BSD 2-Clause License

Copyright (c) 2018, Chris Simons

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import ast
import configparser
import hashlib
import io
import json
import lzma
import random
import subprocess
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA256 = "4128896c4134d865a290e2e8e62279d07157131b207f4bc7d29e8c873fe85645"
PATCH = ROOT / "runtime/patches/0001-pinned-upscaler-manifest.patch"
OPTI_PATH = Path("drive_c/windows/system32/umu")
PROVIDER_PATH = Path("drive_c/windows/system32/amdxcffx64.dll")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class UpscalerPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "tests/fixtures/ge-proton11-6-upscalers.py").read_bytes()
        if sha256(cls.source) != BASE_SHA256:
            raise AssertionError("Upstream fixture is not the pinned GE-Proton11-6 source")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "protonfixes/upscalers.py")
            source.parent.mkdir()
            source.write_bytes(cls.source)
            result = subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(PATCH)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode or "offset" in result.stdout or "fuzz" in result.stdout:
                raise AssertionError(result.stdout + result.stderr)
            cls.patched = source.read_text()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.compat = self.root / "compat"
        self.compat.mkdir()
        self.prefix = self.compat / "pfx"
        self.prefix.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.manifest_path = self.root / "manifest.json"
        self.module = self.load_module(self.patched)
        rng = random.Random(42)
        self.files = {
            "dxgi.dll": rng.randbytes(4096),
            "OptiScaler/plugins/OptiPatcher.asi": rng.randbytes(4096),
            "OptiScaler/amd_fidelityfx_upscaler_dx12.dll": rng.randbytes(4096),
            "OptiScaler.ini": b"[FSR]\nFsr4ForceModel=auto\n[Plugins]\nLoadAsiPlugins=false\n",
        }
        self.provider = rng.randbytes(4096)
        self.opti_archive = self.artifacts / "optiscaler.tar.xz"
        self.write_opti_archive()
        self.provider_archive = self.artifacts / "provider.dll.xz"
        self.provider_archive.write_bytes(lzma.compress(self.provider))
        self.manifest = {
            "optiscaler": [
                {
                    "version": "nightly-exact",
                    "is_dev_file": False,
                    "download_url": "artifacts/optiscaler.tar.xz",
                    "zip_sha256_hash": sha256(self.opti_archive.read_bytes()),
                    "md5_hash": {
                        name: "" if name.endswith(".ini") else hashlib.md5(data).hexdigest()
                        for name, data in self.files.items()
                    },
                    "sha256_hash": {
                        name: sha256(data)
                        for name, data in self.files.items()
                        if not name.endswith(".ini")
                    },
                }
            ],
            "fsr_40_drv": [
                {
                    "version": "4.1.1",
                    "is_dev_file": False,
                    "download_url": "artifacts/provider.dll.xz",
                    "zip_sha256_hash": sha256(self.provider_archive.read_bytes()),
                    "md5_hash": hashlib.md5(self.provider).hexdigest(),
                    "sha256_hash": sha256(self.provider),
                }
            ],
        }
        self.env = {
            "PROTON_UPSCALER_MANIFEST": str(self.manifest_path),
            "PROTON_USE_OPTISCALER": "nightly-exact",
            "PROTON_FSR4_UPGRADE": "4.1.1",
            "PROTON_OPTISCALER_CONFIG": "FSR.Fsr4ForceModel=2;Plugins.LoadAsiPlugins=true",
        }
        self.compat_config = {"optiscaler", "fsr4"}
        self.downloads = []
        self.urlopen = mock.patch.object(
            self.module.urllib.request, "urlopen", side_effect=self.local_urlopen
        ).start()
        self.addCleanup(mock.patch.stopall)

    def load_module(self, source):
        tree = ast.parse(source)
        # Stub only GE's logger/config. Execute every upstream function unchanged
        # apart from the actual applied patch, with real filesystem/archive I/O.
        tree.body = [
            node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.level)
        ]
        module = types.ModuleType("tested_upscalers")
        module.log = mock.Mock()
        module.config = types.SimpleNamespace(path=types.SimpleNamespace(cache_dir=self.cache))
        exec(compile(tree, "protonfixes/upscalers.py", "exec"), module.__dict__)
        return module

    def local_urlopen(self, request, **_kwargs):
        url = request if isinstance(request, str) else request.full_url
        self.downloads.append(url)
        parsed = urlparse(url)
        if parsed.scheme != "file":
            raise AssertionError(f"Unexpected network access: {url}")
        path = Path(unquote(parsed.path))
        if not path.resolve().is_relative_to(self.root):
            raise AssertionError(f"Read outside isolated test directory: {path}")
        return io.BytesIO(path.read_bytes())

    def write_opti_archive(self):
        with tarfile.open(self.opti_archive, "w:xz") as archive:
            for name, data in self.files.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))

    def setup_runtime(self):
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.module.setup_upscalers(
            self.compat_config, self.env, str(self.compat), str(self.prefix)
        )

    def test_first_launch_uses_only_pinned_local_payload_and_config(self):
        self.env["SteamAppId"] = "999999999"  # No title catalog or executable path.
        self.setup_runtime()
        self.assertEqual(self.env["WINE_OPTISCALER_NAME"], "dxgi.dll")
        self.assertNotIn("WINE_UPSCALER_REPLACE", self.env)
        self.assertEqual((self.prefix / PROVIDER_PATH).read_bytes(), self.provider)
        for name, data in self.files.items():
            if not name.endswith(".ini"):
                self.assertEqual((self.prefix / OPTI_PATH / name).read_bytes(), data)
        ini = configparser.ConfigParser()
        ini.read(self.prefix / OPTI_PATH / "OptiScaler.ini")
        self.assertEqual(ini["FSR"]["Fsr4ForceModel"], "2")
        self.assertEqual(ini["Plugins"]["LoadAsiPlugins"], "true")
        self.assertEqual(self.downloads, [])
        self.assertFalse((self.cache / "upscalers/manifest.json").exists())

    def test_warm_launch_verifies_installed_files_without_download(self):
        self.setup_runtime()
        self.urlopen.side_effect = AssertionError("Warm launch must not download")
        self.setup_runtime()
        self.assertEqual(self.env["WINE_OPTISCALER_NAME"], "dxgi.dll")
        self.assertFalse((self.prefix / OPTI_PATH / "OptiScaler.ini.old").exists())

    def test_cached_archives_repair_deleted_prefix_without_network(self):
        self.setup_runtime()
        (self.prefix / PROVIDER_PATH).unlink()
        (self.prefix / OPTI_PATH / "dxgi.dll").unlink()
        self.urlopen.side_effect = AssertionError("Valid local cache must be reused")
        self.setup_runtime()
        self.assertEqual((self.prefix / PROVIDER_PATH).read_bytes(), self.provider)
        self.assertEqual(
            (self.prefix / OPTI_PATH / "dxgi.dll").read_bytes(), self.files["dxgi.dll"]
        )

    def test_local_manifest_never_falls_back_to_remote_or_cached_manifest(self):
        cached = self.cache / "upscalers/manifest.json"
        cached.parent.mkdir(parents=True)
        cached.write_text(json.dumps(self.manifest))
        for value in ("relative.json", str(self.root / "missing.json")):
            with self.subTest(value=value):
                env = dict(self.env, PROTON_UPSCALER_MANIFEST=value)
                with self.assertRaises((RuntimeError, FileNotFoundError)):
                    self.module.setup_upscalers(set(), env, str(self.compat), str(self.prefix))
        self.manifest_path.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            self.module.setup_upscalers(set(), self.env, str(self.compat), str(self.prefix))
        self.assertEqual(self.downloads, [])
        self.assertEqual(list(self.prefix.iterdir()), [])

    def test_exact_version_does_not_match_substrings_or_latest(self):
        for request in ("nightly", "future-nightly"):
            with self.subTest(request=request):
                self.env["PROTON_USE_OPTISCALER"] = request
                with self.assertRaisesRegex(RuntimeError, "OptiScaler.*refusing launch"):
                    self.setup_runtime()
                self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_default_requires_one_unambiguous_locked_version(self):
        self.env["PROTON_USE_OPTISCALER"] = "1"
        self.setup_runtime()
        self.env.pop("WINE_OPTISCALER_NAME")
        self.manifest["optiscaler"].append(dict(self.manifest["optiscaler"][0], version="other"))
        with self.assertRaisesRegex(RuntimeError, "refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_explicit_unlocked_component_is_an_error(self):
        for name in ("dlss", "xess", "ffx3", "ffx4"):
            with self.subTest(name=name):
                self.compat_config = {name}
                with self.assertRaisesRegex(RuntimeError, f"Pinned {name}.*refusing launch"):
                    self.setup_runtime()
        self.assertEqual(self.downloads, [])

    def test_complete_ffx4_group_is_enabled_implicitly_with_exact_versions(self):
        for name in ("fg", "ldr", "up"):
            self.manifest[f"fsr_40_{name}_dx12"] = [dict(self.manifest["fsr_40_drv"][0])]
        self.setup_runtime()
        for name in ("framegeneration", "loader", "upscaler"):
            target = self.prefix / OPTI_PATH / f"amd_fidelityfx_{name}_dx12.dll"
            self.assertEqual(target.read_bytes(), self.provider)
        self.assertEqual(self.downloads, [])

    def test_explicit_ffx4_version_cannot_be_suppressed_by_an_incomplete_lock(self):
        self.compat_config.add("ffx4")
        self.env["PROTON_FFX4_UPGRADE"] = "missing-version"
        self.manifest["fsr_40_up_dx12"] = [dict(self.manifest["fsr_40_drv"][0])]
        with self.assertRaisesRegex(RuntimeError, "Pinned ffx4.*refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_changed_archive_hash_stops_launch_before_extraction(self):
        self.opti_archive.write_bytes(b"changed archive" * 200)
        with self.assertRaisesRegex(RuntimeError, "OptiScaler.*refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)
        self.assertFalse((self.prefix / OPTI_PATH / "dxgi.dll").exists())
        self.assertFalse((self.cache / "upscalers" / self.opti_archive.name).exists())
        self.assertEqual(list(self.prefix.iterdir()), [])

    def test_extracted_provider_must_match_sha256_even_with_matching_md5(self):
        self.manifest["fsr_40_drv"][0]["sha256_hash"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "Pinned fsr4.*refusing launch"):
            self.setup_runtime()
        self.assertFalse((self.prefix / PROVIDER_PATH).exists())
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_colliding_global_cache_archive_is_unused(self):
        cached = self.cache / "upscalers" / self.provider_archive.name
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"bad cached archive" * 200)
        self.setup_runtime()
        self.assertEqual(cached.read_bytes(), b"bad cached archive" * 200)
        self.assertEqual((self.prefix / PROVIDER_PATH).read_bytes(), self.provider)

    def test_dangling_cache_symlink_is_not_used_as_a_download_destination(self):
        cached = self.cache / "upscalers" / self.provider_archive.name
        cached.parent.mkdir(parents=True)
        outside = self.root / "must-not-be-created"
        cached.symlink_to(outside)
        self.setup_runtime()
        self.assertFalse(outside.exists())
        self.assertTrue(cached.is_symlink())

    def test_changed_archive_is_rejected_even_when_prefix_hashes_still_match(self):
        self.setup_runtime()
        self.env.pop("WINE_OPTISCALER_NAME")
        self.opti_archive.write_bytes(b"changed archive" * 200)
        with self.assertRaisesRegex(RuntimeError, "OptiScaler.*refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)
        self.assertEqual(
            (self.prefix / OPTI_PATH / "dxgi.dll").read_bytes(), self.files["dxgi.dll"]
        )

    def test_local_manifest_does_not_allow_remote_artifacts(self):
        self.manifest["fsr_40_drv"][0]["download_url"] = "https://example.invalid/provider.xz"
        with self.assertRaisesRegex(RuntimeError, "local file URLs"):
            self.setup_runtime()
        self.assertEqual(list(self.prefix.iterdir()), [])
        self.assertEqual(self.downloads, [])

    def test_missing_sha256_is_not_an_md5_only_opt_out(self):
        for field in ("sha256_hash", "zip_sha256_hash"):
            with self.subTest(field=field):
                item = self.manifest["fsr_40_drv"][0]
                saved = item.pop(field)
                with self.assertRaisesRegex(RuntimeError, "Pinned fsr4.*refusing launch"):
                    self.setup_runtime()
                item[field] = saved
        self.assertEqual(self.downloads, [])

    def test_plugin_and_bundled_sdk_require_critical_hashes(self):
        hashes = self.manifest["optiscaler"][0]["sha256_hash"]
        for name in (
            "OptiScaler/plugins/OptiPatcher.asi",
            "OptiScaler/amd_fidelityfx_upscaler_dx12.dll",
        ):
            with self.subTest(name=name):
                checksum = hashes.pop(name)
                with self.assertRaisesRegex(RuntimeError, "OptiScaler.*refusing launch"):
                    self.setup_runtime()
                hashes[name] = checksum

    def test_changed_critical_files_cannot_be_approved_by_tracking_file(self):
        self.setup_runtime()
        tracking_path = self.compat / "upscaler_files"
        for target, section, check in (
            (PROVIDER_PATH, "fsr4_files", "check_upscaler"),
            (OPTI_PATH / "OptiScaler/plugins/OptiPatcher.asi", "opti_files", "check_optiscaler"),
            (
                OPTI_PATH / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll",
                "opti_files",
                "check_optiscaler",
            ),
        ):
            with self.subTest(target=target):
                original = (self.prefix / target).read_bytes()
                changed = original[::-1]
                (self.prefix / target).write_bytes(changed)
                tracking = json.loads(tracking_path.read_text())
                tracking[section][str(target)]["md5_hash"] = hashlib.md5(changed).hexdigest()
                tracking_path.write_text(json.dumps(tracking))
                args = [str(self.compat), str(self.prefix)]
                if check == "check_upscaler":
                    args = ["fsr4", *args, "4.1.1"]
                else:
                    args.append("nightly-exact")
                self.assertFalse(getattr(self.module, check)(*args, ignore_version=True))
                self.setup_runtime()
                self.assertEqual((self.prefix / target).read_bytes(), original)

    def test_failed_upgrade_does_not_accept_old_version_with_ignore_version(self):
        self.setup_runtime()
        self.env.pop("WINE_OPTISCALER_NAME")
        self.env["PROTON_FSR4_UPGRADE"] = "4.1.2"
        item = self.manifest["fsr_40_drv"][0]
        item["version"] = "4.1.2"
        item["sha256_hash"] = "1" * 64
        with self.assertRaisesRegex(RuntimeError, "Pinned fsr4.*refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)
        self.assertEqual((self.prefix / PROVIDER_PATH).read_bytes(), self.provider)

    def test_relative_artifact_traversal_and_escaping_symlink_are_rejected(self):
        escape = self.artifacts / "escape.dll.xz"
        escape.symlink_to("/etc/hosts")
        for url in ("../outside.xz", "/etc/hosts", "artifacts/escape.dll.xz"):
            with self.subTest(url=url):
                self.manifest["fsr_40_drv"][0]["download_url"] = url
                with self.assertRaisesRegex(RuntimeError, "refusing launch"):
                    self.setup_runtime()
        self.assertEqual(self.downloads, [])

    def test_missing_or_renamed_relative_artifact_is_an_error_even_when_cached(self):
        self.setup_runtime()
        self.env.pop("WINE_OPTISCALER_NAME")
        self.provider_archive.rename(self.provider_archive.with_suffix(".renamed"))
        with self.assertRaisesRegex(RuntimeError, "Pinned fsr4.*refusing launch"):
            self.setup_runtime()
        self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_missing_or_malformed_preset_key_stops_launch(self):
        for value in ("FSR.MissingModel=2", "broken"):
            with self.subTest(value=value):
                self.env["PROTON_OPTISCALER_CONFIG"] = value
                with self.assertRaisesRegex(RuntimeError, "pinned OptiScaler"):
                    self.setup_runtime()
                self.assertNotIn("WINE_OPTISCALER_NAME", self.env)

    def test_explicit_file_url_is_supported(self):
        self.manifest["fsr_40_drv"][0]["download_url"] = self.provider_archive.as_uri()
        self.setup_runtime()
        self.assertEqual((self.prefix / PROVIDER_PATH).read_bytes(), self.provider)

    def test_no_manifest_preserves_stock_implicit_components_and_soft_failures(self):
        for source in (self.source.decode(), self.patched):
            with self.subTest(source="patched" if source == self.patched else "stock"):
                module = self.load_module(source)
                with (
                    mock.patch.object(module, "setup_upscaler", return_value=False) as upscaler,
                    mock.patch.object(module, "setup_optiscaler", return_value=False),
                ):
                    module.setup_upscalers({"optiscaler"}, {}, str(self.compat), str(self.prefix))
                self.assertEqual(
                    [call.args[0] for call in upscaler.call_args_list], ["dlss", "xess", "ffx3"]
                )

    def test_no_manifest_preserves_stock_substring_and_latest_selection(self):
        upstream_manifest = {
            "optiscaler": [
                {"version": "0.9.3", "is_dev_file": False},
                {"version": "0.9.4", "is_dev_file": False},
            ]
        }
        for source in (self.source.decode(), self.patched):
            with self.subTest(source="patched" if source == self.patched else "stock"):
                module = self.load_module(source)
                self.urlopen.side_effect = lambda *_args, **_kwargs: io.BytesIO(
                    json.dumps(upstream_manifest).encode()
                )
                select = module.__dict__["__get_dll_manifest"]
                self.assertEqual(select("optiscaler", "9.3")["version"], "0.9.3")
                self.assertEqual(select("optiscaler", "unavailable")["version"], "0.9.4")

    def test_no_manifest_preserves_stock_manifest_reuse_between_setup_calls(self):
        manifest = {"optiscaler": [{"version": "stock", "is_dev_file": False}]}
        for source in (self.source.decode(), self.patched):
            with self.subTest(source="patched" if source == self.patched else "stock"):
                module = self.load_module(source)
                self.urlopen.reset_mock()
                self.urlopen.side_effect = lambda *_args, **_kwargs: io.BytesIO(
                    json.dumps(manifest).encode()
                )
                select = module.__dict__["__get_dll_manifest"]
                self.assertEqual(select("optiscaler")["version"], "stock")
                module.setup_upscalers(set(), {}, str(self.compat), str(self.prefix))
                self.assertEqual(select("optiscaler")["version"], "stock")
                self.assertEqual(self.urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
