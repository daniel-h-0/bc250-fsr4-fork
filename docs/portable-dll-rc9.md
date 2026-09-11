# RC9: retained performance checkpoint

RC9 publishes the best retained 1440p development checkpoint as one Windows
x64 DLL, labeled **4.1.1r9**. Fresh final-DLL measurements are **3.92825 ms at
1080p, 5.91826 ms at 1440p and 12.08447 ms at 4K**, using Quality input.
The [installation guide](../dll/INSTALL.md) describes DLL replacement.

## Fresh release measurements

| Output / input | Median of four run medians | Range of run medians |
| --- | ---: | ---: |
| 1920×1080 / 1280×720 | 3.92825 ms | 3.87222–3.99682 ms |
| 2560×1440 / 1706×960 | 5.91826 ms | 5.90224–5.92666 ms |
| 3840×2160 / 2560×1440 | 12.08447 ms | 12.05920–12.33966 ms |

These September 11 runs use the exact final release DLL, standard Mesa 26.2.1,
ordinary GE-Proton 11-6 and the frozen revision 3 D3D12 probe. Each of the twelve
launches runs 600 frames and scores frames 300–599. Every sampled scoring clock
is 1850 MHz. Tracing and shader dumping are off during timing. All complete
images match the previously verified references at their respective resolutions.

The [new four-way chart](gpu-cost.md) replaces only the RC7 arm with these RC9
values. Its three baseline arms preserve all 36 original September 10 runs and
every timestamp. The data checker rejects changes to those baseline records.
The [old RC7 chart](gpu-cost-rc7.md) remains available. Against its RC7 values,
RC9 is 10.45% lower at 1440p and 13.32% lower at 4K, while 1080p is 2.18%
higher. These are comparisons across measurement dates, not a fresh matched
RC7/RC9 experiment. The 1080p and 4K run ranges are shown without exclusions.

The [public release record](data/portable-dll-rc9.json) includes all 7,200 fresh
timestamps, observed shader identities, image hashes, sampled clocks and exact
DLL/driver/probe identities. These measurements describe synthetic whole-upscaler
GPU cost; they do not predict whole-game FPS.

## Retained checkpoint and implementation

RC9 changes twelve shader slots from RC8, or nineteen from RC7. It extends exact
Winograd convolution to model passes 2, 4 and 10 and improves coefficient grouping
in passes 1 and 12. Bounded packed accumulation also improves passes 5, 7, 9 and
11. Final-output unsigned lane extraction preserves every 16-bit value. Earlier
native dot products, shared model validation, vector weight checks, color reuse
and measured wave choices remain. There are 348 complete editable shader sources;
329 keep RC7's compiled hashes and 336 keep RC8's.

The source retains dynamic model-weight guards and original fallbacks. Inherited
actual-graph CPU checks cover arithmetic, addresses, shared-memory exchange,
byte extremes and integer wrap/bias behavior. GPU checks of changed model weights
in passes 7, 8, 9 and 11 match the original fallback, and deliberate fast-path
witnesses establish use of the optimized path. The record binds this component
evidence to the current compiled shader hashes. It does not claim every possible
image or shape has been tested.

The retained development DLL separately measured **6.62420 → 5.90599 ms
(10.842% lower)** against RC7 in eight matched 1440p Quality runs. Every candidate
run was below every control. That earlier cohort is included with its actual
development DLL identity and timestamps; it is not pooled with the new chart.

RC9 and that retained DLL differ only in four bytes belonging to the display
label and PE checksum. All 348 compiled shader replacements match exactly.
The RC7 scalar-cast compatibility repair, SDK buffer-UAV synchronization fix,
five public exports and numeric API/provider identity remain intact.

## Images, build and scope

Fresh 64-frame preflights at all three resolutions identify each complete active
model family and match the sealed reference images. Seven further 1440p cases
also match: HDR, SDR, motion, reset, changing render resolution, RCAS sharpening
and Balanced input at 1506×848. The RC7 reference images were already verified;
the RC9 images are fresh. Every changed slot hash is observed at 1440p and 4K.
1080p selects its own smaller model family.

The [complete source build](../dll/README.md) assembles and validates all 348
shaders with the pinned DXC input and must reproduce the exact release DLL.
`scripts/check-repo.py` recomputes the published statistics, preserves historical
RC7/RC8 record identities and verifies the new chart's unchanged baseline data.
The historical [RC8 manifest](data/portable-dll-rc8-manifest.json) records RC8's
sources as they existed at its tag; its paths are not claims about RC9 contents.

This release has no new game rendering or endurance checks. The seven
[RC7 game checks](portable-dll-rc7.md#supported-scope), its No Man's Sky initial
compilation caveat and its unresolved odd-output reference cases retain their
original scope. Native Windows, other GPUs and frame generation remain unqualified.
Installation uses a compatible native FidelityFX integration or separately
installed upstream OptiScaler, with no RC9-specific driver or Proton tool.
Restore the backed-up DLL to undo the change.

Final DLL: **111,815,680 bytes**, SHA256:

```text
eefcac03ab17b04a29a5bb16e3f3e9c3181ba9ea46b05a61cb49a5003e1516ef
```
