# INT8 dot-product micro-benchmark (GFX1013 / BC-250)

Headless Vulkan compute benchmark for packed 4x8 integer dot operations:

- `OpSDotKHR`
- `OpUDotKHR`
- `OpSUDotKHR`

This benchmark originated in PR #1 by `higorprado` and is included here as a diagnostic tool for v2 and future experiments. The v2 copy fixes two Vulkan-validity issues found during review: it explicitly enables `shaderIntegerDotProduct` when creating the device, and it chooses a compatible host-visible/coherent memory type from each buffer's `memoryTypeBits`.

## What it measures

Each kernel runs 262144 lanes. By default each lane executes a dependent chain of 16 dot products for 16384 loop iterations (~68.7 billion dot products per dispatch).

Operands are XOR-modified by the loop counter to discourage hoisting/CSE. The benchmark warms the GPU before timing, reports the active `pp_dpm_sclk` entry when available, and verifies sampled output lanes against a CPU implementation.

This is a micro-benchmark. It is useful for checking lowering throughput and correctness, but it does **not** predict FSR4 game performance by itself; EXP-028 showed that whole-shader register pressure and spill behavior can matter more than isolated dot throughput.

## Requirements

- Vulkan 1.3 headers and loader
- `spirv-tools` (`spirv-as`, `spirv-val`, `spirv-dis`)
- C compiler (`gcc` or `clang`)
- a Vulkan device exposing `shaderIntegerDotProduct`

## Build and generate kernels

From this directory:

```bash
python3 gen_kernels.py 8 64 _mini
python3 gen_kernels.py

gcc -O2 -Wall -Wextra -o bench bench.c -lvulkan
```

## Run

Quick correctness test:

```bash
VK_DRIVER_FILES=/path/to/radv-bc250-fsr4.json ./bench dot_sdot_mini.spv sdot 64 8 fast
```

Performance run:

```bash
VK_DRIVER_FILES=/path/to/radv-bc250-fsr4.json ./bench dot_sdot.spv sdot 16384 16
VK_DRIVER_FILES=/path/to/radv-bc250-fsr4.json ./bench dot_udot.spv udot 16384 16
VK_DRIVER_FILES=/path/to/radv-bc250-fsr4.json ./bench dot_sudot.spv sudot 16384 16
```

For the system driver, omit `VK_DRIVER_FILES`.

Always compare at the same GPU clock and repeat runs. A result is only usable if `verify: PASS` is printed.

## Historical PR #1 numbers

PR #1 reported the following on its **Mesa 26.1.6** branch with the GPU at 2150 MHz. These numbers are preserved for provenance only; they are **not v2 / EXP-028 results** and should be re-measured on Mesa 26.2.0.

| kernel | system 26.3.0-devel | PR pre-v2 26.1.6 | PR modified 26.1.6 |
|---|---:|---:|---:|
| sdot | 648 Gdot/s | 694 Gdot/s | 694 Gdot/s |
| udot | 650 Gdot/s | 650 Gdot/s | 696 Gdot/s |
| sudot | 648 Gdot/s | 209 Gdot/s | 695 Gdot/s |

Do not infer from those historical results that global reassociation is desirable for EXP-028. The FSR4 shader-corpus work on Mesa 26.2.0 found pathological signed-dot kernels where global signed reassociation massively increases VGPR pressure, spills and scratch usage; v2 intentionally uses selective signed lowering instead.
