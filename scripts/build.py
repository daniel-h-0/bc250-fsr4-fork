#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproduce the patched source in a new directory; optionally build private RADV."""
import argparse, hashlib, json, os, shutil, subprocess, tarfile, urllib.request, shlex
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"v4"
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(args,**kwargs):
    subprocess.run([str(x) for x in args],check=True,**kwargs)
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mesa-archive',type=Path,help='Optional verified offline Mesa archive; downloads the pinned input otherwise')
    ap.add_argument('--work',type=Path,default=ROOT.parent/'.work/native')
    ap.add_argument('--prepare-only',action='store_true')
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--jobs',type=int,default=4)
    ap.add_argument('--arch',choices=['64'],default='64')
    a=ap.parse_args()
    if a.jobs<1:ap.error('--jobs must be positive')
    m=json.loads((ROOT/'manifest.json').read_text())
    archive=(a.mesa_archive or ROOT.parent/'.work/downloads'/m['base_archive']['name']).expanduser().resolve()
    if not archive.exists() and not a.mesa_archive:
        archive.parent.mkdir(parents=True,exist_ok=True)
        temporary=archive.with_suffix('.partial.'+str(os.getpid()))
        with urllib.request.urlopen(m['base_archive']['url'],timeout=120) as response,temporary.open('wb') as output:
            shutil.copyfileobj(response,output)
        if sha(temporary)!=m['base_archive']['sha256']:
            temporary.unlink()
            raise SystemExit('Downloaded Mesa archive failed SHA256 validation.')
        temporary.replace(archive)
    if sha(archive)!=m['base_archive']['sha256']:raise SystemExit('The Mesa source archive does not match the pinned 26.2.2 input.')
    for rel,h in m['source_inputs'].items():
        if sha(ROOT/rel)!=h:raise SystemExit('Bundle source input changed: '+rel)
    work=a.work.expanduser().resolve();source=work/'mesa-26.2.2';build=work/'build'
    env=os.environ.copy()
    env.setdefault('CFLAGS','-O2 -march=x86-64 -mtune=generic')
    env.setdefault('CXXFLAGS','-O2 -march=x86-64 -mtune=generic')
    compiler=subprocess.check_output(shlex.split(env.get('CC','cc'))+['--version'],text=True).splitlines()[0]
    build_environment={key:env.get(key) for key in ('CC','CXX','CFLAGS','CXXFLAGS','LDFLAGS','PKG_CONFIG_PATH','PKG_CONFIG_LIBDIR')}
    inputs=dict(base_archive=m['base_archive'],source_inputs=m['source_inputs'],sources=m['sources'],arch=a.arch,build_environment=build_environment,compiler=compiler,recipe_sha256=sha(__file__))
    if a.resume:
        if json.loads((work/'inputs.json').read_text())!=inputs:raise SystemExit('Resume inputs changed; use a new work directory.')
    else:
        work.mkdir(parents=True,exist_ok=False)
        with tarfile.open(archive) as t:t.extractall(work,filter='data')
        for rel in m['patch_order']:run(['patch','--batch','--fuzz=0','-p1','-i',ROOT/rel],cwd=source)
        cache=source/'subprojects/packagecache';cache.mkdir(exist_ok=True)
        shutil.copyfile(ROOT/'source-dependencies/wayland-protocols-1.41.tar.xz',cache/'wayland-protocols-1.41.tar.xz')
        (work/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    for rel,h in m['sources'].items():
        if sha(source/rel)!=h:raise SystemExit('Materialized source mismatch: '+rel)
    print('PASS: all 15 changed source files match the production source.',flush=True)
    if a.prepare_only:return
    options=['--prefix=/usr','--libdir='+('lib32' if a.arch=='32' else 'lib'),'--buildtype=release','--wrap-mode=nodownload','-Db_ndebug=true','-Dvulkan-drivers=amd','-Dgallium-drivers=','-Dllvm=disabled','-Dplatforms=x11,wayland','-Dglx=disabled','-Degl=disabled','-Dgbm=disabled','-Dgles1=disabled','-Dgles2=disabled','-Dopengl=false','-Dvideo-codecs=','-Dvalgrind=disabled','-Dbuild-tests=false','-Dglvnd=disabled','-Dradv-u_trace=false']
    if not (build/'build.ninja').exists():run(['meson','setup',build,source,*options],env=env)
    run(['ninja','-C',build,'-j',a.jobs,'src/amd/vulkan/libvulkan_radeon.so'],env=env)
    lib=build/'src/amd/vulkan/libvulkan_radeon.so'
    (work/('icd'+a.arch+'.json')).write_text(json.dumps(dict(file_format_version='1.0.0',ICD=dict(library_path=str(lib),api_version='1.4.354')),indent=2)+'\n')
    (work/'build-result.json').write_text(json.dumps(dict(sha256=sha(lib),library=str(lib),options=options,cflags=env.get('CFLAGS'),cxxflags=env.get('CXXFLAGS'),compiler=compiler,build_environment=build_environment,recipe_sha256=sha(__file__),manifest_sha256=sha(ROOT/'manifest.json'),note='Fresh source build; see release qualification before deployment.'),indent=2)+'\n')
    print('Built a private library:',lib)
if __name__=='__main__':main()
