# Provenance

The base DLL and shader/model material come from AMD FidelityFX SDK v2.3.0,
commit `60f4ea81909200d8542eca14dccb2628b763a9a3`. The exact input URL and SHA256
are in `dll/manifest.json` in the complete source distribution. `AMD-SDK-LICENSE.md` is the complete upstream notice;
`Kits\FidelityFX\signedbin\amd_fidelityfx_upscaler_dx12.dll` appears explicitly
in its list governed by the MIT permission text at the end.

The shader algorithms continue the v4 BC250 work in
<https://github.com/daniel-h-0/bc250-fsr4-fork>, including the pinned
patches retained under `v4/patches/` in the complete source distribution. The fork preserves the original work of
dmorazasanchez/bc250-fsr4 and its contributors. `V4-THIRD-PARTY.md` records that
lineage and the distinction between new tools and inherited material.
RC12's 34 new programs (the 1080p and 8K context-size families) are generated from
the SDK's own DXIL by this project's lowering tools (`dll/port`), which apply the
RC8/RC9 work of the 1440p–4K family.

Mesa's shader/compiler contributions retain their per-file notices in the
patches. This source package does not contain a Mesa runtime or a Proton runtime.

The LLVM/DXIL assembly is assembled and validated with Microsoft's DXC
1.9.2607. `LICENSE-MS.txt` and `LICENSE-LLVM.txt` are its original notices.
DXC is obtained separately for rebuilding and is not needed beside the DLL.

New tools explicitly carrying `SPDX-License-Identifier: MIT` use
`LICENSE.new-code`. This is not a blanket license declaration for the package.
No game shader dumps, game files, saves, account data or credentials are included.

## Host-code edits

The DLL keeps AMD's host code except for these edits. Offsets are file offsets
in the SDK 2.3.0 DLL; `dll/port/profiles/sdk-2.3.0.json` pins every byte.

| Edit | Bytes | In | Origin |
| --- | --- | --- | --- |
| INT8 eligibility | 18, in two runs at `0x8189` and `0x8191` | RC7–RC12, unchanged | The third-party "4.1.1b" DLL (below) |
| Buffer-UAV barrier | 1, at `0x4789` (`01` → `00`) | RC7–RC12, unchanged | This project (below) |
| Provider label | RC7–RC9: the SDK's 8-byte name slot. RC10–RC12: the 4-byte displacement (`0x4a8`) of the instruction at `0x4a5`, pointing to a name in the appended section | RC7–RC12 | This project. The name form (`4.1.1` + suffix) follows 4.1.1b's `4.1.1b` |

### INT8 eligibility

The SDK offers its INT8 FSR4 provider only on GPUs its compatibility check
accepts. The edit replaces the values that check reads with constants and takes
its success path, so the INT8 provider is also offered on GPUs such as the BC250.
Without it the SDK selects FSR 3.1.5 there.

- **Origin.** The 18 bytes equal the host-code change of a community-modified
  FSR4 DLL distributed as "4.1.1b" (34,013,696 bytes, SHA256
  `0dd77d9c78d1ef9bc330cf4697ab3ffe24bc1aa7850e4130263dc922107fbd75`). Its author
  and licence are unverified. This project compared that DLL with the SDK on
  2026-09-06 and adopted the edit in its first standalone DLL and in RC7.
- **RC12.** Unchanged from RC11. An independent re-derivation from the SDK
  code alone was attempted twice on 2026-10-23 and did not complete.
  `dll/rederived.json` records the element as pending; its comparison build
  (`dll/build.py --config rederived`) keeps the SDK's bytes and does not offer the
  INT8 provider on the BC250. On 2026-10-24 the maintainer (daniel-h-0) chose to
  keep RC11's edit for RC12. `dll/rc12.json` applies it unchanged from the drop profile.

### Buffer-UAV barrier

SDK 2.3.0's padding-clear compute jobs set `FFX_GPU_JOB_FLAGS_SKIP_BARRIERS`, so
a clear can overlap the preceding model dispatch on the shared scratch buffer
([SDK source](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/60f4ea81909200d8542eca14dccb2628b763a9a3/Kits/FidelityFX/backend/dx12/ffx_dx12.cpp#L3599)).
The edit clears one mask byte so the SDK's existing `addBarrier` call also runs
for those jobs' non-null buffer UAVs.

- **Origin.** This project's own. RC7's review (2026-09-10) traced intermittent
  small-image differences to this skipped barrier. With the barrier, AMD's
  original INT8 shaders and this project's produced the same image with the
  scratch buffer initialized to zero or to `0x3f` bytes.
- **Not from 4.1.1b.** That DLL's executable code differs from the SDK's only in
  the 18 eligibility bytes (comparison of 2026-09-06); its byte at `0x4789` is
  the SDK's.

## Final-pass divisions

RC9–RC11 replaced two `sdiv i32 x, 2` with `lshr i32 x, 1` in final-pass slot
`0x248220`, an idea taken from 4.1.1b's final-pass shaders. RC12 restores AMD's
division there and keeps it in every other final-pass program, including the new
1080p and 8K ones.

- The dividends are the output width and height from a constant buffer.
  `dll/port/sdiv_shift.py` cannot prove them non-negative from the shader.
- The host code copies the caller's `upscaleSize` (two `uint32_t`) into them
  without a check. A component of 2^31 or more makes the dividend negative, so
  the shift is exact only for in-spec input. Every such dispatch is empty or
  exceeds D3D12's limit of 65,535 groups per dimension.
- Keeping the division costs nothing measurable on the BC250: +0.002 ± 0.001 ms
  per frame at 1440p Performance, where `0x248220` is the only program that differs
  from RC11, within the run-to-run spread of identical DLLs.

No 4.1.1b shader is in the DLL. Of its shader changes, only this shift was
adopted (RC9–RC11); RC12 does not contain it.
