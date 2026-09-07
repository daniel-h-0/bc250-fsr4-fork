# Upgrade a working v3 installation

**Prioritize the game runtime and a coherent userspace, then migrate the
private driver.** A working upstream v3 binary already uses most of the same
libraries as the published v4 binary. Installing v4 does not, by itself,
require a new kernel, firmware flash, LLVM upgrade or system Mesa replacement.

## Before upgrading

1. **Use guided setup for the game's tested runtime.** After migrating the
   driver, close Steam and games, then run **`./setup-game.sh`** from the
   maintained `v4` checkout. Choose your installed game and Steam account;
   it installs/selects **GE-Proton11-6-x86_64**, pinned OptiScaler nightly
   **20260904**, OptiPatcher **v0.41** and INT8 model 2. It also sets the
   explicit **`PROTON_FSR4_UPGRADE=4.1.1`** selection; the GE default for `=1`
   is different. Follow [the short game setup guide](games.md), then restart
   Steam and choose the indicated in-game upscaler.
2. **Check the installation tools:** Python **3.12 or newer** and
   `vulkan-tools` providing `vulkaninfo` are required. The v4 installer must
   pass both eager dependency resolution and actual BC250 Vulkan initialization.
   An old v3 installation message or a plain `ldd` result is insufficient.
3. **Keep Arch/CachyOS packages coherent.** If the system is behind on updates
   or required libraries are missing, complete a reviewed normal full-system
   update before installing additional packages. Do not cherry-pick glibc,
   LLVM, libdrm or Mesa packages, invent library symlinks, or force old versions
   onto a newer installation. Arch explicitly does not support partial
   upgrades. Preserve your working recovery path and, if a kernel update is
   part of that separate transaction, reboot and verify it before testing v4.
