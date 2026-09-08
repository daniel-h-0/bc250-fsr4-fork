# BC250 FSR4 v4

Optimized Mesa 26.2.2 RADV for the AMD BC250, continuing
[dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4)
with the original history preserved. Install once, then select
**BC250 FSR4 (4.1.1 INT8)** in Steam for a compatible DX12 game.

The [v4.0.0-rc1 driver](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc1)
has recorded [qualification](docs/qualification.md) and
[performance results](docs/performance.md). The **4.0.0-rc2 unified distribution** passed separate
[FSR-input and DLSS-input gameplay checks](docs/runtime-qualification.md).
The earlier performance results used the previous integration.

## Start a Steam game

**Already using rc1?** Follow the [rc1 transition guide](docs/upgrading-rc1.md)
first. It preserves the existing driver and covers retiring the old game hooks.

Download `bc250-fsr4-setup-4.0.0-rc2.tar.gz` and its checksum from the
[releases page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases), verify
the checksum and extract it. No Git checkout or Wine compilation is needed.
Close Steam and games. From the extracted folder, run as your desktop user:

```sh
./bc250-fsr4 install
```

For a standard v3 installation, use `./bc250-fsr4 install --upgrade-v3`.
The installer reuses a compatible verified driver, or installs a private one
when needed, then installs the Steam runtime. It manages both components
through the same interface:

```sh
./bc250-fsr4 update
./bc250-fsr4 status
./bc250-fsr4 rollback
```

Restart Steam. In the game's **Properties → Compatibility**, enable the
specific compatibility tool option and choose **BC250 FSR4 (4.1.1 INT8)**.
Launch its DX12 version and select the upscaler in the game's graphics menu.
Repeat that Steam selection for each compatible game you want to opt in.

