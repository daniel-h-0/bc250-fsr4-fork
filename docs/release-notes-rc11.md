# v4.0.0-rc11

**Use the DLL ZIP with OptiScaler and your normal driver/Proton.**
The [current installation guide](beginner-guide.md) starts with replacing
OptiScaler's bundled upscaler DLL. No custom driver or shared-cache helper is required.

## What changed

RC11 improves the **optional** Linux tools:

- The shared-cache helper installs permanently and generates Steam launch options.
- The private-driver option includes a permanent launcher with integrated caching.
- Status, updates and rollback handle missing/unwritable storage, interrupted
  removal and independently edited files more reliably.

**The shaders are unchanged from RC10.** RC11 retains all 348 shader programs;
the DLL changes only its provider label to `4.1.1r11` and PE checksum. The driver
binary is also unchanged. There is no new shader-speed or FPS improvement.
Normal first-use compilation still applies with either installation route.

## Downloads

Choose **`bc250-fsr4-dll-4.0.0-rc11.zip`** for the recommended installation.
The Linux driver archive is an alternative for an existing AMD-provider setup,
not an extra performance upgrade. Source and `SHA256SUMS` are also available.
Older releases remain available.

The maintained guide was simplified after release. The original archives and
tag retain their original instructions and unchanged DLL; follow the current
guide above for the simplest setup.

## Validation and optional tools

RC11 passed nine synthetic image comparisons against RC10 and a complete
shader rebuild. Earlier game checks retain their original versions and scope.
[Exact identity and validation](portable-dll-rc11.md).

[Optional shared cache](shared-shader-cache.md) ·
[Optional driver installation](driver-cache-setup.md) ·
[Driver versus DLL](driver-rc10.md#dll-versus-driver).
