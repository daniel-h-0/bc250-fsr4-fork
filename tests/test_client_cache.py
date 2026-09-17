# SPDX-License-Identifier: MIT
"""Launcher enrollment, recovery and real wrapper process tests in private folders."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("client_cache_test", ROOT / "scripts/client-cache.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CacheEnrollment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="client cache '")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.game = {
            "root": str(self.home / "games/Example"),
            "appid": "example",
            "platform": "GOG",
        }
        Path(self.game["root"]).mkdir(parents=True)
        self.manager = m.Manager(self.home / "state", self.home, idle_check=lambda *_: None)
        self.base = self.home / ".config/heroic"
        self.write(
            self.base / "gog_store/installed.json",
            {
                "installed": [
                    {"appName": "example", "platform": "windows", "install_path": self.game["root"]}
                ]
            },
        )
        self.path = self.base / "GamesConfig/example.json"
        self.write(
            self.path,
            {
                "example": {
                    "wrapperOptions": [{"exe": "/existing wrapper", "args": "--flag 'two words'"}],
                    "enviromentOptions": [{"key": "KEEP", "value": "yes"}],
                    "winePrefix": "/original/prefix",
                },
                "other": 42,
            },
        )
        self.before = self.path.read_bytes()

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def enable(self):
        return self.manager.apply(self.game, True)

    def test_heroic_enable_repeat_disable(self):
        self.assertEqual(self.enable()["state"], "configured")
        obj = json.loads(self.path.read_text())
        self.assertEqual(
            obj["example"]["wrapperOptions"][0],
            json.loads(self.before)["example"]["wrapperOptions"][0],
        )
        self.assertEqual(
            obj["example"]["enviromentOptions"],
            json.loads(self.before)["example"]["enviromentOptions"],
        )
        self.assertEqual(obj["example"]["winePrefix"], "/original/prefix")
        self.enable()
        self.assertEqual(len(json.loads(self.path.read_text())["example"]["wrapperOptions"]), 2)
        store = self.manager.store / "keep-cache"
        store.write_text("retained")
        self.assertEqual(self.manager.apply(self.game, False)["state"], "off")
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(store.read_text(), "retained")

    def test_unrelated_edits_survive_disable(self):
        self.enable()
        data = json.loads(self.path.read_text())
        data["other"] = "changed"
        self.write(self.path, data)
        self.manager.apply(self.game, False)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["other"], "changed")
        self.assertEqual(data["example"], json.loads(self.before)["example"])

    def test_changed_managed_field_refuses_without_writes(self):
        self.enable()
        data = json.loads(self.path.read_text())
        data["example"]["wrapperOptions"].append({"exe": "new-user-wrapper", "args": ""})
        self.write(self.path, data)
        before = self.path.read_bytes()
        self.assertEqual(self.manager.status(self.game)["state"], "changed")
        with self.assertRaisesRegex(ValueError, "later edits"):
            self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_inherited_heroic_wrappers_restored(self):
        data = json.loads(self.before)
        del data["example"]["wrapperOptions"]
        self.write(self.path, data)
        self.write(
            self.base / "config.json",
            {"defaultSettings": {"wrapperOptions": [{"exe": "inherited", "args": "--keep"}]}},
        )
        before = self.path.read_bytes()
        self.enable()
        self.assertEqual(
            json.loads(self.path.read_text())["example"]["wrapperOptions"][0]["exe"], "inherited"
        )
        self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_moved_game_does_not_stack_or_adopt_unowned_wrapper(self):
        self.enable()
        before = self.path.read_bytes()
        moved = dict(self.game, root=str(Path(self.game["root"]) / "moved"))
        Path(moved["root"]).mkdir()
        with self.assertRaisesRegex(ValueError, "moved entry"):
            self.manager.apply(moved, True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_managed_json_refused_after_enrollment(self):
        self.enable()
        data = self.path.read_text().replace('"other": 42', '"other": 42, "other": 43')
        self.path.write_text(data)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_text(), data)

    def test_missing_wrapper_reported_and_repaired(self):
        self.enable()
        self.manager.wrapper(self.game).unlink()
        self.assertEqual(self.manager.status(self.game)["state"], "repair")
        self.enable()
        self.assertEqual(self.manager.status(self.game)["state"], "configured")

    def test_invalid_receipt_refused(self):
        self.enable()
        path = self.manager.folder(self.game) / "receipt.json"
        data = json.loads(path.read_text())
        data["target"]["keys"] = ["example", "winePrefix"]
        self.write(path, data)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "managed launcher field"):
            self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_process_exit_after_profile_write_recovers(self):
        code = """
import importlib.util, json, os, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('child',sys.argv[1]); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
manager=m.Manager(Path(sys.argv[2])/'state',Path(sys.argv[2]),idle_check=lambda *_:None)
game=json.loads(sys.argv[3]);original=m.atomic
profile=Path(sys.argv[2])/'.config/heroic/GamesConfig/example.json'
def crash(path,data,mode=0o600):
    original(path,data,mode)
    if Path(path)==profile: os._exit(77)
