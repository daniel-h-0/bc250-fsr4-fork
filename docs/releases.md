# Releases and distribution

BC250 FSR4 is one distribution with one install/update/status/rollback
interface. Driver and Steam runtime identities are recorded internally;
changing the installer does not change the qualified driver or extend its
old gameplay evidence.

| Artifact | Identity and purpose |
| --- | --- |
| Driver `v4.0.0-rc1` | The published Mesa 26.2.2 ELF and its [qualification](qualification.md); source tag `362c4c4a74456002e4697ca0e1d1bb3aaff1539d` |
| Distribution `4.0.0-rc2` | The component/preset lock in `runtime/manifest.json`; [FSR and DLSS input checks](runtime-qualification.md) passed |
| Source snapshot | An exact Git commit with file hashes and modes, for development or auditing |

The later [performance campaign](performance.md) used the unchanged rc1 driver
through the earlier integration. A source version label does not prove that
a rebuilt ELF is the same binary. FSR **4.1.1 INT8** identifies the provider;
it is distinct from both project versions and the newer 4.1.1b mod.

## End-user setup bundle

The distribution release tag is `v4.0.0-rc2`. Its small
`bc250-fsr4-setup-4.0.0-rc2.tar.gz` bundle contains the installer tools,
runtime manifest, integration patch and essential documentation/notices,
with an adjacent SHA256 checksum. Users extract it and follow the
[quickstart](../README.md#start-a-steam-game); Git is optional.

Normal `./bc250-fsr4 install` downloads the pinned GE-Proton,
OptiScaler, OptiPatcher, AMD SDK bridge and FSR provider components, verifies them and assembles
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

## Driver artifacts

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

Give component, patch or preset changes a new distribution version.
Validate installation and both gameplay routes, publish a matching setup
bundle/checksum and record the scope in the changelog. Reuse an unchanged
qualified driver by its exact hash.

Give a new driver ELF its own build identity and qualification. Keep prior
runtime versions, source records and distribution-specific base packages
available for recovery. Documentation or tooling changes alone must not be
presented as new driver or runtime gameplay acceptance.
