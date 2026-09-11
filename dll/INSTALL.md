# FSR 4.1.1 INT8 — RC9 DLL

Project version **4.0.0-rc9**; SDK display name **4.1.1r9**.
This archive contains one modified Windows x64 upscaler DLL. The v4
performance changes are already compiled into it.

**Tested on AMD BC250 / Linux with ordinary Proton.** Native Windows and
other GPUs need separate testing.

## First launch: shader compilation can look like a freeze

**Expect a potentially long pause when this FSR path is first used without a
usable shader cache.** The graphics driver and Proton still need to translate
and compile the supplied shaders for your GPU and software combination. This
can happen when enabling FSR in a menu or loading a game with FSR already
selected. The game may stop updating or appear unresponsive for tens of seconds
or longer, without showing compilation progress. Allow time before force-closing
it; the pause alone does not establish a crash or failed installation.

Later launches can reuse cached shaders. A GPU, driver, Proton, game or FSR DLL
update, a cleared/disabled cache, or a newly selected shader variant can trigger
compilation again. Keep the cache between attempts. If the game actually times
out or exits, preserve its error/log and try one restart with the same files
and cache. Repeated failures, GPU/device errors or a whole-system lockup need
investigation; do not assume every freeze is compilation.

In one RC7 No Man's Sky check, the first dispatch stalled for about 66 seconds
and triggered the game's hang detector; a restart with the same files and cache
rendered successfully. That diagnostic run is an example, not a promised wait
time or a guarantee for another game.
[Details and troubleshooting](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/first-run-shader-compilation.md).

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
no RC9-specific Heroic switch; Heroic launch behavior has not been separately
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
with RC7; they have not been retested with RC9:

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

Native Windows requires a D3D12 runtime and
driver accepting DXIL 1.9 / Shader Model 6.9.

[Tested configurations and details](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc9/docs/portable-dll-rc9.md)

## Update, undo and checksums

Keep the original game/adapter DLL backup. To update, close the game and replace
only the upscaler DLL. To undo, restore the backup. Game updates may restore their
own DLL.

The DLL is 111,815,680 bytes, SHA256:

```text
eefcac03ab17b04a29a5bb16e3f3e9c3181ba9ea46b05a61cb49a5003e1516ef
```

Verify with `sha256sum -c SHA256SUMS` on Linux, or PowerShell
`Get-FileHash .\amd_fidelityfx_upscaler_dx12.dll -Algorithm SHA256` on Windows.
The modified DLL is not AMD-signed. Retain the included notices.

Source and build instructions: [BC250 FSR4 repository](https://github.com/daniel-h-0/bc250-fsr4-fork).
Upgrading from the older BC250 driver/Steam tool:
[RC6 upgrade notes](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc9/docs/legacy-rc6.md#upgrade-a-game-to-rc7).
