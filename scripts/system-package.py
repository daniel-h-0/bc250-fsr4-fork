#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build/install Arch packages with the distribution's original RADV package metadata."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
sys.dont_write_bytecode = True
import driver

ROOT = Path(__file__).resolve().parents[1]


def call(command, **kwargs):
    return subprocess.check_output([str(x) for x in command], text=True, **kwargs)


def package_info(path):
    fields = {}
    for line in call(['bsdtar', '-xOf', path, '.PKGINFO']).splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            fields.setdefault(key, []).append(value)
    return fields


def quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def array(values):
    return '(' + ' '.join(quote(v) for v in values) + ')'


def build(args):
    if os.geteuid() == 0:
        raise RuntimeError('Build packages as a normal user (makepkg refuses root).')
    archive = args.archive.expanduser().resolve()
    base = args.base_package.expanduser().resolve()
    if '.INSTALL' in call(['bsdtar','-tf',base]).splitlines():
        raise RuntimeError('Base package has install scriptlets; this overlay route cannot safely preserve their semantics.')
    info = package_info(base)
    if info['pkgname'] != ['vulkan-radeon'] or info['arch'] != ['x86_64']:
        raise RuntimeError('Base archive must be your x86_64 vulkan-radeon package.')
    version = info['pkgver'][0]
    epoch, sep, tail = version.partition(':')
    if not sep:
        tail, epoch = epoch, '0'
    mesa, rel = tail.rsplit('-', 1)
    checksum = args.sha256 or Path(str(archive)+'.sha256').read_text().split()[0]
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        root, release = driver.extract_verified(archive, stage, checksum)
        if mesa != release['mesa']:
            raise RuntimeError('Mesa version mismatch: rebuild/rebase before replacing another Mesa version.')
        driver.probe(root/'lib/libvulkan_radeon.so', stage)
        shutil.copy2(root/'lib/libvulkan_radeon.so', output/'v4.so')
    # Keep the original package intact for both metadata and an exact rollback.
    shutil.copy2(base, output/'base.pkg.tar.zst')
    # Verify the original library directly, independently of the package name.
    old_library = subprocess.check_output(['bsdtar', '-xOf', str(base), 'usr/lib/libvulkan_radeon.so'])
    metadata = {'schema': 1, 'version': release['version'], 'mesa': mesa,
                'driver_sha256': release['driver_sha256'], 'base_version': version,
                'base_package_sha256': driver.digest(base),
                'base_driver_sha256': __import__('hashlib').sha256(old_library).hexdigest(),
                'library': '/usr/lib/libvulkan_radeon.so',
                'rollback_package': '/usr/share/bc250-fsr4-v4/rollback/base.pkg.tar.zst',
                'scope': '64-bit only; distribution lib32-vulkan-radeon is untouched'}
    (output/'system.json').write_text(json.dumps(metadata, indent=2)+'\n')
    shutil.copy2(ROOT/'scripts/system-control.py', output/'system-control.py')
    shutil.copy2(ROOT/'LICENSE.new-code', output/'LICENSE.new-code')
    (output/'95-bc250-fsr4-v4.hook').write_text('''[Trigger]
Operation = Install
Operation = Upgrade
Operation = Remove
Type = Package
Target = vulkan-radeon

[Action]
Description = Check BC250 FSR4 v4 driver identity
When = PostTransaction
Exec = /usr/bin/bc250-fsr4 check-update
''')
    files = ['base.pkg.tar.zst', 'v4.so', 'system.json', 'system-control.py', '95-bc250-fsr4-v4.hook', 'LICENSE.new-code']
    text = f'''pkgbase=bc250-fsr4-v4
pkgname=(vulkan-radeon bc250-fsr4-v4)
pkgver={mesa}
pkgrel={rel}
epoch={epoch}
arch=(x86_64)
url='https://github.com/daniel-h-0/bc250-fsr4-fork'
license=('MIT AND BSD-3-Clause AND SGI-B-2.0')
options=('!strip' '!debug')
source={array(files)}
noextract=('base.pkg.tar.zst')
sha256sums={array([driver.digest(output/f) for f in files])}

package_vulkan-radeon() {{
  pkgdesc='Mesa RADV with BC250 FSR4 v4 optimizations (64-bit)'
'''
    for field, key in [('depend','depends'), ('optdepend','optdepends'), ('provides','provides'), ('replaces','replaces'), ('conflict','conflicts')]:
        if field in info:
            text += f'  {key}={array(info[field])}\n'
    text += '''  bsdtar -xf "$srcdir/base.pkg.tar.zst" -C "$pkgdir" --exclude=.PKGINFO --exclude=.BUILDINFO --exclude=.MTREE --exclude=.INSTALL
  install -m755 "$srcdir/v4.so" "$pkgdir/usr/lib/libvulkan_radeon.so"
}

package_bc250-fsr4-v4() {
  pkgdesc='BC250 FSR4 v4 status, package update notice and exact rollback'
  depends=('python' 'pacman' 'vulkan-radeon')
  conflicts=('bc250-fsr4-integration')
  install -Dm755 "$srcdir/system-control.py" "$pkgdir/usr/bin/bc250-fsr4"
  ln -s bc250-fsr4 "$pkgdir/usr/bin/bc250-fsr4-driver"
  install -Dm644 "$srcdir/system.json" "$pkgdir/usr/share/bc250-fsr4-v4/system.json"
  install -Dm644 "$srcdir/base.pkg.tar.zst" "$pkgdir/usr/share/bc250-fsr4-v4/rollback/base.pkg.tar.zst"
  install -Dm644 "$srcdir/95-bc250-fsr4-v4.hook" "$pkgdir/usr/share/libalpm/hooks/95-bc250-fsr4-v4.hook"
  install -Dm644 "$srcdir/LICENSE.new-code" "$pkgdir/usr/share/licenses/bc250-fsr4-v4/LICENSE"
}
'''
    (output/'PKGBUILD').write_text(text)
    with (output/'makepkg.log').open('w') as log:
        subprocess.run(['makepkg', '--nodeps', '--noconfirm'], cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True)
    packages = sorted(p for p in output.glob('*.pkg.tar.zst') if p.name != 'base.pkg.tar.zst')
    if len(packages) != 2:
        raise RuntimeError('Expected driver and control packages.')
    report = {'metadata': metadata, 'packages': {p.name: driver.digest(p) for p in packages}}
    (output/'packages.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Packages ready. Review ' + str(output/'packages.json'))
    print('Install: python3 scripts/system-package.py install ' + __import__('shlex').quote(str(output)))


