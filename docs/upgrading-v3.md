# Upgrade a working v3 installation

This is the retained driver/runtime migration. For RC9's portable DLL, start
with the [current walkthrough](beginner-guide.md); do not install a driver merely
to follow that DLL guide.

A working v3 setup already has most libraries required by the v4 driver.
The migration does not require a new kernel, firmware flash or LLVM upgrade.
If you already completed the move to rc1, follow the
[rc1 transition guide](upgrading-rc1.md) instead; do not repeat the v3 migration.

## Upgrade sequence

1. Check Python **3.11+**, a working Vulkan loader and the [prerequisites](legacy-rc6.md#prerequisites).
   If Arch/CachyOS packages need updating, use a coherent full-system update
   and preserve the normal recovery path. Do not cherry-pick core libraries
   or invent compatibility symlinks.
2. Close Steam and games, then undo the game's previous OptiScaler integration
   using its own recovery procedure. For this project's retired wizard, use
   [legacy recovery](game-troubleshooting.md#recover-the-retired-game-wizard).
3. From the current distribution, run
   `./bc250-fsr4 install --upgrade-v3`. For a custom v3 ICD, use
   `--upgrade-v3-icd PATH` instead. The installer preserves migrated ICD bytes
   and creates a private-driver transaction even if a compatible driver is
   already installed. Explicit v3 migration cannot be combined with `--driver system`.
4. Restart Steam and select **BC250 FSR4 (4.1.1 INT8)** under the game's
   Properties → Compatibility. Follow [the game guide](games.md).

Use `./bc250-fsr4 status` to inspect both components and
`./bc250-fsr4 rollback` to undo the managed installation.

The shared runtime has [recorded gameplay checks](runtime-qualification.md).
It uses FSR **4.1.1 INT8**; do not combine it with the newer **4.1.1b** mod.

## What changes

The [upstream v3 instructions](https://github.com/dmorazasanchez/bc250-fsr4/blob/6173651fa3a5a557cba2c2ff802e2d6f49881bc1/README.md)
describe a private Mesa 26.2.0 driver, tested with glibc 2.44 and LLVM 22.1.
They leave the system driver and game runtime configuration to the user.
Its [installer](https://github.com/dmorazasanchez/bc250-fsr4/blob/6173651fa3a5a557cba2c2ff802e2d6f49881bc1/install-v3.sh)
does not require the eager loading checks now used by v4.

| Component | v4 requirement |
| --- | --- |
| Driver | Private Mesa 26.2.2 archive with a successful host probe. System Mesa need not match for this route. |
| Optional system package | An exact original Mesa 26.2.2 `vulkan-radeon` package from the target distribution; see [system installation](system-install.md). |
| Python / loader tools | Python 3.11+ and a coherent Vulkan loader, including the distribution's 32-bit components. |
| LLVM | No LLVM dependency in the v4 archive. Do not change LLVM solely for v4; other applications may need it. |
| C/C++ runtime | The default portable build targets Debian 12 glibc 2.36 / GCC 12; its highest required libc symbol is GLIBC 2.34. These are separate from the original CachyOS ELF's ABI. |
| Display / SPIR-V | The portable build keeps X11/Wayland presentation, disables optional display-info and SPIRV-Tools dependencies, and statically links current DRM. |
| Source builds | Pinned Mesa 26.2.2 requires libdrm/libdrm_amdgpu ≥2.4.133, libdisplay-info ≥0.1.1 and SPIRV-Tools ≥2024.1 when enabled. |
| Kernel / firmware | Keep the working BC250 setup. The recorded 7.2.3-1.83 kernel is a test reference, not an established minimum. |
| Game runtime | The same installer manages the BC250 FSR4 compatibility tool; Steam's Compatibility menu controls each game's opt-in. |

The original v3 and CachyOS v4 ELFs had the same direct GLIBC/C++ symbol floors;
v3 additionally linked LLVM. The default portable artifact has a separate
[ABI qualification](rc4-compatibility.md). The installer resolves ELF
relocations eagerly and initializes Vulkan with a standard-library Python
probe; `ldd`, `vulkaninfo` and `patch` are not installation prerequisites.

Primary references: [Arch system maintenance](https://wiki.archlinux.org/title/System_maintenance#Partial_upgrades_are_unsupported),
[libdisplay-info ABI 3 files](https://archlinux.org/packages/extra/x86_64/libdisplay-info/files/),
and [Vulkan loader eager resolution](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderDriverInterface.md#additional-settings-for-driver-debugging).
The exact tested binaries and environment are recorded in
[qualification](qualification.md) and [performance](performance.md).

## Recovery and newer distributions

Private rollback restores driver selection and migrated ICD bytes. It does
not revert a separate distribution update; retain the distribution's recovery
snapshot when you need the earlier complete environment. See
[driver rollback](legacy-rc6.md#private-archive-install-or-v3-upgrade).

If your distribution has moved beyond Mesa 26.2.2, use a private archive that
passes its checks or build the private source route. Do not downgrade a
coherent Mesa stack to fit the optional package route. A system package for
a newer Mesa needs an explicit rebase and new qualification.

Passing the loader probe establishes driver readiness.
[Real game evidence](game-troubleshooting.md#verify-a-real-game) remains separate.
