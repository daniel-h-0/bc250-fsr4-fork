# v4.0.0-rc11.1 (driver-only pre-release)

RC11.1 changes only the optional [Linux driver](driver-cache-setup.md). The DLL
(`8192ea97…`) and the OptiScaler Client stay at RC11. The archive's installer is RC11's;
`scripts/shared-cache.py` is the current `v4` copy, which also accepts an absolute
`BC250_FSR4_CACHE_STATUS_FILE` status path (cache logic unchanged).

## What changed

The driver is Mesa 26.2.2 plus one upstream compiler fix on top of RC11's recipe:
[`a13dfbea0f89`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/a13dfbea0f898dde77382ccba8f699673885952a)
"nir/opt_uub: fix imax optimization", first released in Mesa 26.2.3. Mesa 26.2.2's
`nir_opt_uub` pass replaced `imax(x, C)` by `x` where `0 <= x <= C`; the result
must be `C`. Any shader with that pattern could be miscompiled. HelixSR's frame
generation is one: on RC11 its generated frames turn black within seconds.

| Item | RC11 | RC11.1 |
| --- | --- | --- |
| Mesa recipe | 26.2.2 + patches 0001–0003 + RC9 overlay | same + `0004-nir-opt_uub-fix-imax-optimization.patch` |
| Changed Mesa source | | `src/compiler/nir/nir_opt_uub.c` only |
| Driver SHA256 (stripped) | `5c4d74b1…` | `7ba5edbdd05c69d85ed64d2f3f73b62d31a07da7801fb41a99fb5679be29e485` |
| Unstripped SHA256 | `f6fbeeb2…` | `9c66e57c2d22c05f48d3b0243331401a454b7b4b526ad3150ab76e084a50acdf` |

Download `bc250-fsr4-v4.0.0-rc11.1-linux-glibc236-x86_64.tar.gz`, SHA256
`6f89cf227cd14afc49e66304ee872cff68a9378aec2147c1ab644b2cd5193a5d`. It was
packaged from commit `24f74fe`; its bundled guides are from that commit.

## Build check

The RC11 driver first rebuilt byte for byte from this branch's parent. In the
RC11.1 build, only the `opt_uub` function and its cold part differ (26 bytes
longer): 9,761 functions compared with addresses mapped to the old layout. The
other differences are the 32-byte shift of the code after it, the build ID and
`opt_uub`'s unwind range and symbol size. [Record](data/driver-rc11.1.json).

## Validation

GPU checks on October 23 ran the driver from this exact archive as a private ICD
on BC250 (headless, GE-Proton 11-7), with the published RC11 driver as control.

| Check | Result |
| --- | --- |
| FSR4 compiled programs (provider and DLL routes, FSR4 passes on and off) | Identical to RC11: 68 of 68 statistic blocks in each of 4 cases, ACO code hashes included |
| FSR4 output, AMD provider route, 1080p | 7 of 7 rows bit-identical to the RC11 references (RC11 also 7 of 7) |
| FSR4 output, RC11 DLL route, 1440p | 3 of 3 rows bit-identical to RC11 |
| HelixSR frame generation | RC11.1 clean (Light 2 of 2, Full 1 of 1); RC11 affected (Light black 2 of 2, Full dark lattice 1 of 1) |

Not run: GE-Proton 11-6 (the provider guide's version), timing and gameplay.
FSR4's compiled programs are unchanged, so its GPU cost is RC11's.
[Record](data/driver-rc11.1.json).

## Affected downloads and system Mesa

- Earlier driver downloads lack the fix and stay unchanged: RC10/RC11 (`5c4d74b1…`,
  affected in the HelixSR check), the rc1 CachyOS driver (`6bc07c5a…`, affected) in
  the rc1 release and the rc1 portable driver (`13163d13…`, same source) in the rc4
  release.
- DLL users: system Mesa 26.2.x before 26.2.3 has the same upstream bug and can
  break other shaders, e.g. HelixSR frame generation; update to 26.2.3 or newer.
  FSR4 output is unaffected: the fix leaves its compiled programs unchanged.

[Downloads and checksums](releases.md) · [Driver installation](driver-cache-setup.md)
