# RC4 installation compatibility

RC4 makes the portable private driver the normal download and checks whether
it can load before activating the Steam tool. Use the
[RC4 setup bundle](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc4)
and the [quickstart](legacy-rc6.md#start-a-steam-game). Existing rc2/rc3 users
can follow the [update guide](upgrading-rc2.md).

## What the installer handles

- A new private installation downloads the portable driver automatically.
  An original CachyOS private binary is replaced during update. Other verified
  source-compatible builds are checked before reuse; failed automatic selections
  fall back to the portable build. An explicitly requested system driver must pass.
- The default archive's checksum, exact driver hash and Mesa source identity are
  verified. A supplied `--driver-archive` is honored even when an older driver
  was built from the same source. Both selections have coordinated rollback.
- Python 3.11 is supported, including Debian's package without the newer tar
  filter API. The compatibility extractor rejects path traversal and writes
  through links, creates regular data before links and checks link confinement.
- `patch`, `ldd` and `vulkaninfo` are no longer installer prerequisites.
  If libarchive's `bsdtar` is absent, a checksum-pinned official static 7-Zip
  extractor is downloaded into the cache and used in temporary staging.
- A standard-library Vulkan probe performs eager ELF relocation and checks for
  BC250 RADV GFX1013. The available Steam Linux Runtime 4 container is checked
  too, including when Steam installed it on a secondary drive.
- Driver rebinding can reuse an intact installed runtime without its old
  upstream download cache. Changed runtime files or policy still fail verification.

The installer needs a functioning BC250 kernel/graphics setup, Python 3.11+,
a Vulkan loader and native Steam. It does not install OS packages, kernels,
firmware, game-local mods or game settings. RC3's upscaler components and preset
are unchanged; the supported device remains x86_64 AMD BC250 `1002:13fe`.

## Diagnose a failed launch

```sh
./bc250-fsr4 doctor
```

This reports installation integrity plus actual host/container driver checks.
If Runtime 4 is not downloaded yet, its check says `pending`; Steam obtains
AppID `4183110` when the compatibility tool is selected. Run `doctor` again
after that download. A failed check returns a nonzero exit code.

A driver-loading failure before Proton starts is retained at
`~/.local/state/bc250-fsr4/last-launch-error.json`, or under `XDG_STATE_HOME`
when set. The usual `BC250_RUNTIME_DEBUG=1 %command%` option enables Proton
and OptiScaler diagnostics for failures later in startup.

For the old private driver, update using the new setup directory:

```sh
./bc250-fsr4 update
./bc250-fsr4 doctor
```

Keep any custom `--prefix` and `--steam-root` options. Close Steam and games
before changing versions, and restart Steam afterward. `rollback` restores
the preceding managed runtime and driver binding; retained payloads remain
available. It does not undo unrelated mods or game settings.

## Portable driver and qualification scope

The source remains Mesa/FSR driver `4.0.0-rc1`; the distinct build label is
`linux-glibc236-x86_64`. The stripped driver SHA256 is
`13163d1350d346f54d58b82863e5fc2f31ddfab0a0dd6495886c3fec286315d2`.

The build uses pinned Debian 12 GCC 12, glibc 2.36 and Wayland 1.21 inputs,
the original GNU TLS dialect, and static PIC libdrm/AMDGPU 2.4.133. Optional
display-info and SPIRV-Tools support is disabled. The resulting ELF's highest
required libc symbol is GLIBC 2.34; Debian 12 is the tested baseline. The source
build checks reject newer ABI requirements or embedded host library paths.

X11 and Wayland presentation remain enabled. Omitting display-info affects
`VK_KHR_display` direct-display EDID/HDR parsing. This private build is intended
for games behind Steam/Game Mode's compositor, not as a system-wide compositor
driver replacement. Static DRM does not replace any host libraries.

The exact binary passed eager loading and Vulkan initialization with Debian 12,
SteamOS 3.7 and SteamOS 3.8 libraries, plus Steam Runtime 4 with the SteamOS 3.7
and 3.8 graphics providers. It matched all 92 retained complete shader programs,
96 tensor outputs and 12 image/texture outputs. The tooling suite also runs
against Debian's actual Python 3.11 package. All 171 tests pass on Python 3.11
and 3.14. A real RC3 → RC4 → RC3 → RC4 cycle preserves both driver and
runtime selections. Offline assemblies with no helper tools on `PATH` produce
the same runtime payload on both Python versions.

A Windows probe also imports the native OptiScaler WinMM proxy and creates a
D3D12 device through RC4 inside Steam Runtime 4 with a SteamOS 3.8 graphics
provider. Process maps verify the exact portable driver and proxy. It uses a
private virtual X display, without a game or swapchain.

These isolated userspaces share the CachyOS host kernel and BC250 GPU. They do
not establish booted-OS, compositor, every-game or Flatpak Steam acceptance.
Earlier [gameplay/performance evidence](runtime-qualification.md) retains its
original driver/runtime identity. See [the recorded checks](data/runtime-v4.0.0-rc4.json)
and the [original SteamOS failure analysis](steamos-compatibility.md).

## Reproduce the portable driver

Use the full source checkout, Python 3.12+, the normal build tools/Meson virtual
environment, and libarchive's `bsdtar`. Build-time prerequisites are separate
from the smaller end-user installation requirements.

```sh
PATH="$PWD/.venv/bin:$PATH" python3 scripts/build-compat.py --jobs 4
python3 scripts/package.py --work .work/linux-glibc236/mesa --label linux-glibc236-x86_64
```

The [target definition](../v4/build-targets/linux-glibc236.json) pins every Debian
package and the libdrm source archive. Extraction is private; package maintainer
scripts never run. `--cache PATH --offline` reuses those inputs, and each build
needs a new `--work` directory. The archive retains the source, provenance,
licenses and complete static libdrm source. A rebuilt ELF needs its own exact
binary qualification before distribution.