**Do not combine this with the newer 4.1.1b mod or another OptiScaler
deployment.** Undo an existing integration first. The [game guide](docs/games.md)
covers switching, runtime updates and undo. The former three-game wizard is
retired; its [recovery commands](docs/game-troubleshooting.md#recover-the-retired-game-wizard)
remain available.

## Prerequisites

- A functioning Linux BC250 graphics setup: x86_64, PCI `1002:13fe`, RADV
  GFX1013. Firmware, kernel and clock configuration are separate.
- Python **3.12+**, `binutils`, glibc's `ldd`, `vulkan-tools` and a working
  Vulkan loader. The driver must pass eager dependency and device checks.
- Runtime installation also needs `patch`, `bsdtar` from libarchive and
  internet access for the initial pinned downloads. [Offline options](docs/game-troubleshooting.md)
  are available.
- The driver archive must match your distribution's libraries, including
  `libdisplay-info.so.3` and `libSPIRV-Tools.so`. It has **no LLVM
  dependency**. Use the source route if the binary's checks fail.
- Native Linux Steam is the initial target. Steam Flatpak and other sandboxes
  are not qualified.

Keep distribution libraries coherent and retain working 32-bit RADV. v4 ships
only x86_64; never export its private ICD globally. See the
[v3 upgrade notes](docs/upgrading-v3.md) for the actual dependency changes.

## Advanced driver builds and installation

| Route | Purpose |
| --- | --- |
| [Private archive](#private-archive-install-or-v3-upgrade) | Checked user installation and v3 migration; easiest rollback |
| [Native source build](#build-from-source) | Build the pinned driver for your distribution |
| [Container build](#container-build) | Build the same source using Docker or Podman |
| [System packages](docs/system-install.md) | Optional Arch/CachyOS integration with package-owned RADV |

## Private archive install or v3 upgrade

The unified installer already handles this. These component commands are
for driver development and recovery.

The internal driver installer obtains the published archive, checks its checksum and
probes the host before activation. A local archive and adjacent checksum work
without network access:

```sh
./install-v4.sh /path/to/DRIVER.tar.gz
```

For a custom v3 ICD, append `--upgrade-v3-icd PATH`; repeat it for multiple
installations. Only the named manifests are migrated, and their original bytes
are retained for rollback. The standard `--upgrade-v3` option selects
`~/.local/share/bc250-fsr4/v3/radv-bc250-fsr4-v3.json`.

The default private root is `~/.local/share/bc250-fsr4/`, with retained
releases and a stable `current.json`. For a custom root, use
`python3 scripts/driver.py --prefix PATH install ARCHIVE` and pass the same
prefix to subsequent commands.

```sh
python3 scripts/driver.py status
./run-bc250-fsr4.sh vulkaninfo --summary
python3 scripts/driver.py rollback
```

Before driver rollback, switch games using BC250 FSR4 back to their previous
Steam compatibility tool. Rollback restores the previous driver selection and
migrated ICD bytes, while preserving later user edits. If status reports an
interrupted transaction, run `python3 scripts/driver.py recover`.
The [unified interface](docs/games.md#update-or-undo) coordinates the components.
Commands in this advanced driver section operate on the driver alone.

## Build from source

### Obtain the source

Contributors can use an exported source snapshot or the maintained branch:

```sh
git clone --branch v4 https://github.com/daniel-h-0/bc250-fsr4-fork.git
cd bc250-fsr4-fork
```

The original `v4.0.0-rc1` tag and assets remain immutable. See
[release identities](docs/releases.md) before rebuilding or distributing.

### Native build

On a coherent Arch/CachyOS installation:

```sh
sudo pacman -S --needed base-devel python python-pip ninja git \
  libdrm libelf zlib zstd libx11 libxext libxcb libxshmfence \
  libxrandr libxxf86vm wayland libdisplay-info spirv-tools glslang vulkan-tools
./scripts/bootstrap.sh
./scripts/build-native.sh --jobs 4
python3 scripts/package.py --label cachyos-x86_64
```

The builder verifies the pinned Mesa archive, ordered patches and final source
hashes. It builds 64-bit RADV using ACO, with LLVM and game tracing disabled.
Use `--mesa-archive PATH` for a local Mesa archive, `--prepare-only` to check
inputs without compiling, or `--work PATH` for a separate build directory.
Resume requires matching inputs; old rc1 build directories need a fresh build.

## Container build

With Docker or rootless Podman available:

```sh
./build-anywhere.sh --jobs 4
python3 scripts/package.py --work .work/container --label arch-container-x86_64
```

Podman is preferred; select Docker with `BC250_CONTAINER_ENGINE=docker`.
No GPU is needed for building. The resulting binary retains its distribution
ABI requirements and must pass the destination host's checks. A new build
needs qualification of its exact ELF before release.

## What's in v4

The patches provide bounded arithmetic lowerings, selective unrolling and
reduction, composed image/texture optimizations, resolution-family coverage
and guarded store repairs. Unknown inputs retain their correctness fallback.

Matched Deadzone trials on a **40-CU BC250 with a 1850 MHz GPU maximum**
against upstream v3 measured **+14.3%, +18.9% and
+17.2% FPS** at 1080p, 1440p and 4K, respectively, with FSR 4.1.1 INT8 Quality
and hardware ray tracing off. These are scene-specific averages from the
[recorded campaign](docs/performance.md), not new-runtime measurements.

For project work, see [contributing](CONTRIBUTING.md),
[development](docs/development.md), [releases](docs/releases.md) and
[provenance and licenses](THIRD_PARTY.md). Historical upstream experiments
remain under [legacy](legacy/README.md).

## Special thanks

This project builds on substantial work by these projects and their contributors:

- [dmoraza's BC250 FSR4](https://github.com/dmorazasanchez/bc250-fsr4), for the
  original BC250 compatibility work and the history this fork continues.
- [Mesa](https://gitlab.freedesktop.org/mesa/mesa), for RADV and the ACO compiler
  that our driver changes build on.
- [GE-Proton](https://github.com/GloriousEggroll/proton-ge-custom), for the
  compatibility runtime and upscaler integration underneath our Steam tool;
  and [Valve's Proton](https://github.com/ValveSoftware/Proton), with its Wine,
  DXVK and vkd3d-proton foundations.
- [OptiScaler](https://github.com/optiscaler/OptiScaler) and
  [OptiPatcher](https://github.com/optiscaler/OptiPatcher), for upscaler
  interception, replacement and exposing supported games' DLSS inputs.
- [umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes)
  and [proton-upscalers](https://github.com/loathingKernel/proton-upscalers),
  for the upstream prefix/upscaler tooling and component distribution we reuse.
- [AMD FidelityFX SDK](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK),
  for the FSR implementation and API bridge.

Their authors retain credit for their work; [provenance and licenses](THIRD_PARTY.md)
record the component identities and applicable notices. This work was accomplished with 
the assistance of AI tools (GPT-6-Astra-xhigh) with constant human oversight.
