# RC11 DLL quick start

Project version **4.0.0-rc11**; SDK display name **4.1.1r11**.

The DLL contains the FSR optimizations. Use your normal driver and Proton;
you do not need the BC250 compatibility tool or a custom cache installer.
Keep normal shader caches enabled. BC250/Linux is the tested platform;
Windows and other GPUs remain unqualified.

**First OptiScaler install?** Follow the maintained
[installation guide and game recipes](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md).
The steps below assume a working OptiScaler 10.0.0-pre1 installation from the
[pinned September 4 nightly](https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260904).

## One shared DLL

1. Extract this archive into a permanent folder such as `~/Games/BC250-FSR4`.
   Keep the DLL and notices there. Leave the optional `linux/` helpers unused.
2. Close the games you are configuring. Back up each game's `OptiScaler.ini`
   and launch options. In its existing INI sections, set the keys below.
   Replace `YOUR_USER` with your Linux username; use the full path without quotes.

   ```ini
   [Libraries]
   OptiDllPath=auto
   FfxDx12SRPath=Z:\home\YOUR_USER\Games\BC250-FSR4\amd_fidelityfx_upscaler_dx12.dll

   [Upscalers]
   Dx12Upscaler=ffx
   Dx11Upscaler=ffx_12
   VulkanUpscaler=ffx_12

   [FSR]
   UpscalerIndex=0
   Fsr4ForceModel=2
   FsrNonLinearColorSpace=false
   FsrNonLinearSRGB=auto
   FsrNonLinearPQ=auto
   Fsr4EnableWatermark=true

   [FrameGen]
   Enabled=false
   FGInput=nofg
   FGOutput=nofg
   ```

3. Keep the game's working input/spoofing settings. On Steam/Linux, the common
   `winmm.dll` adapter launch line is:

   ```sh
   /usr/bin/env PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 PROTON_USE_XALIA=0 WINEDLLOVERRIDES="winmm=n,b;amdxcffx64=" VKD3D_DISABLE_EXTENSIONS="VK_NVX_binary_import,VK_NVX_image_view_handle" %command%
   ```

   Match the override to your proxy (`dxgi` for the Cyberpunk recipe). Preserve
   unrelated arguments, other mods' overrides and the recipe's renderer flag.
   Keep one `%command%`. Heroic uses separate environment-variable fields;
   native Windows uses neither Proton variables nor `%command%`.
4. Launch and select the recipe's DLSS/FSR input. The rendered watermark should
   show **4.1.1R11**, **FSR-INT8**, **SOURCE: LOCAL** and **COLORSPACE: LINEAR**.
   After checking, close the game, set `Fsr4EnableWatermark=auto`, remove any
   `MLSR-WATERMARK` environment variable, and restart. `false`/`0` still enables
   the banner in the pinned adapter.

The shared folder must be visible inside the launcher/sandbox. OptiScaler can
fall back if the path fails, so verify the version. For per-game storage instead,
replace `OptiScaler/amd_fidelityfx_upscaler_dx12.dll` and use `FfxDx12SRPath=auto`.
Each game still needs its own adapter and settings with either layout.

## First launch and recovery

First use may pause while shaders compile. Keep normal caches; clearing them
can repeat the work. If the game exits, save the error and retry once with the
same cache. Repeated failures or device errors need investigation.
[Troubleshooting](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md#if-the-check-fails).

To update, close all games using the shared file, back it up, then replace it.
All configured games get that update. To undo, restore the shared backup, or
restore one game's previous INI/local DLL and launch options. Keep saves,
prefixes and other mods. Native FidelityFX replacements remain separate
[per-game recipes](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md#native-fidelityfx-games).

## Verify the DLL

The DLL is 94,840,832 bytes, SHA256:

```text
8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175
```

Use `sha256sum -c SHA256SUMS` in this folder, or PowerShell
`Get-FileHash .\amd_fidelityfx_upscaler_dx12.dll -Algorithm SHA256`.
The modified DLL is not AMD-signed. Retain the included notices.

[Validation and limits](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/portable-dll-rc11.md):
RC11 retains RC10's shaders, with synthetic rendering checks. Earlier game checks
belong to RC7. Frame generation and unlisted combinations need separate testing.
[Source, documentation and optional tools](https://github.com/daniel-h-0/bc250-fsr4-fork).
