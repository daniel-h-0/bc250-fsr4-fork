# Release identities and distribution

The published prerelease is
[v4.0.0-rc1](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc1).
The maintained `v4` branch includes later work. These identities answer
different questions:

| Identity | What it identifies |
| --- | --- |
| `v4.0.0-rc1` tag → `362c4c4a74456002e4697ca0e1d1bb3aaff1539d` | Original reviewed source/tooling revision |
| `v4/manifest.json` and its SHA256 | Mesa archive, patch order, resulting source hashes and provider checkpoint |
| `release.json` and `build-provenance.json` inside a binary archive | Exact packaged driver/files, build inputs, toolchain and library requirements |
| Native driver SHA256 `6bc07c5a9d8404aba98dbdd912ffb58988fd760f50460d3b614d85eb8a7638d5` | Exact published rc1 ELF qualified for real gameplay |
| `f7d59b030af519059b06ce3953c2c91e5bd6e1c5` | Later performance evidence for that same ELF |
| Current `v4` commit | Maintained tooling and documentation; not a new binary qualification |
| FSR `4.1.1` INT8 and the pinned provider SHA256 | Qualified game runtime; the newer `4.1.1b` mod is outside this release |

**Do not co-install the newer FSR 4.1.1b mod with this release's game setup.**
This fork uses the pinned 4.1.1 provider and does not incorporate or qualify
4.1.1b. Follow the [runtime compatibility and switching guidance](games.md#runtime-compatibility)
before changing integrations. The Mesa fork's `4.0.0-rc1` version does not
identify an AMD FSR version.

The [qualification report](qualification.md) records scope and limits. The
[performance addendum](performance.md) supplies later measurements without
changing the original tag or binary/source release assets. The manifest's
release version alone does not prove that a locally rebuilt ELF is the
published binary.

## Choose the right archive

The original prerelease contains separate binary, source and performance
assets. The installable CachyOS archive is
`bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz`, with an adjacent
`.tar.gz.sha256` file. Verify the checksum before using it:

```sh
sha256sum -c bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz.sha256
```

A checksum downloaded beside an archive detects corruption; it is not an
independent publisher signature. Use the intended fork's release page and
retain the downloaded archive and checksum when testing or reporting a bug.
Source-only and performance archives cannot be installed as drivers.

The current checkout's `install-v4.sh` uses its adjacent `scripts/driver.py`.
When downloaded alone, the bootstrap uses the installer bundled in the chosen
binary archive. Installing the original binary from a maintained checkout
therefore uses current tooling while keeping the driver identity unchanged.
Installing standalone preserves the original bundled tool revision. See the
[installation guide](../README.md#private-archive-install-or-v3-upgrade).

## Export maintained source

Commit and review the changes, then run from a clean Git checkout:

```sh
python3 scripts/source-release.py --output dist/source
```

The exporter creates a deterministic archive named
`bc250-fsr4-vVERSION-source-SHORTCOMMIT.tar.gz`, an adjacent SHA256 checksum,
and a `source-snapshot.json` inside the archive. That manifest records the
exact commit, tracked files, hashes and modes. The export contains tracked
documentation, tests, CI, source inputs and archived upstream material.
Ignored local outputs such as `.work/`, `dist/` and `.venv/` are excluded.
Archive timestamps, modes, order and gzip settings are fixed; byte-for-byte
repeatability is tested with the same Python/zlib toolchain. The extracted
snapshot's file hashes and executable modes are checked by `check-repo.py`.

For a specific recorded revision:

```sh
python3 scripts/source-release.py --ref v4.0.0-rc1 --output dist/source
```

An explicit ref reads only that revision's Git objects, even if the current
working tree contains edits. The resulting snapshot is a new export of the
recorded source; it does not replace or claim byte identity with the original
attached rc1 source archive. The exporter needs a Git checkout, since an
extracted archive has no Git object database.

## Prepare a future binary release

Run the [development checks and qualification steps](development.md), build
the native or container archive, and qualify its exact stripped ELF. Record
the source commit, source manifest hash, full toolchain/ABI requirements,
driver hash and observed runtime scope. A successful build or tooling test
suite does not transfer the old binary's gameplay acceptance to a new one.

Review [provenance and licenses](../THIRD_PARTY.md) before distribution. Keep
the complete source/tooling records, dependency notices and file manifest
with the archive. Preserve the original base packages needed for system
rollback on each target distribution; those packages are specific to the
installation and are not a universal release artifact.

Give any future binary release its own reviewed identity and qualification.
Keep the rc1 tag and original attached assets immutable. Publish later reports
or source snapshots with distinct names and explicit scope, and describe the
change in the [changelog](../CHANGELOG.md).
