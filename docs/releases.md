# Downloads and release packaging

[RC11](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11)
is the current release. For installation, choose the **DLL ZIP** and follow
the [DLL installation guide](beginner-guide.md); the driver archive and source are
optional alternatives/development downloads. Read the [release notes](release-notes-rc11.md)
and [validation scope](portable-dll-rc11.md). RC10 and RC9 assets remain available.

## Four RC11 uploads

| Asset | Contents |
| --- | --- |
| `bc250-fsr4-dll-4.0.0-rc11.zip` | Recommended DLL and instructions; notices and optional cache helper |
| `bc250-fsr4-v4.0.0-rc11-linux-glibc236-x86_64.tar.gz` | Alternative Linux driver route, tools and notices; unnecessary for DLL replacement |
| `bc250-fsr4-v4.0.0-rc11-source-COMMIT.tar.gz` | Complete editable sources, build tools, detailed docs, charts and evidence |
| `SHA256SUMS` | Hashes for those three uploaded archives |

GitHub also provides its two automatic source downloads. Detailed documentation
and charts remain linked and included in the complete source archive; they do
not need separate release-page uploads. RC9 assets remain available under RC9.

## Packaging

From a reviewed clean source tree:

```sh
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
python3 scripts/package.py --work .work/linux-glibc236/mesa \
  --label linux-glibc236-x86_64 --runtime-only
python3 scripts/source-release.py --output dist/source
python3 scripts/release-assets.py --dll PATH_TO_DLL_ZIP --driver PATH_TO_DRIVER_TAR \
  --source PATH_TO_COMPLETE_SOURCE_TAR --output dist/upload-rc11
```

The DLL packager verifies the DLL, guide identity, notices and optional helper
hashes. The driver packager verifies complete build/source provenance and strips
only the copied library. The staging tool checks the original archive sidecars
and emits one combined list; it never publishes or replaces existing files.
Qualify the final archive's driver and installation/rollback before publication.

`runtime/manifest.json` remains at RC6, with its exact historical driver-source
manifest in `v4/legacy/rc1-manifest.json`. The old setup/install bootstraps retain
those recovery pins. The current `v4/manifest.json` and ordinary source build
produce the RC11 driver package. Its rebuilt ELF is byte-identical to RC10;
RC11 versions the distribution and tools. Both release identities and their
separate qualification records remain available.

<details>
<summary>Historical RC9 documentation refreshes and RC6 distribution</summary>

## Historical RC9 documentation refreshes

### Documentation refresh 2

The September 12 `-docs2` packages correct watermark removal: close the game,
set `Fsr4EnableWatermark=auto`, remove any `MLSR-WATERMARK` launch variable,
and restart. The pinned OptiScaler build sets that variable to `0` for `false`,
which still enables the SDK banner. The DLL, release tag and shader sources
are unchanged. Use the current packages for the corrected instructions;
the original and `-docs1` assets remain available with their own checksums.

### Documentation refresh 1

The original September 11 RC9 ZIP and tar.xz contain the correct DLL and
internal `SHA256SUMS`, but their README footer mistakenly lists RC8's size and
checksum. The maintained guide was corrected first; `-docs1` downloads now
carry the corrected guide, beginner links and first-use compilation guidance.

Both original and refreshed packages contain the same **111,815,680-byte DLL**:
`eefcac03ab17b04a29a5bb16e3f3e9c3181ba9ea46b05a61cb49a5003e1516ef`.
The archive checksums change because the documentation changes. Use the checksum
whose filename matches the archive you downloaded. The refresh has a matching
source snapshot of its own documentation/tooling commit; it does not move the
RC9 tag, overwrite original assets or imply new gameplay qualification.

For a documentation-only refresh, pass `--documentation-revision N` with a
positive integer. This gives new `-docsN` asset names while still requiring the
exact manifest-pinned DLL. Ordinary packaging without the option keeps the
original filenames. Existing files are always refused.

## Retained RC6 distribution

The retained RC6 distribution has one install/update/status/rollback
interface. Driver and Steam runtime identities are recorded internally;
changing the installer does not change the qualified driver or extend its
old gameplay evidence.

| Artifact | Identity and purpose |
| --- | --- |
| Driver `v4.0.0-rc1` | The published Mesa 26.2.2 ELF and its [qualification](qualification.md); source tag `362c4c4a74456002e4697ca0e1d1bb3aaff1539d` |
| Portable driver `4.0.0-rc1-linux-glibc236-x86_64` | RC4’s separate ELF build from the unchanged source; [ABI and correctness qualification](rc4-compatibility.md) |
| Distribution `4.0.0-rc6` | The component/preset lock in `runtime/manifest.json`; [Steam save-folder registration](save-paths-rc6.md), installer/recovery corrections and portable diagnostics, retaining RC3’s [renderer qualification](runtime-qualification.md) |
| Source snapshot | An exact Git commit with file hashes and modes, for development or auditing |

