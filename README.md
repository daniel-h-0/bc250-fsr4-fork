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

The [installation guide](docs/beginner-guide.md) includes first-time OptiScaler
setup, exact paths and game recipes. [Native FidelityFX games](docs/beginner-guide.md#native-fidelityfx-games)
can use direct DLL replacement instead of OptiScaler to skip a setup step.

Testing covers **BC250/Linux**. [Release validation](docs/portable-dll-rc11.md)
shows the supported test scope and work awaiting qualification.

## Performance

![Measured FSR4 GPU cost on BC250: original FSR 4.1.1, FSR 4.1.1b, v3 and RC9 at 1080p, 1440p and 4K Quality. Lower is better.](docs/assets/fsr4-four-way-gpu-cost-rc9.svg)

**RC9 costs 3.93 / 5.92 / 12.08 ms per upscale at 1080p / 1440p / 4K Quality.**
RC9 was measured September 11, 2026; the baselines retain September 10 data.
[Method and raw data](docs/gpu-cost.md).
Subsequent release candidates have not changed performance.

**The DLL and alternative driver deliver the same FSR optimizations**, with
comparable measured GPU cost. The DLL is the simpler install. The driver needs
specific provider/Proton versions and is provided exclusively for compatibility
and convenience. [Comparison](docs/driver-rc10.md#dll-versus-driver).

RC10 reduced cold synthetic setup from **22.24 to 19.38 seconds**, preserving
RC9's native shader code. RC11 retains RC10's shaders and improves optional tools.
[Compilation results](docs/portable-dll-rc10.md) · [Release notes](docs/release-notes-rc11.md).

## Support and development

[Installation troubleshooting](docs/beginner-guide.md#if-the-check-fails) ·
[Update or undo](docs/beginner-guide.md#update-an-existing-installation) ·
[Tested configurations](docs/portable-dll-rc11.md) ·
[Build the DLL](dll/README.md) · [Contribute](CONTRIBUTING.md)

## Special thanks and notes

This continues [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4).
Thanks to AMD/GPUOpen, Mesa/RADV, Microsoft DXC, Wine, vkd3d-proton, Valve Proton,
GE-Proton and OptiScaler. [Licenses and attribution](THIRD_PARTY.md).
Development used GPT-6-Astra with human review.
