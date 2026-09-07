# BC250 FSR4 v4

A continuation of [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4),
based on its v3 branch with the original history preserved. v4 brings the
qualified FSR 4.1.1 INT8 optimizations into a default-on Mesa 26.2.2 RADV build,
and adds checked installation, v3 migration, package integration and rollback.

**Current release: 4.0.0-rc1, x86_64 / AMD BC250 (GFX1013).** See
[qualification](docs/qualification.md) for the exact tested artifacts and limits.

The driver improves a compatible FSR4 path. Games still need an FSR4 INT8
provider/model hook and a supported game input. Installing this driver alone
does not turn every game's upscaler into FSR4. Start with [game setup and proof
of engagement](docs/games.md) after installing the driver.

## Choose an installation

| Route | What changes | Best fit |
| --- | --- | --- |
| [Private archive](#private-archive-install-or-v3-upgrade) | A versioned user directory and explicitly selected v3 ICD files | Existing v3 users; easiest rollback |
| [Native source build](#build-from-source) | Same installable archive, built for your distribution | Missing binary dependencies or local development |
| [Docker / Podman](#container-build) | Same pinned source build in an Arch container | A build environment without host compiler dependencies |
| [System packages](docs/system-install.md) | Package-owned 64-bit `vulkan-radeon`, plus status/rollback helper | Arch/CachyOS users wanting normal system Vulkan launches to use v4 |

No v4 32-bit binary is shipped. Keep your distribution's working
`lib32-vulkan-radeon`. Never set a 64-bit-only `VK_DRIVER_FILES` globally in
`/etc/environment`, Steam's service, or a desktop startup file: it can break
32-bit applications. System packages avoid that override.

## Prerequisites

- A functioning Linux BC250 graphics setup (PCI `1002:13fe`, RADV GFX1013).
  v4 does not flash firmware, install a kernel or change clocks/voltages.
- Python **3.12+**, `binutils` (`readelf`/`strip`), glibc's `ldd`, `vulkan-tools`, and your
  usual working Vulkan loader. The private installer requires a successful
  `vulkaninfo --summary`; a missing tool is an error, not a skipped check.
- Use the archive built for your distribution. The native CachyOS build does
  **not require LLVM 22**, but still depends on system libraries, including
  `libdisplay-info.so.3` and `libSPIRV-Tools.so`. It is not a universal Linux
  binary. An ABI failure leaves the selected installation unchanged; use the
  source route on a different distribution.
- For Windows games, use a compatible Proton build and follow
  [the game guide](docs/games.md). Steam Flatpak and unusual runtime sandboxes
  need additional path/library exposure and are not yet qualified.

## Private archive install or v3 upgrade

Obtain the matching `.tar.gz` and adjacent `.tar.gz.sha256` from this fork's
release, or create them with the source instructions below. Until a GitHub
release is published, the source route is the complete installation route.
The checksum detects corruption; obtain both files from the trusted release.

After a release is published, the standalone installer can download it without
a checkout. Download the script from this fork's `v4` branch, inspect it, then
run it as your desktop user:

```sh
curl -fLO https://raw.githubusercontent.com/daniel-h-0/bc250-fsr4-fork/v4/install-v4.sh
bash install-v4.sh --upgrade-v3
```

`--upgrade-v3` migrates the standard v3 ICD path. Omit it for a new install,
or use `--upgrade-v3-icd PATH` for a custom v3 installation. This standalone
route downloads the named release and its checksum, verifies the archive,
and runs its bundled installer. A local archive works without network access:
`bash install-v4.sh /path/to/ARCHIVE.tar.gz --upgrade-v3`.

From this checkout, with the archive under `dist/`:

```sh
./install-v4.sh dist/bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz
./run-bc250-fsr4.sh vulkaninfo --summary
python3 scripts/driver.py status
```

The installer prints a stable Steam launch option. Add it to the game's
existing options without dropping its Proton/OptiScaler settings. For a
standard v3 installation, preserve your existing Steam launch string by
migrating the exact old ICD instead:

```sh
./install-v4.sh dist/bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz \
  --upgrade-v3-icd "$HOME/.local/share/bc250-fsr4/v3/radv-bc250-fsr4-v3.json"
```

For a source-built v3, pass the actual old `radv-bc250-fsr4-v3.json` or
`radv-bc250-fsr4.json` path. Repeat `--upgrade-v3-icd` for multiple installs.
Only those explicitly named manifests are migrated. Their original driver
files stay in place and their JSON bytes are recorded for rollback. Close the
game before upgrading and relaunch it afterward; a running process keeps its
previously loaded driver. No Steam VDF files are edited.

By default v4 lives under `~/.local/share/bc250-fsr4/`, with immutable
`releases/`, a `current` link, stable `current.json`, and transaction records.
For a custom root use `python3 scripts/driver.py --prefix /your/dedicated/path
install ARCHIVE` and use the same prefix for `status`, `run` and `rollback`.
Do not run the private installer with sudo.

Rollback the most recent private installation:

```sh
python3 scripts/driver.py rollback
```

This restores the previous v4 selection and any migrated v3 manifests. It
refuses to overwrite an ICD you edited after installation. An interrupted transaction is reported by `status`; use
`python3 scripts/driver.py recover` to restore its recorded prior selection.
Payloads remain available for inspection; no automatic directory deletion occurs. If this was
your first private install without a v3 migration, remove its printed launch
option when returning to system RADV.

## Build from source

On an up-to-date Arch/CachyOS host, the build dependencies are:

```sh
sudo pacman -S --needed base-devel python python-pip ninja git \
  libdrm libelf zlib zstd libx11 libxext libxcb libxshmfence \
  libxrandr libxxf86vm wayland libdisplay-info spirv-tools glslang vulkan-tools
./scripts/bootstrap.sh
./scripts/build-native.sh --jobs 4
python3 scripts/package.py --label cachyos-x86_64
```

Use normal distribution update practices before installing build dependencies;
do not force a partial Mesa/LLVM/glibc update. Python build tools live in a
repository-local virtual environment. The builder downloads the SHA256-pinned
Mesa 26.2.2 archive, verifies every patch input, applies the three patches with
zero fuzz, and checks all fifteen modified source files against the qualified
manifest. It builds only 64-bit RADV with ACO, without LLVM or game tracing.

For offline/repeated work, pass `--mesa-archive /path/to/mesa-26.2.2.tar.xz`.
Use `--prepare-only` to verify the source without compiling, `--work PATH` for
another build directory, and `--resume` to resume an interrupted build with
matching inputs. Do not reuse a work directory for different inputs. Build
artifacts are under `.work/native`; archive outputs are under `dist/`.

Compiler and dependency versions are recorded; these are reproducible *source*
inputs, not a claim of bit-identical binaries across different toolchains.
A locally built archive receives the same ABI/device/loader checks on install.
New compiler output still needs appropriate GPU/game qualification before
being advertised as an accepted release.

## Container build

Install and start Docker or configure rootless Podman, then:

```sh
./build-anywhere.sh --jobs 4
python3 scripts/package.py --work .work/container --label arch-container-x86_64
```

Podman is preferred when both are present; select explicitly with
`BC250_CONTAINER_ENGINE=docker`. The container uses the supplied Dockerfile,
compiles the same pinned Mesa sources, and writes output as your user. The
Arch base image and dependency repositories can advance: the resulting ABI
is recorded and still checked on the destination host. A container build does
not make an Arch binary compatible with every distribution. No GPU device is
passed into the build container. ARM builders require working x86_64 emulation;
that configuration is untested.

## What's in v4

- Exact bounded arithmetic lowerings and selective unrolling/reduction.
- Composed image-preparation and texture optimizations.
- Matching arithmetic coverage for the small, middle and large resolution
  buckets of the qualified FSR 4.1.1 INT8 shader family.
- Independent masked-store repair for the eight guarded large-bucket shaders.
- Default-on selection with exact shader, weight, interface and subgroup
  checks. Unknown inputs retain their correctness fallback.
- `BC250_FSR4_DISABLE=1` disables the optimization while keeping the independent
  store repair. The driver's internal cache marker `v3` is a cache generation,
  not the project's public release version.

Performance depends on the game, scene and output configuration. Earlier
matched Deadzone trials measured a useful improvement; they are not a promise
of a uniform percentage in every game or at every resolution. See the
[qualified evidence and limitations](docs/qualification.md).

Inherited v2/v3 documentation, scripts and experiments are archived under
`legacy/v3/` as historical material. The
active v4 source is `v4/manifest.json` plus its ordered patches. Experimental
upstream Linux 7.3/native-DOT/SDWA work is not part of this qualified release.
See [provenance and licenses](THIRD_PARTY.md) and [development](docs/development.md).
