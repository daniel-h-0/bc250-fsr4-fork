# Unified distribution qualification — v4.0.0-rc2

The shared Steam runtime rendered loaded-save gameplay in **Deadzone Rogue**
through an FSR input and **Control Ultimate Edition** through a DLSS input on
September 7, 2026 (EDT). Both showed **FSR4-I8 4.1.1, SOURCE: DRIVER,
commit 143E11E, COLORSPACE: LINEAR** in the actual rendered frame.
Frame generation was disabled. Both games exited and released their Proton
sessions normally.

These two titles exercise the input routes; they are not an installation
allowlist. This is a bounded functionality check, not broad compatibility,
endurance or new performance qualification. The [older performance results](performance.md)
used the previous integration and remain unchanged.

## Exact components and evidence

[Machine-readable evidence](data/runtime-v4.0.0-rc2.json) records the runtime
lock, independently reproduced assembly inventory, observed process IDs,
component and screenshot hashes. The [release evidence archive](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc2/bc250-fsr4-v4.0.0-rc2-runtime-proof.tar.gz)
contains the unedited screenshots and focused log excerpts.

The game processes mapped the exact v4.0.0-rc1 system driver, FSR 4.1.1 provider,
OptiScaler nightly 20260904, OptiPatcher 0.41 and AMD SDK 4.0.2 bridge pinned in
`runtime/manifest.json`. Each mapping was checked against the process's file
namespace and inode before hashing. The provider watermark distinguishes
actual driver-backed rendering from merely loading its DLL.

The test host used CachyOS, kernel 7.2.3-1.83, GFX1013 and native Steam with
Steam Linux Runtime 4. Deadzone used 2560×1440/Balanced; Control used
1920×1080/Quality. Existing game-local proxy deployments were temporarily
removed and preserved so the shared runtime supplied the integration.

## Installation and recovery

- Real system-driver reuse: install, unchanged update, first-install rollback
  and reinstall passed. The system driver hash stayed unchanged.
- Isolated v3 upgrade: a source-verified Mesa 26.2.0 v3 library was migrated
  through the unified command using the real rc1 driver archive and full
  runtime. Private-driver reuse passed; rollback restored the original v3 ICD
  bytes and removed the new Steam entry, retaining recovery payloads.
- Two complete assemblies produced identical inventory bytes. Real GE prefix
  installation and a second offline invocation passed, with no network calls
  and unchanged DLL bytes/mtimes on reuse.
- Automated checks cover driver/runtime failure recovery, interrupted commits,
  first-install undo, independent-edit conflicts and game-session locking.

After testing, 134 original files and six owned Steam fields passed restoration
checks. Steam was restarted, with the system driver unchanged. The new tool
remains available; the games retain their previous production selections.

The setup retains upstream licensing and downloads binaries from their pinned
origins. It does not scan the game library or edit Steam account configuration.

## Issues resolved during qualification

Steam utility calls now skip upscaler injection and all calls disable Python
bytecode writes into the immutable runtime. Explicit prefix paths make the
SDK and plugin available without game-local files. Xalia is disabled because
its inherited proxy kept closed game sessions alive; its Windows UI
accessibility is unavailable with this runtime.

AMD's bundled 4.1.1 SDK initially selected its own local implementation.
The older, unmodified AMD SDK 4.0.2 bridge lets the pinned 4.1.1 driver provider
win. Both final gameplay checks confirmed `SOURCE: DRIVER`; earlier fallback
runs are excluded from acceptance.
