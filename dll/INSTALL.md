# FSR 4.1.1 INT8 — RC7 DLL

Project version **4.0.0-rc7**; SDK display name **4.1.1r7**.
This archive contains one modified Windows x64 upscaler DLL. The v4
performance changes are already compiled into it.

**Tested hardware: AMD BC250 on Linux.** An FFX API and rendering check passes on ordinary Proton 10/11. Windows and other GPUs are candidates for testing, not qualified
platforms. This DLL uses modern DXIL/Shader Model 6.9; native Windows needs
a D3D12 runtime and driver that accept those shaders.

## With OptiScaler

1. Use a working [upstream OptiScaler installation](https://github.com/optiscaler/OptiScaler).
   Close the game. Back up its existing
   `OptiScaler/amd_fidelityfx_upscaler_dx12.dll`, then replace it with this DLL.
2. Set the following in `OptiScaler.ini`. These select FSR4 INT8, linear input,
   and the appropriate D3D12 output path. Keep the game's established renderer.

   ```ini
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

   [FrameGen]
   Enabled=false
   ```

3. Launch and enable the supported DLSS/FSR input in the game's graphics menu.
   The adapter translates that input to this DLL's FSR4 upscaler. Existing
   game-specific OptiScaler spoofing or input settings may still be needed.

On Proton, use an ordinary compatibility tool. For OptiScaler installed as
`winmm.dll`, the Steam launch option is:

```sh
PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 WINEDLLOVERRIDES="winmm=n,b" %command%
```

Use the matching proxy name if your OptiScaler installation uses another name.
The two Proton variables keep separate automatic upscaler integrations off
in tools that provide them; ordinary Valve Proton ignores unsupported options.
OptiScaler's required adapter files stay installed; this download replaces
only its upscaler backend. Keep the adapter's signed `nvngx_dlss.dll` helper
beside its proxy when required: System Shock needs that placement even with
`Libraries.NvngxDlssPath` configured. The RC6 BC250 Steam tool must not remain selected
for a game using this route.

For a visual check, temporarily set `[FSR] Fsr4EnableWatermark=true`.
The rendered image should identify **4.1.1r7**, INT8 and the local source.
Turn the watermark off after checking. A loaded DLL alone does not establish
that the game is using FSR4.

## With a native FidelityFX game

Close the game, back up its compatible `amd_fidelityfx_upscaler_dx12.dll`,
then replace that file. Enable native FSR in the game. No OptiScaler proxy
or Wine override is required for this route.

Loader compatibility is game-specific. The verified integration in
**Deadzone Rogue** uses `Valhalla/Binaries/Win64/amd_fidelityfx_upscaler_dx12.dll`.
In **Kingdom Come: Deliverance II**, the older bundled loader is incompatible
with this SDK provider interface. Use the same replacement bytes as
`Bin/Win64Shared/amd_fidelityfx_loader_dx12.dll`, keeping the original
upscaler file. Those native filename checks were performed with the preceding
DLL; RC7 preserves the SDK interface and adds the Proton and synchronization fixes.

This file implements upscaling. Do not apply the KCD2 loader substitution to
other games without checking their loader and other FidelityFX effects.
Frame generation, ray regeneration, Luma/ReShade combinations and arbitrary
native loader versions are outside RC7's qualification.

## Update, undo and checksums

Keep the original game/adapter DLL backup. To update, close the game and replace
only the RC7 DLL. To undo, restore the backup. Game updates may restore their
own DLL. This DLL installation needs no driver installation, Steam account
management, manual Wine-prefix edits or save migration.

The DLL is 115,176,448 bytes, SHA256:

```text
730c175a38b0f0271ffaa201ca531825c6440a95fb71729d34566c66af8e4893
```

Verify with `sha256sum -c SHA256SUMS` on Linux, or PowerShell
`Get-FileHash .\amd_fidelityfx_upscaler_dx12.dll -Algorithm SHA256` on Windows.
The modified DLL is not AMD-signed. Retain the included notices.

Full compatibility results, existing-RC6 transition, source and rebuild
instructions: [BC250 FSR4 repository](https://github.com/daniel-h-0/bc250-fsr4-fork).
