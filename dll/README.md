# Rebuilding the RC7 DLL

The ordinary download is one DLL. This directory is its developer source:
348 complete editable LLVM/DXIL assembly files, the pinned input manifest,
the assembler/validator client, and a small PE repacker. These files rebuild
the distributed DLL without a GPU, Wine, Proton or Mesa installation.

## Inputs and commands

Use Python 3.11+, an x86-64 Linux C++ compiler (`g++`), and the official
[DXC v1.9.2607 Linux release](https://github.com/microsoft/DirectXShaderCompiler/releases/tag/v1.9.2607).
Obtain the original SDK DLL from the exact `sdk_url` in [manifest.json](manifest.json).
The builder checks both the SDK and `libdxcompiler.so` SHA256 before running.
DXC's own host-library requirements still apply to the build machine.
The pinned archive extracts into `linux_dxc_2026_07_29.x86_x64/`; its library
is `lib/libdxcompiler.so` inside that directory.

From the repository root:

```sh
python3 dll/build.py --verify-sources
python3 dll/build.py \
  --sdk /path/to/original/amd_fidelityfx_upscaler_dx12.dll \
  --dxcompiler /path/to/dxc/lib/libdxcompiler.so \
  --output .work/dll --jobs 2
python3 scripts/package-dll.py \
  --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll --output dist/dll
python3 scripts/package-dll.py \
  --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll --output dist/dll --format tar.xz
```

The output directory must be new and outside `dll/`. Nothing is installed.
Every assembly source is hashed, assembled, validated by DXC, and compared
against its expected shader hash. The complete DLL must be **115,176,448 bytes**,
SHA256 **730c175a38b0f0271ffaa201ca531825c6440a95fb71729d34566c66af8e4893**.
Compiler diagnostics or any mismatch stop the build.

`source-inventory.json` records all source files in this directory except
itself and Python bytecode caches. Extra files, missing files, content changes
and symlinks are rejected. An edited candidate requires deliberate new
shader/output identities and a regenerated inventory; never relabel changed
bytes with the existing release hash. The complete Git source export adds its
own independent file-hash/mode inventory.

## Shader implementation

The source comes from the AMD SDK 2.3.0 DLL at commit
`60f4ea81909200d8542eca14dccb2628b763a9a3`, using its INT8 model path.
The old `fp8_no_scale` entry-point spelling is retained from the SDK; it does
not mean that these selected inference operations use FP8 hardware.

The v4 shader port covers 72 model permutations, 180 image-preparation
permutations and 96 final-output permutations. It carries packed INT8/16-bit
arithmetic, bounded model-loop unrolling, original literal-family bias/scale
reads, image preparation and direct final-output stores into shader bytecode.
The guarded specializations retain the dynamic-weight fallback. The complete
v4 patches and their provenance remain under [v4/](../v4/manifest.json).
The editable assembly here is the authoritative input to this DLL build;
replaying a host Mesa compilation is not a build prerequisite.

RC7 changes only the spelling of 17,964 vector integer extensions in 48
inference shaders: extract each 16-bit lane, extend it to 32 bits with the same
signedness, then reassemble the two lanes. Packed additions, multiplications,
shifts, bitcasts, wave operations and guards stay intact. This avoids the
unsupported vector-cast path in older vkd3d translators. The derivation helper
is [scalarize_casts.py](scalarize_casts.py). The executable CPU test in
[test_dll_release.py](../tests/test_dll_release.py) checks every 16-bit value
in both lanes against original LLVM and mathematical signed/unsigned results.

The shaders retain DXIL 1.9 / Shader Model 6.9. RC7 does not disguise them as
older shader-model bytecode. Native Windows driver support needs separate
qualification; success through Proton does not establish native driver support.

## SDK and PE changes

[repack_dll.py](repack_dll.py) accepts only the pinned SDK image. It preserves
the SDK's five public FFX exports, numeric provider version, imports and host
implementation. It makes the audited 18-byte INT8 eligibility change, and
changes the existing eight-byte display-label slot to `4.1.1r7` plus its NUL.
It does not add a compatibility loader shim or frame-generation implementation.

One additional instruction-immediate byte repairs SDK synchronization. The
SDK's buffer-UAV binding loop normally skips `addBarrier` for jobs marked
`FFX_GPU_JOB_FLAGS_SKIP_BARRIERS`. RC7 keeps that existing call active for
every non-null buffer UAV, including padding clears. This orders the preceding
model dispatch before a clear can overwrite shared scratch data. The texture
and SRV policies, command-list interface and shader arithmetic are unchanged.
The builder verifies the complete eight-byte instruction at file offset
`0x4782`, then changes only its mask byte at `0x4789` from `01` to `00`.
The [SDK source](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/60f4ea81909200d8542eca14dccb2628b763a9a3/Kits/FidelityFX/backend/dx12/ffx_dx12.cpp#L3599)
and the [GPU investigation](../docs/portable-dll-rc7.md#sdk-synchronization-repair)
explain the policy and its verification.

The repacker appends a read-only `.bc250` section containing the validated
shader containers, retargets the SDK's existing relocated pointers and lengths,
updates PE sizes/checksum, and removes the now-invalid Authenticode certificate.
It rejects missing shader pointers, missing DIR64 relocations, duplicate
replacement slots, changed input bytes and existing output DLL/record files.
The sidecar build record lists every new shader RVA and updated pointer.

There is no runtime shader rewrite service, runtime compiler, configuration
writer or private driver in this DLL. Normal D3D12 shader translation and
compilation by the platform still occur, as with any D3D12 upscaler.

## Standalone compatibility probe

The [probe source and instructions](probe/README.md) reproduce the FFX API
and synthetic whole-upscaler workload used in the compatibility and timing
checks. It can help qualify native Windows or another GPU without a game.
The optional probe build needs SDK headers and Wine development/import files;
these are separate from rebuilding or installing the DLL.

## Evidence and notices

[Compatibility and measurements](../docs/portable-dll-rc7.md) distinguish this
candidate's checks from the preceding native-game and v4 driver campaigns.
Performance measurements are not inferred from a source version number.
The release packager accepts only the exact DLL hash and includes all recorded
notices. Both archive formats have deterministic contents and adjacent checksums.

AMD's full mixed-license notice, including its explicit MIT exception for the
upscaler DLL, is retained under [notices/AMD-SDK-LICENSE.md](notices/AMD-SDK-LICENSE.md).
[Provenance](notices/PROVENANCE.md) records AMD, DXC, Mesa/v4 and inherited
BC250 attribution. Game files, game shader dumps, saves and account data are
excluded from the complete source and binary distributions.
