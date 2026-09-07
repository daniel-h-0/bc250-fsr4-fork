# BC250 FSR4 v4

A continuation of [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4),
based on its v3 branch with the original history preserved. v4 brings the
qualified FSR 4.1.1 INT8 optimizations into a default-on Mesa 26.2.2 RADV build,
and adds checked installation, v3 migration, package integration and rollback.

**Published prerelease: [v4.0.0-rc1](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc1),
x86_64 / AMD BC250 (GFX1013).** The `v4` branch contains later documentation and
tooling maintenance. The tag and original release assets remain unchanged;
fresh builds require their own qualification. See [release identities](docs/releases.md)
and [qualification](docs/qualification.md) for the exact tested artifacts and limits.

**FSR version: this setup uses the pinned FSR 4.1.1 INT8 provider, not the
newer 4.1.1b mod. Do not co-install 4.1.1b with this setup or mix its DLLs
with this runtime.** Restore the original game files and undo the competing
integration before switching; see [runtime compatibility](docs/games.md#runtime-compatibility).
The project's `v4` / `4.0.0-rc1` name identifies this Mesa fork's release,
not the FSR provider version.

Install the driver once, then use guided setup to configure a supported game.
The driver alone does not enable FSR4 in every game.

## Start a supported Steam game

On your working Linux BC250 installation, check the
[prerequisites](#prerequisites), then obtain the maintained tools:

```sh
git clone --branch v4 https://github.com/daniel-h-0/bc250-fsr4-fork.git
cd bc250-fsr4-fork
./install-v4.sh
```

For an existing standard v3 installation, use `./install-v4.sh --upgrade-v3`
instead. If you already have a verified v4 private or system installation,
keep it and continue below.

Close Steam and all games, then run:

```sh
./setup-game.sh
```

Choose your installed game and Steam account. Setup downloads the pinned
runtime, selects the required Proton version and configures launch options
with backups. Restart Steam, launch the game and select **FSR** in Deadzone or
Kingdom Come: Deliverance II, or **DLSS** in Control. See the short
[game setup guide](docs/games.md) for prerequisites, supported scope and undo.

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
- Python **3.12+**, `binutils` (`readelf`; `strip` for packaging), glibc's `ldd`, `vulkan-tools`, and your
  usual working Vulkan loader. The private installer requires a successful
  `vulkaninfo --summary`; a missing tool is an error, not a skipped check.
- Use the archive built for your distribution. The native CachyOS build does
  **not require LLVM 22**, but still depends on system libraries, including
  `libdisplay-info.so.3` and `libSPIRV-Tools.so`. It is not a universal Linux
  binary. An ABI failure leaves the selected installation unchanged; use the
  source route on a different distribution.
- For Windows games, use a compatible Proton build and follow
  [the game guide](docs/games.md). Guided setup also needs `bsdtar` from
  libarchive to unpack the pinned runtime. Steam Flatpak and unusual runtime sandboxes
  need additional path/library exposure and are not yet qualified.

## Private archive install or v3 upgrade

**Coming from v3? Read the [v3 upgrade checklist](docs/upgrading-v3.md) first.**
Use Python 3.12+ and install `vulkan-tools`; keep distribution libraries
coherent. Guided game setup selects the tested GE-Proton11-6 and pinned
runtime for the documented profiles. A working
prebuilt v3 already needs most of the same libraries as v4; a new kernel,
firmware flash or LLVM upgrade is not an automatic prerequisite.

Obtain the matching binary `.tar.gz` and adjacent `.tar.gz.sha256` from the
[v4.0.0-rc1 release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc1),
or create them with the source instructions below. Source and performance
archives are separate assets and cannot be installed as drivers.
The checksum detects corruption; obtain both files from the trusted release.

For the maintained installer, first [obtain the v4 checkout](#obtain-the-source)
and run `./install-v4.sh --upgrade-v3` there. This uses the checkout's tooling
with the unchanged published driver archive.

The standalone script can also download the release without a checkout.
Download it into an empty directory, inspect it, then run it as your desktop user:

```sh
curl -fLO https://raw.githubusercontent.com/daniel-h-0/bc250-fsr4-fork/v4/install-v4.sh
bash install-v4.sh --upgrade-v3
```

The standalone route executes the installer bundled in the selected release;
later `v4` tooling fixes are available through the checkout route above.
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

For supported games, run `./setup-game.sh` after installation to configure
Steam automatically. The driver installer also prints a launch option for
[manual setup](docs/game-troubleshooting.md). For a
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
previously loaded driver. These driver-migration commands leave Steam settings
unchanged; guided game setup configures those separately.

By default v4 lives under `~/.local/share/bc250-fsr4/`, with immutable
`releases/`, a `current` link, stable `current.json`, and transaction records.
For a custom root use:

```sh
python3 scripts/driver.py --prefix /your/dedicated/path install ARCHIVE
```

Use the same prefix for `status`, `run`, `rollback` and `recover`.
Do not run the private installer with sudo.

Rollback the most recent private installation:

If guided setup configured a game to use this installation, first use its
[game rollback command](docs/games.md#undo-game-setup) to restore Steam settings.

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

### Obtain the source

```sh
git clone --branch v4 https://github.com/daniel-h-0/bc250-fsr4-fork.git
cd bc250-fsr4-fork
```

The `v4` branch is maintained. To inspect the original release source, use a
separate checkout of tag `v4.0.0-rc1`; its tooling predates the current branch.
See [development](docs/development.md) for repository layout and
[contributing](CONTRIBUTING.md) for changes and checks.

### Native build

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

Build directories from the original rc1 tooling predate the current provenance
checks. Use a new `--work PATH` for those builds; `--resume` cannot upgrade their
old records. Existing qualified release archives remain installable.

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

Fresh matched Deadzone trials against upstream v3 measured **+14.3%,
+18.9% and +17.2% FPS** at 1080p, 1440p and 4K respectively, using
High/custom graphics, FSR 4.1.1 INT8 Quality and hardware ray tracing off. These
are scene-specific averages from two launches per driver at each resolution.
See the [performance data and method](docs/performance.md) and
[qualification limits](docs/qualification.md).

Inherited v2/v3 documentation, scripts and experiments are archived under
`legacy/v3/` as historical material; read the [archive guide](legacy/README.md)
before using them. The active v4 source is `v4/manifest.json` plus its ordered
patches. Experimental upstream Linux 7.3/native-DOT/SDWA work is outside this
qualified release. See [provenance and licenses](THIRD_PARTY.md),
[development](docs/development.md) and the [changelog](CHANGELOG.md).
