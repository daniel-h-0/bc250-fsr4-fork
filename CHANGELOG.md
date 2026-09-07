# Changelog

## Unreleased — maintenance on v4

- Add guided game setup: detect supported Steam games and accounts, fetch
  pinned runtime dependencies, merge launch options and select Proton with
  one rollback record. Preview without changes using `--dry-run`.
- Separate the short end-user game guide from manual integration and
  troubleshooting, and document a working v3 user's actual upgrade needs.
- Clarify that the qualified runtime is pinned FSR 4.1.1 INT8 and should not
  be co-installed with the newer 4.1.1b mod; document switching between setups.
- Recover interrupted game setup and rollback transactions, preserve later
  user edits, and support verified upgrades of helper-managed runtimes.
- Verify retained driver/runtime payloads before rollback or reuse, reject
  symlinks and special files, and accept normalized SHA256 input.
- Resolve game mappings through the process namespace, reject stale log files,
  and report the limits of associating initialization lines with a launch.
- Bind new build provenance to the full materialized source, toolchain,
  dependency versions, recipe and Meson configuration. Refuse stale builds
  during packaging and resume; retain package metadata and complete archives.
- Keep inactive system status valid JSON for scripts and integrations.
- Clarify installation routes, repository layout, source distribution,
  contribution checks and the scope of the inherited archive.
- Add a deterministic source exporter with exact commit, file hashes, modes
  and an adjacent archive checksum. Exported snapshots retain all tracked
  source, documentation, tests, CI and historical material.
- Add a GPU-free repository check for pinned inputs, active documentation
  links, all 360 published performance samples, syntax and tooling tests.
- Check Python 3.12 and 3.14 plus Ruff in CI. Verify source snapshots after
  extraction without Git metadata and prepare their pinned Mesa source on
  pushes and pull requests. Manual container builds also package and check
  the extracted binary distribution; these artifacts remain unqualified.
- Pin maintained GitHub Actions releases using Node.js 24, removing the
  deprecated action-runtime dependency.
- Include the full Mesa license-text directory in newly generated binary
  archives alongside the existing summary and scoped project notices.
- Keep current tooling development separate from the immutable rc1 driver,
  tag, release assets and recorded qualification.

This section describes branch maintenance. It does not announce a new driver
release or qualify binaries built with later toolchains. See
[release identities and distribution](docs/releases.md).

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
