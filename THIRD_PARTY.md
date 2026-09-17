# Provenance and licenses

This fork preserves [dmorazasanchez/bc250-fsr4](https://github.com/dmorazasanchez/bc250-fsr4)
history at v3 baseline `6173651fa3a5a557cba2c2ff802e2d6f49881bc1`.
Credit for the original BC250 FSR4 compatibility work belongs to that project
and its contributors.
The README's [special thanks](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/README.md#special-thanks-and-notes) acknowledges the major
driver, compatibility-runtime and upscaler projects used by this distribution.

The imported repository had no top-level license grant at that revision.
This fork does not relicense inherited files. New Python/shell tools explicitly
marked SPDX MIT use [LICENSE.new-code](LICENSE.new-code); that limited grant
does not label the entire repository MIT.

## Portable DLL distribution (RC7 onward)

The portable DLL archive redistributes the modified AMD FidelityFX SDK 2.3.0 upscaler
DLL and its compiled shader/model material from commit
`60f4ea81909200d8542eca14dccb2628b763a9a3`. The complete
[AMD notice](dll/notices/AMD-SDK-LICENSE.md), including the explicit exception
listing this DLL, is retained. The binary is modified and no longer AMD-signed.

The [DLL provenance notice](dll/notices/PROVENANCE.md) and
[build source](dll/README.md) record the v4/Mesa lineage, Microsoft DXC inputs
and limited MIT grant for new tools. DXC is a build dependency. No custom Mesa,
Proton, OptiScaler, NVIDIA helper or driver-provider binary is bundled in the
portable DLL archive. Users obtain an optional OptiScaler adapter separately under
its upstream terms. The runtime-distribution section below describes retained RC6.

## OptiScaler Client addon

The desktop application is based on **[OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client)**,
Copyright (C) 2026 **Agustín Montaña (Agustinm28)**, with contributions from its
upstream community. Client 1.0.7 is pinned at
[`dd534b7d1cb8a0edf174a6917f5179791603d364`](https://github.com/Optiscaler-Client/Optiscaler-Client/tree/dd534b7d1cb8a0edf174a6917f5179791603d364).
Its desktop interface, game discovery and component services are upstream work.

BC250 maintains the added FSR4 installation/update/restore screen, FFX/INT8
settings, dependency pins and packaging. The client and BC250 C# additions use
**GPL-3.0-or-later**; [upstream's attribution](https://github.com/Optiscaler-Client/Optiscaler-Client/blob/dd534b7d1cb8a0edf174a6917f5179791603d364/README.md#-license--acknowledgments)
and [full license](https://github.com/Optiscaler-Client/Optiscaler-Client/blob/dd534b7d1cb8a0edf174a6917f5179791603d364/LICENSE)
are retained in the complete modified source. The binary archive includes that
source as `source.tar.gz`, the GPL text under `notices/`, and the
[integration notice](integrations/optiscaler-client/NOTICE.md).

OptiScaler Client is an independent manager project. This modified distribution
is maintained and supported by BC250 FSR4; it is not an official release of
either upstream project.

| Upstream software | Credit and role in this route | License / provenance |
| --- | --- | --- |
| [OptiScaler](https://github.com/optiscaler/OptiScaler) | OptiScaler team and contributors; the in-game adapter that routes supported upscaler inputs to the selected backend. Its own credits acknowledge PotatoOfDoom's CyberFSR2 foundation. | [Upstream license](https://github.com/optiscaler/OptiScaler/blob/master/LICENSE); pinned nightly and hash in the [client manifest](integrations/optiscaler-client/manifest.json). |
| [OptiPatcher](https://github.com/optiscaler/OptiPatcher) | OptiScaler team and contributors; ASI plugin that exposes supported games' DLSS inputs without vendor spoofing. | [MIT notice for v0.41](https://github.com/optiscaler/OptiPatcher/blob/v0.41/LICENSE), Copyright (c) 2025 OptiScaler. |
| [AMD FidelityFX SDK](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK) | AMD and SDK contributors; the base FSR4 DLL, shaders and model material that this project's optimizations modify. | [Complete AMD notice](dll/notices/AMD-SDK-LICENSE.md) and [DLL provenance](dll/notices/PROVENANCE.md). |
| [NVIDIA DLSS](https://github.com/NVIDIA/DLSS) | NVIDIA; the signed helper used for input compatibility. The selected output remains the BC250 FSR4 DLL. | [Pinned NVIDIA terms](https://github.com/NVIDIA/DLSS/blob/a291cc7d2cc642a51566f3dfd5376f635cd1b284/LICENSE.txt), downloaded alongside the helper. |
| [Avalonia](https://github.com/AvaloniaUI/Avalonia) and [.NET](https://github.com/dotnet/runtime) | Their authors and contributors; the client's desktop UI framework and managed runtime. | Dependency versions in the client source and [lock file](integrations/optiscaler-client/packages.lock.json); license texts and package metadata under `notices/nuget/`, plus .NET notices in the application archive. |

The downloaded OptiScaler package also carries work from the
[fakenvapi contributors](https://github.com/optiscaler/fakenvapi),
[Nukem's dlssg-to-fsr3](https://github.com/Nukem9/dlssg-to-fsr3),
[Intel XeSS](https://github.com/intel/xess) and
[Microsoft DirectX](https://devblogs.microsoft.com/directx/directx12agility/), under their
respective terms. Their presence in that package is separate from the FFX/INT8
configuration selected by this guide; frame generation is disabled in that setup.

First-run OptiScaler, OptiPatcher and NVIDIA helper downloads come from pinned
upstream locations; those binaries are not included in the client archive.
Their downloaded notices and upstream terms remain applicable. The FSR4 ZIP
inside the archive retains its own AMD and build-tool notices.

## Source inventory

| Material | Provenance and notices |
| --- | --- |
| Portable upscaler DLL and editable shader assembly | `dll/manifest.json`, full AMD SDK notice and `dll/notices/PROVENANCE.md` |
| New SPDX-marked tools | MIT under `LICENSE.new-code` |
| Inherited BC250 material | Original Git history and `legacy/`; retain existing notices |
| Mesa 26.2.2 and patches | Inputs in `v4/manifest.json`; retain original per-file licenses and copyright notices |
| Wayland protocols 1.41 | Pinned archive under `v4/source-dependencies/`; retain its internal `COPYING` |
| SteamOS ABI target | Valve package hashes and source URLs in `v4/build-targets/steamos-3.8.json`; packages are extracted privately for building, not redistributed as an OS |
| Portable ABI target | Debian package hashes and official URLs in `v4/build-targets/linux-glibc236.json`; packages are extracted privately for building, not redistributed as an OS |
| Static libdrm 2.4.133 in portable/SteamOS builds | Upstream source hash in the target definition; the complete upstream source archive and its notices are retained under `licenses/` in driver archives |
| Runtime component identities | `runtime/manifest.json` records upstream URLs, hashes and versions; it grants no rights to the artifacts |
| GE-Proton upscaler source fixture | `tests/fixtures/ge-proton11-6-upscalers.py`, from the pinned GE-Proton11-6 tree; upstream BSD-2-Clause, Copyright (c) 2018 Chris Simons |
| Archived game profiles and recovery | Former profile metadata and transaction recovery under `legacy/game-setup/` |

The upstream fixture derives from umu-protonfixes commit
`d13333be729b3018f9f9bc6790944901319b425b` with GE's downloader changes.
Its [upstream license](https://github.com/Open-Wine-Components/umu-protonfixes/blob/d13333be729b3018f9f9bc6790944901319b425b/LICENSE)
and accompanying fixture notices apply. Changes to that upstream source retain
its attribution and license.

New driver archives include Mesa's license summary and full license-text
directory under `licenses/`. The original rc1 archive contained the summary;
later packaging does not replace its published contents. Source exports retain
tracked provenance and notices.

## RC10 driver shader payload

The RC10 driver additionally contains exact original/optimized SPIR-V pairs
from the AMD SDK/RC9 shader implementation. Its compact download includes the
complete shader notices under `notices/`, Mesa/libdrm notices under `licenses/`,
and [driver provenance](docs/driver-notices.md). The full source/evidence archive
retains the complete source overlay and upstream input hashes.

## Runtime distribution

The public `bc250-fsr4-setup-VERSION.tar.gz` bundle contains installer code,
the integration patch, component manifest and essential documentation/notices.
It does not redistribute GE-Proton, OptiScaler, OptiPatcher, NVIDIA DLSS or AMD provider
binaries. Installation obtains the pinned components separately and retains
the licenses supplied with the upstream archives.

An optional complete offline bundle is assembled locally for private use.
Keep its component provenance and notices with it; the public source bundle
does not grant new redistribution rights to those components.

Game files, proprietary shader dumps, saves, keys and Steam account
configuration are excluded from distributions.

The runtime uses AMD FidelityFX SDK 4.0.2 as the API bridge to the pinned
4.1.1 driver provider. The unmodified DLL comes from AMD SDK v2.0.0 commit
`f4c1da8e92f3fe563b5c28c44e6267ce6b6b8eb2`; its URL and hash are in
`runtime/manifest.json`. The [AMD MIT license](runtime/licenses/FidelityFX-SDK-4.0.2.txt)
is retained in the setup bundle and installed alongside the runtime.

The runtime also obtains an unmodified, NVIDIA-signed DLSS 310.7.0 library
from NVIDIA's DLSS repository at commit
`a291cc7d2cc642a51566f3dfd5376f635cd1b284`. OptiScaler uses it beside the
prefix proxy to satisfy older NGX input file/signature checks; FSR 4.1.1 INT8
remains the selected rendering backend. Its pinned URL and hash are in
`runtime/manifest.json`. The [NVIDIA RTX SDKs license](runtime/licenses/NVIDIA-DLSS.txt)
is retained with the assembled runtime; this project's MIT grant does not
apply to that component.

If libarchive’s `bsdtar` is absent, installation downloads the official static
[7-Zip 26.03 Linux extractor](https://github.com/ip7z/7zip/releases/tag/26.03).
Its archive and executable hashes are pinned in `runtime/manifest.json`.
The complete upstream archive, including `License.txt` and manual notices,
remains in the download cache. The executable is used in temporary staging;
it is not redistributed in the public setup bundle or installed system-wide.
