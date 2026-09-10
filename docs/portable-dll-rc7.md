# RC7 portable DLL: compatibility and review

RC7 makes the optimized **FSR 4.1.1 INT8 DLL** the primary product. Users
replace one compatible upscaler DLL. The performance implementation is
compiled into that file; a custom Mesa package or custom Proton tool is no
longer required. The [installation guide](../dll/INSTALL.md) accompanies the
binary download.

```mermaid
flowchart LR
  native[Native FidelityFX game] --> dll[RC7 upscaler DLL]
  input[DLSS or FSR input] --> adapter[Optional OptiScaler adapter]
  adapter --> dll
  dll --> platform[Normal D3D12 runtime and driver]
```

The same DLL supplies both routes. OptiScaler adapts an application's input
API, including supported DX11/Vulkan interop routes. It is not where RC7's
shader performance changes live. The compact tar.xz and ZIP are two formats
containing the same DLL, instructions, checksums and notices; neither is an
installer or a Steam compatibility tool.

## Supported scope

The qualified hardware is **AMD BC250 / RADV GFX1013 on CachyOS Linux**.
The DLL itself is a Windows x64 PE image. Ordinary D3D12 translation and
driver shader compilation are still required by the platform.

| Context | Evidence and limit |
| --- | --- |
| Direct FFX API / D3D12 | Context creation, GPU dispatch, finite full-image readback and destruction on ordinary Proton 10/11, Experimental, Hotfix and GE-Proton |
| Control / DX12 DLSS | Final RC7 rendered a saved scene with the existing 1440p ray-tracing/HDR settings; fourteen matching shaders |
| System Shock / DX11 DLSS | Final RC7 rendered the 3D title menu at 1440p; fourteen matching shaders |
| No Man's Sky Cosmos / Vulkan DLSS | Final RC7 rendered a saved scene at 1080p Balanced on a restart; fourteen matching shaders; first-compilation hang described below |
| Deadzone Rogue / native FFX | Final RC7 rendered its menu and an existing Zone 1 scene at 1440p Balanced; sixteen matching shaders |
| KCD2 / native FFX | Final RC7 rendered an existing saved scene at 1440p FSR 4.1 Quality; fourteen matching shaders; requires the loader filename and explicit FSR selection |
| Roboquest / DX11 Luma | Final RC7 rendered the existing basecamp at 1440p Native AA through the retained Luma/ReShade combination; fourteen matching shaders |
| DOOM: The Dark Ages / Vulkan FFX | Final RC7 rendered the 3D main menu at 1440p FSR Performance with HDR enabled and frame generation disabled; fourteen matching shaders |
| Native Windows and other GPUs | Unqualified; external testing is required |
| Frame generation, ray regeneration, other mod combinations | Outside this DLL candidate's qualification |
| Heroic, Steam Flatpak, other sandboxes or Linux distributions | No separate launcher, operating-system or sandbox qualification; make the DLL and adapter files accessible inside the application's environment |

These final-DLL game follow-ups use ordinary **GE-Proton 11-6**, the normal
Steam prefixes, and the installed Mesa driver (`6bc07c5a…`). They exclude the
separate `amdxcffx64.dll` driver provider and use no custom Proton manifest or
private driver override. The separate API, image and timing checks establish
operation with standard Mesa (`38742ee1…`). Every completed follow-up includes
actual rendering, the mapped DLL identity, the complete matching model shader
family, and a normal exit through the game's menus.

Earlier System Shock and No Man’s Sky records used the preceding RC7 build
(`b68c1c0b…`) before the SDK barrier repair. Earlier native checks used
`066fc2a4…`. Their original scopes remain in the evidence; the follow-ups above
check the final `730c175a…` DLL itself.

**No Man's Sky first-compilation caveat:** the first in-game change from Off
to DLSS recorded a 65.96-second first-dispatch stall and the game's
`0x1106-HANG` report. No kernel GPU fault or reset was recorded. A fresh launch
with the same DLL, Proton, driver and compiled shader cache rendered the saved
scene and exited normally. That establishes a successful restart, not a
universal cold-start guarantee. Shader dumping and diagnostic logging were
enabled for identification; these game checks are not performance trials.

The exact runtime identities, shader/image hashes and measured results are
in [the candidate record](data/portable-dll-rc7.json). A menu or scene check is
bounded functionality evidence, not an endurance test or an installation
allowlist. Untested game/renderer combinations need separate checks.
Previous RC3 runtime gameplay results do not automatically transfer to this DLL.

## Proton compatibility change

The preceding optimized DLL failed when ordinary Proton 10 and 11 attempted
to translate vector integer extensions. It created the SDK context, then
failed during shader translation. Fresh private prefixes reproduced that
failure, excluding stale-prefix state as its cause.

RC7 lowers **17,964 `sext`/`zext` operations in 48 model shaders** from
two-lane 16-bit vectors to separate scalar extensions followed by vector
reassembly. The other 300 shaders retain their previous compiled hashes.
Packed arithmetic, shifts, bitcasts, wave policy, model guards and memory
operations are unchanged. This preserves each lane's signedness and every
input bit pattern.

