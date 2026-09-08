# Provenance and licenses

This fork preserves [dmorazasanchez/bc250-fsr4](https://github.com/dmorazasanchez/bc250-fsr4)
history at v3 baseline `6173651fa3a5a557cba2c2ff802e2d6f49881bc1`.
Credit for the original BC250 FSR4 compatibility work belongs to that project
and its contributors.
The README's [special thanks](README.md#special-thanks) acknowledges the major
driver, compatibility-runtime and upscaler projects used by this distribution.

The imported repository had no top-level license grant at that revision.
This fork does not relicense inherited files. New Python/shell tools explicitly
marked SPDX MIT use [LICENSE.new-code](LICENSE.new-code); that limited grant
does not label the entire repository MIT.

## Source inventory

| Material | Provenance and notices |
| --- | --- |
| New SPDX-marked tools | MIT under `LICENSE.new-code` |
| Inherited BC250 material | Original Git history and `legacy/`; retain existing notices |
| Mesa 26.2.2 and patches | Inputs in `v4/manifest.json`; retain original per-file licenses and copyright notices |
| Wayland protocols 1.41 | Pinned archive under `v4/source-dependencies/`; retain its internal `COPYING` |
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

## Runtime distribution

The public `bc250-fsr4-setup-VERSION.tar.gz` bundle contains installer code,
the integration patch, component manifest and essential documentation/notices.
It does not redistribute GE-Proton, OptiScaler, OptiPatcher or AMD provider
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
