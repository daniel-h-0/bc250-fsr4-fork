# Changelog

## RC9 documentation refresh 1 — 2026-09-11

- Add an illustrated beginner walkthrough with pinned OptiScaler/helper downloads,
  eight game recipes, Steam/Heroic instructions, a real SDK-rendered RC9/INT8
  watermark reference, and explicit update/undo steps.
- Publish distinct `-docs1` DLL packages with corrected instructions. The original
  RC9 packages' README footer had RC8's size/hash; their DLL and internal checksums
  were correct. Original assets/tags and the exact RC9 DLL remain unchanged.
- Check the installation guide's release version, provider label, size and checksum
  against the manifest during repository validation and packaging. Documentation
  revisions get separate asset names and cannot overwrite existing files.
- Clarify current DLL versus retained driver/runtime instructions and preserve
  historical RC7/RC8 evidence. Fix the retained setup archive's current-guide link.

## 4.0.0-rc9 — 2026-09-11 — retained shader checkpoint and chart refresh

- Publish the retained 1440p checkpoint as DLL `4.1.1r9`: twelve slots change
  from RC8 and nineteen from RC7. All 348 compiled shaders match the qualified
  development DLL; only its label and PE checksum differ.
- Extend exact Winograd and bounded packed arithmetic, preserve dynamic-weight
  fallbacks, scalar-cast compatibility and the SDK synchronization repair.
- Measure the final DLL at 1080p/1440p/4K Quality: 3.92825/5.91826/12.08447 ms.
  Refresh only RC9 in the chart; preserve all 36 original baseline runs.
  Retain the separate earlier RC7/development matched result with its original
  identity. This refresh shows a slightly higher 1080p value than the RC7 chart.
- Verify three complete model/image preflights and seven additional 1440p image
  cases against sealed references. No new game or endurance claim.
  See [RC9 measurements and scope](docs/portable-dll-rc9.md).

## 4.0.0-rc8 — 2026-09-10 — 1440p performance checkpoint

- Reduce measured whole-upscaler GPU time at 1440p Quality by 8.52% versus
  RC7 in fresh matched final-DLL tests: 6.61728 ms → 6.05354 ms. Publish all
  4,800 timestamps, per-run medians, sampled clocks and image hashes.
- Update sixteen shader slots with native integer dot products, Winograd
  convolution, streamed arithmetic, improved weight checks and wave choices.
  Retain dynamic-weight fallbacks, all other 332 shader hashes, the RC7
  integer-cast compatibility repair and the SDK synchronization repair.
- Identify the DLL as `4.1.1r8`; preserve its numeric API/provider identity.
  Rebuild all 348 validated shaders into the exact published DLL bytes.
- Match all seven final-DLL image pairs for HDR, SDR, motion, reset, dynamic
  resolution, sharpening and Balanced input at 1440p. Keep component
  arithmetic/fallback evidence separate from fresh release checks.
- Keep RC7's chart and seven game-route checks as historical evidence. RC8
  has no fresh game, 1080p/4K, native-Windows or other-GPU qualification.
  See [RC8 measurements and scope](docs/portable-dll-rc8.md).

## 4.0.0-rc7 — 2026-09-10 — portable DLL candidate

- Make one Windows x64 upscaler DLL the primary download. Embed all 348
  model, image-preparation and final-output shader permutations, carrying
  the v4 optimizations and guarded fallbacks without a custom Mesa or Proton
  installation.
- Express 17,964 two-lane integer extensions as equivalent scalar lane
  operations. This fixes the vkd3d translation failure on ordinary Proton
  10 and 11 while retaining packed arithmetic and measured v4 parity.
- Restore the SDK's existing buffer-UAV barrier for padding-clear compute jobs.
  A one-byte host-code change orders the clear after preceding model work,
  addressing the intermittent image corruption reproduced during review.
- Identify the modified SDK as `4.1.1r7`, preserving its numeric API/provider
  identity. Document native-loader compatibility, the optional OptiScaler
  input adapter, DLL replacement, undo and tested platforms.
