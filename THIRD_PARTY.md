# Provenance and licenses

This fork preserves dmorazasanchez/bc250-fsr4 history. Its v3 baseline is
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. Credit for the original BC250
FSR4 compatibility work belongs to that project and its contributors.

The imported repository has no top-level license grant at that revision.
This fork does not claim to relicense those inherited files. A repository-wide
license grant for that original material remains a maintainer handoff item.
New v4 Python/shell tooling explicitly marked SPDX MIT uses LICENSE.new-code.

Mesa code retains its per-file licenses and copyright notices. Mesa's license
summary is included in binary archives as `licenses/Mesa-license.rst`; full
source is reproducible from the pinned archive and ordered patches. The patches
modify existing Mesa files; inspect those files' original notices. Mesa is
predominantly MIT, with additional permissive licenses listed in its summary.
The bundled Wayland protocols source archive retains its internal COPYING.

AMD FSR provider DLLs, game files, OptiScaler, OptiPatcher, Luma and Proton are
not included in the driver release. Obtain them from their respective authors;
their own licenses and game distribution terms apply. No game shader dumps,
saves, keys, account configuration or proprietary game payloads are bundled.