The later [performance campaign](performance.md) used the unchanged rc1 driver
through the earlier integration. A source version label does not prove that
a rebuilt ELF is the same binary. FSR **4.1.1 INT8** identifies the provider;
it is distinct from both project versions and the newer 4.1.1b mod.

### End-user setup bundle

The distribution release tag is `v4.0.0-rc6`. Its small
`bc250-fsr4-setup-4.0.0-rc6.tar.gz` bundle contains the installer tools,
runtime manifest, integration patch and essential documentation/notices,
with an adjacent SHA256 checksum. Users extract it and follow the
[quickstart](legacy-rc6.md#start-a-steam-game); Git is optional.
Existing rc1 users should first read the [transition guide](upgrading-rc1.md).
The original rc1 archive installs the driver component; it cannot update itself
into the unified tools. Obtain the rc6 setup archive in a separate directory.

Normal `./bc250-fsr4 install` downloads the pinned GE-Proton,
OptiScaler, OptiPatcher, AMD SDK bridge, signed NVIDIA DLSS helper and FSR provider components, verifies them and assembles
the tool locally. It uses the original GE loader and prefix manager with the
recorded narrow patch. No Wine compilation is required.

The public setup bundle does not redistribute those upstream runtime binaries.
Users can retain downloaded components for offline reinstallation with
`--cache PATH --offline`. The shared assembly/packaging tool can also create
a complete private offline bundle, accepted by `./bc250-fsr4 install --runtime-archive PATH`.
Preserve its checksum and upstream notices; that private artifact is separate
from the public setup distribution.

Obtain archives and checksums from the intended
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases).
An adjacent checksum detects corruption; it is not an independent signature.

Build the small distribution from a clean reviewed checkout:

```sh
python3 scripts/source-release.py --setup --output dist/setup
```

### Driver artifacts

The default RC4–RC6 private artifact is
`bc250-fsr4-v4.0.0-rc1-linux-glibc236-x86_64.tar.gz`, attached to the RC4
release. Its source version is unchanged, but its ABI/build identity and ELF
hash are distinct from the original. The current setup pins the default archive
and verifies its driver/source identity before activation.

The original installable driver is
`bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz`, with an adjacent checksum.
Source and performance archives are not drivers. Its exact ELF hash and
environment are in [qualification](qualification.md#exact-driver-and-source).

A current installer bundle can install that unchanged driver with maintained
tooling. The original rc1 archive retains its older bundled tools. Keep both
identities explicit; never replace the rc1 assets or move its tag.

For a new driver, build with `scripts/build.py` and package with
`scripts/package.py`. Packaging records source/provenance, dependencies,
symbol requirements and file hashes, and strips a copy of the built library.
Qualify the exact distributed ELF before publishing a new driver release.
Review [notices](../THIRD_PARTY.md) and the [acceptance requirements](development.md#checks-and-acceptance).

</details>

## Source snapshots

From a clean reviewed checkout:

```sh
python3 scripts/source-release.py --output dist/source
```

The deterministic `bc250-fsr4-vVERSION-source-SHORTCOMMIT.tar.gz` export
contains tracked source, tests, documentation and historical material, plus
`source-snapshot.json` with the exact commit, file hashes and modes. Ignored
build outputs and personal state are excluded. The extracted snapshot can
run `python3 scripts/check-repo.py` without Git metadata.

Use `--ref TAG` to export a recorded revision. This reads that revision's
Git objects even if the working tree has unrelated edits; the exporter itself
needs a Git checkout. A new export of an old tag is not a replacement for its
original attached source archive.

## Publishing changes

For a portable DLL release, build from the reviewed immutable source commit,
verify the DLL and archive checksums, and attach the DLL archive(s), matching
checksums and complete source export. Record the actual GPU/API outcomes and
remaining qualification limits. Keep the existing RC6 recovery assets available.
A DLL-only release does not require a new driver or setup bundle.

For retained driver/runtime releases, give component, patch or preset changes
a new distribution version.
Validate installation and the affected renderer/input routes, publish a matching setup
bundle/checksum and record the scope in the changelog. Reuse an unchanged
qualified driver by its exact hash.

Give a new driver ELF its own build identity and qualification. Keep prior
runtime versions, source records and distribution-specific base packages
available for recovery. Documentation or tooling changes alone must not be
presented as new driver or runtime gameplay acceptance.
