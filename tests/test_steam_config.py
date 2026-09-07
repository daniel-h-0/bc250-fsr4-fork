# SPDX-License-Identifier: MIT
"""Native Steam discovery and field edits, using only isolated local fixtures."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import steam_config as steam


class VdfTests(unittest.TestCase):
    def test_replacement_preserves_all_other_bytes_comments_case_and_escapes(self):
        original = b'\xef\xbb\xbf// comment\r\n"Root"\r\n{\r\n  "MiXeD"  "old" // keep comment\r\n  "Other" "C:\\\\path\\\\file \\"quoted\\""\r\n}\r\n'
        document = steam.Document(original)
        self.assertEqual(document.get(("root", "mixed")), "old")
        updated = document.set(("ROOT", "mixed"), 'new "quoted" \\ path')
        expected = original.replace(b'"old"', b'"new \\"quoted\\" \\\\ path"')
        self.assertEqual(updated, expected)
        self.assertEqual(steam.Document(updated).get(("Root", "MiXeD")), 'new "quoted" \\ path')
        self.assertEqual(document.set(("root", "mixed"), "old"), original)

    def test_missing_nested_objects_insert_and_parse(self):
        for original in (b'"root" {}', b'"root"\n{\n}\n', b'"root" {"keep" "yes"}\n'):
            with self.subTest(original=original):
                updated = steam.Document(original).set(
                    ("root", "apps", "123", "LaunchOptions"), "%command%"
                )
                document = steam.Document(updated)
                self.assertEqual(
                    document.get(("root", "apps", "123", "LaunchOptions")), "%command%"
                )
                self.assertEqual(
                    document.get(("root", "keep")), steam.Document(original).get(("root", "keep"))
                )

    def test_duplicate_selected_key_is_refused_without_rewriting_unrelated_duplicates(self):
        original = b'"root" { "Other" "1" "other" "2" "Selected" "safe" }'
        self.assertIn(
            b'"Other" "1" "other" "2"', steam.Document(original).set(("root", "selected"), "new")
        )
        with self.assertRaisesRegex(steam.SteamConfigError, "Duplicate target"):
            steam.Document(original).set(("root", "other"), "new")

    def test_unsupported_or_malformed_text_refuses(self):
        for data in (
            b'"root" {',
            b'"root" { "value" }',
            b'"unterminated',
            b"\x00binary",
            b'#base "other.vdf"',
            b'"#base" "other.vdf"',
            b'"x" "y" [$LINUX]',
        ):
            with self.subTest(data=data), self.assertRaises(steam.SteamConfigError):
                steam.Document(data)

    def test_non_utf8_unrelated_value_survives(self):
        original = b'"Root" { "keep" "\xff" "change" "old" }'
        updated = steam.Document(original).set(("Root", "change"), "new")
        self.assertEqual(updated, original.replace(b'"old"', b'"new"'))

    def test_rollback_is_byte_exact_when_no_later_edit(self):
        original = b'// retain layout\n"root" {"other" "keep"}\n'
        change = steam.plan_change(original, {("root", "new", "field"): "added"})
        self.assertEqual(
            steam.restore_settings(bytes.fromhex(change["after_hex"]), change), original
        )

    def test_rollback_preserves_later_unrelated_values_and_created_siblings(self):
        original = b'"root" { "existing" "old" "other" "keep" }'
        change = steam.plan_change(
            original, {("root", "existing"): "managed", ("root", "new", "owned"): "added"}
        )
        current = steam.Document(bytes.fromhex(change["after_hex"])).set(
            ("root", "other"), "later-user-value"
        )
        current = steam.Document(current).set(("root", "new", "user-sibling"), "keep-too")
        restored = steam.Document(steam.restore_settings(current, change))
        self.assertEqual(restored.get(("root", "existing")), "old")
        self.assertEqual(restored.get(("root", "other")), "later-user-value")
        self.assertEqual(restored.get(("root", "new", "user-sibling")), "keep-too")
        self.assertIsNone(restored.get(("root", "new", "owned")))

    def test_rollback_prunes_only_new_empty_objects(self):
        original = b'"root" { "other" "keep" }'
        change = steam.plan_change(original, {("root", "new", "owned"): "added"})
        current = steam.Document(bytes.fromhex(change["after_hex"])).set(("root", "other"), "later")
        restored = steam.Document(steam.restore_settings(current, change))
        self.assertFalse(restored.has_object(("root", "new")))
        self.assertEqual(restored.get(("root", "other")), "later")

    def test_owned_edit_blocks_rollback_and_recovery(self):
        change = steam.plan_change(b'"root" { "owned" "before" }', {("root", "owned"): "managed"})
        current = steam.Document(bytes.fromhex(change["after_hex"])).set(
            ("root", "owned"), "user-edit"
        )
        for recover in (False, True):
            with (
                self.subTest(recover=recover),
                self.assertRaisesRegex(steam.SteamConfigError, "managed Steam field changed"),
            ):
                steam.restore_settings(current, change, recover=recover)

    def test_partial_transaction_recovery_accepts_known_before_and_after(self):
        original = b'"root" { "one" "before-one" "two" "before-two" }'
        change = steam.plan_change(
            original, {("root", "one"): "after-one", ("root", "two"): "after-two"}
        )
        partial = steam.Document(original).set(("root", "one"), "after-one")
        self.assertEqual(steam.restore_settings(partial, change, recover=True), original)


class LaunchOptionsTests(unittest.TestCase):
    def test_old_v3_options_merge_without_losing_wrapper_wine_overrides_or_arguments(self):
        original = "MANGOHUD_CONFIG='fps,frametime' VK_ICD_FILENAMES=/old/v3.json PROTON_FSR4_UPGRADE=old WINEDLLOVERRIDES='dxgi,d3d11=b;winmm=n,b' gamemoderun %command% -keep"
        environment = {
            "PROTON_FSR4_UPGRADE": "4.1.1",
            "WINEDLLOVERRIDES": "dxgi=n,b",
            "VK_DRIVER_FILES": "/new path/current.json",
        }
        updated = steam.compose_launch_options(
            original, environment, ["-dx12"], remove_environment=("VK_ICD_FILENAMES",)
        )
        self.assertNotIn("VK_ICD_FILENAMES", updated)
        self.assertIn("MANGOHUD_CONFIG='fps,frametime'", updated)
        self.assertIn("gamemoderun %command% -keep -dx12", updated)
        tokens = shlex.split(updated)
        self.assertIn("WINEDLLOVERRIDES=d3d11=b;winmm=n,b;dxgi=n,b", tokens)
        self.assertIn("VK_DRIVER_FILES=/new path/current.json", tokens)
        self.assertEqual(
            steam.compose_launch_options(
                updated, environment, ["-dx12"], remove_environment=("VK_ICD_FILENAMES",)
            ),
            updated,
        )

    def test_plain_game_arguments_gain_command_placeholder(self):
        result = steam.compose_launch_options(
            '-dx12 --title "hello world"', {"PROTON_FSR4_UPGRADE": "4.1.1"}, ["-dx12"]
        )
        self.assertEqual(result, 'PROTON_FSR4_UPGRADE=4.1.1 %command% -dx12 --title "hello world"')

    def test_original_v3_home_expansion_can_be_replaced_or_removed_without_evaluation(self):
        original = (
            'VK_DRIVER_FILES="$HOME/.local/share/bc250-fsr4/v3/radv-bc250-fsr4-v3.json" %command%'
        )
        private = steam.compose_launch_options(
            original, {"VK_DRIVER_FILES": "/new private/current.json"}
        )
        self.assertEqual(private, "VK_DRIVER_FILES='/new private/current.json' %command%")
        system = steam.compose_launch_options(
            original, {"PROTON_FSR4_UPGRADE": "4.1.1"}, remove_environment=("VK_DRIVER_FILES",)
        )
        self.assertNotIn("VK_DRIVER_FILES", system)
        self.assertNotIn("$HOME", system)
        self.assertEqual(shlex.split(system), ["PROTON_FSR4_UPGRADE=4.1.1", "%command%"])

    def test_assignment_values_with_spaces_reach_executed_command(self):
        environment = {
            "BC250_TEST_VALUE": "path with spaces",
            "WINEDLLOVERRIDES": "winmm=n,b;dxgi=n,b",
        }
        result = steam.compose_launch_options("", environment)
        code = "import json,os; print(json.dumps({k:os.environ[k] for k in ['BC250_TEST_VALUE','WINEDLLOVERRIDES']}))"
        command = result.replace(
            "%command%", shlex.quote(sys.executable) + " -c " + shlex.quote(code)
        )
        completed = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=True
        )
        self.assertEqual(json.loads(completed.stdout), environment)

    def test_whole_quoted_assignment_is_repaired_as_valid_shell_assignment(self):
        value = "path with spaces"
        result = steam.compose_launch_options(
            "'BC250_TEST_VALUE=path with spaces' %command%", {"BC250_TEST_VALUE": value}
        )
        self.assertEqual(result, "BC250_TEST_VALUE='path with spaces' %command%")
        code = "import os; print(os.environ['BC250_TEST_VALUE'])"
        command = result.replace(
            "%command%", shlex.quote(sys.executable) + " -c " + shlex.quote(code)
        )
        completed = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=True
        )
        self.assertEqual(completed.stdout.strip(), value)

    def test_ambiguous_shell_constructs_refuse(self):
        for original in (
            "%command% && echo later",
            'bash -c "%command%"',
            "FOO=$(touch nope) %command%",
            "env -u WINEDLLOVERRIDES %command%",
            "env -i %command%",
            'WINEDLLOVERRIDES="$EXISTING" %command%',
            "VK_DRIVER_FILES=one VK_DRIVER_FILES=two %command%",
        ):
            with self.subTest(original=original), self.assertRaises(steam.SteamConfigError):
                steam.compose_launch_options(
                    original, {"VK_DRIVER_FILES": "/new", "WINEDLLOVERRIDES": "dxgi=n,b"}
                )

    def test_unrelated_variable_expansion_and_quoted_semicolon_remain_unchanged(self):
        original = 'MANGOHUD_CONFIG="fps;frametime" "$HOME/bin/save-wrapper" %command%'
        result = steam.compose_launch_options(original, {"PROTON_FSR4_UPGRADE": "4.1.1"})
        self.assertTrue(result.endswith(original))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / ".local/share/Steam"
        (self.root / "config").mkdir(parents=True)
        (self.root / "steamapps").mkdir()
        (self.home / ".steam").mkdir()
        (self.home / ".steam/root").symlink_to(self.root)
        self.config = self.root / "config/config.vdf"
        self.config.write_text(
            '"InstallConfigStore" { "Software" { "Valve" { "Steam" { "CompatToolMapping" { "0" { "name" "default-proton" } } "unrelated" "keep" } } } }'
        )
        for account in ("42", "43"):
            path = self.root / "userdata" / account / "config/localconfig.vdf"
            path.parent.mkdir(parents=True)
            path.write_text(
                '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "123" { "LaunchOptions" "mangohud %command% -keep" } "999" { "LaunchOptions" "unrelated-game" } } } } } }'
            )
        self.profiles = [
            {"id": "fixture", "appid": "123", "title": "Fixture game", "executable": "bin/game.exe"}
        ]

    def install_fixture(self, library):
        game = library / "steamapps/common/game with spaces"
        (game / "bin").mkdir(parents=True)
        (game / "bin/game.exe").write_bytes(b"fixture executable")
        (library / "steamapps/appmanifest_123.acf").write_text(
            '"AppState" { "appid" "123" "name" "Fixture" "installdir" "game with spaces" "StateFlags" "4" }'
        )
        return game

    def test_native_roots_deduplicate_symlink_aliases(self):
        self.assertEqual(steam.discover_roots(self.home), [self.root])

    def test_modern_and_legacy_external_libraries_are_discovered(self):
        library = self.home / "second library"
        expected = self.install_fixture(library)
        folders = self.root / "steamapps/libraryfolders.vdf"
        for entry in (
            '"1" { "path" ' + json.dumps(str(library)) + ' "apps" {"123" "1"} }',
            '"1" ' + json.dumps(str(library)),
        ):
            with self.subTest(entry=entry):
                folders.write_text('"libraryfolders" { ' + entry + " }")
                games = steam.discover_games(self.root, self.profiles)
                self.assertEqual(len(games), 1)
                self.assertEqual(games[0]["game"], expected)
                self.assertEqual(games[0]["profile"], "fixture")

    def test_uninstalled_or_incomplete_game_is_not_offered(self):
        self.install_fixture(self.root)
        manifest = self.root / "steamapps/appmanifest_123.acf"
        manifest.write_text(manifest.read_text().replace('"StateFlags" "4"', '"StateFlags" "2"'))
        self.assertEqual(steam.discover_games(self.root, self.profiles), [])

    def test_two_installed_copies_refuse_automatic_selection(self):
        self.install_fixture(self.root)
        library = self.home / "second library"
        self.install_fixture(library)
        (self.root / "steamapps/libraryfolders.vdf").write_text(
            '"libraryfolders" { "1" { "path" ' + json.dumps(str(library)) + " } }"
        )
        with self.assertRaisesRegex(steam.SteamConfigError, "Multiple installed copies"):
            steam.discover_games(self.root, self.profiles)

    def test_autologin_selects_account_without_mostrecent_field(self):
        first, second = 76561197960265728 + 42, 76561197960265728 + 43
        (self.root / "config/loginusers.vdf").write_text(
            f'"users" {{ "{first}" {{ "PersonaName" "Fixture A" "AutoLogin" "0" }} "{second}" {{ "PersonaName" "Fixture B" "AutoLogin" "1" "RememberPassword" "secret-unused" }} }}'
        )
        accounts = steam.discover_accounts(self.root)
        self.assertEqual([account["id"] for account in accounts if account["preferred"]], ["43"])
        self.assertEqual(accounts[1]["label"], "Fixture B")
        self.assertNotIn("secret-unused", repr(accounts))

    def test_plan_targets_one_account_and_never_changes_global_default_or_files(self):
        paths = [
            self.config,
            self.root / "userdata/42/config/localconfig.vdf",
            self.root / "userdata/43/config/localconfig.vdf",
        ]
        originals = {path: path.read_bytes() for path in paths}
        changes = steam.plan_settings(
            self.root, ["42"], "123", {"WINEDLLOVERRIDES": "dxgi=n,b"}, "pinned-proton"
        )
        self.assertEqual({change["path"] for change in changes}, {str(paths[0]), str(paths[1])})
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        global_change = next(change for change in changes if change["path"] == str(self.config))
        global_doc = steam.Document(bytes.fromhex(global_change["after_hex"]))
        mapping = ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping")
        self.assertEqual(global_doc.get((*mapping, "0", "name")), "default-proton")
        self.assertEqual(global_doc.get((*mapping, "123", "name")), "pinned-proton")

    def test_symlinked_config_refuses_before_any_plan_is_returned(self):
        original = self.config.read_bytes()
        target = self.home / "relocated-config.vdf"
        target.write_bytes(original)
        self.config.unlink()
        self.config.symlink_to(target)
        with self.assertRaisesRegex(steam.SteamConfigError, "regular file"):
            steam.plan_settings(self.root, ["42"], "123", {}, "pinned-proton")
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(target.read_bytes(), original)

    def test_global_appid_zero_is_refused(self):
        with self.assertRaisesRegex(steam.SteamConfigError, "AppID 0"):
            steam.plan_settings(self.root, ["42"], "0", {}, "pinned-proton")


if __name__ == "__main__":
    unittest.main()
