# Changelog

## Unreleased — SteamOS driver ABI repair

- Reproduce shared startup failures with Valve's SteamOS 3.7/3.8 libraries.
  Keep the original driver and runtime release records unchanged.
- Build a separate SteamOS driver against pinned target headers/libraries,
  use the original GNU TLS ABI, omit the optional direct-display dependency
  and statically link the required libdrm/AMDGPU version.
- Verify and honor an explicit replacement driver archive even when an older
  source-compatible driver exists; preserve installation and rollback ownership.
- Record the [candidate's checks and limits](docs/steamos-compatibility.md).
  This is not a new performance campaign or full SteamOS gameplay acceptance.

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