4. **Choose the private migration first** when retaining the v3 per-game
   model. From the maintained `v4` checkout, `./install-v4.sh --upgrade-v3`
   migrates the standard old ICD while preserving its original bytes for
   rollback. Use `--upgrade-v3-icd PATH` for a source-built or custom v3 path.
   Guided game setup handles the supported game's Steam settings afterward.
   See the
   [installation and rollback commands](../README.md#private-archive-install-or-v3-upgrade).

The game-version recommendation is the combination actually qualified here,
not a claim that every earlier Proton release fails or every later one is
equivalent. Primary references: [GE-Proton11-6 options](https://github.com/GloriousEggroll/proton-ge-custom/blob/GE-Proton11-6/README.md#options),
[OptiScaler nightly 20260904](https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260904),
[OptiPatcher v0.41](https://github.com/optiscaler/OptiPatcher/releases/tag/v0.41),
and [Arch system maintenance](https://wiki.archlinux.org/title/System_maintenance#Partial_upgrades_are_unsupported).

**The newer FSR 4.1.1b mod is outside this setup and must not be co-installed.**
Undo a competing integration and restore its replaced game files before
switching. See [runtime compatibility](games.md#runtime-compatibility).

## What a v3 installation actually establishes

The [original v3 instructions](https://github.com/dmorazasanchez/bc250-fsr4/blob/6173651fa3a5a557cba2c2ff802e2d6f49881bc1/README.md)
describe a private Mesa **26.2.0** driver, tested on CachyOS/Arch with glibc
**2.44** and `libLLVM.so.22.1`. They leave the system driver untouched and
delegate game/OptiScaler options to the user. Consequently, a v3 user may
still have a different system Mesa, an old per-game Proton selection and
independently installed runtime DLLs.

The [v3 installer](https://github.com/dmorazasanchez/bc250-fsr4/blob/6173651fa3a5a557cba2c2ff802e2d6f49881bc1/install-v3.sh)
checks plain `ldd` output and makes `vulkaninfo` optional. The archived source
`check.sh` prints a suggested Vulkan command without running it. A successful
old setup therefore does not always prove that the actual driver loads all
required symbols or that a game uses the intended provider.

## Requirements and recommendations by component

| Component | Likely v3 state | What to do for v4 |
| --- | --- | --- |
| Private RADV | Mesa 26.2.0 selected through a per-game ICD | Migrate that ICD to the qualified v4 Mesa 26.2.2 archive; system Mesa need not have the same version. The archive must pass the host ABI/device probe. |
| System RADV | Not changed by upstream v3 | The optional package route requires an exact original **Mesa 26.2.2** `vulkan-radeon` package from this distribution. It is not required for private migration. |
| Vulkan loader and tools | Loader present for games; `vulkaninfo` may be absent | Install distribution `vulkan-tools` and keep the loader and any 32-bit companion coherent. The tested loader/tools were **1.4.357.0-1**; no universal minimum loader package version was established. |
| Python | `python3`, with no explicit v3 minimum | **3.12+** is required for v4 tooling. |
| glibc / C++ runtime | Original v3 binary tested on glibc 2.44 | The exact v4 ELF directly requires **GLIBC 2.38**, **GLIBCXX 3.4.29**, **CXXABI 1.3.9** symbols. Those are symbol floors, not a complete distribution compatibility guarantee or instructions to downgrade libraries. |
| LLVM | Original prebuilt v3 needs `libLLVM.so.22.1` | v4 uses ACO with LLVM disabled and has **no LLVM dependency**. Do not install, downgrade or remove LLVM solely for v4; other installed applications may need it. |
| Display/SPIR-V libraries | Original prebuilt v3 already needs `libdisplay-info.so.3` and `libSPIRV-Tools.so`; a source-built v3 may differ | The published v4 ELF needs those same libraries. Arch **libdisplay-info 0.3.0** supplies ABI 3. Keep matching distribution packages and let the eager probe check their symbols. |
| libdrm | Determined by the v3 build and userspace | A **native v4 source build requires libdrm/libdrm_amdgpu ≥2.4.133** in pinned Mesa 26.2.2. The tested host has **2.4.134**. Binary installation checks the actual linked symbols; a SONAME alone is not proof. |
| Kernel / firmware | A separately configured BC250 installation; v3 specifies no minimum kernel/firmware version | Keep a working BC250-supported kernel and firmware. **linux-cachyos-bc250 7.2.3-1.83** is the recorded test reference, not an established minimum. Kernel 7.3 RC, native-DOT experiments and firmware flashing are not v4 prerequisites. |
| Game runtime | Version and model selection may come from another guide | For these profiles, strongly prefer the exact GE/OptiScaler/OptiPatcher/provider combination above and verify native **4.1.1 INT8** engagement after launch. |

Keep distribution 32-bit RADV installed for other Steam/Wine workloads; v4
ships only x86_64. Never export its 64-bit-only ICD globally.

The kernel reference comes from the [recorded performance setup](performance.md).
If a separate kernel refresh is needed, the BC250 kernel maintainer identifies
[`linux-cachyos-bc250` as the stable/default family](https://github.com/MastaG/linux-cachyos-bc250#kernel-choices).
That is distinct from its RC/testing family. This guide does not establish
new firmware, CPU-unlock, clock or voltage requirements.

The binary ABI comparison uses the original v3 release ELF
`001c04f779191e59bac96efc2f61b0d068ef0e7f71b2afaae4df5c0c2f8c77bd`
and the [qualified v4 ELF](qualification.md#exact-driver-and-source).
Their direct GLIBC/GLIBCXX/CXXABI symbol floors are the same; v3's direct
shared-library list is v4's plus LLVM. Dependencies of those libraries may
require additional symbols. The original v3 ELF can still fail with an
LLVM-versioned C++ symbol even when `libLLVM.so.22.1` exists; this happened on
the qualification host and is not a reason to force an LLVM downgrade for v4.
Private rollback restores driver selection and migrated ICD bytes, not the
distribution libraries from before a system update. Retain the distribution's
recovery snapshot or other normal recovery method as well if you need to
return to that earlier complete v3 environment.

The [Arch libdisplay-info file list](https://archlinux.org/packages/extra/x86_64/libdisplay-info/files/)
confirms ABI 3. Source-build requirements come from the pinned Mesa archive's
`meson.build`: libdrm/amdgpu ≥2.4.133, libdisplay-info ≥0.1.1, and
SPIRV-Tools ≥2024.1 when enabled. A source build can adapt to a distribution's
available ABI, but still needs those development requirements; building in
the Arch container does not remove the resulting binary's host dependencies.

## If the distribution has moved beyond Mesa 26.2.2

Do not downgrade a coherent newer Mesa stack to fit the optional system
package route. Use the private archive if its checks pass, or build the
private source route against supported local dependencies. A system package
for a newer Mesa version needs an explicit source rebase and new qualification.
The system installer deliberately refuses a mismatched base version. See
[system updates and rollback](system-install.md#updates-and-rollback).

The final installation gate is [the v4 probe](../scripts/driver.py): it checks
x86_64/BC250 identity, resolves all relocations with `ldd -r`, and runs
`vulkaninfo --summary` with the selected ICD and `LD_BIND_NOW=1` before
activation. Khronos documents [eager symbol resolution](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderDriverInterface.md#additional-settings-for-driver-debugging)
as a way to expose missing driver symbols. Passing it establishes loader/ABI
readiness; [real game proof](game-troubleshooting.md#verify-a-real-game) remains a separate step.
