# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_launch", ROOT / "runtime/launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LaunchTests(unittest.TestCase):
    def test_only_game_verbs_with_nonzero_identity_enable_injection(self):
        for verb in ("getcompatpath", "getnativepath", "destroyprefix", "runinprefix"):
            self.assertFalse(launch.game_launch([verb, "argument"], {"SteamAppId": "12345"}))
        for env in ({}, {"SteamAppId": "0"}, {"SteamAppId": "0", "SteamGameId": "0"}):
            self.assertFalse(launch.game_launch(["run"], env))
        for verb in ("run", "waitforexitandrun"):
            self.assertTrue(launch.game_launch([verb], {"SteamAppId": "12345"}))
            self.assertTrue(launch.game_launch([verb], {"SteamAppId": "0", "SteamGameId": "99"}))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "runtime-lock.json").write_bytes((ROOT / "runtime/manifest.json").read_bytes())
        self.library = self.root / "driver.so"
        self.library.write_bytes(b"verified driver fixture")
        self.driver = {
            "library": str(self.library),
            "sha256": launch.digest(self.library),
            "environment": {},
            "source_manifest_sha256": json.loads((self.root / "runtime-lock.json").read_text())[
                "driver"
            ]["source_manifest_sha256"],
        }

    def test_arbitrary_game_identity_takes_the_same_path(self):
        for appid in ("123456789", "not-a-steam-game"):
            env = launch.environment(
                self.root, self.driver, {"SteamAppId": appid, "CUSTOM": "preserve"}
            )
            self.assertEqual(env["SteamAppId"], appid)
            self.assertEqual(env["CUSTOM"], "preserve")
            self.assertEqual(env["PROTON_USE_XALIA"], "0")
            self.assertIn(
                r"Libraries.OptiDllPath=C:\windows\system32\umu\OptiScaler",
                env["PROTON_OPTISCALER_CONFIG"],
            )
            self.assertEqual(env["PROTON_FSR4_UPGRADE"], "4.1.1")
            self.assertEqual(env["PROTON_OPTISCALER_NAME"], "winmm.dll")
            self.assertIn("FSR.Fsr4ForceModel=2", env["PROTON_OPTISCALER_CONFIG"])
            for api, backend in (("Dx11", "ffx_12"), ("Dx12", "ffx"), ("Vulkan", "ffx_12")):
                self.assertIn(f"Upscalers.{api}Upscaler={backend}", env["PROTON_OPTISCALER_CONFIG"])
            self.assertIn("Plugins.LoadReShade=true", env["PROTON_OPTISCALER_CONFIG"])
            self.assertIn("Hotfix.CreateD3D12DeviceForLuma=false", env["PROTON_OPTISCALER_CONFIG"])
            self.assertEqual(
                env["PROTON_UPSCALER_MANIFEST"], str(self.root / "ge/upscaler-manifest.json")
            )

    def test_stale_launch_flags_cannot_replace_pinned_runtime(self):
        env = launch.environment(
            self.root,
            self.driver,
            {
                "PROTON_FSR4_UPGRADE": "4.1.1b",
                "PROTON_USE_OPTISCALER": "0.9.4",
                "VK_DRIVER_FILES": "/old/v3.json",
                "PROTON_DLSS_UPGRADE": "latest",
                "PROTON_OPTISCALER_NAME": "dxgi.dll",
                "WINE_OPTISCALER_NAME": "dbghelp.dll",
            },
        )
        self.assertEqual(env["PROTON_FSR4_UPGRADE"], "4.1.1")
        self.assertEqual(env["PROTON_USE_OPTISCALER"], "10.0.0-pre1-20260904")
        self.assertNotIn("VK_DRIVER_FILES", env)
        self.assertNotIn("PROTON_DLSS_UPGRADE", env)
        self.assertEqual(env["PROTON_OPTISCALER_NAME"], "winmm.dll")
        self.assertNotIn("WINE_OPTISCALER_NAME", env)

    def test_changed_driver_fails_before_proton(self):
        self.library.write_bytes(b"distribution replacement")
        with self.assertRaisesRegex(RuntimeError, "driver changed"):
            launch.environment(self.root, self.driver, {})

    def test_bridge_rejects_spoofed_nvx_and_preserves_caller_exclusions(self):
        env = launch.environment(
            self.root, self.driver, {"VKD3D_DISABLE_EXTENSIONS": "VK_EXT_example"}
        )
        self.assertEqual(
            env["VKD3D_DISABLE_EXTENSIONS"].split(";"),
            ["VK_EXT_example", "VK_NVX_binary_import", "VK_NVX_image_view_handle"],
        )
        utility = launch.environment(self.root, self.driver, {}, game=False)
        self.assertNotIn("VKD3D_DISABLE_EXTENSIONS", utility)

    def test_steam_utility_does_not_receive_game_injection(self):
        env = launch.environment(
            self.root,
            self.driver,
            {"STEAM_COMPAT_APP_ID": "999999", "PROTON_USE_OPTISCALER": "old"},
            game=False,
        )
        self.assertEqual(env["STEAM_COMPAT_APP_ID"], "999999")
        self.assertNotIn("PROTON_USE_OPTISCALER", env)
        self.assertNotIn("PROTON_UPSCALER_MANIFEST", env)
        self.assertNotIn("PROTON_OPTISCALER_NAME", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def test_different_runtime_driver_contract_refuses_launch(self):
        self.driver["source_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "different driver source"):
            launch.environment(self.root, self.driver, {})

    def test_private_driver_selector_and_explicit_debug(self):
        self.driver["environment"] = {"VK_DRIVER_FILES": "/private/current.json"}
        env = launch.environment(self.root, self.driver, {"BC250_RUNTIME_DEBUG": "1"})
        self.assertEqual(env["VK_DRIVER_FILES"], "/private/current.json")
        self.assertIn("FSR.Fsr4EnableWatermark=true", env["PROTON_OPTISCALER_CONFIG"])
        self.assertEqual(env["PROTON_LOG"], "1")

    def test_private_current_cannot_silently_select_another_driver(self):
        icd = self.root / "current.json"
        icd.write_text('{"ICD":{"library_path":"replacement.so"}}')
        self.driver.update({"mode": "private", "environment": {"VK_DRIVER_FILES": str(icd)}})
        with self.assertRaisesRegex(RuntimeError, "Private driver selection changed"):
            launch.environment(self.root, self.driver, {})

    def test_steam_container_checks_exported_host_system_driver(self):
        exported = self.root / "host/usr/lib/libvulkan_radeon.so"
        exported.parent.mkdir(parents=True)
        exported.write_bytes(self.library.read_bytes())
        self.driver.update({"mode": "system", "library": "/usr/lib/libvulkan_radeon.so"})
        with patch.object(launch, "HOST", self.root / "host"):
            env = launch.environment(self.root, self.driver, {})
        self.assertNotIn("VK_DRIVER_FILES", env)

    def test_handoff_preserves_steam_descriptor_and_holds_version_lock(self):
        tool = self.root / "tool"
        version = tool / "versions/test"
        (version / "ge").mkdir(parents=True)
        (tool / "current").symlink_to("versions/test")
        (tool / "driver.json").write_text(json.dumps(self.driver))
        shutil.copy2(ROOT / "runtime/launch.py", version / "launch.py")
        shutil.copy2(self.root / "runtime-lock.json", version / "runtime-lock.json")
        child = version / "ge/proton"
        child.write_text(
            "#!/usr/bin/env python3\nimport fcntl, os, pathlib, sys\n"
            "assert os.read(int(os.environ['PASSED_DESCRIPTOR']), 4) == b'test'\n"
            "with pathlib.Path(__file__).parents[3].joinpath('.runtime.lock').open('a') as lock:\n"
            " try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            " except BlockingIOError: sys.exit(23)\n"
            "sys.exit(99)\n"
        )
        child.chmod(0o755)
        (version / "runtime-release.json").write_text(
            json.dumps({"files": {}, "critical_files": []})
        )
        read, write = os.pipe()
        try:
            os.write(write, b"test")
            result = subprocess.run(
                [sys.executable, "-B", str(version / "launch.py"), "run"],
                env={**os.environ, "PASSED_DESCRIPTOR": str(read)},
                pass_fds=(read,),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 23, result.stderr)
        finally:
            os.close(read)
            os.close(write)


if __name__ == "__main__":
    unittest.main()
