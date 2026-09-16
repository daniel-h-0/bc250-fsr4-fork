# BC250 FSR4

**FSR 4.1.1 INT8 optimizations in one DLL.** Use it through OptiScaler with your
normal graphics driver and Proton. Replace its bundled upscaler DLL.

**[Download RC11](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc11/bc250-fsr4-dll-4.0.0-rc11.zip)** ·
**[Install with OptiScaler](docs/beginner-guide.md)** ·
[All documentation](docs/README.md)

## Install

1. With OptiScaler installed, back up `OptiScaler/amd_fidelityfx_upscaler_dx12.dll`
   and replace it with the DLL from the [RC11 ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc11/bc250-fsr4-dll-4.0.0-rc11.zip).
2. Select the FFX / INT8 backend using the [guide's settings](docs/beginner-guide.md#2-select-fsr4-int8).
3. Keep working launch options, select the game's usual upscaler input, and play.

The [installation guide](docs/beginner-guide.md) provides exact paths, settings
and game recipes, including first-time OptiScaler setup. No custom driver,
BC250 compatibility tool or shared-cache helper is required. Normal shader
caches stay enabled; first use can still pause for compilation.

BC250/Linux is the tested platform. Windows and other GPUs remain unqualified.
[Native FidelityFX games](docs/beginner-guide.md#native-fidelityfx-games) can use
direct DLL replacement instead.

## Performance

![Measured FSR4 GPU cost on BC250: original FSR 4.1.1, FSR 4.1.1b, v3 and RC9 at 1080p, 1440p and 4K Quality. Lower is better.](docs/assets/fsr4-four-way-gpu-cost-rc9.svg)

**RC9 costs 3.93 / 5.92 / 12.08 ms at 1080p / 1440p / 4K Quality.** These are
upscaler GPU timings, not whole-game FPS. RC9 was measured on September 11, 2026;
the three baselines retain September 10 data. [Method and raw data](docs/gpu-cost.md).

**The custom driver is an alternative delivery method, not an extra performance
upgrade for DLL users.** Both carry the same FSR optimizations; the tested
three-resolution comparison found comparable GPU cost. The driver has stricter
provider/Proton requirements.
[DLL versus driver: results and limits](docs/driver-rc10.md#dll-versus-driver).

RC10 reduced measured cold synthetic setup from **22.24 to 19.38 seconds** while
preserving RC9's native shader code. RC11 retains RC10's shaders and changes
packaging/cache tools. Neither adds a new steady-state FPS claim to this chart.
[RC10 measurements](docs/portable-dll-rc10.md) · [RC11 release notes](docs/release-notes-rc11.md).

## Support and development

[Installation troubleshooting](docs/beginner-guide.md#if-the-check-fails) ·
[Update or undo](docs/beginner-guide.md#update-an-existing-installation) ·
[Tested configurations](docs/portable-dll-rc11.md) ·
[Build the DLL](dll/README.md) · [Contribute](CONTRIBUTING.md)

RC11 has synthetic D3D12 rendering checks; the seven earlier game-route checks
belong to RC7. Frame generation and unlisted integrations need separate testing.

## Special thanks and notes

This continues [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4).
Thanks to AMD/GPUOpen, Mesa/RADV, Microsoft DXC, Wine, vkd3d-proton, Valve Proton,
GE-Proton and OptiScaler. [Licenses and attribution](THIRD_PARTY.md).
Development used GPT-6-Astra with human review.
