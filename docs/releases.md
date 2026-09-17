# Downloads and release packaging

**[Install across your games](optiscaler-client.md)** with the BC250 client build,
or use the [RC11 DLL ZIP with refreshed instructions](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/opticlient-v1.0.7-bc250.2/bc250-fsr4-dll-4.0.0-rc11-docs2.zip)
for a [manual installation](beginner-guide.md).
[RC11 release notes](release-notes-rc11.md) and
[DLL validation](portable-dll-rc11.md) cover the unchanged renderer.

## Client addon

[OptiScaler Client 1.0.7-bc250.2](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/opticlient-v1.0.7-bc250.2)
is a separately versioned prerelease addon for RC11. It adds selection-based
installation, DLL updates and file restoration using one general setup across
OptiScaler-compatible games. RC11 remains the DLL release;
this addon changes installation rather than shader code or measured performance.

The application is a modified build of [OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client)
by Agustín Montaña (Agustinm28) and contributors. BC250 maintains the FSR4-specific
integration and this distribution. [Upstream roles, credits and licenses](../THIRD_PARTY.md#optiscaler-client-addon).

The addon release contains the Linux client, the `rc11-docs2` DLL ZIP, a complete
project source snapshot and `SHA256SUMS`. The application also includes its full
modified client source. The [client validation record](../integrations/optiscaler-client/README.md#validation)
separates installation/loading checks from the existing DLL rendering evidence.

## Downloads

| Download | Who needs it |
| --- | --- |
| `bc250-opticlient-1.0.7-bc250.2-linux-x64.tar.gz` | Client route: Linux application, RC11 ZIP, complete modified client source and dependency download pins. |
| `bc250-fsr4-dll-4.0.0-rc11-docs2.zip` | Manual installation or client import; unchanged RC11 DLL with current instructions and notices. |
| `bc250-fsr4-v4.0.0-rc11-linux-glibc236-x86_64.tar.gz` | Alternative AMD-provider/driver route, retained in the [RC11 release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11). |
| `bc250-fsr4-v4.0.0-rc11-source-COMMIT.tar.gz` | Developers: complete source, tools, docs and evidence. |
| `SHA256SUMS` | Checksums for the corresponding downloads. |

GitHub's automatic **Source code** downloads are not the DLL ZIP. The archive
checksums differ from the DLL checksum inside the ZIP. To check a downloaded
archive beside the release's `SHA256SUMS`:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

Original archives/tags remain unchanged when the maintained docs are revised.
The client has its own version and archive checksum; RC11's DLL is unchanged.
This client build bundles the `4.0.0-rc11-docs2` DLL ZIP, whose instructions
include the client route. It contains the same RC11 DLL as the original download.

## Packaging

Maintainers use a reviewed clean checkout and qualified build outputs:

```sh
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll \
  --documentation-revision 2
python3 scripts/source-release.py --output dist/source
```

Build the client separately with the .NET 10 SDK and `bsdtar`:

```sh
python3 scripts/package-opticlient.py --dotnet /path/to/dotnet \
  --dll-zip dist/bc250-fsr4-dll-4.0.0-rc11-docs2.zip
python3 scripts/release-assets.py --dll PATH_TO_DLL_ZIP --client PATH_TO_CLIENT_TAR \
  --source PATH_TO_COMPLETE_SOURCE_TAR --output dist/upload-opticlient
```

The [client build guide](../integrations/optiscaler-client/README.md) covers its
tests, source delivery and first-run upstream downloads. Include the client
archive and its checksum when publishing this route. The client archive embeds
the DLL ZIP supplied to the command; document which documentation revision it contains.

The DLL packager checks the exact DLL, guide identity, notices and helper hashes.
The driver packager checks build provenance and strips a copy of the library.
The staging tool checks archive sidecars and writes a combined checksum list;
it does not publish. Omit the driver or client argument when staging only the
other components.

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
runtime components. Use its [retained install/recovery guide](legacy/runtime/legacy-rc6.md)
only for that tool. Offline options are in [legacy troubleshooting](legacy/runtime/game-troubleshooting.md).
Maintainers can export it with `python3 scripts/source-release.py --setup --output dist/setup`.

### Driver artifacts

The RC4–RC6 tool's portable driver and the original CachyOS rc1 driver have
different ELF identities despite their shared source version. Their
[ABI record](legacy/runtime/rc4-compatibility.md) and [original qualification](legacy/runtime/qualification.md)
retain those hashes. Do not infer binary identity or game support from a filename.

</details>
