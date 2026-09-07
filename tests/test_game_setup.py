# SPDX-License-Identifier: MIT
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
spec=importlib.util.spec_from_file_location('game_setup',Path(__file__).resolve().parents[1]/'scripts/game-setup.py')
game_setup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_setup)


class GameSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.game=self.root/'game with spaces'
        self.game.mkdir()
        (self.game/'game.exe').write_bytes(b'fixture executable')
        self.sdk=self.game/'fsr.dll'
        self.sdk.write_bytes(b'original SDK')
        self.ini=self.game/'OptiScaler.ini'
        self.original=b'; preserve exact original\n[User]\nSetting=keep-me\n'
        self.ini.write_bytes(self.original)
        self.runtime=self.root/'runtime'
        (self.runtime/'OptiScaler').mkdir(parents=True)
        (self.runtime/'OptiScaler.dll').write_bytes(b'known proxy')
        (self.runtime/'OptiScaler.ini').write_bytes(b'')
        self.state=self.root/'state'
        self.state.mkdir()
        self.policy={'proton':'fixture','optiscaler':{'dll_sha256':game_setup.driver.digest(self.runtime/'OptiScaler.dll')},'profiles':[{'id':'deadzone','title':'Fixture','executable':'game.exe','proxy':'dxgi.dll','config':{'Inputs.EnableFfxInputs':'false'},'native_fsr4':{'path':'fsr.dll','md5':hashlib.md5(self.sdk.read_bytes()).hexdigest()}}]}
        self.args=argparse.Namespace(profile='deadzone',game=self.game,watermark=False)
        self.stopped=patch.object(game_setup,'require_stopped')
        self.fetch=patch.object(game_setup,'payload',return_value=self.runtime)
        self.stopped.start();self.fetch.start()
        self.addCleanup(self.stopped.stop);self.addCleanup(self.fetch.stop);self.addCleanup(self.tmp.cleanup)

    def test_game_setup_and_exact_rollback(self):
        game_setup.install(self.args,self.state,self.policy)
        self.assertIn('Fsr4ForceModel=2',self.ini.read_text())
        self.assertIn('FsrNonLinearSRGB=auto',self.ini.read_text())
        self.assertIn('Setting=keep-me',self.ini.read_text())
        self.assertTrue((self.game/'dxgi.dll').is_symlink())
        record=next((self.state/'transactions').glob('*.json'))
        game_setup.rollback(record)
        self.assertEqual(self.ini.read_bytes(),self.original)
        self.assertFalse((self.game/'dxgi.dll').exists())
        self.assertEqual(self.sdk.read_bytes(),b'original SDK')

    def test_changed_sdk_fails_before_writes(self):
        self.sdk.write_bytes(b'game updated')
        with self.assertRaisesRegex(RuntimeError,'SDK changed'):
            game_setup.install(self.args,self.state,self.policy)
        self.assertEqual(self.ini.read_bytes(),self.original)
        self.assertFalse((self.state/'transactions').exists())

    def test_unrelated_proxy_is_preserved(self):
        (self.game/'dxgi.dll').write_bytes(b'other mod')
        with self.assertRaisesRegex(RuntimeError,'unrelated proxy'):
            game_setup.install(self.args,self.state,self.policy)
        self.assertEqual((self.game/'dxgi.dll').read_bytes(),b'other mod')
        self.assertEqual(self.ini.read_bytes(),self.original)

    def test_later_user_edit_blocks_rollback(self):
        game_setup.install(self.args,self.state,self.policy)
        self.ini.write_text('user changed settings')
        record=next((self.state/'transactions').glob('*.json'))
        with self.assertRaisesRegex(RuntimeError,'changed after setup'):
            game_setup.rollback(record)
        self.assertEqual(self.ini.read_text(),'user changed settings')
