# v4.0.0-rc11 — simpler Linux caching and reliable recovery

RC11 makes the shared shader cache easier to install and maintain, and integrates
it into the Linux driver launcher. It includes the fixes from two further
installer/documentation reviews.

- **Install the DLL cache helper once.** Run `sh linux/shared-cache.sh install`,
  paste the game's existing Steam launch options when prompted, and copy the
  generated line back. The files are installed permanently, so the extracted
  download can be removed afterward.
- **One driver launcher.** Driver installation now supplies a permanent command
  that selects private RADV and prepares shared caching. Caching defaults on for
  new CLI installs; updates retain the existing choice. Per-launch opt-out keeps
  the private driver selected.
- **Useful status and graceful fallback.** Read-only status distinguishes cache
  storage from observed use. Actual write checks catch unwritable/full storage.
  Optional cache failures, including malformed settings, retain the game's
  original cache environment instead of preventing launch.
- **Safer updates and recovery.** Compatible driver updates adopt the verified
  bundled tools and license. Rollback checks retained tools before restoring
  them. Interrupted helper removal can be retried or reinstalled; relocated and
  independently edited managed files are preserved.

**The shaders are unchanged from RC10.** All 348 DLL shader programs are identical;
the DLL changes only its provider label to **4.1.1r11** and its PE checksum. The
driver binary is byte-identical to RC10. RC11 makes no new shader-speed or FPS
claim. First use can still compile shaders, and existing ordinary caches are
preserved but not imported into the shared store.

Validation includes a complete 348-shader rebuild, nine fresh synthetic image
comparisons against RC10, an actual SDK-rendered RC11 watermark, and verified
package installation, upgrade and rollback. Cache/installer tests run in four
Linux userspaces; this is not full graphics qualification of those distros.
The earlier Control-to-System-Shock cache reuse test remains separate evidence:
System Shock reached its menu, not gameplay. Control's driver gameplay result
belongs to RC10 and uses the same driver binary.

The release retains four uploads: **DLL ZIP**, **Linux driver archive**,
**complete source/evidence archive**, and **SHA256SUMS**. RC10 and older downloads
remain available. The driver route still needs the pinned AMD-provider/translator
combination; the portable DLL remains the primary route. Windows, other GPUs,
frame generation and unlisted integrations remain unqualified.

When upgrading the original RC10 driver tools, use the new download's installer
with your existing `--prefix`. Remove an old portable wrapper invocation before
generating its replacement, preserving unrelated settings. After a temporary
watermark check, use `Fsr4EnableWatermark=auto` and remove `MLSR-WATERMARK`.

[DLL installation](../dll/INSTALL.md) · [Driver installation](driver-cache-setup.md) ·
[Shared-cache setup and updates](shared-shader-cache.md) ·
[Exact identity and validation](portable-dll-rc11.md)
