#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare explicitly selected, known game integrations; never edit Steam's library."""
import argparse
import configparser
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
import sys
sys.dont_write_bytecode = True
import driver

ROOT = Path(__file__).resolve().parents[1]
COMMON = {
    'Upscalers.Dx11Upscaler': 'ffx_12', 'Upscalers.Dx12Upscaler': 'ffx',
    'Upscalers.VulkanUpscaler': 'ffx_12', 'FrameGen.Enabled': 'false',
    'FSR.UpscalerIndex': '0', 'FSR.Fsr4ForceModel': '2',
    'FSR.Fsr4EnableWatermark': 'auto', 'FSR.FsrNonLinearColorSpace': 'false',
    'FSR.FsrNonLinearSRGB': 'auto', 'FSR.FsrNonLinearPQ': 'auto',
    'Hotfix.RestoreComputeSignature': 'false', 'Hotfix.RestoreGraphicSignature': 'false',
    'Hotfix.ExtendedStateRestore': 'false', 'Menu.DisableSplash': 'true',
    'Log.LogToFile': 'false',
}


def download(url, path, expected):
    if path.is_file() and driver.digest(path) == expected:
        return path
    temporary = path.with_suffix('.partial')
    with urllib.request.urlopen(url, timeout=90) as response, temporary.open('wb') as output:
        shutil.copyfileobj(response, output)
    if driver.digest(temporary) != expected:
        temporary.unlink()
        raise RuntimeError('Upstream download checksum changed; update/requalify the pin, do not bypass it: ' + url)
    temporary.replace(path)
    return path


def payload(state, policy):
    release = state/'payloads'/(policy['optiscaler']['sha256'][:16] + '-' + policy['optipatcher']['sha256'][:12])
    def valid(root):
        return (root/'OptiScaler.dll').is_file() and driver.digest(root/'OptiScaler.dll') == policy['optiscaler']['dll_sha256'] and (root/'OptiScaler/plugins/OptiPatcher.asi').is_file() and driver.digest(root/'OptiScaler/plugins/OptiPatcher.asi') == policy['optipatcher']['sha256']
    if release.exists():
        manifest = json.loads((release/'payload.json').read_text())
        if not valid(release) or any(driver.digest(release/p) != h for p,h in manifest['files'].items()):
            raise RuntimeError('Existing runtime payload was modified; preserving it for inspection.')
        return release
    cache = state/'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(policy['optiscaler']['url'], cache/'OptiScaler.7z', policy['optiscaler']['sha256'])
    patcher = download(policy['optipatcher']['url'], cache/'OptiPatcher.asi', policy['optipatcher']['sha256'])
    release.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=release.parent) as tmp:
        staged = Path(tmp)
        subprocess.run(['bsdtar', '-xf', str(archive), '--no-same-owner', '--no-same-permissions', '-C', str(staged)], check=True)
        if any(p.is_symlink() or not p.resolve().is_relative_to(staged.resolve()) for p in staged.rglob('*')):
            raise RuntimeError('Unexpected link in runtime archive.')
        (staged/'OptiScaler/plugins').mkdir(parents=True, exist_ok=True)
        shutil.copy2(patcher, staged/'OptiScaler/plugins/OptiPatcher.asi')
        if not valid(staged):
            raise RuntimeError('Extracted runtime does not match the pinned binaries.')
        driver.write_json(staged/'payload.json', {'files': {str(p.relative_to(staged)): driver.digest(p) for p in staged.rglob('*') if p.is_file()}})
        staged.rename(release)
    return release


def capture(path):
    if path.is_symlink():
        return {'type': 'symlink', 'target': os.readlink(path)}
    if path.is_file():
        return {'type': 'file', 'bytes_hex': path.read_bytes().hex()}
    if path.exists():
        raise RuntimeError('Refusing to replace a real directory or special file: ' + str(path))
    return {'type': 'absent'}


def restore(path, item):
    if item['type'] == 'file':
        driver.atomic(path, bytes.fromhex(item['bytes_hex']))
    elif item['type'] == 'absent':
        if path.exists() or path.is_symlink():
            path.unlink()
    elif item['type'] == 'symlink':
        temporary = path.with_name('.' + path.name + '.bc250-next')
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(item['target'])
        os.replace(temporary, path)