def install(args):
    root = args.directory.expanduser().resolve()
    report = json.loads((root/'packages.json').read_text())
    metadata = report['metadata']
    if subprocess.run(['pgrep', '-x', 'steam'], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('Exit Steam and all games before replacing the system Vulkan driver.')
    if call(['pacman', '-Q', 'vulkan-radeon']).strip() != 'vulkan-radeon ' + metadata['base_version']:
        raise RuntimeError('Installed RADV version differs from the retained base package. Regenerate from the matching archive; do not force it.')
    if driver.digest(metadata['library']) != metadata['base_driver_sha256']:
        raise RuntimeError('Installed driver does not match the rollback archive. Preserve/reconcile the local modification first.')
    packages = []
    for name, expected in report['packages'].items():
        if Path(name).name != name or driver.digest(root/name) != expected:
            raise RuntimeError('Package checksum mismatch: ' + name)
        packages.append(str(root/name))
    # The user's normal pacman prompt reviews the concrete package transaction.
    subprocess.run(['sudo', 'pacman', '-U', *packages], check=True)
    subprocess.run(['/usr/bin/bc250-fsr4', 'status'], check=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('archive', type=Path)
    b.add_argument('--sha256')
    b.add_argument('--base-package', type=Path, required=True)
    b.add_argument('--output', type=Path, required=True)
    i = sub.add_parser('install')
    i.add_argument('directory', type=Path)
    args = p.parse_args()
    try:
        build(args) if args.command == 'build' else install(args)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        p.exit(1, 'ERROR: ' + str(error) + '\n')
