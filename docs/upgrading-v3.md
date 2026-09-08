# Upgrade a working v3 installation

A working v3 setup already has most libraries required by the v4 driver.
The migration does not require a new kernel, firmware flash or LLVM upgrade.

## Upgrade sequence

1. Check Python **3.12+**, `vulkan-tools` and the [prerequisites](../README.md#prerequisites).
   If Arch/CachyOS packages need updating, use a coherent full-system update
   and preserve the normal recovery path. Do not cherry-pick core libraries
   or invent compatibility symlinks.
2. From the current installer bundle, run `./install-v4.sh --upgrade-v3`.
   For a source-built or custom v3 ICD, use `--upgrade-v3-icd PATH` instead.
   The installer validates driver loading before activation and preserves the
   old ICD bytes for rollback.
3. Undo the previous game's OptiScaler or other runtime integration using its
   own recovery procedure. For this project's retired wizard, use
   [legacy recovery](game-troubleshooting.md#recover-the-retired-game-wizard).
4. Close Steam and games, run `./install-runtime.sh install`, then restart
   Steam and select **BC250 FSR4 (4.1.1 INT8)** under the game's
   Properties → Compatibility. Follow [the game guide](games.md).

The new compatibility tool's native FSR and DLSS gameplay qualification is
pending. It uses FSR **4.1.1 INT8**, not the newer **4.1.1b** mod; do not
combine them.

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
| Python / loader tools | Python 3.12+, `vulkan-tools` and a coherent Vulkan loader, including the distribution's 32-bit components. |
| LLVM | No LLVM dependency in the v4 archive. Do not change LLVM solely for v4; other applications may need it. |
| C/C++ runtime | The published v4 ELF directly requires GLIBC 2.38, GLIBCXX 3.4.29 and CXXABI 1.3.9 symbols. These are symbol floors, not a complete distribution guarantee. |
| Display / SPIR-V | `libdisplay-info.so.3` and `libSPIRV-Tools.so`, also required by the original prebuilt v3. |
| Source builds | Pinned Mesa 26.2.2 requires libdrm/libdrm_amdgpu ≥2.4.133, libdisplay-info ≥0.1.1 and SPIRV-Tools ≥2024.1 when enabled. |
| Kernel / firmware | Keep the working BC250 setup. The recorded 7.2.3-1.83 kernel is a test reference, not an established minimum. |
| Game runtime | Install the independent BC250 FSR4 compatibility tool; Steam's Compatibility menu controls each game's opt-in. |

The v3 and published v4 ELFs have the same direct GLIBC/C++ symbol floors;
v3 additionally links LLVM. Dependencies can impose further requirements,
which is why the installer resolves all relocations with `ldd -r` and runs
`vulkaninfo --summary` with the selected ICD and `LD_BIND_NOW=1`.

Primary references: [Arch system maintenance](https://wiki.archlinux.org/title/System_maintenance#Partial_upgrades_are_unsupported),
[libdisplay-info ABI 3 files](https://archlinux.org/packages/extra/x86_64/libdisplay-info/files/),
and [Vulkan loader eager resolution](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderDriverInterface.md#additional-settings-for-driver-debugging).
The exact tested binaries and environment are recorded in
[qualification](qualification.md) and [performance](performance.md).

## Recovery and newer distributions

Private rollback restores driver selection and migrated ICD bytes. It does
not revert a separate distribution update; retain the distribution's recovery
snapshot when you need the earlier complete environment. See
[driver rollback](../README.md#private-archive-install-or-v3-upgrade).

If your distribution has moved beyond Mesa 26.2.2, use a private archive that
passes its checks or build the private source route. Do not downgrade a
coherent Mesa stack to fit the optional package route. A system package for
a newer Mesa needs an explicit rebase and new qualification.

Passing the loader probe establishes driver readiness.
[Real game evidence](game-troubleshooting.md#verify-a-real-game) remains separate.
