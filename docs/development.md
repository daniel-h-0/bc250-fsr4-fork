# Development

The maintained `v4` branch preserves upstream v3 history at
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. Keep `upstream` pointed at
`https://github.com/dmorazasanchez/bc250-fsr4.git` and `origin` at the fork.

## Ownership

| Path | Responsibility |
| --- | --- |
| `v4/manifest.json`, `v4/patches/`, `v4/source-dependencies/` | Pinned Mesa source, ordered changes and build inputs |
| `runtime/manifest.json` | Distribution version, upstream component hashes, driver compatibility and preset |
| `runtime/launch.py`, `runtime/patches/` | Steam entry point and narrow GE-Proton integration |
| `bc250-fsr4`, `scripts/manage.py` | Unified component installation, update, status, doctor and rollback |
| `scripts/runtime.py`, `scripts/runtime_bundle.py` | Runtime installation, status, rollback and shared assembly/packaging |
| `scripts/driver.py`, build and package tools | Driver preparation, verification, installation and recovery |
| `scripts/vulkan_probe.py`, `scripts/safe_archive.py` | Host/container Vulkan checks and data-only archive extraction |
| `scripts/build-compat.py`, `scripts/build-steamos.py`, `v4/build-targets/` | Pinned target compilers, libraries and ABI checks |
| `tests/`, `.github/workflows/` | GPU-free checks and CI |
| `docs/qualification.*`, `docs/performance.md`, `docs/data/`, `docs/assets/` | Immutable driver qualification and later performance evidence |
| `legacy/game-setup/` | Recovery for retired game-local transactions |
| `legacy/v3/` | Archived upstream tools and experiments |

One distribution records its driver and runtime component identities. An update
does not require rebuilding an unchanged driver; a new driver ELF does require
its own qualification. See [release contracts](releases.md).

## Driver changes

The source manifest pins Mesa 26.2.2, three ordered patches and fifteen
resulting file hashes. Changes cover the v3 arithmetic checkpoint, resolution
families and store guards, and default-on selection/cache identity.

Rebase onto a new Mesa version explicitly. Apply patches without fuzz, update
the final source hashes and qualify the resulting compiler output. Unknown
shader, weight, interface and subgroup inputs must retain their fallback.
`BC250_FSR4_DISABLE=1` disables the profile-specific rewrites and image-preparation
replacement. The generic GFX1013 dot lowerings, deferred-dot optimization and
independent store repair remain active. This switch does not select stock Mesa
or recreate upstream v3. The internal `v3` cache marker is a generation ID.

Use the [native or container build commands](../README.md#build-from-source).
Build provenance records materialized source, recipe, compiler, dependencies
and flags. Matching source hashes alone do not establish identical binaries.
Target build records also pin imported builder and extraction helper code.
Full materialized-source records include file hashes, modes and internal link
targets. Resume and packaging reject added or missing files: an added header
can shadow a recorded header without changing any recorded file. Meson may
materialize pinned wrap inputs during initial configuration; those are recorded
before compilation. Generators run with Python bytecode writing disabled so
they do not add unrecorded source files during the build.
Changed recipes require a fresh build; keep the original checkout to inspect
older completed builds. This is build-input provenance, not a claim that every
transitive host tool or library is hermetic or that rebuilds are byte-identical.

## Runtime changes

The runtime uses GE-Proton11-6's existing loader and prefix manager. Its one
upstream patch adds a strict local component manifest; keep that patch bounded
and test it against the exact recorded upstream source. The fixture is
`tests/fixtures/ge-proton11-6-upscalers.py`; retain its upstream notices.

`runtime/manifest.json` selects FSR 4.1.1 INT8 model 2, OptiScaler nightly
20260904, OptiPatcher 0.41 and AMD SDK 4.0.2 as an API bridge.
The bridge must be older than the driver provider: the SDK hides an equally
versioned driver provider and would silently select its own model. Hash changes require a new distribution identity and
review of the actual artifacts. The assembled release retains its components
and inventory so upgrades and rollback select complete versions.

Keep Steam configuration, accounts, game discovery and game-directory proxy
installation outside the active runtime. Steam's Compatibility menu is the
opt-in mechanism. A new game observation belongs in a compatibility report,
not an installer allowlist or a new executable-path rule.

`scripts/runtime_bundle.py` supplies the same assembly code to the installer
and optional offline packager. Public setup bundles contain this project's
tools, patch, manifest and notices; runtime binaries are fetched separately
from the pinned upstream locations. See [distribution](releases.md).

## Checks and acceptance

Follow [contributor setup](../CONTRIBUTING.md), then run:

```sh
python3 scripts/check-repo.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

CI checks source hashes, documentation links, recorded performance arithmetic
and tooling tests. It exports and checks source snapshots; a manual workflow
also builds and packages an **unqualified** driver.

Driver qualification covers complete compiler programs and GPU outputs,
guarded/fallback cases, loader checks, installation, v3 migration and rollback.
Some numerical inputs are locally obtained game artifacts and cannot be
redistributed; identify them by hash and state that reproduction limit.

Runtime acceptance covers clean install, reuse, upgrade, rollback, offline
cache use, failed assembly and preservation of unrelated data. The upstream
patch must accept the pinned source, reject drift and retain ordinary upstream
behavior when its opt-in manifest is absent. Active tools must not scan games
or edit Steam VDF files.

The [RC3 runtime qualification](runtime-qualification.md) covers the recorded
DX11, DX12 and Vulkan scenes with FSR and DLSS inputs.
For a new component set, record current mapped driver/provider identities,
INT8 behavior and a correct rendered frame through both routes.
Keep compatibility reports separate from the old driver/performance record.
Use existing logs and bounded checks; do not enable game GPU tracing.

Recovery tests interrupt processes between transaction steps. They do not
simulate sudden power loss or prove storage durability: atomic file replacement
alone does not flush containing directory entries or entire runtime trees.
Retain normal filesystem backups and prior release payloads.

## Preserve the evidence

The original rc1 tag, assets and recorded qualification remain unchanged.
Package from a clean checkout or intact source snapshot; current build
provenance uses schema 2 and old rc1 build directories need a fresh build.
For artifact naming, source exports and publication, use the
[release guide](releases.md).
