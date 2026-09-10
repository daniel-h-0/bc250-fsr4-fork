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

Mesa's shader/compiler contributions retain their per-file notices in the
patches. This source package does not contain a Mesa runtime or a Proton runtime.

The LLVM/DXIL assembly is assembled and validated with Microsoft's DXC
1.9.2607. `LICENSE-MS.txt` and `LICENSE-LLVM.txt` are its original notices.
DXC is obtained separately for rebuilding and is not needed beside the DLL.

New tools explicitly carrying `SPDX-License-Identifier: MIT` use
`LICENSE.new-code`. This is not a blanket license declaration for the package.
No game shader dumps, game files, saves, account data or credentials are included.
