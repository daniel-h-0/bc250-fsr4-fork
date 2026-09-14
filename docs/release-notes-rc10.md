# v4.0.0-rc10 — draft for review

RC10 focuses on the wait when FSR4 shaders first compile, and offers a Linux
driver alternative alongside the primary portable DLL.

- **Less cold compilation work.** Redundant intermediate operations are removed
  from 48 shader slots. The selected candidate reduced synthetic cold upscaler
  setup from 22.24 to 19.38 seconds on BC250, about **13%**. This is not a claim
  about total game loading time or FPS. All 36 distinct changed programs compile
  to the same native instructions, constants and hardware configuration as RC9
  on the three compared Mesa builds; the other 300 slots retain RC9 bytecode.
- **Optional shared shader cache on Linux.** Games can reuse compatible Mesa
  compilations while preserving their existing Steam caches. A separate reuse
  test went from 22.55 seconds to populate the shared store to 1.45 seconds
  through a new application view. The helper handles XDG paths, concurrent
  launches and preparation failures. It is opt-in; sandbox paths must be visible,
  and a driver or translator change can still require compilation.
- **Linux driver compatibility download.** The private Mesa 26.2.2 build brings
  the RC9 optimizations to the pinned AMD FSR 4.1.1 INT8 provider path. It has
  comparable synthetic GPU cost to RC9 at 1080p, 1440p and 4K, plus explicit
  installation and rollback. Its exact shader matches depend on the tested
  provider/Proton versions. The portable DLL remains the recommended route.
- **Four uploads.** One DLL ZIP, one Linux driver archive, the complete source
  and evidence archive, and one `SHA256SUMS`. Detailed documentation, charts,
  prior results and rebuild inputs remain available in the repository/source
  archive. Existing RC9 downloads remain available.

The DLL identifies itself as **4.1.1r10**. RC9's model, packed arithmetic,
weight guards and synchronization repair are retained. The driver option
preserves a pre-existing dynamic-resolution difference in the older provider
route; it does not promise identical images between every API integration.

The final binaries pass 21 synthetic rendering checks and a complete DLL
rebuild; the driver package passes real installation, upgrade and rollback.
All 244 repository tests pass. Additional direct-DLL checks pass installed
Proton 11 and Experimental. The driver recipe retains the tested GE-Proton pin;
Valve Proton 11's provider fallback is documented.

Qualification remains BC250/Linux focused. The isolated Control smoke test
loads the DLL but is not gameplay qualification; earlier game evidence remains
labeled with its original release. Windows, other GPUs, frame
generation and unlisted game/Proton combinations are unqualified. Keep shader
caches between launches. After a temporary watermark check, use
`Fsr4EnableWatermark=auto` and remove `MLSR-WATERMARK`.

[Install](../dll/INSTALL.md) · [Driver option](driver-rc10.md) ·
[Shared cache](shared-shader-cache.md) · [Measurements and limits](portable-dll-rc10.md)

Publication is pending review of this draft and the final qualification record.
