#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create the binary/source release archive from a completed pinned source build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import sys
sys.dont_write_bytecode = True
import driver

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, default=ROOT/'.work/native')
    p.add_argument('--output', type=Path, default=ROOT/'dist')
    p.add_argument('--label', default='linux-x86_64', help='ABI/build label, e.g. cachyos-x86_64')
    args = p.parse_args()
    if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.label):
        p.error('Use lowercase letters, digits, - or _ in the build label.')
    manifest = json.loads((ROOT/'v4/manifest.json').read_text())
    built = json.loads((args.work/'build-result.json').read_text())
    library = args.work/'build/src/amd/vulkan/libvulkan_radeon.so'
    if driver.digest(library) != built['sha256'] or built['manifest_sha256'] != driver.digest(ROOT/'v4/manifest.json'):
        raise RuntimeError('Build inputs or binary changed; refusing to package.')
    name = 'bc250-fsr4-v' + manifest['version'] + '-' + args.label
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output/(name + '.tar.gz')
    if archive.exists():
        raise RuntimeError('Output already exists; choose a new output directory or label.')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)/name
        (root/'lib').mkdir(parents=True)
        shutil.copy2(library, root/'lib/libvulkan_radeon.so')
        # Strip only the copied release, never mutate build evidence.
        subprocess.run(['strip', '--strip-unneeded', str(root/'lib/libvulkan_radeon.so')], check=True)
        for relative in ('scripts', 'v4', 'docs', 'tests'):
            shutil.copytree(ROOT/relative, root/relative, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for relative in ('.dockerignore', '.gitignore', 'README.md', 'requirements-build.txt', 'Dockerfile', 'build-anywhere.sh', 'install-v4.sh', 'run-bc250-fsr4.sh', 'build-bc250.sh', 'setup.sh', 'check.sh', 'LICENSE.new-code', 'THIRD_PARTY.md'):
            shutil.copy2(ROOT/relative, root/relative)
        (root/'licenses').mkdir()
        shutil.copy2(args.work/'mesa-26.2.2/docs/license.rst', root/'licenses/Mesa-license.rst')
        source_notice = args.work/'mesa-26.2.2/LICENSE'
        if source_notice.exists():
            shutil.copy2(source_notice, root/'licenses/Mesa-LICENSE')
        # Absolute build paths are deliberately omitted from public provenance.
        provenance = {key: value for key, value in built.items() if key != 'library'}
        provenance['unstripped_sha256'] = provenance.pop('sha256')
        provenance['dependencies'] = subprocess.check_output(['readelf', '-d', str(library)], text=True)
        provenance['symbol_versions'] = subprocess.check_output(['readelf', '--version-info', str(library)], text=True)
        (root/'build-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        release = {'schema': 1, 'version': manifest['version'], 'mesa': manifest['mesa'], 'architecture': 'x86_64', 'label': args.label, 'driver_sha256': driver.digest(root/'lib/libvulkan_radeon.so'), 'source_manifest_sha256': driver.digest(ROOT/'v4/manifest.json'), 'files': {str(f.relative_to(root)): driver.digest(f) for f in sorted(root.rglob('*')) if f.is_file()}}
        (root/'release.json').write_text(json.dumps(release, indent=2) + '\n')
        driver.verify_release(root)
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(root, arcname=name)
    Path(str(archive) + '.sha256').write_text(driver.digest(archive) + '  ' + archive.name + '\n')
    print(archive)


if __name__ == '__main__':
    main()
