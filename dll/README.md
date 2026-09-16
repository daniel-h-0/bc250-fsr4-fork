# Rebuilding the RC11 DLL

For installation, use the [OptiScaler DLL guide](../docs/beginner-guide.md).
This directory contains the 348 editable LLVM/DXIL sources, pinned inputs,
assembler/validator and PE repacker used to build RC11.

## Inputs and commands

Use Python 3.11+, an x86-64 Linux C++ compiler (`g++`) and
[DXC v1.9.2607](https://github.com/microsoft/DirectXShaderCompiler/releases/tag/v1.9.2607).
Get the original SDK DLL from `sdk_url` in [manifest.json](manifest.json).
The builder verifies the SDK and `libdxcompiler.so` hashes. The DXC archive's
library is `linux_dxc_2026_07_29.x86_x64/lib/libdxcompiler.so`.

From the repository root:

```sh
python3 dll/build.py --verify-sources
python3 dll/build.py \
  --sdk /path/to/original/amd_fidelityfx_upscaler_dx12.dll \
  --dxcompiler /path/to/dxc/lib/libdxcompiler.so \
  --output .work/dll --jobs 2
python3 scripts/package-dll.py \
  --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll --output dist/dll
```

Choose a new output directory outside `dll/`. The builder hashes, assembles and
validates every shader, then checks the complete DLL: **94,840,832 bytes**,
SHA256 **8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175**.
Any mismatch stops the build.

`source-inventory.json` records every source file except itself and Python caches.
Extra/missing/changed files and symlinks fail verification. Update the inventory
when editing source or documentation. Changed shader/output bytes also require
new manifest identities and qualification. Source exports carry a separate
commit/file inventory.

## Shader implementation

The input is AMD SDK 2.3.0 at commit `60f4ea81909200d8542eca14dccb2628b763a9a3`,
using INT8 model 2. The SDK's `fp8_no_scale` entry-point name is retained; the
selected inference operations use the INT8 path.

| Component | Implementation |
| --- | --- |
| 72 model permutations | Packed INT8/16-bit arithmetic, bounded loops and original bias/scale reads, with model guards and dynamic-weight fallback. |
| 180 preparation permutations | Specialized image preparation. |
| 96 final-output permutations | Direct stores and retained color reuse. |

The assembly in this directory is the DLL's build input. [Mesa source lineage](../v4/manifest.json)
records the driver work from which the optimizations were derived.

| Checkpoint | Retained change / evidence |
| --- | --- |
| RC7 | Scalarized integer extensions in 48 inference shaders; [derivation](scalarize_casts.py) and [exhaustive lane tests](../tests/test_dll_release.py). |
| RC9 | Exact Winograd convolution in passes 1/2/4/10/12, bounded grouping, streamed accumulation and unsigned halfword extraction; [checkpoint](../docs/legacy/research/portable-dll-rc9.md). |
| RC10 | Pinned `early-cse`, `dce`, `strip-dead-prototypes` on 48 slots. The 36 distinct changed programs match native code on three Mesa builds; 300 slots retain RC9 bytes. [Measurements](../docs/legacy/research/portable-dll-rc10.md). |
| RC11 | RC10 shader programs, with provider label `4.1.1r11` and updated PE checksum. [Validation](../docs/portable-dll-rc11.md). |

The shaders use DXIL 1.9 / Shader Model 6.9. Testing covers BC250/Linux through
Proton; other runtimes/drivers need their own qualification.

## SDK and PE changes

[repack_dll.py](repack_dll.py) accepts the pinned SDK image and preserves its five
FFX exports, numeric provider version, imports and host implementation. It applies:

- The audited 18-byte INT8 eligibility change.
- A provider-name pointer at offset `0x4a5` to the appended RC11 label, retaining
  the original name slot and adjacent `FSR4-i8` watermark.
- The buffer-UAV synchronization mask at `0x4789`, changing `01` to `00` after
  verifying the complete instruction at `0x4782`. This keeps `addBarrier` active
  for non-null buffer UAVs, including padding clears. [SDK source](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/60f4ea81909200d8542eca14dccb2628b763a9a3/Kits/FidelityFX/backend/dx12/ffx_dx12.cpp#L3599)
  and [verification](../docs/legacy/research/portable-dll-rc7.md#sdk-synchronization-repair).

The repacker appends a read-only `.bc250` section, retargets shader pointers and
lengths, updates PE sizes/checksum and removes the invalidated Authenticode
certificate. It checks input bytes, relocation/pointer coverage and unique slots;
existing output files are protected. The sidecar records every updated RVA/pointer.

## Standalone compatibility probe

The [FFX probe](probe/README.md) exercises the API and GPU upscaler without a
game. Building it additionally needs SDK headers and Wine development/import
files. Normal platform shader compilation still occurs when rendering.

## Evidence and notices

The [release guide](../docs/releases.md#packaging) owns packaging and publication.
The packager requires the manifest-pinned DLL, matching guide identity and recorded
notices. Documentation refreshes use distinct `-docsN` filenames with the same DLL.

Retain [AMD's license](notices/AMD-SDK-LICENSE.md), including its MIT exception for
the upscaler DLL, and [provenance](notices/PROVENANCE.md) for AMD, DXC, Mesa/v4 and
inherited BC250 work. Source/binary distributions contain project inputs and
notices; privately obtained game artifacts stay outside them.
