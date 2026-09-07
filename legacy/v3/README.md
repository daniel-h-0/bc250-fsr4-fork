# BC-250 FSR4 V3 — Deferred SDot Hybrid

Experimental Mesa RADV build for the **AMD BC-250 / GFX1013 (PCI ID `1002:13FE`)**, focused on making **FSR 4 INT8** substantially faster without enabling the BC-250's broken native signed packed-dot path.

**V3 is based on Mesa 26.2.0 and the runtime-validated EXP-042B hybrid.**

> BC-250 / GFX1013 only. Use this per-user/per-game. **Do not replace your system Mesa with this library.**

## V3 result

Same-scene Cyberpunk 2077 development test, FSR 4.1.1 INT8, frame generation OFF:

| Development build | FPS |
|---|---:|
| EXP-035B2 | 58 |
| EXP-040E | 59 |
| EXP-042A | 61 |
| **EXP-042B / V3** | **63** |

These are local same-scene development measurements, not a universal game benchmark.

## What changed

V3 combines the best runtime-validated parts of the investigation:

- FSR4 `iadd(0, SDot)` wrapper fusion so the software fallback can optimize the real accumulator path.
- Signed i24 `MUL24/MAD24` lowering. Native `v_dot4_i32_i8` remains disabled.
- A GFX1013-only dense-reduction pre-pass for shapes that improved without losing occupancy.
- Two shorter dependency chains for pressure-sensitive reduction families.
- A **deferred-SDot optimization round**: remaining signed packed dots survive one NIR optimization round, then the real GFX1013 capability set is restored and they are lowered in software.
- ACO support required by the tested `imad24_ir3` path.
- The BC-250 compute-queue/base compatibility changes used by the tested runtime build.

The key safety rule is unchanged: **V3 does not expose or use the broken native signed packed-dot instruction.** Direct BC-250 testing showed native `v_dot4_i32_i8` returned `0` for a case whose correct result is `70`.

## Why V3 is faster

The pathological captured shader `ed7...` changed from:

- 256 -> 168 VGPR
- 1314 -> 0 VGPR spills
- 320512 -> 0 scratch bytes
- 4 -> 6 waves/SIMD
- 21750 -> 16904 instructions

Across the 64-shader FSR4 capture, EXP-042B changed 13 shaders:

- 12 lower the static inverse-throughput estimate
- 1 (`ed7`) has a slightly worse static inverse estimate but removes the catastrophic VGPR spill/scratch behavior above
- 0 new VGPR spill regressions
- 0 shaders lose resident waves

The game result is authoritative: EXP-042B reached **63 FPS** in the same scene where EXP-042A reached 61 FPS.

## Easiest install — precompiled release

The release contains the **exact runtime-tested V3 RADV binary** packaged for the current CachyOS/Arch-style LLVM 22 stack.

Install without replacing system Mesa:

```bash
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v3/install-v3.sh | bash
```

The installer:

1. checks x86_64 and, when `lspci` is available, BC-250 PCI ID `1002:13FE`
2. downloads the V3 release archive
3. verifies its SHA256
4. installs under `~/.local/share/bc250-fsr4/v3`
5. generates a private Vulkan ICD
6. checks dynamic dependencies
7. runs `vulkaninfo --summary` when available
8. prints the Steam Launch Option

No `sudo` is required and no system Mesa file is overwritten.

### Steam

After installation:

```text
VK_DRIVER_FILES="$HOME/.local/share/bc250-fsr4/v3/radv-bc250-fsr4-v3.json" %command%
```

Add any game/OptiScaler-specific options after that as usual.

### Uninstall / rollback

```bash
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v3/uninstall-v3.sh | bash
```

Then remove `VK_DRIVER_FILES=...` from the game's Steam Launch Options. Your system RADV installation was never modified.

## Precompiled binary compatibility

The precompiled release is the exact binary tested on:

- CachyOS / Arch-style rolling userspace
- x86_64
- glibc 2.44
- LLVM 22.1 (`libLLVM.so.22.1`)
- Mesa 26.2.0 source base
- AMD BC-250 / GFX1013

It also dynamically uses normal Vulkan/Mesa userspace dependencies such as libdrm, libelf, Wayland/XCB, zlib/zstd and SPIR-V Tools.

The installer runs `ldd` and refuses to present an incompatible binary as working. If your distribution has a different LLVM/ABI stack, use the reproducible source build below.

## Reproducible source build

Requirements:

- Docker
- x86_64 host, or Docker buildx/QEMU on ARM
- BC-250 for runtime validation

```bash
git clone -b v3 --single-branch https://github.com/dmorazasanchez/bc250-fsr4.git
cd bc250-fsr4
./build-anywhere.sh
./setup.sh
./check.sh
```

The source builder checks out Mesa 26.2.0 and applies `bc250-fsr4-v3.patch`.

Test it without touching system Mesa:

```bash
./run-bc250-fsr4.sh vulkaninfo --summary
```

## Source layout

- `bc250-fsr4-v3.patch` — complete V3 delta against Mesa 26.2.0
- `build-anywhere.sh` — reproducible Docker build entry point
- `build-bc250.sh` — applies V3 and builds RADV
- `install-v3.sh` — precompiled per-user installer
- `uninstall-v3.sh` — removes the per-user V3 installation
- `setup.sh` / `check.sh` — source-build ICD generation and validation
- `V3.md` — technical investigation notes
- `V2.md` and V2 patches — retained as historical material

## Safety / scope

This is experimental software for a very unusual GPU. It has been tested specifically on:

```text
AMD BC-250
GFX1013
PCI ID 0x13FE
Mesa 26.2.0
FSR 4.1.1 INT8
```

Games can still crash, hang, show corruption or reset the GPU.

Do not enable Mesa's native signed packed-dot path on BC-250. V3 deliberately does not do that.

## Credits

Thanks to the BC-250 community for reverse engineering, testing and sharing results.

Special thanks to **higorprado** for earlier packaging/benchmarking contributions and to the MastaG BC-250 work for the compute-queue/RADV compatibility base used during development.
