# BC250 FSR4

**FSR 4.1.1 INT8 optimizations in one DLL.** Replace the upscaler DLL in
OptiScaler or a compatible native FidelityFX game. Use your normal graphics
driver and Proton.

**[Install across your games](docs/optiscaler-client.md)** ·
[Manual install and game recipes](docs/beginner-guide.md) ·
[Download the DLL](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/opticlient-v1.0.7-bc250.1/bc250-fsr4-dll-4.0.0-rc11-docs1.zip) ·
[All documentation](docs/README.md)

## Install

1. Open the [BC250 build of OptiScaler Client](docs/optiscaler-client.md) and scan your library.
2. Choose **Install across your games**, select games, and install. RC11 and its
   FFX/INT8 settings are included; each game shows any first-time loading step.
3. Retain working launch options and launch normally. Import future DLL ZIPs once
   to update selected games together.

Prefer to copy the DLL yourself? The [manual guide](docs/beginner-guide.md) covers
OptiScaler setup, custom paths and game recipes. [Native FidelityFX games](docs/beginner-guide.md#native-fidelityfx-games)
can use direct replacement without OptiScaler. The client route manages local
OptiScaler copies on Linux; its guide lists supported fresh setups and existing adapters.

Testing covers **BC250/Linux**. [Release validation](docs/portable-dll-rc11.md)
shows the supported test scope and work awaiting qualification.

## Performance

![Measured FSR4 GPU cost on BC250: original FSR 4.1.1, FSR 4.1.1b, v3 and RC9 at 1080p, 1440p and 4K Quality. Lower is better.](docs/assets/fsr4-four-way-gpu-cost-rc9.svg)

**RC9 costs 3.93 / 5.92 / 12.08 ms per upscale at 1080p / 1440p / 4K Quality.**
RC9 was measured September 11, 2026; the baselines retain September 10 data.
[Method and raw data](docs/gpu-cost.md).
Subsequent release candidates have not changed performance.

The optional [Linux driver package](docs/driver-cache-setup.md) is a private
Mesa/RADV build for selected games. Paired with AMD's FSR provider, it applies
the optimizations through the graphics driver.

**The DLL and alternative driver deliver the same FSR optimizations**, with
comparable measured GPU cost. The DLL is the simpler install. The driver needs
specific provider/Proton versions and is provided exclusively for compatibility
and convenience. [Comparison](docs/driver-rc10.md#dll-versus-driver).

RC10 reduced cold synthetic setup from **22.24 to 19.38 seconds**, preserving
RC9's native shader code (and runtime performance). RC11 retains RC10's shaders and improves optional tools.
[Compilation results](docs/legacy/research/portable-dll-rc10.md) · [Release notes](docs/release-notes-rc11.md).

## Support and development

[Installation troubleshooting](docs/beginner-guide.md#if-the-check-fails) ·
[Client update or restore](docs/optiscaler-client.md#update-or-restore) ·
[Manual update or undo](docs/beginner-guide.md#update-an-existing-installation) ·
[Tested configurations](docs/portable-dll-rc11.md) ·
[Build the DLL](dll/README.md) · [Contribute](CONTRIBUTING.md)

## Special thanks and notes

This continues [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4) - many thanks to him for
originating this work and achieving the first bundle of performance wins.
Thanks to AMD/GPUOpen, Mesa/RADV, Microsoft DXC, Wine, vkd3d-proton, Valve Proton,
GE-Proton and OptiScaler. [Licenses and attribution](THIRD_PARTY.md).
GPT-6-Astra was used in the development of this project, with constant human review.
