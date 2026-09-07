#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
script_file=${BASH_SOURCE[0]:-}
local_driver=
if [[ -n "$script_file" && -f "$script_file" ]]; then
    root=$(cd -- "$(dirname -- "$script_file")" && pwd)
    if [[ -f "$root/scripts/driver.py" ]]; then local_driver="$root/scripts/driver.py"; fi
fi
BC250_FSR4_BOOTSTRAP_DRIVER="$local_driver" python3 - "$@" <<'PY'
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

VERSION = 'v4.0.0-rc1'
REPOSITORY = 'daniel-h-0/bc250-fsr4'
ASSET = 'bc250-fsr4-' + VERSION + '-cachyos-x86_64.tar.gz'
args = sys.argv[1:]
if '--help' in args or '-h' in args:
    print('Usage: install-v4.sh [ARCHIVE.tar.gz] [--upgrade-v3 | --upgrade-v3-icd PATH] [--sha256 DIGEST]')
    print('Without ARCHIVE, download the named release and checksum from ' + REPOSITORY + '.')
    print('BC250_FSR4_PREFIX selects a dedicated private installation directory.')
    raise SystemExit(0)
if sys.version_info < (3,12):
    raise SystemExit('Python 3.12 or newer is required. No installation changed.')
archive = Path(args.pop(0)).expanduser().resolve() if args and not args[0].startswith('-') else None
if '--upgrade-v3' in args:
    index = args.index('--upgrade-v3')
    legacy = Path.home()/'.local/share/bc250-fsr4/v3/radv-bc250-fsr4-v3.json'
    args[index:index+1] = ['--upgrade-v3-icd',str(legacy)]

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def get(url,path):
    with urllib.request.urlopen(url,timeout=90) as response,path.open('wb') as output:
        shutil.copyfileobj(response,output)

try:
    with tempfile.TemporaryDirectory(prefix='bc250-fsr4-download-') as tmp:
        temporary=Path(tmp)
        if archive is None:
            archive=temporary/ASSET
            base='https://github.com/'+REPOSITORY+'/releases/download/'+VERSION+'/'
            get(base+ASSET,archive)
            get(base+ASSET+'.sha256',Path(str(archive)+'.sha256'))
        if '--sha256' in args:
            expected=args[args.index('--sha256')+1]
        else:
            expected=Path(str(archive)+'.sha256').read_text().split()[0]
        if not re.fullmatch('[0-9a-fA-F]{64}',expected) or sha(archive).lower()!=expected.lower():
            raise RuntimeError('Archive SHA256 mismatch; nothing installed.')
        helper=os.environ.get('BC250_FSR4_BOOTSTRAP_DRIVER')
        if not helper:
            unpack=temporary/'unpack'
            unpack.mkdir()
            with tarfile.open(archive) as bundle:
                members=bundle.getmembers()
                if sum(m.size for m in members)>512*1024*1024:
                    raise RuntimeError('Oversized archive.')
                for member in members:
                    path=Path(member.name)
                    if not (member.isfile() or member.isdir()) or path.is_absolute() or '..' in path.parts:
                        raise RuntimeError('Unsafe archive member.')
                bundle.extractall(unpack,filter='data')
            roots=list(unpack.iterdir())
            if len(roots)!=1 or not (roots[0]/'scripts/driver.py').is_file():
                raise RuntimeError('Release installer was not found in archive.')
            helper=str(roots[0]/'scripts/driver.py')
        command=[sys.executable,helper]
        if os.environ.get('BC250_FSR4_PREFIX'):
            command+=['--prefix',os.environ['BC250_FSR4_PREFIX']]
        command+=['install',str(archive),*args]
        subprocess.run(command,check=True)
except (OSError,ValueError,IndexError,RuntimeError,tarfile.TarError,subprocess.SubprocessError) as error:
    raise SystemExit('ERROR: '+str(error)+'\nIf the named GitHub release is not published yet, use the README source-build route.')
PY
