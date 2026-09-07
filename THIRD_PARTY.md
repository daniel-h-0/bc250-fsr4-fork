# Provenance and licenses

This fork preserves dmorazasanchez/bc250-fsr4 history. Its v3 baseline is
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. Credit for the original BC250
FSR4 compatibility work belongs to that project and its contributors.

The imported repository has no top-level license grant at that revision.
This fork does not claim to relicense those inherited files. A repository-wide
license grant for that original material remains a maintainer handoff item.
New v4 Python/shell tooling explicitly marked SPDX MIT uses LICENSE.new-code.
This limited grant does not label the whole repository MIT. Files without
that marker are not automatically covered by it. Preserve the original
notices and clarify the applicable grant before relicensing or incorporating
inherited material into another distribution.

Mesa code retains its per-file licenses and copyright notices. Mesa's license
summary is included in binary archives as `licenses/Mesa-license.rst`; current
packaging also includes its full license-text directory as `licenses/Mesa/`.
The original rc1 bundle contained the summary; this later addition does not
replace that published archive. Full source is reproducible from the pinned
archive and ordered patches. The patches
modify existing Mesa files; inspect those files' original notices. Mesa is
predominantly MIT, with additional permissive licenses listed in its summary.
The bundled Wayland protocols source archive retains its internal COPYING.

## Inventory and distribution boundaries

| Material | Source and treatment |
| --- | --- |
| New SPDX-marked v4 tooling | [LICENSE.new-code](LICENSE.new-code); preserve its copyright and permission notice |
| Inherited BC250 patches, scripts and documentation | Original Git history and `legacy/`; no repository-wide upstream license grant is asserted |
| Mesa 26.2.2 | Archive URL/hash and modifications in `v4/manifest.json`; original per-file notices remain authoritative |
| Wayland protocols 1.41 | Vendored archive in `v4/source-dependencies/`, hash pinned in the manifest; retain its internal `COPYING` |
| Game profiles | `v4/games.json` records upstream artifact URLs, versions and hashes; it does not grant rights to those artifacts |

The source exporter preserves tracked provenance and notices. A newly packaged
binary includes the pinned source recipe, Mesa license summary and license texts; review
the licenses and notices of its actual source/dependencies when distributing
a rebuilt binary. A source hash or an archive's inclusion here is an identity
record, not a new license grant. The original rc1 release remains unchanged;
later packaging maintenance cannot retroactively alter its contents.

## Separately obtained runtimes

AMD FSR provider DLLs, game files, OptiScaler, OptiPatcher, Luma and Proton are
not included in the driver release. Obtain them from their respective authors;
their own licenses and game distribution terms apply. No game shader dumps,
saves, keys, account configuration or proprietary game payloads are bundled.
