# Optional driver: requirements and provider setup

The normal route is [OptiScaler DLL replacement](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md).
This page covers the alternative **BC250 / Linux x86-64 AMD-provider route**.
Its private Mesa 26.2.2 driver carries the RC9 shader optimizations. RC11 ships
the same binary as RC10.

## DLL versus driver

Both routes include the FSR optimizations. The three-resolution comparison
found comparable GPU cost. Choose the DLL for the simpler setup; this driver
recognizes exact translated provider shaders, so changing the provider or Proton
can lose optimization coverage. [Comparison record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/legacy/research/portable-dll-rc10.md).

## Install the private driver

Use the [current RC11 installer](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-cache-setup.md).
It installs privately and leaves system Mesa unchanged. Requirements are BC250,
Linux x86-64, Python 3.11+, a Vulkan loader, glibc 2.36+, GLIBCXX 3.4.30+ and the
usual graphics/runtime libraries. The installer probes dependencies and Vulkan
initialization before selecting the driver. See the tested configurations below.

Use **GE-Proton 11-6** with the provider versions below.

## The AMD-provider path

The tested provider setup uses ordinary GE-Proton **11-6**, upstream OptiScaler
**10.0.0-pre1 (September 4)** with **OptiPatcher 0.41**, the original AMD
**4.1.1** provider and the older SDK **4.0.2** bridge. The exact upstream
URLs and hashes are retained in
[the runtime input manifest](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/runtime/manifest.json).
The retained runtime's driver selection is a separate RC1 recovery pin.

The two AMD downloads for this recipe are:

| Download | Use / SHA256 of the DLL |
| --- | --- |
| [SDK 4.0.2 bridge](https://raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/f4c1da8e92f3fe563b5c28c44e6267ce6b6b8eb2/Kits/FidelityFX/signedbin/amd_fidelityfx_upscaler_dx12.dll) | Save as `amd_fidelityfx_upscaler_dx12.dll`; `241e6e5e4d848424eb8ec9a6b22c43fe34cf0cf52d30002ca435ba42e53a9ca0` |
| [Original FSR 4.1.1 provider, xz-compressed](https://loathingKernel.github.io/proton-upscalers/amdxcffx64_v4.1.1_398EA93C15D554EFB7ECE1F4CD057554.xz) | Decompress and rename the result to `amdxcffx64.dll`; `4e7dc37aebea3a90e3d3cc43e24cb2b54176b2535315f20dbe63b3b7cfc56b1e` |

The compressed provider archive itself has SHA256
`5de9b6d9f5475a0f2622e4cbce88cde46c68929d9bd0bbc353c70056997bb771`. Keep the upstream notices linked in the input
manifest. Verify the extracted DLL hashes before replacing files. For Steam,
the game's prefix is usually under `steamapps/compatdata/APPID/pfx` in its
Steam library; Heroic uses the configured Wine-prefix path.

For that route, close the game, back up the current integration and use the
pinned SDK bridge as `OptiScaler/amd_fidelityfx_upscaler_dx12.dll`. The
unmodified `amdxcffx64.dll` provider belongs in that game's Wine prefix under
`drive_c/windows/system32/`. Keep OptiScaler's other files and enable its INT8
selection hook with these settings:

```ini
[FSR]
UpscalerIndex=0
Fsr4ForceModel=2
Fsr4EnableWatermark=auto

[FrameGen]
Enabled=false
```

Retain the appropriate `ffx` / `ffx_12` backend and input settings for the game.
For a `winmm.dll` proxy, the recorded environment contains:

```sh
PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 PROTON_USE_XALIA=0 WINEDLLOVERRIDES="winmm=n,b;amdxcffx64=n" VKD3D_DISABLE_EXTENSIONS="VK_NVX_binary_import,VK_NVX_image_view_handle"
```

These accompany the private-driver wrapper. Adapt only the proxy name for an
established different adapter installation. If the current launch options contain `amdxcffx64=`, replace it with
`amdxcffx64=n` when selecting this provider route. Keep a record of the previous files and launch options for undo.

Keep the pinned SDK bridge for this provider route. The original
provider continues to identify itself as **4.1.1**, with source **DRIVER**.
The primary DLL identifies itself as **4.1.1r11**, with source **LOCAL**.
For temporary confirmation, `BC250_FSR4_RC9_LOG=true` prints exact shader matches
during compilation. Warm cache hits can be silent. `BC250_FSR4_RC9_DISABLE=true`
disables the new port and has a separate cache identity. Remove diagnostic
variables after checking. Leave `MLSR-WATERMARK` absent for a hidden watermark.

## Qualification and limitations

The [RC10 gameplay record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/legacy/research/driver-gameplay-rc10.md)
confirms Control gameplay and System Shock menu rendering through this route.
The [development record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/legacy/research/rc10-development.md)
contains the shader, image and timing comparisons. Those results retain their
original scope; arbitrary Proton versions, other GPUs, native Windows and
frame generation remain unqualified. DLL cold-compilation measurements apply to the DLL route.

## Shared cache and undo

The current installer handles optional caching and coordinated rollback;
follow its [status/recovery instructions](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-cache-setup.md#rollback-and-interrupted-operations).
Also restore any provider/bridge files and launch overrides you changed for
this route using their backups.

The [original RC10 instructions](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/driver-rc10.md)
remain available for that archive. The current compact driver download contains
runtime tools/provenance/notices; complete source and evidence are in the source archive.
