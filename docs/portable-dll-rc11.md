# RC11: release validation

For installation, [replace OptiScaler's bundled upscaler DLL](beginner-guide.md)
and select FFX/INT8. This page records the release's validation and identities.

RC11 retains RC10's shaders and adds optional cache/driver tooling improvements.
Those tools are separate from the normal DLL install; they add no new shader
optimization or FPS claim over RC10.

## Exact identities

| Component | Identity |
| --- | --- |
| Distribution | `4.0.0-rc11` |
| DLL provider name | `4.1.1r11` |
| DLL size | 94,840,832 bytes |
| DLL SHA256 | `8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175` |
| Driver SHA256 | `5c4d74b141f946b8b11442d9886f10b62f4ecd022065d498d79ee4ac798eea4b` |

All **348 shader sources and validated shader programs are identical to RC10**.
The rebuilt DLL differs in exactly two bytes: file offset `353`, which adjusts
the PE checksum, and offset `94840547`, which changes the provider label's final
digit from `0` to `1`. Reversing those two changes reconstructs RC10's exact DLL
SHA256. The runtime instruction bytes, model data, weight guards and
synchronization behavior are unchanged. CI verifies that reverse comparison
against its independently rebuilt DLL.

The RC11 driver was rebuilt from the same Mesa source/patch inputs and retains
the exact RC10 stripped ELF. Its new distribution manifest versions the tools
and package; it does not represent a new driver optimization.

## Current validation

The [release record](data/portable-dll-rc11.json) retains exact identities and
nine fresh, 64-frame synthetic output comparisons on BC250/Linux: 1080p, 1440p,
4K, and SDR/HDR/motion/reset/resize/RCAS scenarios. Every output matches its RC10
reference byte for byte, with the new DLL actually loaded and the provider
reporting `4.1.1r11`. These are rendering checks, not loading-time or FPS benchmarks.

The [watermark reference](assets/rc11-watermark-reference.png) was rendered by
the actual SDK on a synthetic pattern; its [capture record](data/beginner-watermark-rc11.json)
pins the DLL, probe and pixels. No overlay text was edited or added.

The unchanged cache/installer implementation has 27 helper tests and 34 driver
installer tests exercised in four userspaces; the complete repository has 267
tests. The [latest installer review](cache-review2.md) describes skipped
compiler-dependent checks in minimal images and the graphics qualification limits.
Release validation also checks extracted packages, persistent launchers, updates
from previous tools, cache-failure fallback, exact rollback and complete source
export. A fresh driver probe verifies this private binary before selection.

## Inherited evidence and limits

[RC10's compilation results](portable-dll-rc10.md),
[Control driver gameplay](driver-gameplay-rc10.md) and
[Control-to-System-Shock shared-cache reuse](cache-setup-qualification.md)
keep their original dates, binary identities and scopes. System Shock was checked
at its menu, not through gameplay. The current release is not a fresh playthrough
of the installed game library. The older seven-game DLL checks remain RC7 evidence.

The driver route still depends on the pinned AMD provider/GE-Proton translation
combination. Its pre-existing dynamic-resolution difference remains documented.
Native Windows, other GPUs, frame generation and unlisted integrations remain
unqualified. Cache sharing requires visible writable paths and compatible Mesa
inputs; old ordinary caches are not imported and first use can still compile.

[Install the DLL](../dll/INSTALL.md) · [Install the driver](driver-cache-setup.md) ·
[Cache setup, updates and removal](shared-shader-cache.md) ·
[Release notes](release-notes-rc11.md)
