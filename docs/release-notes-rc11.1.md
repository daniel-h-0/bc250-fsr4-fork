# v4.0.0-rc11.1 (driver-only pre-release)

RC11.1 changes only the optional [Linux driver](driver-cache-setup.md). The DLL
and the OptiScaler Client stay at RC11. The archive's installer is RC11's;
`scripts/shared-cache.py` is the current `v4` copy, which also accepts an absolute
`BC250_FSR4_CACHE_STATUS_FILE` status path (cache logic unchanged).

## What changed

One upstream Mesa compiler fix on top of RC11's recipe:
[`a13dfbea0f89`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/a13dfbea0f898dde77382ccba8f699673885952a)
"nir/opt_uub: fix imax optimization", first released in Mesa 26.2.3. Mesa 26.2.2's
`nir_opt_uub` pass replaced `imax(x, C)` by `x` where `0 <= x <= C`; the result
must be `C`. Any shader with that pattern could be miscompiled.

| Item | RC11 | RC11.1 |
| --- | --- | --- |
| Mesa recipe | 26.2.2 + patches 0001–0003 + RC9 overlay | same + `0004-nir-opt_uub-fix-imax-optimization.patch` |
| Changed Mesa source | | `src/compiler/nir/nir_opt_uub.c` only |
| Driver SHA256 (stripped) | `5c4d74b1…` | `7ba5edbd…` |
| Unstripped SHA256 | `f6fbeeb2…` | `9c66e57c…` |

## Build check

The RC11 driver first rebuilt byte for byte from this branch's parent. In the
RC11.1 build, only the `opt_uub` function and its cold part differ (26 bytes
longer): 9,761 functions compared with addresses mapped to the old layout. The
other differences are the 32-byte shift of the code after it, the build ID and
`opt_uub`'s unwind range and symbol size. [Record](data/driver-rc11.1.json).

## Validation

Pending. The pre-release is published only after GPU checks of the exact archive.

[Downloads and checksums](releases.md) · [Driver installation](driver-cache-setup.md)
