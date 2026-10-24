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
| 2560×1440–3840×2160 | RC8/RC9 work | Unchanged, except `0x248220` (below) |
| 7680×4320 | RC7-level programs | RC8/RC9 work: 17 slots |

- The 34 new slots (24 distinct programs) cover passes 1, 2, 3, 5, 6, 7, 8, 9, 11
  and 12, the preparation pass and the final pass. The project's lowering tools
  generate them from AMD's SDK 2.3.0 DXIL. They give the same output bits as RC11.
- The final pass keeps AMD's division in every program. RC9–RC11 used a shift in
  slot `0x248220` that is exact only for in-spec input.
- The DLL identifies itself as **4.1.1r12**.
- Host-code edits (INT8 eligibility, buffer-UAV barrier) are RC11's, unchanged.
  Their origin: [provenance](../dll/notices/PROVENANCE.md).

## Performance

FSR4 GPU time per frame on the BC250, October 23:

- **1080p output:** −0.414 ms at Quality (3.675 → 3.262 ms, −11.3%) and
  −0.432 ms at Performance (−11.8%).
- **7680×4320 context maximum:** −0.723 ms at 1440p Performance. RC11's 8K
  programs cost 0.72 ms more than its 1440p–4K programs; RC12's cost the same.
- **1440p–4K context maxima:** unchanged (+0.002 ms, within noise).

[Tables and method](gpu-cost.md#october-23-2026).

## Validation

The DLL is 93,110,784 bytes, SHA256
`4b0df27761d7ced52c30dcbd1641c55bdcf4d2ae10d59f7e5b4b2f895b6bba79`.
`python3 dll/build.py --config rc12` rebuilds it from the pinned SDK and DXC.

GPU checks and timing used a test build with this DLL's programs, host code and
layout and another label. Its images were byte-identical to RC11's in all three
families, and every dispatched program matched the build record. The release DLL
then passed the same untimed census and image runs as `4.1.1r12`
([validation](portable-dll-rc12.md)).

Not tested: games (earlier game checks belong to RC7), Windows, other GPUs and
frame generation. [Exact identities and test coverage](portable-dll-rc12.md) ·
[Downloads and checksums](releases.md).
