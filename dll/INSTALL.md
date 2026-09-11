# FSR 4.1.1 INT8 — RC9 DLL

Project version **4.0.0-rc9**; SDK display name **4.1.1r9**.
This archive contains one modified Windows x64 upscaler DLL. The v4
performance changes are already compiled into it.

**Tested on AMD BC250 / Linux with ordinary Proton.** Native Windows and
other GPUs need separate testing.

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
PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 WINEDLLOVERRIDES="winmm=n,b;amdxcffx64=" %command%
```

Use the matching proxy name if your OptiScaler installation uses another name.
For Heroic or another Wine launcher, enter these as environment-variable
name/value pairs instead of using Steam’s `%command%` placeholder. There is
no RC8-specific Heroic switch; Heroic launch behavior has not been separately
qualified with this candidate.
These variables load the adapter and keep competing automatic upscaler
integrations off. Keep OptiScaler's other files installed, including its
signed `nvngx_dlss.dll` helper beside the proxy when required.

For a visual check, temporarily set `[FSR] Fsr4EnableWatermark=true`.
The rendered image should identify **4.1.1r9**, INT8 and the local source.
Turn the watermark off after checking.

## With a native FidelityFX game

Close the game, back up the compatible game DLL, then replace it and select
native FSR in the graphics menu. These replacement locations were verified
with RC7; RC8 game follow-ups are pending:

| Game | Replace this file |
| --- | --- |
| Deadzone Rogue | `Valhalla/Binaries/Win64/amd_fidelityfx_upscaler_dx12.dll` |
| Kingdom Come: Deliverance II | `Bin/Win64Shared/amd_fidelityfx_loader_dx12.dll` |

For KCD2, rename the downloaded DLL to the loader filename, keep the original
upscaler file, and explicitly select **FSR 4.1** in-game; RC7 used Quality.
Other games may use different loader interfaces. Check compatibility before
applying that rename elsewhere.

## Known limits

This release covers upscaling. Frame generation, ray regeneration and
unlisted game or mod combinations need separate testing. The seven recorded
game-route checks belong to RC7; RC9 has synthetic D3D12 image/performance
checks at 1080p, 1440p and 4K and has not been retested in games.

Initial shader compilation can cause a long pause. With RC7, No Man's Sky hit its
hang detector on the first in-game switch to DLSS; restarting with the same
DLL and compiled cache worked. Native Windows requires a D3D12 runtime and
driver accepting DXIL 1.9 / Shader Model 6.9.

[Tested configurations and details](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc9/docs/portable-dll-rc9.md)

## Update, undo and checksums

Keep the original game/adapter DLL backup. To update, close the game and replace
only the RC8 DLL. To undo, restore the backup. Game updates may restore their
own DLL.

The DLL is 112,343,040 bytes, SHA256:

```text
f8816fed46bce60179228a58905e16788f021fad0b68c08d1e3555564093b2b4
```

Verify with `sha256sum -c SHA256SUMS` on Linux, or PowerShell
`Get-FileHash .\amd_fidelityfx_upscaler_dx12.dll -Algorithm SHA256` on Windows.
The modified DLL is not AMD-signed. Retain the included notices.

Source and build instructions: [BC250 FSR4 repository](https://github.com/daniel-h-0/bc250-fsr4-fork).
Upgrading from the older BC250 driver/Steam tool:
[RC6 upgrade notes](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc9/docs/legacy-rc6.md#upgrade-a-game-to-rc7).