m.atomic=crash
manager.apply(game,True)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(ROOT / "scripts/client-cache.py"),
                str(self.home),
                json.dumps(self.game),
            ],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.manager.status(self.game)["state"], "recovery")
        self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_running_launcher_defers_before_any_install(self):
        def busy(*_):
            raise ValueError("Close Heroic")

        self.manager.idle = busy
        with self.assertRaisesRegex(ValueError, "Close Heroic"):
            self.enable()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(self.manager.tools.exists())

    def test_profile_symlink_refused(self):
        other = self.path.with_suffix(".original")
        self.path.rename(other)
        self.path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "configuration file"):
            self.enable()
        self.assertEqual(other.read_bytes(), self.before)

    def test_failed_profile_write_rolls_back(self):
        original = m.atomic
        failed = False

        def write(path, data, mode=0o600):
            nonlocal failed
            if Path(path) == self.path and not failed:
                failed = True
                raise OSError("injected write failure")
            return original(path, data, mode)

        with patch.object(m, "atomic", write), self.assertRaisesRegex(OSError, "injected"):
            self.enable()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(self.manager.read_receipt(self.game))
        self.assertFalse((self.manager.folder(self.game) / "pending.json").exists())

    def test_interrupted_commit_recovery_preserves_unrelated_edits(self):
        original = m.atomic

        def crash(path, data, mode=0o600):
            original(path, data, mode)
            if Path(path) == self.path:
                raise KeyboardInterrupt()

        with patch.object(m, "atomic", crash), self.assertRaises(KeyboardInterrupt):
            self.enable()
        folder = self.manager.folder(self.game)
        self.assertTrue((folder / "pending.json").exists())
        data = json.loads(self.path.read_text())
        data["other"] = "later"
        self.write(self.path, data)
        self.manager.apply(self.game, False)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["other"], "later")
        self.assertEqual(data["example"], json.loads(self.before)["example"])
        self.assertFalse((folder / "pending.json").exists())

    def test_crash_after_commit_keeps_completed_enrollment(self):
        original = m.save

        def crash(path, value):
            original(path, value)
            if Path(path).name == "pending.json" and value.get("committed"):
                raise KeyboardInterrupt()

        with patch.object(m, "save", crash), self.assertRaises(KeyboardInterrupt):
            self.enable()
        self.manager.recover(self.game)
        self.assertEqual(self.manager.status(self.game)["state"], "configured")
        self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_missing_game_is_not_a_guessed_launcher(self):
        self.game["appid"] = "other"
        with self.assertRaisesRegex(ValueError, "uniquely"):
            self.enable()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_duplicate_heroic_locations_are_ambiguous(self):
        dest = self.home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"
        for suffix in ("gog_store/installed.json", "GamesConfig/example.json"):
            self.write(dest / suffix, json.loads((self.base / suffix).read_text()))
        with self.assertRaisesRegex(ValueError, "uniquely"):
            self.enable()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_flatpak_preflight_failure_preserves_settings(self):
        dest = self.home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"
        dest.parent.mkdir(parents=True)
        self.base.rename(dest)
        self.path = dest / "GamesConfig/example.json"

        def blocked(*_):
            raise ValueError("Flatpak missing access")

        self.manager.sandbox_check = blocked
        with self.assertRaisesRegex(ValueError, "Flatpak"):
            self.enable()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(self.manager.read_receipt(self.game))

    def test_real_wrapper_shared_store_and_record(self):
        self.enable()
        wrapper = self.manager.wrapper(self.game)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MESA_", "BC250_FSR4_"))}
        env["XDG_CACHE_HOME"] = str(self.home / "caller-cache")
        command = [
            str(wrapper),
            sys.executable,
            "-c",
            "import json,os;print(json.dumps(dict(os.environ)))",
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(
            Path(actual["MESA_SHADER_CACHE_DIR"]).joinpath("mesa_shader_cache").resolve(),
            self.manager.store / "mesa_shader_cache",
        )
        self.assertEqual(self.manager.status(self.game)["last_launch"]["result"], "prepared")
        env["MESA_SHADER_CACHE_DISABLE"] = "true"
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        self.assertNotIn("MESA_SHADER_CACHE_DIR", json.loads(result.stdout))
        self.assertEqual(self.manager.status(self.game)["last_launch"]["result"], "fallback")

    def test_two_games_share_store_keep_original_steam_reads(self):
        self.enable()
        other = dict(self.game, root=str(self.home / "games/Other"))
        Path(other["root"]).mkdir()
        self.manager.install_tools(other)
        roots = []
        for i, game in enumerate((self.game, other)):
            old = self.home / ("steam-cache-" + str(i))
            (old / "mesa_shader_cache_sf").mkdir(parents=True)
            original = old / "mesa_shader_cache_sf/foz_cache.foz"
            original.write_bytes(b"read-only Steam cache")
            env = {
                k: v for k, v in os.environ.items() if not k.startswith(("MESA_", "BC250_FSR4_"))
            }
            env["MESA_SHADER_CACHE_DIR"] = str(old)
            result = subprocess.run(
                [
                    str(self.manager.wrapper(game)),
                    sys.executable,
                    "-c",
                    "import os;print(os.environ['MESA_SHADER_CACHE_DIR'])",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            view = Path(result.stdout.strip())
            roots.append(view)
            self.assertEqual(
                (view / "mesa_shader_cache_sf").resolve(), old / "mesa_shader_cache_sf"
            )
            self.assertEqual(original.read_bytes(), b"read-only Steam cache")
        self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(
            (roots[0] / "mesa_shader_cache").resolve(), (roots[1] / "mesa_shader_cache").resolve()
        )

    def test_missing_runtime_helper_still_launches(self):
        self.enable()
        (self.manager.tools / "bc250-fsr4-cache").unlink()
        result = subprocess.run(
            [str(self.manager.wrapper(self.game)), sys.executable, "-c", "print('original game')"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "original game")

    def test_missing_python_runtime_still_launches(self):
        self.enable()
        result = subprocess.run(
            [str(self.manager.wrapper(self.game)), sys.executable, "-c", "print('original game')"],
            env={"PATH": "/nonexistent"},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "original game")

    def test_status_is_read_only(self):
        self.assertEqual(self.manager.status(self.game)["state"], "off")
        self.assertFalse(self.manager.state.exists())
        self.assertFalse(self.manager.tools.exists())

    def steam(self):
        self.game.update(platform="Steam", appid="123")
        base = self.home / ".local/share/Steam"
        root = base / "steamapps/common/Example"
        root.mkdir(parents=True)
        self.game["root"] = str(root)
        (base / "steamapps/appmanifest_123.acf").write_text('"AppState" { "installdir" "Example" }')
        self.path = base / "userdata/1/config/localconfig.vdf"
        self.path.parent.mkdir(parents=True)
        self.before = b'// keep comment\n"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "123" { "LaunchOptions" "env KEEP=1 /existing/wrapper %command% -flag" "Other" "unchanged" } } } } } }\n'
        self.path.write_bytes(self.before)
        return base

    def test_steam_options_preserved_and_exact_undo(self):
        self.steam()
        self.enable()
        value = m.Document(self.path.read_bytes()).get(m.STEAM_APPS + ["123", "LaunchOptions"])
        self.assertTrue(value.startswith("env KEEP=1 /existing/wrapper "))
        self.assertTrue(value.endswith("%command% -flag"))
        self.assertIn(b"// keep comment", self.path.read_bytes())
        self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_steam_most_recent_account_only(self):
        base = self.steam()
        other = base / "userdata/2/config/localconfig.vdf"
        other.parent.mkdir(parents=True)
        other.write_bytes(self.before)
        (base / "config").mkdir()
        (base / "config/loginusers.vdf").write_text(
            '"users" { "76561197960265729" { "MostRecent" "1" } "76561197960265730" { "MostRecent" "0" } }'
        )
        self.enable()
        self.assertEqual(other.read_bytes(), self.before)
        self.assertNotEqual(self.path.read_bytes(), self.before)

    def test_steam_multiple_accounts_without_identity_refused(self):
        base = self.steam()
        other = base / "userdata/2/config/localconfig.vdf"
        other.parent.mkdir(parents=True)
        other.write_bytes(self.before)
        with self.assertRaisesRegex(ValueError, "uniquely"):
            self.enable()

    def test_lutris_lossless_prefix_edit_and_undo(self):
        self.game.update(platform="Lutris", appid="example-42")
        exe = Path(self.game["root"]) / "game.exe"
        exe.write_bytes(b"fixture")
        self.path = self.home / ".local/share/lutris/games/example-42.yml"
        self.path.parent.mkdir(parents=True)
        self.before = (
            "# comment\ngame:\n  exe: "
            + json.dumps(str(exe))
            + '\nsystem:\n  prefix_command: "existing --keep"\n  env:\n    KEEP: yes\nrunner: wine\n'
        ).encode()
        self.path.write_bytes(self.before)
        self.enable()
        self.assertIn(b"  env:\n    KEEP: yes\n", self.path.read_bytes())
        self.manager.apply(self.game, False)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_yaml_complex_or_duplicate_targets_refused(self):
        for data in (
            b"system: {prefix_command: something}\n",
            b'system:\n  prefix_command: "one"\n  prefix_command: "two"\n',
            b"system:\n  prefix_command: |\n    one\n",
        ):
            with self.assertRaises(ValueError):
                m.field(data, {"format": "yaml", "keys": ["system", "prefix_command"]})


if __name__ == "__main__":
    unittest.main()
