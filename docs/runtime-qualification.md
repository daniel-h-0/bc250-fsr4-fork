# Shared runtime qualification — v4.0.0-rc3

RC4’s portable driver and installer have a [separate compatibility record](rc4-compatibility.md).
The gameplay results below remain the RC3 qualification with the original driver.

The initial six scene checks below passed. A later quiet Roboquest/Luma check
was interrupted by an unexplained host freeze requiring a manual reboot. The
journal contained no identifying GPU fault, panic or OOM record. This incident
is retained in the evidence; it is not proof of a specific runtime or hardware
cause. Post-reboot quiet gameplay and normal exits passed in Roboquest/Luma and DOOM
without recurrence during those checks.

RC3 extends the shared Steam runtime to DX11 and Vulkan inputs while retaining
DX12 support and the **unchanged v4.0.0-rc1 driver**. On September 8, 2026 (EDT),
six loaded-save scenes showed **FSR4-I8 4.1.1, SOURCE: DRIVER, commit 143E11E,
COLORSPACE: LINEAR** in the actual rendered frame. Frame generation was off.

| Game | Renderer / input | Observed output | Observed mode |
| --- | --- | --- | --- |
| Control Ultimate Edition | DX12 / DLSS | 1920×1080 | Quality, 1.50× |
| Deadzone Rogue | DX12 / FSR | 2560×1440 | Balanced, 1.70× |
| System Shock | DX11 / DLSS | 2560×1440 | Watermark says Native-AA, 1.30× |
| Roboquest with existing Luma | DX11 / Luma DLSS | 2560×1440 | Native-AA, 1.00× |
| No Man's Sky | Vulkan / DLSS | 1920×1080 | Balanced, 1.69× |
| DOOM: The Dark Ages | Vulkan / FSR | 2560×1440 | Balanced, 1.74× |

System Shock actually rendered at 1969×1108 before reconstruction; its
Native-AA watermark label does **not** establish a 1:1 input resolution.
Roboquest used the existing separately installed Luma/ReShade chain. RC3 does
not install Luma. All six checks loaded existing saves and exited normally.

This is bounded functionality and exit evidence, not an installation allowlist,
universal game compatibility, endurance qualification or a performance result.
The [RC2 record](runtime-qualification-rc2.md) and
[earlier driver performance campaign](performance.md) remain historical evidence.

## Exact components and evidence

The [machine-readable record](data/runtime-v4.0.0-rc3.json) identifies the frozen
runtime lock, assembly inventory, each observed process, mapped component
hashes, screenshots and focused log excerpts. The
[release evidence archive](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc3/bc250-fsr4-v4.0.0-rc3-runtime-proof.tar.gz)
contains the unedited screenshots and excerpts.

Every scene mapped the pinned system driver, FSR 4.1.1 provider, OptiScaler
nightly 20260904, OptiPatcher 0.41 and AMD SDK 4.0.2 bridge. Files were resolved
inside the game process's namespace and checked against the mapped inode before
hashing. The watermark establishes driver-backed rendering in addition to DLL
loading. The signed NVIDIA DLSS 310.7.0 helper is checked separately: it enables
NGX signature validation; the rendering implementation remains AMD FSR INT8.

The host used CachyOS, kernel 7.2.3-1.83, RADV GFX1013 and native Linux Steam
with Steam Linux Runtime 4. The physical display was 2560×1440/120 throughout.
Game output sizes in the table are independent of that physical mode.
Existing owned game-local OptiScaler files were backed up and removed before
testing; unrelated Luma/ReShade and game-shipped SDK files were retained.

## Compatibility fixes

- A prefix-local WinMM proxy leaves existing DXGI mod chains available.
- DX11 and Vulkan inputs use OptiScaler's D3D12 FFX bridge. Existing ReShade
  loading is enabled; early Luma D3D12 device creation is disabled because it
  caused Roboquest to fail during startup.
- A pinned, licensed NVIDIA helper satisfies games that validate NGX signatures.
  System Shock passed through its established DX11 renderer.
- Vulkan extension advertising enables No Man's Sky's DLSS selector. Vendor
  spoofing stays on upstream automatic detection: forcing it globally crashed
  DOOM at startup. Unsupported NVX extensions are excluded specifically from
  vkd3d's D3D12 device using `VKD3D_DISABLE_EXTENSIONS`; otherwise the Vulkan
  bridge failed with `VK_ERROR_EXTENSION_NOT_PRESENT`. Existing caller
  exclusions are preserved.

These settings apply to the selected runtime. The installer contains no AppID
catalog, does not change game renderers, and does not edit Steam account fields.
Upstream OptiScaler still has its own detection rules. Xalia remains disabled
for the session-exit reason documented in the RC2 record.

## Installation and recovery

Two complete assemblies produced identical inventory and lock bytes. Real GE
prefix installation completed RC2 → RC3 → RC2 → RC3 offline, including cold
installation and a warm invocation at every stage. DLL bytes and mtimes stayed
unchanged on reuse; old proxies and RC3-only signature-helper/license files were
removed when appropriate. Unrelated prefix content was retained, and the INI
configuration matched the previous state after rollback and reinstall.

The unified update route reuses the verified system driver. Its update,
rollback and reinstall checks and local automated results are recorded in the
machine-readable evidence. The extracted setup and source are checked again
during release packaging. Existing v3 migration
and first-install recovery remain covered by the RC2 evidence and current tests.
See the [RC2 update guide](upgrading-rc2.md) for the exact commands and distinction
between runtime rollback and restoring a separately retired manual deployment.

Debug launch options were removed, and 1,016 original save/settings files were
verified after restoration from the fresh qualification snapshot. Test-created
files were quarantined. Steam’s six cloud indexes were retained as current
metadata, and the four original files that the cloud had reintroduced from the
test session were restored and successfully uploaded before final verification.
The host's separately managed migration
adds the four newly qualified titles to its shared-runtime selection; that local
library policy is not shipped in this distribution.
