# SPDX-License-Identifier: MIT
"""Shared-cache opt-in keeps per-game source caches and launch settings intact."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            (one / "mesa_shader_cache_db").resolve(), (two / "mesa_shader_cache_db").resolve()
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

    def test_existing_wrong_link_or_data_is_not_replaced(self):
        env = cache.prepare(self.env, self.shared)
        link = Path(env["MESA_SHADER_CACHE_DIR"]) / "mesa_shader_cache_db"
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
