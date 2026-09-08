# SteamOS driver compatibility

The original CachyOS driver shipped with rc1 and reused by rc2/rc3 has
reproducible incompatibilities with SteamOS 3.7 and 3.8. These failures occur
before game code runs. The earlier CachyOS gameplay qualification did not
establish SteamOS compatibility.

This branch adds an **unpublished SteamOS driver candidate**. The published
rc1 driver and rc3 runtime remain unchanged. The runtime can use this new
driver because its pinned Mesa/FSR source contract is unchanged; the new ELF
has a separate hash and build label. Full SteamOS gameplay is not qualified.

## Confirmed failures and correction

Tests use hash-verified packages from Valve's SteamOS repositories in isolated
userspaces on the BC250. They use the CachyOS host kernel and GPU, not a
booted SteamOS installation or the external tester's machine.

| Layer | Published driver failure | Candidate correction |
| --- | --- | --- |
| libc | `GLIBC_ABI_GNU2_TLS` is absent from the tested SteamOS libc | Explicit original GNU TLS ABI; target compiler and headers |
| Wayland | Rebuilding with only the TLS fix still requires unavailable `wl_fixes_interface` | Compile against SteamOS Wayland 1.23.1 |
| Display dependency | `libdisplay-info.so.3` is missing on 3.7 and is not imported for the tested SteamOS graphics stack into Steam Runtime 4 | Disable the optional direct-display EDID parser, matching the inspected SteamOS RADV dependency set |
| DRM userspace | 3.7's older libdrm rejects Mesa's GPU-info query even after the ELF loads | Statically link pinned libdrm 2.4.133 and its AMDGPU component into this private driver |
| Installer | An explicit replacement archive could be ignored when a source-compatible driver was already installed | Verify and honor the requested archive, preserving coordinated rollback |

The target retains X11 and Wayland presentation. Disabling `display-info`
affects `VK_KHR_display` direct-display EDID/HDR metadata parsing; it does not
disable the separate Wayland presentation code. This candidate is intended
for Steam/Game Mode's compositor path, not as a system-wide replacement for
the compositor's driver. Its static DRM components do not replace host
libraries. No game preset, OptiScaler component, kernel or clock setting was
changed for this repair.

## Build the candidate

Use a full source checkout and the normal
[build prerequisites](../README.md#build-from-source), plus `bsdtar`.
The build host still needs working compiler support tools and Meson; the
target's compiler, headers and link libraries come from the pinned packages.

```sh
PATH="$PWD/.venv/bin:$PATH" python scripts/build-steamos.py --jobs 4
python scripts/package.py --work .work/steamos-3.8/mesa --label steamos-abi1-x86_64
```

The builder downloads the exact package and libdrm source hashes in
[`v4/build-targets/steamos-3.8.json`](../v4/build-targets/steamos-3.8.json).
It extracts packages under the chosen `--work` directory and never invokes
their install scripts. `--cache PATH --offline` reuses verified downloads;
use a fresh `--work` directory for a new attempt. This does not install or
upgrade any SteamOS/CachyOS system package.

The archive retains the target package provenance and the complete libdrm
source archive, including its copyright and license notices. Packaging
rejects changed target definitions, builder code or retained libdrm source.
It also retains the normal Mesa source, build provenance and license records.

## Try the corrected driver

Use the **corrected installer from this branch or the candidate driver
archive**, since the published rc3 installer can ignore an explicit archive.
Close Steam and games, then run as the desktop user:

```sh
./bc250-fsr4 update --driver private --driver-archive /path/to/STEAMOS-DRIVER.tar.gz
./bc250-fsr4 status
```

Keep the archive's adjacent `.sha256` file, or provide `--driver-sha256`.
The installer binds the existing RC3 runtime to the new private driver and
retains the previous selection. On first installation, use `install` with
the same arguments. Restart Steam afterward.

With Steam and games closed, `./bc250-fsr4 rollback` reverses the coordinated
change. Do not replace libc, Wayland, libdrm or other system libraries by hand.
An ordinary install without an explicit candidate archive still selects the
published driver; this development branch is not a new published release.

## Evidence and limits

The candidate passes eager ELF loading and BC250 GFX1013 Vulkan initialization
with the isolated 3.7 and 3.8 package sets, and with Steam Runtime 4 using
the 3.8 graphics provider. It is also checked against retained FSR compiler
and GPU-output references. Detailed results are recorded in
[`data/steamos-compatibility-20260908.json`](data/steamos-compatibility-20260908.json).

These checks establish specific ABI fixes. They do not qualify a SteamOS
kernel, compositor, display mode, every game, Flatpak Steam or the external
tester's exact failure. The report that every game failed is consistent with
these shared startup failures, but no tester log was available to identify
which one occurred there.

Primary inputs: [Valve's package repositories](https://steamdeck-packages.steamos.cloud/archlinux-mirror/),
[libdrm source releases](https://dri.freedesktop.org/libdrm/), and
[Valve's Steam Runtime notes](https://github.com/ValveSoftware/steam-runtime/blob/master/doc/steamlinuxruntime-known-issues.md).
