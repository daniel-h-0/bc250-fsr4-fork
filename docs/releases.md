# Downloads and release packaging

**Install the [RC11 DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc11/bc250-fsr4-dll-4.0.0-rc11.zip)
using the [OptiScaler replacement guide](beginner-guide.md).**
[Release notes](release-notes-rc11.md) explain the changes;
[validation](portable-dll-rc11.md) records what was tested.

## Four RC11 uploads

| Download | Who needs it |
| --- | --- |
| `bc250-fsr4-dll-4.0.0-rc11.zip` | Normal install: DLL, instructions, notices and optional cache helper. |
| `bc250-fsr4-v4.0.0-rc11-linux-glibc236-x86_64.tar.gz` | Users choosing the alternative AMD-provider/driver route. |
| `bc250-fsr4-v4.0.0-rc11-source-COMMIT.tar.gz` | Developers: complete source, tools, docs and evidence. |
| `SHA256SUMS` | Checksums for the three archives above. |

GitHub's automatic **Source code** downloads are not the DLL ZIP. The archive
checksums differ from the DLL checksum inside the ZIP. To check a downloaded
archive beside the release's `SHA256SUMS`:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

Original archives/tags remain unchanged when the maintained docs are revised.

## Packaging

Maintainers use a reviewed clean checkout and qualified build outputs:

```sh
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
python3 scripts/package.py --work .work/linux-glibc236/mesa \
  --label linux-glibc236-x86_64 --runtime-only
python3 scripts/source-release.py --output dist/source
python3 scripts/release-assets.py --dll PATH_TO_DLL_ZIP --driver PATH_TO_DRIVER_TAR \
  --source PATH_TO_COMPLETE_SOURCE_TAR --output dist/upload-rc11
```

The DLL packager checks the exact DLL, guide identity, notices and helper hashes.
The driver packager checks build provenance and strips a copy of the library.
The staging tool checks archive sidecars and writes a combined checksum list;
it does not publish. A DLL-only staging run can omit the driver argument.

For corrected archive instructions without a binary change, pass
`--documentation-revision N` to the DLL packager. It produces a new `-docsN`
filename and still requires the exact manifest-pinned DLL. Retain old assets
and publish matching checksums/source; do not replace downloads in place.

## Source snapshots

The source export above includes tracked files plus `source-snapshot.json`
(commit, hashes and modes); ignored outputs and personal state are excluded.
The extracted snapshot can run `python3 scripts/check-repo.py` without Git.
Use `--ref TAG` to export that revision's Git objects. A fresh export of an old
tag does not replace the original release asset.

## Publishing changes

Build from an immutable reviewed commit. Verify the final archives, publish
checksums and complete source, and record actual validation and limits. New
DLL/driver bytes need their own identity and qualification. Tooling/docs-only
changes do not constitute new game tests. Preserve prior downloads for recovery.
See [contributor checks](../CONTRIBUTING.md) and
[component acceptance](development.md#checks-and-acceptance).

<details>
<summary>Older downloads and documentation corrections</summary>

## Historical RC9 documentation refreshes

### Documentation refresh 2

September 12 `-docs2` archives correct watermark removal: use
`Fsr4EnableWatermark=auto` and remove `MLSR-WATERMARK`. The DLL is unchanged.

### Documentation refresh 1

September 11 `-docs1` archives correct a README footer that listed RC8's DLL
size/hash. All RC9 variants contain the same 111,815,680-byte DLL, SHA256
`eefcac03ab17b04a29a5bb16e3f3e9c3181ba9ea46b05a61cb49a5003e1516ef`.
Use the checksum matching the exact archive filename. Original and refreshed
assets retain their own source snapshots and checksums.

## Retained RC6 distribution

The old Steam compatibility tool remains at RC6 in `runtime/manifest.json`,
with driver-source pin `v4/legacy/rc1-manifest.json`. Current driver builds use
`v4/manifest.json`; the RC11 driver binary equals RC10. These are separate
components, not competing version labels for the same DLL.

### End-user setup bundle

The old `bc250-fsr4-setup-4.0.0-rc6.tar.gz` contains tools that fetch pinned
runtime components. Use its [retained install/recovery guide](legacy-rc6.md)
only for that tool. Offline options are in [legacy troubleshooting](game-troubleshooting.md).
Maintainers can export it with `python3 scripts/source-release.py --setup --output dist/setup`.

### Driver artifacts

The RC4–RC6 tool's portable driver and the original CachyOS rc1 driver have
different ELF identities despite their shared source version. Their
[ABI record](rc4-compatibility.md) and [original qualification](qualification.md)
retain those hashes. Do not infer binary identity or game support from a filename.

</details>