- Include complete editable shader sources, hash-pinned reproducible builds,
  executable lowering checks, strict source inventories and deterministic
  DLL packages. The retained RC6 driver/runtime pins do not change.
- Verify the final DLL through seven normal-Steam game routes, including the
  native Deadzone/KCD2 loaders, DX12/DX11/Vulkan adapters and Roboquest’s
  existing Luma/ReShade setup. Record the No Man’s Sky first-compilation
  hang and successful restart; keep untested routes explicit.
- Record [candidate compatibility and measurement limits](docs/portable-dll-rc7.md).
  No Windows/other-GPU acceptance is implied by the portable format.

## 4.0.0-rc6 — 2026-09-08

- Register the Steam tool as `proton-bc250-fsr4`. Steam's Windows save-folder
  defaults depend on the internal tool name containing `proton`; the old
  `BC250-FSR4` key left those mappings empty. Keep the old key as an alias so
  existing game selections work without account or prefix edits.
- Upgrade only the exact owned registration, preserve its original bytes,
  and retain the fix across runtime rollback. Report the old registration in
  `status` and `doctor`; an otherwise unchanged update also repairs it.
- Include the source-build verification follow-up below. Driver, upstream
  runtime components and graphics preset are unchanged. See the
  [save-path diagnosis, upgrade and validation](docs/save-paths-rc6.md).

## Source-build review follow-up — 2026-09-08

- Reject added or missing materialized source files during resume and packaging.
  A real preprocessor check reproduces an unrecorded header shadowing an
  unchanged recorded header. Also record executable modes and internal links.
- Disable generator bytecode output to keep the source inventory stable.
- Clarify which rewrites `BC250_FSR4_DISABLE=1` controls. Generic GFX1013
  lowerings and the independent store repair remain active.
- These development-tool corrections leave the RC5 runtime, published assets,
  driver bytes and previous qualification unchanged.

## 4.0.0-rc5 — 2026-09-08

- Honor explicit v3 migrations with reversible private-driver transactions,
  including when a compatible driver is already installed.
- Guard managed driver paths, legacy extraction and private archive publication.
- Keep Steam utility calls and zero-ID launches out of game upscaler injection.
- Pin imported build helpers and check setup-version/FSR-cost documentation drift.
- Refresh current upgrade and recovery instructions. Reuse the unchanged RC4
  portable driver and RC3 upstream runtime components; see the
  [review and validation scope](docs/review-rc5.md).

## v4.0.0-rc4 — portable installation and startup checks

- Make an older-library driver build the default private download. Pin Debian 12
  compiler/headers/libraries, retain the original GNU TLS ABI, statically link
  current libdrm/AMDGPU and omit optional display-info and SPIRV-Tools dependencies.
- Automatically replace incompatible/superseded private drivers while preserving
  coordinated rollback and reuse of qualified working system drivers.
- Support maintained Python 3.11; remove normal installation's `patch`, `ldd`
  and `vulkaninfo` requirements. Download a pinned static extractor if needed.
- Check the selected driver on the host and inside available Steam Runtime 4;
  discover the runtime on secondary libraries. Add `bc250-fsr4 doctor` and retain
  pre-Proton launch failures in a small user-state log.
- Reuse verified retained runtime files for offline driver rebinding, and honor
  explicit local replacement archives even when their Mesa source matches.
- Keep RC3’s components and game preset unchanged. See the separate
  [RC4 compatibility qualification](docs/rc4-compatibility.md); earlier game
  performance results and release assets retain their original scope and identity.

## v4.0.0-rc3 — DX11, Vulkan and mod-chain compatibility

- Route DX11 and Vulkan upscaler inputs to the FSR 4.1.1 INT8 D3D12 bridge,
  alongside the existing DX12 route; retain the qualified rc1 driver unchanged.
- Use a prefix-local WinMM proxy and enable existing ReShade/Luma chaining.
  Defer early Luma device creation, which failed during Roboquest startup.
