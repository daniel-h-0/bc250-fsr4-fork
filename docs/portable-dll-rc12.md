# RC12: release validation

[Install across your games](optiscaler-client.md) or use the [manual guide](beginner-guide.md).
[Client validation](../integrations/optiscaler-client/README.md#validation) is separate from the DLL rendering evidence below. RC12 carries
the RC8/RC9 shader work to the 1080p and 8K context-size families; host code and
the 1440p–4K family's programs are RC11's.

## Exact identities

| Component | Identity |
| --- | --- |
| Distribution | `4.0.0-rc12` |
| DLL provider name | `4.1.1r12` |
| DLL size | 93,110,784 bytes |
| DLL SHA256 | `4b0df27761d7ced52c30dcbd1641c55bdcf4d2ae10d59f7e5b4b2f895b6bba79` |
| Configuration | [`dll/rc12.json`](../dll/rc12.json), on [`dll/rederived.json`](../dll/rederived.json) and RC11's [`dll/manifest.json`](../dll/manifest.json) |
| GPU-tested build | `83a0f71bc71fc87abe33878438b0ae7d59aab4602a00103472fe49edad0ea618`, provider name `4.1.1r12t`; not distributed |
| Optional driver | Unchanged: [RC11.1](release-notes-rc11.1.md) |

The tested build differs from the release DLL in 3 bytes:

| File offset | Field | Tested → release |
| --- | --- | --- |
| `0x160` | PE checksum, low byte | `8b` → `16` |
| `0x308` | `.bc250` section VirtualSize, low byte (label one byte shorter) | `86` → `85` |
| `0x58cc184` | Last label character: `4.1.1r12t` → `4.1.1r12` | `74` → `00` |

`python3 scripts/check-rc12-release.py --dll PATH` restores the three bytes and
checks that the result is the tested build. Programs, host code and layout are
the same in both.

## Current validation

| Check | Result |
| --- | --- |
| Rebuild | `python3 dll/build.py --config rc12` reproduces the DLL from the pinned SDK 2.3.0 and DXC 1.9.2607. |
| New programs (CPU) | 34 slots, 24 distinct programs, generated from AMD's SDK DXIL by [`dll/port`](../dll/port/README.md). Each was checked against AMD's and RC11's program for its pass with DXIL interpreters on randomized inputs, plus mutation tests. |
| DLL against RC11 | 35 shader containers change: the 34 new slots and `0x248220` (AMD's division restored). 310 containers move without changes; 3 stay in place. Host code is RC11's except the label pointer. All 1,137 differing bytes before the appended section are accounted for. |
| Images (GPU, tested build) | Byte-identical to RC11's in all three families: 15 census runs (1080p, 1440p and 4K presets; HDR, SDR, motion, reset, sharpening, resize), 8 image runs and every timed run. |
| Programs (GPU, tested build) | Every dispatched program equals the build record. All 34 new slots ran: 17 in the 1080p family (context maximum 1920×1080), 17 in the 8K family (7680×4320). |
| Eligibility edit (GPU) | The same programs without it select FSR 3.1.5 and dispatch no INT8 program. |
| Timing (GPU, tested build) | 1080p Quality −0.414 ms, 1080p Performance −0.432 ms, 1440p Performance with an 8K maximum −0.723 ms, 1440p Performance +0.002 ms (noise). [Table](gpu-cost.md#rc12-against-rc11). |
| Release DLL (GPU) | Untimed start of `4b0df277…` with the tested build's 15 census and 8 image runs: provider name `4.1.1r12`, every dispatched program equals the build record (all 34 new slots ran), images byte-identical to RC11's and the tested build's. |
| Watermark | `4.1.1R12` ([reference](beginner-guide.md#3-play-and-check-once), [capture record](data/beginner-watermark-rc12.json)). Against RC11's capture only the label's last digit differs. |
| Tooling | `scripts/check-repo.py`: 17 checks and 506 tests, including the RC11, comparison and RC12 builds, packaging and source export. |

The tested build's GPU checks ran on October 23, 2026, the release DLL's on
October 24, on BC250 with system Mesa 26.2.5 RADV (`RADV_DIRECTMESH=0`),
GE-Proton 11-6 and the project's synthetic [FFX probe](../dll/probe/README.md).
Timed runs ran at 1850 MHz. The [release record](data/portable-dll-rc12.json)
pins the inputs, run medians and image hashes; `scripts/check-rc12-release.py`
checks it. Host-code origins: [provenance](../dll/notices/PROVENANCE.md).

## Which games gain

FSR4 selects its programs by the context's maximum output size:

| Context maximum | RC12 against RC11 |
| --- | --- |
| Up to 1920×1080 (tested: 1920×1080) | Faster: −0.41 to −0.43 ms per frame at 1080p output |
| 2560×1440 to 3840×2160 | Unchanged |
| 7680×4320 | Faster: −0.72 ms at 1440p Performance, now equal to the 2560×1440 maximum |

Maxima between 1920×1080 and 2560×1440 were not tested.

## Inherited evidence and limits

| Topic | Evidence |
| --- | --- |
| Host-code edits | RC11's bytes. [Buffer-UAV barrier review](legacy/research/portable-dll-rc7.md#sdk-synchronization-repair) (RC7); eligibility origin in the [provenance](../dll/notices/PROVENANCE.md). |
| Cold compilation | [RC10 measurements](legacy/research/portable-dll-rc10.md); not remeasured for the new programs. |
| DLL game routes | [Seven RC7 checks](legacy/research/portable-dll-rc7.md), with each game's actual scope. |
| Alternative driver | [RC11.1](release-notes-rc11.1.md); RC12 changes only the DLL. |
| Shared cache | [Control-to-System-Shock reuse](legacy/research/cache-setup-qualification.md); System Shock reached its menu. |
| Tool portability | [Installer review](legacy/research/cache-review2.md): four isolated Linux userspaces. |

The rendering evidence covers **BC250/Linux** and the synthetic probe. RC12 has
no game checks of its own. The probe runs 57 of the 348 slots (19 per family),
including all 34 new ones; context flags it does not vary select the others.
Native Windows, other GPUs, frame generation and further game integrations
await qualification.

[Release notes](release-notes-rc12.md) · [GPU cost](gpu-cost.md) · [Documentation](README.md)
