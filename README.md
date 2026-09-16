# BC250 FSR4 — portable DLL, RC11

**FSR 4.1.1 INT8 optimizations in one Windows x64 DLL.** Install it through a
working OptiScaler adapter or a compatible native FidelityFX game.

[Download RC11](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11).
[RC10 remains available](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc10).
RC11 ships simpler Linux shared-cache setup and reviewed update/recovery tools.
Its DLL identifies itself as **4.1.1r11** and keeps all 348 RC10 shader programs.
BC250/Linux is the tested platform; Windows and other GPUs remain unqualified.

## Install

The primary download is the [RC11 DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc11/bc250-fsr4-dll-4.0.0-rc11.zip). It contains the
DLL, instructions, notices, checksums and optional Linux cache helpers. Keep
an original DLL backup and the previous launch-option text.

- **Already using OptiScaler:** replace its nested
  `OptiScaler/amd_fidelityfx_upscaler_dx12.dll`. Use the FFX/FSR4 backend and
  INT8 model 2. Follow the [short installation guide](dll/INSTALL.md).
- **First installation:** the [illustrated beginner guide](docs/beginner-guide.md)
  pins one OptiScaler version and gives exact game folders, settings and undo.
- **Native FidelityFX:** follow the game's supported replacement filename.
  Deadzone Rogue uses the upscaler filename; KCD2's recorded route uses the
  loader filename and native FSR 4.1. These are separate recipes.
- **Existing AMD-provider integration:** the optional
  [Linux driver download](docs/driver-cache-setup.md) supplies RC9 optimizations through
  private RADV. Its provider and Proton versions matter; the DLL is the primary route. **Note that there
  is no performance or image quality difference inherent between the two installation methods.** They
  are both provided for ease of use and general compatibility.

Close the game before replacing files. To undo, restore the backed-up DLL and
changed launch settings. Preserve saves, prefixes and other mods. After a
watermark check, use `Fsr4EnableWatermark=auto` and leave `MLSR-WATERMARK` absent.

**First compilation can still look frozen.** RC10 reduces some of the work;
it does not remove the need to compile for the actual GPU/driver combination.
Keep shader caches. [First-launch guidance](docs/first-run-shader-compilation.md).

## What changed in RC11

- **DLL route:** install the optional Linux cache helper once and paste your
  existing Steam launch options when prompted. Setup prints a complete command
  and installs permanent files; the extracted download can then be removed.
- **Driver route:** one permanent launcher selects the private driver and prepares
  shared caching. New CLI installs default to caching on; updates preserve the
  existing preference, with explicit opt-out and joint rollback.
- **Status and recovery:** read-only status, real write checks and safe launch
  fallback; verified tool updates, checked rollback targets, and retryable helper
  removal after interruption. Relocated or edited managed files are preserved.

Follow the [DLL cache guide](docs/shared-shader-cache.md) or
[driver installation guide](docs/driver-cache-setup.md). Each game opts in.
Existing ordinary caches are not imported; first use can still compile shaders.
The helper preserves existing Steam/Fossilize reads and lets Mesa enforce
GPU/driver/compiler compatibility. Four userspace test environments do not
establish universal distro or sandbox graphics support.

RC11 changes only the provider label and PE checksum in the DLL; the driver
binary is identical to RC10. It adds no shader or FPS improvement over RC10.
RC10's earlier compiler cleanup measured **22.24 → 19.38 seconds** for cold
synthetic upscaler setup, about **13%**, while preserving RC9 native code.
[Original measurements](docs/portable-dll-rc10.md) and
[RC11 identity and validation](docs/portable-dll-rc11.md) keep those scopes separate.

## Compatibility and evidence

RC10's synthetic checks cover 1080p, 1440p and 4K, with additional history,
motion and HDR scenarios. Reserved 8K contexts exercise another shader family;
that is not full 8K gameplay. The older AMD-provider route retains a pre-existing
dynamic-resolution difference from the direct SDK route.

The unchanged driver retains RC10's [Control gameplay qualification](docs/driver-gameplay-rc10.md)
through its original AMD-provider route. System Shock's additional DX11 result
covers menu rendering only. Game files, saves and original settings were restored.

The [historical seven-game checks](docs/portable-dll-rc7.md) belong to RC7.
Do not read the RC10 synthetic matrix as a new playthrough of every game.
Frame generation and unlisted integrations need separate qualification.
The [RC9 GPU-cost chart](docs/gpu-cost.md) keeps its original measurements and date.

## Build and review

The [cache setup evidence](docs/cache-setup-qualification.md) records actual
Control-to-System-Shock reuse, with System Shock limited to menu rendering.
The [installer reviews](docs/cache-review2.md) cover the tooling now shipped in RC11.
Current binaries and release packaging have [separate validation](docs/portable-dll-rc11.md).

The [DLL source](dll/README.md) includes all 348 editable LLVM/DXIL sources,
pinned SDK/DXC inputs, and an assembler/validator/repacker. The build must
reproduce the exact release DLL hash.

```sh
python3 scripts/check-repo.py
python3 dll/build.py --sdk /path/to/original/amd_fidelityfx_upscaler_dx12.dll \
  --dxcompiler /path/to/dxc/lib/libdxcompiler.so --output .work/dll --jobs 2
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
```

[RC11 release notes](docs/release-notes-rc11.md) ·
[Four-asset distribution](docs/releases.md) ·
[Full development evidence](docs/rc10-development.md) ·
[Contributing](CONTRIBUTING.md) · [Notices](THIRD_PARTY.md)

## Special thanks and notes

This work continues [dmoraza's BC250 FSR4 project](https://github.com/dmorazasanchez/bc250-fsr4).
Thanks to AMD/GPUOpen, the Mesa and RADV contributors, Microsoft DXC,
Wine, vkd3d-proton, Valve Proton, GE-Proton and OptiScaler for the underlying
algorithms, compilers and compatibility work. Their licenses and attribution
remain with the source and release notices. This work was accomplished with the
assistance of GPT-6-Astra, with constant human review and oversight.
