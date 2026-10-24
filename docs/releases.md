# Downloads and release packaging

**[Install across your games](optiscaler-client.md)** with the BC250 client build
(RC12 included), or use the
[RC12 DLL ZIP](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/v4.0.0-rc12/bc250-fsr4-dll-4.0.0-rc12.zip)
for a [manual installation](beginner-guide.md).
[RC12 release notes](release-notes-rc12.md) and
[DLL validation](portable-dll-rc12.md) cover the current DLL.
The [RC11.1 driver pre-release](release-notes-rc11.1.md) changes only the optional
Linux driver: Mesa 26.2.2 + the upstream `nir_opt_uub` fix. RC12 does not change it.
DLL users: system Mesa 26.2.x before 26.2.3 has that upstream compiler bug, which
can break other shaders such as HelixSR frame generation; update to 26.2.3 or newer
([details](release-notes-rc11.1.md#affected-downloads-and-system-mesa)).

## Client addon

[OptiScaler Client 1.0.7-bc250.6](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/opticlient-v1.0.7-bc250.6)
is a separately versioned prerelease addon. It bundles RC12 and imports later DLL
ZIPs. It adds selection-based
installation, DLL updates, file restoration and optional shared-cache setup using
one general setup across
OptiScaler-compatible games.
This addon changes installation rather than shader code or measured performance.
Version bc250.4 fixes first setup and installation on Bazzite and other Fedora
Atomic systems, where `/home` is a link to `/var/home`. Version bc250.5 treats
both spellings of a game folder as one game. Version bc250.6 bundles the RC12
ZIP in place of RC11's; the application is otherwise bc250.5's.

The application is a modified build of [OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client)
by Agustín Montaña (Agustinm28) and contributors. BC250 maintains the FSR4-specific
integration and this distribution. [Upstream roles, credits and licenses](../THIRD_PARTY.md#optiscaler-client-addon).

The addon release contains the Linux client with its cache tools, the unchanged
RC12 DLL ZIP, a complete
project source snapshot and `SHA256SUMS`. The application also includes its full
modified client source. The [client validation record](../integrations/optiscaler-client/README.md#validation)
separates installation/loading checks from the existing DLL rendering evidence.

## Downloads

| Download | Who needs it |
| --- | --- |
| `bc250-opticlient-1.0.7-bc250.6-linux-x64.tar.gz` | Client route: Linux application, RC12 ZIP, complete modified client source and dependency download pins. |
| `bc250-fsr4-dll-4.0.0-rc12.zip` | Manual installation or client import: the RC12 DLL, instructions, notices and provenance, in the [RC12 release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc12). SHA256 `646778a0d06c42bf3cdcc99f9396680f5cc4a33658b90c0889fd15751bfb800d`. |
| `bc250-fsr4-dll-4.0.0-rc11-docs2.zip` | Previous DLL: RC11 with refreshed instructions; bundled in client bc250.2–bc250.5. |
| `bc250-fsr4-v4.0.0-rc11.1-linux-glibc236-x86_64.tar.gz` | Alternative AMD-provider/driver route: RC11's driver plus the upstream `nir_opt_uub` fix, in the [RC11.1 pre-release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11.1). SHA256 `6f89cf227cd14afc49e66304ee872cff68a9378aec2147c1ab644b2cd5193a5d`. |
| `bc250-fsr4-v4.0.0-rc11-linux-glibc236-x86_64.tar.gz` | Previous driver, retained in the [RC11 release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11); lacks the compiler fix. |
| `bc250-fsr4-v4.0.0-rc12-source-COMMIT.tar.gz` | Developers: complete source, tools, docs and evidence. |
| `SHA256SUMS` | Checksums for the corresponding downloads. |

GitHub's automatic **Source code** downloads are not the DLL ZIP. The archive
checksums differ from the DLL checksum inside the ZIP. To check a downloaded
archive beside the release's `SHA256SUMS`:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

Original archives/tags remain unchanged when the maintained docs are revised.
The client has its own version and archive checksum.
This client build bundles the published `4.0.0-rc12` DLL ZIP unchanged: the same
file and SHA256 as the RC12 release asset. The client checks an imported ZIP's DLL
against the ZIP's `SHA256SUMS`, so later DLL ZIPs need no client update. A client
update keeps the DLL already selected; import the bundled ZIP to switch to it.

## Packaging

Maintainers use a reviewed clean checkout and qualified build outputs:

```sh
python3 scripts/package-dll.py --dll .work/dll/amd_fidelityfx_upscaler_dx12.dll
python3 scripts/source-release.py --output dist/source
python3 scripts/release-assets.py --dll PATH_TO_DLL_ZIP \
  --source PATH_TO_COMPLETE_SOURCE_TAR --output dist/upload
```

The packagers take the release identity from `dll/rc12.json`
(`release_version`), falling back to `dll/manifest.json` for RC11 and earlier.
Build the client separately with the .NET 10 SDK and `bsdtar`. Its packager
requires the ZIP that `integrations/optiscaler-client/manifest.json` pins
(`dll_zip`: the published RC12 ZIP, by SHA256):

```sh
python3 scripts/package-opticlient.py --dotnet /path/to/dotnet \
  --dll-zip PATH_TO/bc250-fsr4-dll-4.0.0-rc12.zip
python3 scripts/release-assets.py --dll PATH_TO_DLL_ZIP --client PATH_TO_CLIENT_TAR \
  --source PATH_TO_COMPLETE_SOURCE_TAR --output dist/upload-opticlient
```

The [client build guide](../integrations/optiscaler-client/README.md) covers its
tests, source delivery and first-run upstream downloads. Include the client
archive and its checksum when publishing this route. The client archive embeds
the DLL ZIP supplied to the command; document which documentation revision it contains.

The DLL packager checks the exact release DLL, guide identity, notices and helper hashes.
The driver packager checks build provenance and strips a copy of the library.
The staging tool checks archive sidecars and writes a combined checksum list;
it does not publish. Omit the driver or client argument when staging only the
other components.

For corrected archive instructions without a binary change, pass
`--documentation-revision N` to the DLL packager. It produces a new `-docsN`
filename and still requires the exact pinned release DLL. Retain old assets
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
`v4/manifest.json` (RC11.1; RC11's recipe is `v4/legacy/rc11-manifest.json`). The
RC11 driver binary equals RC10. These are separate
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

These rc1 drivers (assets of the rc1 and rc4 releases) and the RC10/RC11 driver are
frozen and lack the upstream `nir_opt_uub` fix: they can miscompile shaders outside
FSR4, e.g. HelixSR frame generation. The retained RC6 tool keeps its rc1 pin; use
the [RC11.1 driver](release-notes-rc11.1.md) for the current route.

</details>
