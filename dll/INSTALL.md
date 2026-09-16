# RC11 DLL quick start

Project version **4.0.0-rc11**; SDK display name **4.1.1r11**.

**Replace OptiScaler's bundled upscaler DLL and select FFX/INT8.** Use your normal
driver, Proton and working launch settings.

These steps assume a working OptiScaler installation. For a first-time setup,
use the [beginner guide and game recipes](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md).
The documented layout is OptiScaler 10.0.0-pre1 from September 4, 2026.

## Install

1. Close the game. Back up **`OptiScaler/amd_fidelityfx_upscaler_dx12.dll`**
   beside its executable, then replace that file with the DLL from this archive.
   Keep the filename and the rest of OptiScaler's files.
2. In `OptiScaler.ini`, edit these keys in their existing sections:

   ```ini
   [Upscalers]
   Dx12Upscaler=ffx
   Dx11Upscaler=ffx_12
   VulkanUpscaler=ffx_12

   [FSR]
   UpscalerIndex=0
   Fsr4ForceModel=2
   FsrNonLinearColorSpace=false
   ```

   Keep other game-specific settings. Leave frame generation off in-game and
   set `[FrameGen] Enabled=false` in OptiScaler. If you previously selected a
   shared DLL, set `[Libraries] OptiDllPath=auto` and `FfxDx12SRPath=auto` to use
   this local copy.
3. Keep the launch settings that already load OptiScaler. Launch and select
   the game's supported DLSS/FSR input. Its menu label can remain unchanged.

For a first check, set `[FSR] Fsr4EnableWatermark=true` and restart. Look for
**4.1.1R11**, **FSR-INT8** and **SOURCE: LOCAL** in a rendered scene. Then use
`Fsr4EnableWatermark=auto` and restart to hide it. Remove any `MLSR-WATERMARK`
environment variable.

## Fresh Linux installation only

Loading OptiScaler for the first time normally needs the Wine override matching
its adapter name. For `winmm.dll`, the Steam launch option is
`WINEDLLOVERRIDES="winmm=n,b" %command%`; use `dxgi` for a `dxgi.dll` adapter.
Skip this if OptiScaler already loads. Keep other existing options and the
[game recipe's requirements](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md#game-recipes).
These launch options apply to Linux.

## Update or undo

Close the game and replace the same DLL to update; restore its backup to undo.
Restore any settings you changed. Each game's OptiScaler copy updates separately.
Keep saves, prefixes and other mods. Shared DLL paths and native FidelityFX
replacements are optional routes in the full guide.

First use may pause for shader compilation. Keep normal caches enabled.
[Troubleshooting](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md#if-the-check-fails).
The archive's `linux/` cache helpers can be left unused.

## Verify the DLL

The DLL is 94,840,832 bytes, SHA256:

```text
8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175
```

Use `sha256sum -c SHA256SUMS` in the extracted folder, or PowerShell
`Get-FileHash .\amd_fidelityfx_upscaler_dx12.dll -Algorithm SHA256`.
The modified DLL is not AMD-signed. Retain the included notices.

BC250/Linux is tested. RC11 has synthetic rendering checks; earlier game checks
belong to RC7. Windows, other GPUs, frame generation and unlisted combinations
remain unqualified. [Validation](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/portable-dll-rc11.md).
