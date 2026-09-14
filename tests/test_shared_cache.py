# SPDX-License-Identifier: MIT
"""Shared-cache opt-in keeps per-game source caches and launch settings intact."""

import errno
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("shared_cache", ROOT / "scripts/shared-cache.py")
cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cache)


class SharedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / "shared"
        self.original = self.root / "game-cache"
        self.fossilize = self.original / "mesa_shader_cache_sf"
        self.fossilize.mkdir(parents=True)
        (self.fossilize / "existing.foz").write_bytes(b"retain original compiled shaders")
        self.env = {
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CACHE_HOME": str(self.root / "xdg"),
            "MESA_SHADER_CACHE_DIR": str(self.original),
            "MESA_DISK_CACHE_SINGLE_FILE": "1",
            "MESA_DISK_CACHE_READ_ONLY_FOZ_DBS": "steam_cache,steam_precompiled",
            "VKD3D_SHADER_CACHE_PATH": "/original/translation-cache",
            "STEAM_COMPAT_DATA_PATH": "/original/prefix",
            "WINEDLLOVERRIDES": "winmm=n,b",
        }

    def test_distinct_games_share_database_and_keep_separate_readonly_views(self):
        first = cache.prepare(self.env, self.shared)
        second = cache.prepare(
            dict(self.env, MESA_SHADER_CACHE_DIR=str(self.root / "other")), self.shared
        )
        one, two = Path(first["MESA_SHADER_CACHE_DIR"]), Path(second["MESA_SHADER_CACHE_DIR"])
        self.assertNotEqual(one, two)
        self.assertEqual(
            (one / "mesa_shader_cache").resolve(), (two / "mesa_shader_cache").resolve()
        )
        self.assertEqual((one / "mesa_shader_cache_sf").resolve(), self.fossilize)
        self.assertFalse((two / "mesa_shader_cache_sf").exists())
        self.assertEqual(
            (self.fossilize / "existing.foz").read_bytes(), b"retain original compiled shaders"
        )
        self.assertEqual(
            first["MESA_DISK_CACHE_READ_ONLY_FOZ_DBS"], "steam_cache,steam_precompiled,foz_cache"
        )

    def test_prefix_translation_cache_and_other_launch_fields_are_unchanged(self):
        result = cache.prepare(self.env, self.shared)
        for name in ["VKD3D_SHADER_CACHE_PATH", "STEAM_COMPAT_DATA_PATH", "WINEDLLOVERRIDES"]:
            self.assertEqual(result[name], self.env[name])
        self.assertEqual(self.env["MESA_DISK_CACHE_SINGLE_FILE"], "1")
        self.assertEqual(result["MESA_DISK_CACHE_SINGLE_FILE"], "0")

    def test_repeat_and_nested_invocation_reuse_the_same_view(self):
        first = cache.prepare(self.env, self.shared)
        self.assertEqual(cache.prepare(self.env, self.shared), first)
        self.assertEqual(cache.prepare(first, self.shared), first)

    def test_concurrent_launchers_prepare_one_consistent_view(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: cache.prepare(self.env, self.shared), range(32)))
        self.assertTrue(all(result == results[0] for result in results))
        view = Path(results[0]["MESA_SHADER_CACHE_DIR"])
        self.assertEqual((view / "mesa_shader_cache").resolve(), self.shared / "mesa_shader_cache")
        self.assertEqual((view / "mesa_shader_cache_sf").resolve(), self.fossilize)

    def test_existing_wrong_link_or_data_is_not_replaced(self):
        env = cache.prepare(self.env, self.shared)
        link = Path(env["MESA_SHADER_CACHE_DIR"]) / "mesa_shader_cache"
        link.unlink()
        link.mkdir()
        (link / "keep").write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "already contains data"):
            cache.prepare(self.env, self.shared)
        self.assertEqual((link / "keep").read_bytes(), b"existing")

    def test_disable_is_respected_and_size_override_survives(self):
        with self.assertRaisesRegex(ValueError, "explicitly disables"):
            cache.prepare(dict(self.env, MESA_SHADER_CACHE_DISABLE="true"), self.shared)
        env = cache.prepare(dict(self.env, MESA_SHADER_CACHE_MAX_SIZE="4G"), self.shared)
        self.assertEqual(env["MESA_SHADER_CACHE_MAX_SIZE"], "4G")

    def test_original_root_inside_shared_storage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            cache.prepare(dict(self.env, MESA_SHADER_CACHE_DIR=str(self.shared)), self.shared)

    def test_launcher_preserves_argument_boundaries(self):
        script = ROOT / "scripts/shared-cache.py"
        args = ["argument with spaces", "literal; punctuation"]
        child = subprocess.run(
            [
                sys.executable,
                str(script),
                "--cache-dir",
                str(self.shared),
                "--",
                sys.executable,
                "-c",
                "import json,sys;print(json.dumps(sys.argv[1:]))",
                *args,
            ],
            env=dict(os.environ, **self.env),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(child.stdout), args)

    def test_preparation_failure_still_launches_with_original_cache_settings(self):
        self.shared.write_bytes(b"existing file, not a cache directory")
        script = ROOT / "scripts/shared-cache.py"
        child = subprocess.run(
            [
                sys.executable,
                str(script),
                "--cache-dir",
                str(self.shared),
                "--",
                sys.executable,
                "-c",
                "import os;print(os.environ['MESA_SHADER_CACHE_DIR'])",
            ],
            env=dict(os.environ, **self.env),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(child.stdout.strip(), str(self.original))
        self.assertIn("launching with original settings", child.stderr)
        self.assertEqual(self.shared.read_bytes(), b"existing file, not a cache directory")

    def test_fossilize_created_after_first_launch_is_picked_up(self):
        original = self.root / "late-game-cache"
        env = dict(self.env, MESA_SHADER_CACHE_DIR=str(original))
        first = cache.prepare(env, self.shared)
        self.assertEqual(first["MESA_DISK_CACHE_COMBINE_RW_WITH_RO_FOZ"], "0")
        view = Path(first["MESA_SHADER_CACHE_DIR"])
        self.assertFalse((view / "mesa_shader_cache_sf").exists())
        (original / "mesa_shader_cache_sf").mkdir(parents=True)
        second = cache.prepare(env, self.shared)
        self.assertEqual(second["MESA_DISK_CACHE_COMBINE_RW_WITH_RO_FOZ"], "1")
        self.assertEqual(
            (view / "mesa_shader_cache_sf").resolve(), original / "mesa_shader_cache_sf"
        )

    def test_backend_selection_and_dynamic_readonly_list_are_preserved(self):
        env = dict(
            self.env, MESA_DISK_CACHE_READ_ONLY_FOZ_DBS_DYNAMIC_LIST="/original/dynamic-list"
        )
        result = cache.prepare(env, self.shared, "database")
        self.assertEqual(result["MESA_DISK_CACHE_DATABASE"], "1")
        self.assertEqual(
            result["MESA_DISK_CACHE_READ_ONLY_FOZ_DBS"], env["MESA_DISK_CACHE_READ_ONLY_FOZ_DBS"]
        )
        self.assertEqual(
            result["MESA_DISK_CACHE_READ_ONLY_FOZ_DBS_DYNAMIC_LIST"], "/original/dynamic-list"
        )
        self.assertEqual(
            (Path(result["MESA_SHADER_CACHE_DIR"]) / "mesa_shader_cache_db").resolve(),
            self.shared / "mesa_shader_cache_db",
        )

    def test_relative_xdg_is_ignored_and_atomic_distro_home_is_supported(self):
        user_home = self.root / "var/home/player"
        result = cache.prepare({"HOME": str(user_home), "XDG_CACHE_HOME": "relative"})
        self.assertTrue(
            str(Path(result["MESA_SHADER_CACHE_DIR"])).startswith(str(user_home / ".cache"))
        )
        self.assertEqual(result["BC250_FSR4_CACHE_ORIGINAL_ROOT"], str(user_home / ".cache"))

    def test_builtins_created_later_do_not_conflict_with_owned_view(self):
        first = cache.prepare(self.env, self.shared)
        (self.original / "radv_builtin_shaders").mkdir()
        (self.original / "radv_builtin_shaders/keep").write_bytes(b"original builtin cache")
        second = cache.prepare(self.env, self.shared)
        self.assertEqual(first, second)
        self.assertEqual(
            (Path(second["MESA_SHADER_CACHE_DIR"]) / "radv_builtin_shaders").resolve(),
            self.shared / "radv_builtin_shaders",
        )
        self.assertEqual(
            (self.original / "radv_builtin_shaders/keep").read_bytes(), b"original builtin cache"
        )

    def test_no_python_bootstrap_still_runs_the_original_command(self):
        empty_path = self.root / "empty-path"
        empty_path.mkdir()
        child = subprocess.run(
            [
                "/bin/sh",
                str(ROOT / "scripts/shared-cache.sh"),
                "--cache-dir",
                str(self.shared),
                "--",
                sys.executable,
                "-c",
                "import sys;print(sys.argv[1])",
                "preserved argument",
            ],
            env=dict(os.environ, PATH=str(empty_path)),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(child.stdout.strip(), "preserved argument")
        self.assertIn("launching with original cache settings", child.stderr)
        self.assertFalse(self.shared.exists())

    def test_status_and_show_do_not_create_paths(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        report = cache.inspect(self.env, self.shared)
        self.assertFalse(report["store_exists"])
        child = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/shared-cache.py"),
                "--cache-dir",
                str(self.shared),
                "--show",
            ],
            env=dict(os.environ, **self.env),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("MESA_SHADER_CACHE_DIR", json.loads(child.stdout))
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))

    def test_existing_unwritable_store_falls_back_and_records_reason(self):
        if os.geteuid() == 0:
            self.skipTest("Real mode-bit write failure requires an unprivileged user")
        cache.prepare(self.env, self.shared)
        store = self.shared / "mesa_shader_cache"
        store.chmod(0o500)
        try:
            result = cache.launch_environment(self.env, self.shared)
        finally:
            store.chmod(0o700)
        self.assertEqual(result, self.env)
        record = cache.inspect(self.env, self.shared)["last_launch"]
        self.assertEqual(record["result"], "fallback")
        self.assertIn("Permission denied", record["reason"])

    def test_full_disk_write_failure_cleans_probe_and_preserves_environment(self):
        with patch.object(cache.os, "write", side_effect=OSError(errno.ENOSPC, "No space left")):
            self.assertEqual(cache.launch_environment(self.env, self.shared), self.env)
        self.assertFalse(list(self.shared.rglob(".bc250-write-*")))
        self.assertIn(
            "No space left", cache.inspect(self.env, self.shared)["last_launch"]["reason"]
        )

    def test_legacy_size_limit_is_preserved(self):
        result = cache.prepare(dict(self.env, MESA_GLSL_CACHE_MAX_SIZE="4G"), self.shared)
        self.assertEqual(result["MESA_SHADER_CACHE_MAX_SIZE"], "4G")

    def test_steam_options_preserve_assignments_wrappers_and_spaces(self):
        original = 'WINEDLLOVERRIDES="winmm=n,b" ~/.lsfg %command% -dx12'
        launcher = self.root / "a space/cache"
        result = cache.steam_command(original, [launcher, "--"])
        self.assertTrue(result.startswith('WINEDLLOVERRIDES="winmm=n,b" ~/.lsfg '))
        self.assertTrue(result.endswith(" -- %command% -dx12"))
        self.assertEqual(cache.steam_command(result, [launcher, "--"]), result)
        for invalid in [
            "%command% %command%",
            '"%command%"',
            "echo ' %command% '",
            "no-placeholder",
            "%command%\nexit",
        ]:
            with self.subTest(options=invalid), self.assertRaises(ValueError):
                cache.steam_command(invalid, [launcher, "--"])

    def test_installed_pair_survives_download_removal_and_uninstall_keeps_caches(self):
        downloads = self.root / "download"
        downloads.mkdir()
        for name in ["shared-cache.py", "shared-cache.sh"]:
            (downloads / name).write_bytes((ROOT / "scripts" / name).read_bytes())
        prefix = self.root / "installed tools"
        with patch.object(cache, "SOURCE", downloads):
            launcher = cache.install(prefix)
        self.assertEqual(
            (prefix / "current/LICENSE.new-code").read_bytes(),
            (ROOT / "LICENSE.new-code").read_bytes(),
        )
        for path in downloads.iterdir():
            path.unlink()
        downloads.rmdir()
        child = subprocess.run(
            [
                str(launcher),
                "--cache-dir",
                str(self.shared),
                "--",
                sys.executable,
                "-c",
                "import os;print(os.environ['MESA_SHADER_CACHE_DIR'])",
            ],
            env=dict(os.environ, **self.env),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(Path(child.stdout.strip()).is_dir())
        marker = self.shared / "retained-cache"
        marker.write_text("cache remains")
        (prefix / "unrelated-note").write_text("retain")
        # A killed earlier setup may leave an unselected staging directory.
        interrupted = prefix / "launcher-tools/.stage-interrupted"
        interrupted.mkdir()
        (interrupted / "partial").write_text("retain unselected data")
        cache.uninstall(prefix)
        self.assertFalse(launcher.exists())
        self.assertEqual(marker.read_text(), "cache remains")
        self.assertEqual((prefix / "unrelated-note").read_text(), "retain")
        self.assertEqual((interrupted / "partial").read_text(), "retain unselected data")

    def test_installer_preserves_unrelated_launcher_and_tampered_payload(self):
        prefix = self.root / "installed"
        prefix.mkdir()
        launcher = prefix / "bc250-fsr4-cache"
        launcher.write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            cache.install(prefix)
        self.assertEqual(launcher.read_text(), "unrelated")
        launcher.unlink()
        cache.install(prefix)
        (prefix / "current/shared-cache.py").write_text("modified")
        with self.assertRaisesRegex(ValueError, "changed"):
            cache.install(prefix)
        self.assertEqual((prefix / "current/shared-cache.py").read_text(), "modified")
