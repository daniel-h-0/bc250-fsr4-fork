# BC250 FSR4 — portable DLL, RC10

**FSR 4.1.1 INT8 optimizations in one Windows x64 DLL.** Install it through a
working OptiScaler adapter or a compatible native FidelityFX game.

[Download RC10](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc10).
[RC9 remains available](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc9).
The RC10 DLL identifies itself as **4.1.1r10**. Its shader cleanup reduces cold
compilation work while retaining RC9's model and arithmetic. BC250/Linux is
the tested platform; Windows and other GPUs remain unqualified.

## Install

The primary download is the [RC10 DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc10/bc250-fsr4-dll-4.0.0-rc10.zip). It contains the
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
  [Linux driver download](docs/driver-rc10.md) supplies RC9 optimizations through
  private RADV. Its provider and Proton versions matter; the DLL is the primary route.

Close the game before replacing files. To undo, restore the backed-up DLL and
changed launch settings. Preserve saves, prefixes and other mods. After a
watermark check, use `Fsr4EnableWatermark=auto` and leave `MLSR-WATERMARK` absent.

**First compilation can still look frozen.** RC10 reduces some of the work;
it does not remove the need to compile for the actual GPU/driver combination.
Keep shader caches. [First-launch guidance](docs/first-run-shader-compilation.md).

## What changed

The selected compiler-cleanup candidate measured **22.24 → 19.38 seconds** for
cold synthetic context creation plus first dispatch on BC250, about **13%**.
All 36 distinct changed programs produce the same native instructions,
constants and hardware configuration as RC9 on three compared Mesa builds.
The 48 changed slots retain model-weight guards and fallback computation;
the other 300 slots keep RC9 shader bytes. This is a startup-work improvement,
not a new FPS claim. [Results, exact identity and limits](docs/portable-dll-rc10.md).

The optional [Linux shared-cache launcher](docs/shared-shader-cache.md) reuses
compatible Mesa compilations across applications while preserving their Steam
caches. It handles XDG paths, concurrent starts and preparation failures. The
four tested userspaces establish launcher portability, not universal graphics
qualification. Each application opts in; existing multi-file caches are not imported.

The driver option carries RC9 optimizations in Mesa 26.2.2 and has comparable
synthetic GPU cost to the RC9 DLL. Its exact translated-shader matches limit
coverage to verified provider/translator combinations. It includes installation
and rollback without replacing system Mesa.

## Compatibility and evidence

The synthetic checks cover 1080p, 1440p and 4K, with additional history,
motion and HDR scenarios. Reserved 8K contexts exercise another shader family;
that is not full 8K gameplay. The older AMD-provider route retains a pre-existing
dynamic-resolution difference from the direct SDK route.

The driver option passed [Control gameplay qualification](docs/driver-gameplay-rc10.md)
through its original AMD-provider route. System Shock's additional DX11 result
covers menu rendering only. Game files, saves and original settings were restored.

The [historical seven-game checks](docs/portable-dll-rc7.md) belong to RC7.
Do not read the RC10 synthetic matrix as a new playthrough of every game.
Frame generation and unlisted integrations need separate qualification.
The [RC9 GPU-cost chart](docs/gpu-cost.md) keeps its original measurements and date.

## Build and review

Current development also includes [simpler Linux cache setup](docs/shared-shader-cache.md)
and a [driver installer with integrated caching](docs/driver-cache-setup.md).
These tooling changes are separate from the published RC10 downloads.
The [follow-up review](docs/cache-review.md) records update/fallback corrections,
portability checks and the unchanged scope of the gameplay evidence.

The [DLL source](dll/README.md) includes all 348 editable LLVM/DXIL sources,
pinned SDK/DXC inputs, and an assembler/validator/repacker. The build must
reproduce the exact release DLL hash.

```sh
python3 scripts/check-repo.py
python3 dll/build.py --sdk /path/to/original/amd_fidelityfx_upscaler_dx12.dll \
  --dxcompiler /path/to/dxc/lib/libdxcompiler.so --output .work/dll --jobs 2
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
```

[RC10 release notes](docs/release-notes-rc10.md) ·
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
