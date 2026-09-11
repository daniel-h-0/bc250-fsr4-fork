# RC8: 1440p performance checkpoint

RC8 reduces synthetic whole-upscaler GPU time by **8.52% versus RC7** in
fresh 1440p Quality tests on AMD BC250. It is distributed as the same single
Windows x64 DLL, labeled **4.1.1r8**. Follow the [installation guide](../dll/INSTALL.md)
to replace a compatible native game DLL or a working OptiScaler backend.

## Measured result

| Final DLL | Median of four run medians | Range of run medians |
| --- | ---: | ---: |
| RC7 / 4.1.1r7 | 6.61728 ms | 6.61514–6.62004 ms |
| RC8 / 4.1.1r8 | 6.05354 ms | 6.04648–6.05696 ms |
| Reduction | 0.56374 ms / 8.52% | Every RC8 run was below every RC7 run |

The fixed workload renders 1706×960 input to 2560×1440 output. Eight fresh
600-frame runs use the order RC7, RC8, RC8, RC7 twice. Each run scores its last
300 frames. Shader dumping and tracing are off for these measurements; all
sampled GPU clocks during the scored portion are 1850 MHz. Both sides use
the same standard Mesa 26.2.1 driver and ordinary GE-Proton 11-6. All eight
full output images are byte-identical.

The [public record](data/portable-dll-rc8.json) contains all 4,800 timestamps,
the sampled clocks, exact DLL/driver/probe identities, image hashes and the
calculation inputs. `scripts/check-repo.py` recomputes the results. The
[standalone D3D12 probe](../dll/probe/README.md) provides the workload source.
This measures the upscaler's GPU cost, not game FPS. The older four-way
[README chart](gpu-cost.md) remains a separate RC7 campaign. No RC8 timing
claim is made for 1080p or 4K, and earlier private-candidate timings are not
pooled into this final-byte result.

## Implementation and correctness

Sixteen of the 348 shader slots change from RC7. The checkpoint combines
native packed integer dot products in model passes 7 and 8, streamed pass 11
arithmetic, Winograd convolution in passes 1 and 12, vector weight checks in
passes 3 and 6, cooperative pass 9 weight validation, native final-output
spatial/color math, and selected
wave sizes. Dynamic model-weight guards and fallback paths are retained.
The other 332 shader slots are binary-identical to RC7.

The final RC8 DLL passes seven additional 64-frame image pairs against RC7
at 2560×1440: HDR, SDR, motion, reset, changing render resolution, RCAS
sharpening, and Balanced input at 1506×848. Every complete image matches
byte for byte and is finite. All sixteen changed slot hashes were observed
in each RC8 image case and in a separate static preflight.

Development checks additionally cover actual integer arithmetic, address
mapping, overflow/bias boundaries and modified model weights. The public
record distinguishes this inherited component evidence from the final DLL's
fresh GPU runs. Modified-weight checks for passes 7, 8, 9 and 11 selected the
original dynamic fallback and matched the reference image; deliberate
fast-path witnesses established that the optimized path was used with the
unmodified weights. These checks do not establish correctness at every
possible input size. RC7's three unresolved odd-output reference cases remain
[documented limits](portable-dll-rc7.md#correctness-and-performance).

The RC7 scalar integer-cast compatibility repair and SDK buffer-UAV barrier
repair are preserved. RC8 retains the same five public FFX exports and numeric
provider/API identity. The selected qualified development DLL and RC8 differ
only in the display label and PE checksum: all 348 compiled shaders match.
The [complete source](../dll/README.md) rebuilds every shader with the pinned
DXC validator and reproduces the exact DLL bytes.

## Scope and installation

The fresh RC8 checks cover direct D3D12 dispatch on BC250/Linux with ordinary
GE-Proton and standard Mesa. RC8 has no new game rendering or endurance
checks. The seven [RC7 game-route checks](portable-dll-rc7.md#supported-scope),
including its native-loader filenames and No Man's Sky first-compilation
caveat, remain historical evidence. They are not relabeled as RC8 tests.
Native Windows, other GPUs and frame generation remain unqualified.

The download includes the DLL, instructions, checksums and notices. It
requires no RC8-specific driver or Proton installation. Keep a backup of the
replaced DLL so restoring that file reverses the update. The retained RC6
driver/runtime bundle is separate and is not an RC8 installer.

Final DLL: **112,343,040 bytes**, SHA256:

```text
f8816fed46bce60179228a58905e16788f021fad0b68c08d1e3555564093b2b4
```
