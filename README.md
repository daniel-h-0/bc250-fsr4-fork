# BC250 FSR4 — portable DLL, RC7

**RC7 puts the v4 FSR 4.1.1 INT8 optimizations in one Windows x64 DLL.**
Use it with a compatible native FidelityFX game or an ordinary OptiScaler
installation. A custom Mesa build, custom Proton tool and BC250 installer
are no longer part of this installation.

This is the **4.0.0-rc7 release candidate**; the DLL identifies itself as
**4.1.1r7**. Its exact bytes are tested on BC250/Linux. Windows and other
GPUs remain unqualified. Read the [compatibility results](docs/portable-dll-rc7.md)
before choosing a route. RC6 remains available with its
[existing installation and recovery guide](docs/legacy-rc6.md).

## Install

Extract `bc250-fsr4-dll-4.0.0-rc7.zip` (or the smaller `.tar.xz` archive).
It contains one DLL, instructions, checksums and notices. Close the game and
back up any file you replace.

**Already using OptiScaler:** replace
`OptiScaler/amd_fidelityfx_upscaler_dx12.dll` with the RC7 DLL. Select the
FFX/FSR4 backend and INT8 model 2. The [short installation guide](dll/INSTALL.md)
has the exact settings and the ordinary Proton launch option.

**Native FidelityFX game:** replace its compatible upscaler DLL. Native
loader versions differ: Deadzone Rogue uses the upscaler filename, while
the tested KCD2 integration requires the same bytes under the loader filename.
Follow the [filename and loader notes](docs/portable-dll-rc7.md#native-game-loaders).

To undo a DLL replacement, close the game and restore the backed-up file.
Game updates may replace it. Keep one upscaler integration active per game;
the [RC6 transition](docs/portable-dll-rc7.md#coming-from-rc6) explains how to
switch an existing Steam selection without deleting saves or prefixes.

## What changed

The DLL embeds all 348 optimized shader permutations: model inference,
image preparation and final output. It retains dynamic-weight guards and
fallbacks. RC7 also rewrites 17,964 integer vector extensions as equivalent
per-lane operations, allowing ordinary Proton 10 and 11 to translate the
shaders while keeping their packed arithmetic.

RC7 also repairs a missing SDK synchronization barrier before padding clears.
This addresses the intermittent synthetic-image corruption found during review
without adding a runtime setting or another installed component.

In matched 240-frame trials, whole-upscaler GPU cost stayed within
**1.14% of the existing v4 driver path** at 1080p, 1440p and 4K. Every scored output image was byte-identical.
These are upscaler timings on BC250, not whole-game FPS promises for other GPUs.
[Measurements and limits](docs/portable-dll-rc7.md#correctness-and-performance)
include the previously inconclusive arbitrary-size cases.

## Build and review

The complete, editable LLVM/DXIL sources and pinned SDK/DXC identities are
under [dll/](dll/README.md). Rebuilding validates every shader and must produce
the exact release DLL hash. Build tools are needed only by developers.

```sh
python3 scripts/check-repo.py
python3 dll/build.py --sdk /path/to/original/amd_fidelityfx_upscaler_dx12.dll \
  --dxcompiler /path/to/dxc/lib/libdxcompiler.so --output .work/dll --jobs 2
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
```

See [distribution and source archives](docs/releases.md), the
[changelog](CHANGELOG.md), and [provenance and licenses](THIRD_PARTY.md).

## Special thanks

This work continues [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4).
Thanks to AMD/GPUOpen, the Mesa and RADV contributors, Microsoft DXC,
Wine, vkd3d-proton, Valve Proton, GE-Proton and OptiScaler for the underlying
algorithms, compilers and compatibility work. Their licenses and attribution
remain with the source and release notices.