def require_stopped():
    if subprocess.run(['pgrep', '-x', 'steam'], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('Exit Steam and all games before changing runtime files.')


def install(args, state, policy):
    require_stopped()
    profile = next(p for p in policy['profiles'] if p['id'] == args.profile)
    game = args.game.expanduser().resolve()
    executable = game/profile['executable']
    if not executable.is_file():
        raise RuntimeError('Expected game executable was not found: ' + str(executable))
    target = executable.parent
    if 'native_fsr4' in profile:
        native = profile['native_fsr4']
        sdk = target/native['path']
        if not sdk.is_file() or hashlib.md5(sdk.read_bytes()).hexdigest() != native['md5']:
            raise RuntimeError('The game-native FSR SDK changed. Requalify this profile before injection.')
    runtime = payload(state, policy)
    paths = [target/profile['proxy'], target/'OptiScaler', target/'OptiScaler.ini']
    if paths[0].is_file() and driver.digest(paths[0]) != policy['optiscaler']['dll_sha256']:
        raise RuntimeError('An unrelated proxy DLL already exists; preserving it: ' + str(paths[0]))
    if paths[1].exists() and not paths[1].is_symlink():
        raise RuntimeError('An existing real OptiScaler directory needs manual reconciliation: ' + str(paths[1]))
    if paths[1].is_symlink():
        helper = paths[1]/'amd_fidelityfx_upscaler_dx12.dll'
        expected = runtime/'OptiScaler/amd_fidelityfx_upscaler_dx12.dll'
        if not helper.is_file() or driver.digest(helper) != driver.digest(expected):
            raise RuntimeError('Existing runtime directory is from a different version; preserving it.')
    before = [capture(p) for p in paths]
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(paths[2] if paths[2].is_file() else runtime/'OptiScaler.ini')
    values = {**COMMON, **profile['config']}
    if args.watermark:
        values['FSR.Fsr4EnableWatermark'] = 'true'
    for dotted, value in values.items():
        section, key = dotted.split('.', 1)
        if not ini.has_section(section):
            ini.add_section(section)
        ini.set(section, key, value)
    stream = io.StringIO()
    ini.write(stream, space_around_delimiters=False)
    after = [{'type':'symlink','target':str(runtime/'OptiScaler.dll')}, {'type':'symlink','target':str(runtime/'OptiScaler')}, {'type':'file','bytes_hex':stream.getvalue().encode().hex()}]
    changes = [{'path':str(p),'before':b,'after':a} for p,b,a in zip(paths,before,after)]
    transaction = {'schema':1,'profile':profile['id'],'state':'prepared','changes':changes}
    directory = state/'transactions'
    directory.mkdir(exist_ok=True)
    record = directory/(str(time.time_ns())+'.json')
    driver.write_json(record, transaction)
    written = []
    try:
        for item in changes:
            path = Path(item['path'])
            if capture(path) != item['before']:
                raise RuntimeError('Game file changed while staging: ' + str(path))
            restore(path, item['after'])
            written.append(item)
        transaction['state'] = 'active'
        driver.write_json(record, transaction)
    except BaseException:
        for item in reversed(written):
            restore(Path(item['path']),item['before'])
        transaction['state'] = 'aborted'
        driver.write_json(record, transaction)
        raise
    print('Configured ' + profile['title'] + '. Select ' + policy['proton'] + ' in Steam Compatibility.')
    print('Merge with existing Steam launch options:')
    print('PROTON_FSR4_UPGRADE=4.1.1 WINEDLLOVERRIDES=' + profile['proxy'].removesuffix('.dll') + '=n,b %command% ' + ' '.join(shlex.quote(a) for a in profile.get('launch_args',[])))
    print('Rollback record: ' + str(record))
    print('Game-side upscaler selection is still required; see docs/games.md.')


def rollback(path):
    require_stopped()
    transaction = json.loads(path.read_text())
    if transaction['state'] != 'active':
        raise RuntimeError('This game transaction is not active.')
    for item in transaction['changes']:
        if capture(Path(item['path'])) != item['after']:
            raise RuntimeError('A game file changed after setup; preserving it: ' + item['path'])
    for item in reversed(transaction['changes']):
        restore(Path(item['path']), item['before'])
    transaction['state'] = 'rolled-back'
    driver.write_json(path, transaction)
    print('Original game runtime files restored exactly.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=Path.home()/'.local/share/bc250-fsr4/game-runtime')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('fetch')
    i = sub.add_parser('install')
    i.add_argument('--game', type=Path, required=True)
    i.add_argument('--profile', choices=['deadzone','kcd2','control'], required=True)
    i.add_argument('--watermark', action='store_true', help='Temporary visual proof; normal installs leave it off')
    r = sub.add_parser('rollback')
    r.add_argument('record', type=Path)
    args = p.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Run game setup as your desktop user, without sudo.')
    state = args.state.expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state/'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = json.loads((ROOT/'v4/games.json').read_text())
        if args.command == 'fetch':
            print(payload(state, policy))
        elif args.command == 'install':
            install(args,state,policy)
        elif args.command == 'rollback':
            rollback(args.record.expanduser().resolve())


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, configparser.Error, subprocess.SubprocessError) as error:
        raise SystemExit('ERROR: ' + str(error))
