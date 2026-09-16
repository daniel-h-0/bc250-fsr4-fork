# Portable DLL RC10

Historical RC10 evidence. Current installation uses the
[OptiScaler DLL replacement guide](beginner-guide.md).

RC10 prepares FSR4 shaders for faster cold compilation and adds an optional
shared Mesa cache plus a Linux driver compatibility download. It retains the
RC9 model and arithmetic. This is a release candidate toward v4 final.

## Compiler cleanup

Pinned LLVM `early-cse`, `dce` and `strip-dead-prototypes` passes remove repeated
intermediate work from 48 shader slots. Every changed bytecode is covered by
native-code comparison: 36 distinct programs, each checked on standard Mesa,
the retained custom driver and the new driver prototype. Instructions,
constants, shader information and hardware configuration match RC9. The other
300 slots keep RC9 bytecode, including 24 alternative convolution slots without
that comparison. Weight guards, fallbacks, packed arithmetic and SDK ordering
repairs remain.

The selected shader candidate reduced median cold context-plus-first-dispatch
CPU time from **22.244 to 19.383 seconds (12.9%)** in four balanced trials on
BC250 / kernel 7.2.5. This measures synthetic upscaler setup, not total game
startup or ongoing FPS. Earlier broader-candidate trials independently found
12.2%. The shipping DLL uses the selected candidate's exact shader bytes and
an audited longer display label. All individual results, including slower
outliers in sustained rendering, remain in the evidence.

[Compiler and native-code evidence](rc10-development.md#narrowed-dll-proposal)
includes the original candidates and method. No new GPU speedup over RC9 is
claimed. The [RC9 GPU chart](gpu-cost.md) remains labeled with its original date
and measurements.

## Shared caching

The DLL ZIP includes an opt-in Linux launcher under `linux/`. Compatible Mesa
compilations can be reused across applications; existing Steam/Fossilize caches
are retained as read-only inputs. The current helper measured **22.546 seconds**
for initial population and **1.447 seconds** through a new application view.
This is a separate reuse experiment and must not be added to the compiler
cleanup percentage as a combined game-startup claim.

Launch/filesystem checks pass in four isolated userspaces, from Python 3.8 to
3.14. XDG/HOME paths, concurrent starts, late Steam cache creation, missing
Python and unwritable storage are covered. Full graphics stacks across all
those distros are not qualified. Old multi-file/database caches are preserved
but not imported; opting in may require an initial compilation. See the
[shared-cache guide](shared-shader-cache.md).

## Driver option

The [Linux driver guide](driver-rc10.md) describes the private Mesa 26.2.2 build,
installation, provider/Proton pins and rollback. Its 42 exact original/RC9 shader
pairs cover three resolution families. Synthetic GPU cost is comparable with
the RC9 DLL at 1080p, 1440p and 4K. Unknown or changed translated inputs keep the
earlier driver path. This does not establish RC9 parity for arbitrary Proton
versions. The primary DLL remains the simpler recommended route.

## Exact release identity and validation

Project version **4.0.0-rc10**, SDK display label **4.1.1r10**. DLL size
**94,840,832 bytes**, SHA256:

```text
a96040f8c0790a0d490f061b377a2ebb31cca1f2591ab5c9469f0ef8e6aa3d89
```

A clean assembly/validation of all 348 sources reproduces that DLL exactly.
The label's original eight-byte slot cannot hold `4.1.1r10` and its terminator;
the repacker now places the name in its read-only section and redirects the
single audited provider-name LEA. The adjacent watermark and numeric SDK/API
versions are unchanged. The actual FFX API reports the new label.

Final artifact, installer and translator checks are recorded in
[data/portable-dll-rc10.json](data/portable-dll-rc10.json). Synthetic API checks
are distinguished from real-game observations. The historical seven-game matrix
belongs to RC7, and is not relabeled as new RC10 gameplay evidence. Native
Windows, other GPUs, frame generation and unlisted combinations need separate
qualification.

The installed Valve Proton 11.0-2c and Experimental 11.0-20260910b both pass
an additional 1440p direct-DLL image check. Experimental also matches all 14
expected driver substitution programs for that context and its provider image
matches RC9. Proton 11.0-2c removes the manually supplied provider and falls
back to FSR3; that driver/provider check fails and remains recorded. These
single-context checks do not replace the GE-Proton three-resolution matrix.

The extracted driver package passed a real private install, upgrade from the
retained RC1 driver, Vulkan initialization, bad-checksum rejection and ordered
rollback to RC1 and then no selected driver. Enabled/disabled cache UUIDs are
distinct and repeat consistently. The system driver was not replaced.

The driver option subsequently passed normal Steam gameplay in Control,
including saved-scene navigation and all 14 expected substitutions. System
Shock's DX11 route passed animated menu rendering with 13 substitutions; it is
not a gameplay pass. The [gameplay record](driver-gameplay-rc10.md) distinguishes
those results from the earlier incomplete offline smoke test. These driver
checks do not establish a new gameplay matrix for the primary DLL.

Use the [installation guide](../dll/INSTALL.md), retain the original DLL backup
and launch options, and leave watermark control at `auto` with `MLSR-WATERMARK`
absent after any temporary visual check.
