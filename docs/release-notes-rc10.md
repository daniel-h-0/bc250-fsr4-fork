# v4.0.0-rc10 — faster startup and a Linux driver option

RC10 reduces the work needed when FSR4 shaders first compile and adds a Linux
driver alternative alongside the primary portable DLL.

- **Less cold compilation work.** Redundant intermediate operations are removed
  from 48 shader slots. The selected candidate reduced synthetic cold upscaler
  setup from 22.24 to 19.38 seconds on BC250, about **13%**. This measures upscaler
  setup, not total game loading time or FPS. All 36 distinct changed programs
  produce the same native instructions, constants and hardware configuration as
  RC9 on the three compared Mesa builds; the other 300 slots retain RC9 bytecode.
- **Optional shared shader cache on Linux.** Opted-in games can reuse compatible
  Mesa compilations while preserving their existing Steam caches. A separate
  test measured 22.55 seconds to populate the shared store and 1.45 seconds
  through a new application view. The launcher handles XDG paths, concurrent
  starts and preparation failures. Sandbox paths must be visible; changing the
  driver or translator can still require compilation.
- **Linux driver compatibility download.** The private Mesa 26.2.2 build brings
  RC9 optimizations to the pinned AMD FSR 4.1.1 INT8 provider path. It has comparable
  synthetic GPU cost to RC9 at 1080p, 1440p and 4K, and now passes **Control gameplay
  qualification** through a normal Steam launch. Installation, upgrade and rollback
  are verified. Its exact shader matches depend on the provider and Proton versions;
  the guide uses GE-Proton 11-6. The portable DLL remains the recommended route.
- **Four uploads.** One DLL ZIP, one Linux driver archive, the complete source
  and evidence archive, and one `SHA256SUMS`. Detailed documentation, charts,
  prior results and rebuild inputs stay available in the repository/source
  archive. Existing RC9 downloads remain available.

The DLL identifies itself as **4.1.1r10**. RC9's model, packed arithmetic,
weight guards and synchronization repair remain. The original AMD provider used
by the driver option still reports **4.1.1 / SOURCE: DRIVER**; use the driver hash
to identify that release.

Validation includes all 348 shaders rebuilt to the exact DLL hash, 21 final-binary
synthetic rendering checks, and private driver install/upgrade/rollback tests.
Control's driver test verified a saved scene, movement and 14 exact shader
substitutions. System Shock's additional DX11 check rendered its animated menu
with 13 substitutions; **it is not a gameplay pass**. Original game files, saves,
cloud settings and launch options were restored after testing.

Qualification remains BC250/Linux focused. The older provider route retains a
pre-existing dynamic-resolution difference from the direct SDK route. Windows,
other GPUs, frame generation and unlisted game/Proton combinations remain
unqualified. Keep shader caches between launches. After a temporary watermark
check, use `Fsr4EnableWatermark=auto` and remove `MLSR-WATERMARK`.

[Install](../dll/INSTALL.md) · [Driver option](driver-rc10.md) ·
[Shared cache](shared-shader-cache.md) · [Measurements](portable-dll-rc10.md) ·
[Gameplay evidence and limits](driver-gameplay-rc10.md)