- Supply a pinned signed NVIDIA NGX helper for signature validation and retain
  its license. Rendering continues through AMD FSR, independent of that helper.
- Advertise Vulkan DLSS input capabilities while excluding unsupported NVX
  extensions from vkd3d's D3D12 bridge. Preserve upstream vendor detection.
- Qualify six loaded-save scenes across DX11, DX12 and Vulkan, including Luma;
  document the [evidence and limits](docs/runtime-qualification.md).
- Verify RC2 → RC3 → RC2 → RC3 prefix reconciliation, tracked-file removal,
  offline reuse and [the existing-user update route](docs/upgrading-rc2.md).

## Documentation follow-up — rc1 transition

- Document rc1 driver reuse, legacy hook recovery, custom paths, launch-option
  cleanup and the limits of undoing the transition to the unified runtime.
- Distinguish the unified command from the older system-package helper and
  include the transition guide in future setup exports.
- Acknowledge the principal upstream projects in the README's special thanks.

Driver/runtime pins, the rc2 release tag and published assets are unchanged.

## v4.0.0-rc2 — unified distribution

- Add one `bc250-fsr4 install/update/status/rollback` interface for the driver
  and Steam runtime, reusing a compatible verified driver and supporting v3 upgrades.
- Add the BC250 FSR4 Steam compatibility tool.
  Users opt in through Steam's Compatibility menu; the active runtime has no
  game allowlist, executable scan or Steam-account configuration writer.
- Use pinned GE-Proton loader/prefix management with a narrow local-manifest
  patch, FSR 4.1.1 INT8 model 2, OptiScaler nightly 20260904 and OptiPatcher 0.41.
  Both FSR and DLSS input routes passed [runtime gameplay checks](docs/runtime-qualification-rc2.md).
- Distribute a small setup bundle. Assemble pinned upstream components locally,
  support cached offline installation and optional private complete bundles,
  and retain immutable runtime versions for rollback.
- Retire the per-game wizard and keep its rollback/recovery entry point for
  existing installations. Consolidate user guidance around the new runtime.
- Strengthen driver build provenance, payload verification, v3 migration,
  interrupted-transaction recovery and preservation of later user edits.
- Export deterministic source snapshots with exact commit/file manifests.
  Check source inputs, documentation, all 360 performance samples and tooling
  tests without a GPU; verify extracted distributions in CI.
- Check Python 3.12/3.14 and Ruff, and pin maintained GitHub Actions revisions.
  Manual container builds produce explicitly unqualified artifacts.
- Include Mesa's license texts in new driver archives and preserve upstream
  notices for runtime source fixtures and downloaded components.

These changes preserve the original rc1 driver, tag, release assets and
qualification. See [release identities](docs/releases.md).

## 2026-09-07 — performance qualification addendum

Commit `f7d59b030af519059b06ce3953c2c91e5bd6e1c5` records the fresh v3/v4
Deadzone comparison at 1080p, 1440p and 4K. The test used the exact published rc1
driver with native FSR 4.1.1 INT8 Quality and hardware ray tracing off. The
measured scene averages improved by 14.3%, 18.9% and 17.2%, respectively.
[Method, raw aggregates and limits](docs/performance.md) accompany the results.
The rc1 tag and original binary/source release assets were unchanged.

## 2026-09-07 — v4.0.0-rc1

Tag `v4.0.0-rc1` records source commit
`362c4c4a74456002e4697ca0e1d1bb3aaff1539d`. The first v4 prerelease provides
Mesa 26.2.2 RADV for BC250 GFX1013, default-on qualified FSR 4.1.1 INT8
optimizations, private v3 migration, rollback and Arch/CachyOS package
integration. Only x86_64 is released.

The exact native binary passed compiler/output checks and real Deadzone
private/system installation checks. The container build has a narrower test
scope. See the [qualification record](docs/qualification.md) and
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc1).

Earlier upstream history and its original release notes remain in the
[legacy archive](legacy/README.md).