Executable LLVM checks cover all 65,536 16-bit values in each lane for both
signed and unsigned extensions. GPU comparisons match the original INT8
reference exactly on the previously failing translator. All 348 rebuilt
shader containers pass the pinned DXC validator.

The shaders still use **DXIL 1.9 / Shader Model 6.9**. They are not relabeled
as older bytecode. Native Windows requires a D3D12 runtime and driver that
accept those shaders. Linux success through vkd3d does not establish native
Windows support. Other GPUs may have different wave or integer performance;
the BC250 measurements below are not predictions for them.

## SDK synchronization repair

Review found an intermittent small-image error in the preceding optimized
DLL. The cause was the SDK's padding-clear scheduling: its compute jobs set
`FFX_GPU_JOB_FLAGS_SKIP_BARRIERS`, allowing a clear to overlap preceding model
work on the shared scratch buffer. The
[pinned SDK buffer-UAV binding loop](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/60f4ea81909200d8542eca14dccb2628b763a9a3/Kits/FidelityFX/backend/dx12/ffx_dx12.cpp#L3599)
contains the skipped barrier call.

RC7 changes one instruction-immediate byte so this existing call remains
active for non-null buffer UAVs. It adds no new code section, runtime hook,
configuration option or installed component. The texture and SRV policies
and all shader arithmetic remain unchanged.

The diagnosis used the original INT8 shaders as a control. With the missing
UAV barriers supplied before padding clears, both implementations produced
the same four-frame 192×144 image even when the entire scratch buffer began
with either zero or `0x3f` bytes. Its SHA256 is
`d52cb63f378dc0eb3f90044884534f91c6fec208919c9060ab5618d5bf107c9b`.
The final DLL produces that image without probe-supplied barriers. The earlier
`d6b3c5...` hash belonged to the unsynchronized path and is superseded for this
small test. The [optional probe audit](../dll/probe/README.md#observe-the-sdk-barrier-repair)
observes the SDK's barriers without adding GPU commands.

The investigation also ruled out changing shader-cache bytes and rejected
explicit discard-store guards and forced compiler waits as fixes. Those
diagnostic variants are not included in the release.

## Ordinary OptiScaler setup

Install the upstream adapter normally, then replace its upscaler backend
with RC7. The [short guide](../dll/INSTALL.md#with-optiscaler) gives the FFX
backend, INT8 model and linear-color settings. Retain upstream game-specific
input/spoofing options. Frame generation was off in these checks.

The adapter used for the recorded game checks is the unmodified
OptiScaler 10.0.0-pre1 nightly from September 4, 2026; its WinMM proxy hash is
`469bcfe59108f04f8b8cf45953e515f0a0cd24b00717be62b7dcf5c20430410d`.
OptiPatcher 0.41 and the ordinary signed NGX helper accompany that adapter.
These components are obtained separately and are not bundled in RC7.

**Signature-helper placement matters.** System Shock did not expose DLSS
when the test kept the helper only in an external library directory, even
with `Libraries.NvngxDlssPath` set. Placing the signed `nvngx_dlss.dll`
beside the proxy restored NGX initialization and actual DX11-to-D3D12 FSR
dispatch. The initial incomplete-layout trials remain negative evidence.
This is why the guide starts with a working upstream adapter installation.

Some Proton releases ship their own optional `amdxcffx64.dll` under `contrib`.
Its presence does not identify the selected rendering backend. The installed
follow-ups disabled that provider through `WINEDLLOVERRIDES`, and still
rendered using the complete expected RC7 shader family. No Man's Sky used
ordinary Vulkan capability spoofing to expose DLSS, and excluded unsupported
`VK_NVX_binary_import,VK_NVX_image_view_handle` extensions only from vkd3d
through `VKD3D_DISABLE_EXTENSIONS`. Its existing signed NGX helper was retained.
No RC6 driver-provider or custom Proton manifest was used.

**Roboquest's retained mod combination:** Luma Unreal Engine `latest-623`
(add-on SHA256 `cee6b0e72d14673281cafae65b18803935b92d6121abaa5791e410a4e2b79583`)
and ReShade 6.8.0.1 remained loaded. The existing Luma HDR and DLSS selection
fed OptiScaler's DX11-to-D3D12 path at 2560×1440 Native AA. Upstream
`Plugins.LoadReshade=true` loaded that existing installation, with
`CreateD3D12DeviceForLuma`, `RestoreComputeSignature`, `RestoreGraphicSignature`
and `ExtendedStateRestore` left false. This qualifies the recorded combination,
not arbitrary Luma builds or ReShade effects.

Enable the temporary FSR watermark or inspect actual adapter dispatch logs
to distinguish the local `4.1.1r7` path from a fallback. DLL loading alone is
insufficient. Leave the default linear-input policy in place unless the
application integration specifically supplies a different color encoding.
Set the optional sRGB/PQ child controls to `auto`; forcing those child keys
to `false` has different semantics in this OptiScaler version.

## Native game loaders

The DLL exports the same five FFX entry points and retains the numeric
SDK/provider identity. It implements upscaling, not every FidelityFX effect.
Native loader compatibility must be checked against the game integration.

| Game | Replacement location | Verified native integration |
| --- | --- | --- |
| Deadzone Rogue 1.4.2.0 | `Valhalla/Binaries/Win64/amd_fidelityfx_upscaler_dx12.dll` | Rendered native-FSR menu and matching shader hashes |
| KCD2 1.5.6 | `Bin/Win64Shared/amd_fidelityfx_loader_dx12.dll` | Same DLL bytes under this filename; rendered a saved scene with FSR 4.1 Quality, preserving the original upscaler file |

Both native routes passed follow-up checks with the final RC7 DLL
`730c175a…` on ordinary GE-Proton 11-6 and the normal Steam prefixes.
Deadzone rendered its menu and an existing Zone 1 scene with native FSR
Balanced at 2560×1440; sixteen SDK shader hashes match RC7. KCD2 rendered
its existing saved scene with FSR 4.1 Quality at 2560×1440; fourteen hashes
match. Both exited through their menus. These follow-ups used the installed
Mesa driver (`6bc07c5a…`); the separate API and timing checks establish
standard-Mesa operation. The earlier native checks used `066fc2a4…` and
remain historical evidence.

KCD2's previous DLSS selection resolved to Off after the adapter was removed.
Selecting **FSR 4.1 / Quality** activated the native route. Wait for initial
shader compilation after applying the selection; a loaded replacement DLL
with no matching dispatched shaders is insufficient evidence.

KCD2's older loader called an incompatible private provider-vtable slot when
only the upscaler file was replaced. Calling the new SDK at the loader entry
point avoided that old private interface. Do not generalize this rename to
games whose loader also services frame generation or other FidelityFX effects.
Restore the original file if a native integration fails; use a separately
qualified adapter route when available.

## Correctness and performance

The RC7 comparison used a full-upscaler D3D12 timing probe and 240 frames per
run. At each output size the order was existing v4, v4 with the same SDK
synchronization repair, DLL, DLL, repaired v4, existing v4. It used ordinary
GE-Proton and a standard Mesa driver for the DLL, with the retained v4 driver
as control. Debug shader dumping and per-pass tracing were off in scored runs.

| Output | v4 GPU time | RC7 DLL GPU time | Difference |
| --- | ---: | ---: | ---: |
| 1920×1080 | 3.73320 ms | 3.77562 ms | +1.14% |
| 2560×1440 | 6.57114 ms | 6.61629 ms | +0.69% |
| 3840×2160 | 13.61564 ms | 13.75154 ms | +1.00% |

The table compares the existing v4 driver path with the corrected RC7 DLL. Each
entry is the mean of two per-run medians. All eighteen scored full output
images were byte-identical within their output-size groups. This is practical
parity within 1.14% for these checks, not a formal statistical equivalence
bound or a whole-game FPS measurement.

For the comparison with the same SDK repair applied to both sides, v4 measured
3.78318, 6.62189 and 13.54739 ms respectively. The DLL's differences were
−0.20%, −0.08% and +1.51%. The record includes all 4,320 frame timestamps and
both comparisons. Absolute timings drifted between campaigns; only matched
controls in this campaign are used.

The small-image mismatch found during review is addressed by the SDK
synchronization repair above. Larger scored outputs retained their image
hashes after the repair. Size and temporal checks have separate records;
performance parity does not establish correctness at every possible size.

The final DLL's image matrix contains 20 cases and 54 complete images,
compared with the original SDK shaders using the same synchronization repair
and standard Mesa. Seventeen cases, containing 42 images, match exactly.
They cover tiny and standard sizes, sharpening, HDR, motion, resets, dynamic
resolution and an 8K context reserve. The formerly inconclusive 2400×900 and
600×1800 outputs now pass repeated comparisons.

Three cases remain unqualified: 193×145, 2401×901 and 193×145 with the large
context reserve. The original-shader reference varies on repeat in each case,
even with the barrier repair. These are recorded limits, not passes or a
newly inferred blanket alignment rule. All six guarded-model families
previously passed modified runtime-weight and fast-path witness checks;
RC7 preserves those guards and fallbacks, with the documented integer-cast
and host-synchronization changes.

## Review and reproduction

The [developer guide](../dll/README.md) describes the source inventory, SDK
host-byte changes, PE pointer/relocation checks and exact rebuild. The new
tests exercise integer-extension equivalence, input-boundary failures,
deterministic packaging, checksum contents and separate DLL/runtime release
identities. The CI DLL job rebuilds the same pinned source without GPU access.

Qualification reports should name the DLL hash, GPU, OS/driver, Proton or
D3D12 runtime version, game/version, renderer, adapter version and input,
selected FSR mode, visible output and normal exit result. A Windows/other-GPU
report also needs the actual native shader-compilation/dispatch outcome.
Keep logs focused and omit account data, saves and proprietary game shaders.

The older bundled integration has separate
[RC6 upgrade and recovery notes](legacy-rc6.md#upgrade-a-game-to-rc7).
