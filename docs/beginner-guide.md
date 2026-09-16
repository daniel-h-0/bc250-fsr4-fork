# Install with OptiScaler: one DLL for multiple games

Use your normal graphics driver and Proton. Store RC11 once, then point each
game's OptiScaler at it. Each game keeps its own adapter, INI and input settings.
**There is no custom-driver install or shared-cache setup in this route.**
Normal game/driver shader caches remain enabled automatically.

Already using OptiScaler? Complete [1](#1-download-and-store-the-dll) and
[3](#3-point-optiscaler-at-the-shared-dll), keep your working game-specific
settings, then [check the result](#check-that-rc11-is-rendering).
For a fresh install, follow all four steps.

## 1. Download and store the DLL

Download the [RC11 DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc11/bc250-fsr4-dll-4.0.0-rc11.zip).
Extract it into a permanent folder, for example **`~/Games/BC250-FSR4`**.
Keep the DLL, checksums and notices there. The optional `linux/` helper folder
can be left unused. Do not run its installer for this route.

The shared file will be:

```text
/home/YOUR_USER/Games/BC250-FSR4/amd_fidelityfx_upscaler_dx12.dll
```

Replace `YOUR_USER` with your Linux username. Keep this folder in place: every
game you configure below will load this file. This shares the DLL, not compiled
shader caches or Proton prefixes.

<details>
<summary>Verify the download</summary>

In the extracted folder, run `sha256sum -c SHA256SUMS`. The RC11 DLL is
94,840,832 bytes, SHA256:

```text
8192ea97620f8e6407bff346bf905f0d555ff73d89fe14616eb1fa5e41ab3175
```

The release-page `SHA256SUMS` verifies archives; the copy inside the ZIP verifies
the extracted files. These are different hashes.

</details>

## 2. Install OptiScaler in each game

Skip this step if the game already has a working installation of the pinned
OptiScaler version below. Other versions may use different settings/layouts.

| Download | Where it goes |
| --- | --- |
| [OptiScaler 10.0.0-pre1, September 4, 2026](https://github.com/optiscaler/OptiScaler-nightly/releases/download/nightly-20260904/OptiScaler_v10.0.0-pre1_20260904.7z) | Extract its contents beside the actual game executable. |
| [OptiPatcher 0.41](https://github.com/optiscaler/OptiPatcher/releases/download/v0.41/OptiPatcher_v0.41.asi) | Save as `OptiScaler/plugins/OptiPatcher.asi`. |
| [Signed NVIDIA DLSS helper 310.7.0](https://raw.githubusercontent.com/NVIDIA/DLSS/a291cc7d2cc642a51566f3dfd5376f635cd1b284/lib/Windows_x86_64/rel/nvngx_dlss.dll) | Beside the executable, **only if it has no `nvngx_dlss.dll` already**. |

Close the game. Steam's **Properties → Installed Files → Browse** opens its
root folder; use the [game recipes](#game-recipes) to find the actual executable.
Back up any files you will replace and the current launch options outside the
game directory. Preserve existing mods; if one already owns the proposed proxy
filename, use that mod's supported chaining method.

Copy the OptiScaler archive's contents, keeping its subfolders. Rename
**`OptiScaler.dll` to `winmm.dll`**, or **`dxgi.dll` for Cyberpunk**.
Keep `OptiScaler.ini` under its original name. Add the plug-in and helper above.
You do not need to replace OptiScaler's bundled upscaler DLL: the override in
step 3 takes priority.

```text
Shared folder: ~/Games/BC250-FSR4/
  amd_fidelityfx_upscaler_dx12.dll       RC11, used by all configured games

Each game's executable folder:
  Game.exe
  winmm.dll                            renamed OptiScaler.dll (or dxgi.dll)
  OptiScaler.ini                       points to the shared RC11 DLL
  nvngx_dlss.dll                       existing game copy, or signed helper
  OptiScaler/                          keep the adapter's other files
    plugins/OptiPatcher.asi
  Licenses/
```

The renamed proxy is OptiScaler; **do not rename RC11 to `winmm.dll` or `dxgi.dll`**.

## 3. Point OptiScaler at the shared DLL

Edit these keys in each game's existing `OptiScaler.ini` sections. Do not append
duplicate sections. For the example Linux folder, use this **Windows-style path**:

```ini
[Libraries]
OptiDllPath=auto
FfxDx12SRPath=Z:\home\YOUR_USER\Games\BC250-FSR4\amd_fidelityfx_upscaler_dx12.dll
NvngxDlssPath=auto

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

Use the full path, without quotes, `~` or `$HOME`. Proton normally exposes Linux
paths through `Z:`. The folder must also be visible inside the game's launcher
or sandbox; a `Z:` path does not grant filesystem access. Leave `OptiDllPath=auto`
so the adapter's other libraries and plug-ins remain local to each game.

OptiScaler tries `FfxDx12SRPath` first, but **can fall back to another DLL if it
cannot load that path**. Check the watermark below; if the version is wrong,
check the path and the `Loaded from` entry for the upscaler in `OptiScaler.log`.
If no log exists, temporarily set `[Log] LogToFile=true` and `LogLevel=2`,
restart, then return those settings to their previous values after checking.
[Upstream path handling](https://github.com/optiscaler/OptiScaler/blob/da70e61e1542a0b99adcb24168ff941e42109567/OptiScaler/Util.cpp#L759).

If a shared folder is inconvenient, put RC11 at each game's
`OptiScaler/amd_fidelityfx_upscaler_dx12.dll` and set `FfxDx12SRPath=auto` instead.
That fallback requires updating each copy separately.

## 4. Set the launch options and in-game input

In Steam **Properties → Compatibility**, select ordinary Proton; the recorded
adapter checks used **GE-Proton 11-6**. In **General → Launch Options**, use:

```sh
/usr/bin/env PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 PROTON_USE_XALIA=0 WINEDLLOVERRIDES="winmm=n,b;amdxcffx64=" VKD3D_DISABLE_EXTENSIONS="VK_NVX_binary_import,VK_NVX_image_view_handle" %command%
```

For Cyberpunk, change `winmm=n,b` to `dxgi=n,b`. Add a renderer argument only
where the recipe below requires it. Preserve unrelated existing options and
other mods' DLL overrides; keep exactly one lowercase `%command%`.
These settings load OptiScaler and disable competing automatic upscaler routes.

Use the recipe's graphics-menu input. OptiScaler converts it to FSR4, so the
menu may still say **DLSS** or **FSR3**. Leave frame generation off. First use
can pause while shaders compile; [what to do if it stalls](first-run-shader-compilation.md).

### Heroic

Use ordinary GE-Proton and the same game files/INI. Enter these as separate
name/value pairs in the game's environment settings:

| Name | Value |
| --- | --- |
| `PROTON_FSR4_UPGRADE` | `0` |
| `PROTON_USE_OPTISCALER` | `0` |
| `PROTON_USE_XALIA` | `0` |
| `WINEDLLOVERRIDES` | `winmm=n,b;amdxcffx64=` (use `dxgi` for Cyberpunk) |
| `VKD3D_DISABLE_EXTENSIONS` | `VK_NVX_binary_import,VK_NVX_image_view_handle` |

Put renderer arguments in the arguments field. Do not paste `%command%` or the
whole Steam line into Heroic. Keep the existing prefix and save location.

## Game recipes

Paths start at the game's installation root. Match the executable before
copying: store builds and game updates can differ. Apply these additions to
the common settings above.

### Cyberpunk 2077: Steam or Heroic

Place **`dxgi.dll`** beside `bin/x64/Cyberpunk2077.exe`. Set `[Spoofing] Dxgi=false`
and, under `[Inputs]`, `EnableDlssInputs=false` and `EnableFfxInputs=auto`.
Choose **FSR3** in-game. Use `dxgi` in the launch override.

### Control Ultimate Edition

Place **`winmm.dll`** beside `Control_DX12.exe`. Set `[Spoofing] Dxgi=false`.
Append **`-dx12`** after `%command%` and choose **DLSS** in-game.

### System Shock

Place **`winmm.dll`** beside
`SystemShock/Binaries/Win64/SystemReShock-Win64-Shipping.exe`, with the signed
helper beside the proxy. Set `[Spoofing] Dxgi=true`. Append **`-dx11`** after
`%command%` and choose **DLSS** in-game.

### No Man's Sky

Place **`winmm.dll`** beside `Binaries/NMS.exe`. Under `[Spoofing]`, set
`Dxgi=false`, `Vulkan=true` and `VulkanExtensionSpoofing=true`. Keep Vulkan and
choose **DLSS**. The recorded first compilation hit the game's hang detector;
one restart with the same cache worked.

### DOOM: The Dark Ages

Place **`winmm.dll`** beside `DOOMTheDarkAges.exe`. Set `[Spoofing] Dxgi=false`.
Keep Vulkan and choose **FSR 3.1** in-game.

These recipes come from earlier checks, not fresh RC11 playthroughs;
[scope and evidence](#tested-scope) are below.

### Roboquest: existing Luma installation

This needs a working Luma/ReShade setup; Roboquest has no native input for this
recipe. The recorded combination used Luma Unreal Engine `latest-623` and
ReShade `6.8.0.1`. Preserve those mods. Place the `winmm.dll` adapter beside
`RoboQuest/Binaries/Win64/RoboQuest-Win64-Shipping.exe`, keep DX11, use Luma's
DLSS input, and add:

```ini
[Plugins]
LoadReshade=true
[Dx11withDx12]
DontUseNTShared=true
[Spoofing]
Dxgi=false
[Hotfix]
CreateD3D12DeviceForLuma=false
RestoreComputeSignature=false
RestoreGraphicSignature=false
ExtendedStateRestore=false
```

### Native FidelityFX games

These two routes bypass OptiScaler and use **per-game copies**, not the shared
path setting. Back up the file before replacing it with RC11:

| Game | File to replace | In-game choice |
| --- | --- | --- |
| Deadzone Rogue | `Valhalla/Binaries/Win64/amd_fidelityfx_upscaler_dx12.dll` | Native FSR4 |
| Kingdom Come: Deliverance II | `Bin/Win64Shared/amd_fidelityfx_loader_dx12.dll` | FSR 4.1 |

For KCD2, rename a copy of RC11 to the loader filename and keep the game's
original upscaler DLL. This was checked with KCD2 1.5.6; it is not a generic
rename for other games. Both native recipes use this Steam/Linux line:

```sh
/usr/bin/env PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 PROTON_USE_XALIA=0 WINEDLLOVERRIDES="amdxcffx64=" %command%
```

Preserve unrelated settings, including an existing `SteamDeck=0`. To check the
native watermark, temporarily add `'MLSR-WATERMARK=1'` after `/usr/bin/env`;
remove that variable afterward.

## Check that RC11 is rendering

Restart, select the recipe's upscaler input, and enter a rendered scene.
The watermark should show **`FSR-INT8`**, **`4.1.1R11`**, **`SOURCE: LOCAL`** and
**`COLORSPACE: LINEAR`** with the adapter settings above.

<details>
<summary>What the RC11 watermark looks like</summary>

![RC11 watermark rendered by the SDK over a synthetic test pattern.](assets/rc11-watermark-reference.png)

This is a synthetic probe, not a game screenshot. Quality and scaling ratio vary
with your settings. [Capture record](data/beginner-watermark-rc11.json).

</details>

After checking, close the game and set **`Fsr4EnableWatermark=auto`**, then restart.
Remove any explicit `MLSR-WATERMARK` environment variable. In the pinned adapter,
`false` sets that variable to `0`, which still enables the banner.

## If the check fails

| Symptom | Check |
| --- | --- |
| OptiScaler does not open with Insert | Executable folder, proxy name and matching Wine override. |
| DLSS is missing | Recipe's input/spoofing settings and signed helper beside the proxy. |
| Wrong version or `SOURCE: DRIVER` | Shared path, sandbox access and upscaler `Loaded from` log entry; keep competing integrations off. |
| Menu still says DLSS/FSR3 | Expected: that is the adapter's input. Check the rendered watermark. |
| First use stalls | [Compilation troubleshooting](first-run-shader-compilation.md); keep normal caches. |
| No watermark on title screen | Try an actual scene and restart after changing watermark settings. |
| Watermark will not disappear | Use `auto` and remove `MLSR-WATERMARK`, including a value of `0`. |

## Update an existing installation

Close **all games using the shared DLL**. Back it up, then replace that one file.
Every game pointing to it gets the update next launch. Keep the old copy to
restore if a game regresses; test one game before continuing with the others.
The adapter, INIs and per-game launch options need no reinstall for a DLL update.
Native installations and the per-game fallback need their own copies updated.

To migrate from a local copy, back up the INI and change only `FfxDx12SRPath`
to the shared file; keep the local DLL for rollback. If coming from the old
BC250 driver/Steam tool, use its [migration instructions](legacy-rc6.md#upgrade-a-game-to-rc7)
first and select ordinary Proton. Do not combine its wrapper with this route.

## Undo

For one game, restore its previous `FfxDx12SRPath`/INI and local DLL if replaced.
For a shared-DLL update, close all games using it and restore the backed-up
shared file. Do not delete the shared folder while another game still uses it.

If removing a fresh OptiScaler install, remove only files you added and restore
replaced originals, the saved launch options and previous Proton selection.
Preserve game-provided helpers, other mods, saves and prefixes. Native recipes
restore the original game DLL; KCD2 restores the loader.

## Tested scope

BC250/Linux is the tested platform. RC11 has synthetic rendering checks; the
[earlier game-route checks](portable-dll-rc7.md#supported-scope) used RC7, and the
recorded Cyberpunk install used RC9. The shared-path setting follows the pinned
OptiScaler loader; it does not constitute a new gameplay check of every title.
See [RC11 validation](portable-dll-rc11.md) for the release's actual coverage.
Frame generation and unlisted combinations need separate testing.

Native Windows and other GPUs remain unqualified. On Windows, a shared path
would use its native drive letter and omit all Proton variables; the shaders
require a runtime/driver accepting DXIL 1.9 / Shader Model 6.9.

Optional [shared shader caching](shared-shader-cache.md) can be added later.
It is independent of sharing this DLL and is not needed to get its optimizations.
[Documentation index](README.md) · [Upstream manual installation](https://github.com/optiscaler/OptiScaler/wiki/Manual-Installation)
