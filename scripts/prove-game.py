#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record native FSR4 engagement from a live Linux game, without tracing it."""
import argparse
import configparser
import json
import os
from pathlib import Path
import time
import sys
sys.dont_write_bytecode = True
import driver


def mapped(pid, name):
    matches = []
    for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].endswith('/' + name):
            continue
        path = Path(fields[5].removeprefix('/run/host'))
        stat = path.stat()
        major, minor = (int(v, 16) for v in fields[3].split(':'))
        namespace_path = Path(f'/proc/{pid}/root')/fields[5].lstrip('/')
        if stat.st_ino != int(fields[4]) or not path.samefile(namespace_path):
            raise RuntimeError('Mapped file no longer matches its namespace file/inode: ' + str(path))
        item = {'name':name, 'host_path':str(path), 'sha256':driver.digest(path), 'inode':stat.st_ino, 'mapping_verified':True, 'namespace_samefile':True, 'maps_device':[major,minor], 'stat_device':[os.major(stat.st_dev),os.minor(stat.st_dev)]}
        if item not in matches:
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError('Expected one mapped ' + name + ', found ' + str(len(matches)))
    return matches[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--release-manifest', type=Path, required=True)
    p.add_argument('--engine-log', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True, help='The game-side OptiScaler.ini')
    args = p.parse_args()
    release = json.loads(args.release_manifest.read_text())
    policy = json.loads((Path(__file__).resolve().parents[1]/'v4/games.json').read_text())
    environment = dict(item.split(b'=', 1) for item in Path(f'/proc/{args.pid}/environ').read_bytes().split(b'\0') if b'=' in item)
    overrides = {k.decode():v.decode(errors='replace') for k,v in environment.items() if k.startswith(b'BC250_FSR4_')}
    if overrides:
        raise RuntimeError('Remove research overrides for a production-default proof: ' + json.dumps(overrides))
    library = mapped(args.pid, 'libvulkan_radeon.so')
    provider = mapped(args.pid, 'amdxcffx64.dll')
    opti = mapped(args.pid, 'OptiScaler.dll') if any('OptiScaler.dll' in line for line in Path(f'/proc/{args.pid}/maps').read_text().splitlines()) else mapped(args.pid, 'dxgi.dll')
    if library['sha256'] != release['driver_sha256']:
        raise RuntimeError('The game is not using the selected v4 release binary.')
    if provider['sha256'] != policy['provider_sha256'] or opti['sha256'] != policy['optiscaler']['dll_sha256']:
        raise RuntimeError('FSR provider or model-hook binary differs from the qualified input.')
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(args.config)
    if ini.get('FSR', 'Fsr4ForceModel', fallback='') != '2' or ini.get('FrameGen','Enabled',fallback='').lower() != 'false':
        raise RuntimeError('Expected INT8 model 2 and disabled frame generation.')
    log = args.engine_log.read_text(errors='replace')
    lines = [line for line in log.splitlines() if "Successfully initialized FSR Upscaling provider using version '4.1.1'" in line]
    if not lines:
        raise RuntimeError('No successful native FSR 4.1.1 initialization in the supplied game log.')
    print(json.dumps({'observed_epoch':time.time(), 'pid':args.pid, 'release':release['version'], 'driver':library, 'provider':provider, 'model_hook':opti, 'model':'INT8 (2)', 'frame_generation':False, 'production_overrides':overrides, 'initialization':lines[-2:], 'engine_log_sha256':driver.digest(args.engine_log), 'config_sha256':driver.digest(args.config), 'scope':'Native-route live mappings, configuration and initialization; inspect a current rendered frame as the visual proof. No per-frame tracing or performance claim.'}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        raise SystemExit('ERROR: ' + str(error))
