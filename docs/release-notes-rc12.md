# v4.0.0-rc12

**[Install across your games](optiscaler-client.md)** with the BC250 client build
(import this ZIP), or [download the DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc12/bc250-fsr4-dll-4.0.0-rc12.zip)
for [manual installation](beginner-guide.md).
Use your normal driver, Proton and working launch settings. The optional Linux
driver is unchanged: [RC11.1](release-notes-rc11.1.md).

## What changed

FSR4 picks its shader programs by the context's maximum output size. RC8 and RC9
optimized only the programs used for a 2560×1440–3840×2160 maximum. RC12 applies
the same work to the two other families:

| Context maximum | RC11 | RC12 |
| --- | --- | --- |
| Up to 1920×1080 (a 1080p display) | RC7-level programs | RC8/RC9 work: 17 slots |
| 2560×1440–3840×2160 | RC8/RC9 work | unchanged, except `0x248220` (below) |
| 7680×4320 | RC7-level programs | RC8/RC9 work: 17 slots |

- The 34 new slots (24 distinct programs) cover passes 1, 2, 3, 5, 6, 7, 8, 9, 11
  and 12, the preparation pass and the final pass. They are generated from AMD's
  SDK 2.3.0 DXIL by the project's lowering tools and give the same output bits as RC11.
- The final pass keeps AMD's division in every program. RC9–RC11 used a shift in
  slot `0x248220` that is exact only for in-spec input.
- The DLL identifies itself as **4.1.1r12**.
- Host-code edits (INT8 eligibility, buffer-UAV barrier) are RC11's, unchanged.
  Their origin: [provenance](../dll/notices/PROVENANCE.md).

## Validation

The DLL is 93,110,784 bytes, SHA256
`4b0df27761d7ced52c30dcbd1641c55bdcf4d2ae10d59f7e5b4b2f895b6bba79`.
`python3 dll/build.py --config rc12` rebuilds it from the pinned SDK and DXC.

| Check | Result |
| --- | --- |
| New programs (CPU) | Each checked against AMD's and RC11's program for its pass with DXIL interpreters on randomized inputs, plus mutation tests |
| Bytes against RC11 | 35 shader containers (the 34 slots and `0x248220`), the label, shader pointers and PE sizes. Host code identical except the label pointer |
| Images (GPU) | Byte-identical to RC11's in all three families: 15 census runs (1080p, 1440p and 4K presets; HDR, motion, reset, sharpening, resize), 8 image runs and every timed run |
| Programs (GPU) | Every dispatched program equals the build record; the new programs run in the 1080p and 8K families |

FSR4 GPU time per frame on the BC250 at 1850 MHz (mean of 8 rounds, 5 for 1080p
Performance; standard errors ≤ 0.004 ms):

| Output (input) | Context maximum | RC11 | RC12 | Change |
| --- | --- | ---: | ---: | ---: |
| 1080p Quality (1280×720) | 1920×1080 | 3.675 ms | 3.262 ms | −0.414 ms (−11.3%) |
| 1080p Performance (960×540) | 1920×1080 | 3.653 ms | 3.221 ms | −0.432 ms (−11.8%) |
| 1440p Performance (1280×720) | 7680×4320 | 6.105 ms | 5.383 ms | −0.723 ms (−11.8%) |
| 1440p Performance (1280×720) | 2560×1440 | 5.381 ms | 5.383 ms | +0.002 ms (noise) |

Two copies of RC11 differed by up to 0.002 ms in the same runs. The last row
prices AMD's division in `0x248220`.

The GPU checks ran on October 23 on a test build
(`83a0f71bc71fc87abe33878438b0ae7d59aab4602a00103472fe49edad0ea618`) with this
DLL's programs, host code and layout. It differs only in its label (`4.1.1r12t`),
the appended section's length and the PE checksum (3 bytes;
`scripts/check-rc12-release.py --dll` restores them and checks the hash). Stack:
system Mesa 26.2.5 RADV, GE-Proton11-6, the project's synthetic FFX probe.
Without the eligibility edit the same programs fall back to FSR 3.1.5.

Not tested: games (earlier game checks belong to RC7), Windows, other GPUs and
frame generation. [Record](data/portable-dll-rc12.json) ·
[Downloads and checksums](releases.md).
