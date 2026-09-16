# Driver and legacy-runtime development

DLL work starts in the [DLL source guide](../dll/README.md). Normal users follow
[OptiScaler DLL replacement](beginner-guide.md). This page defines the separate
driver and retained RC6 Steam-tool contracts.

## Ownership

| Path | Responsibility |
| --- | --- |
| `v4/manifest.json`, `v4/patches/`, `v4/source-dependencies/` | Current driver source, ordered patches and inputs. |
| `scripts/build*.py`, `v4/build-targets/` | Materialization, compilers, target libraries and ABI checks. |
| `scripts/driver.py`, `scripts/package.py` | Private driver installation, packaging and recovery. |
| `runtime/manifest.json`, `runtime/launch.py`, `runtime/patches/` | RC6 component pins, Steam entry point and GE-Proton patch. |
| `bc250-fsr4`, `scripts/manage.py`, `scripts/runtime*.py` | Retained combined driver/runtime interface and shared bundle assembly. |
| `scripts/vulkan_probe.py`, `scripts/safe_archive.py` | Vulkan startup checks and data-only archive extraction. |
| `tests/`, `.github/workflows/` | Tests and CI. |
| `docs/data/`, `docs/assets/`, versioned reports | Recorded identities, measurements and validation. |
| `legacy/game-setup/`, `legacy/v3/` | Transaction recovery and archived upstream material. |

## Driver changes

Treat `v4/manifest.json` as the authority for source version, patch order and
file hashes. Rebase deliberately, apply without fuzz, and qualify changed
compiler output. Unknown shaders, weights, interfaces or subgroup inputs must
retain their fallback. Use the [build commands](legacy-rc6.md#build-from-source).

Build provenance records source files/modes/internal links, recipes, compiler,
dependencies and flags. Resume/packaging reject changed, missing or extra source
files: even an added header can change compilation. Pinned Meson wrap inputs are
recorded before compilation; generators avoid unrecorded Python bytecode.
Changed recipes need a fresh build. Matching source hashes alone do not prove
identical binaries or a fully hermetic toolchain.

`BC250_FSR4_DISABLE=1` disables profile-specific rewrites and image preparation;
generic dot lowerings and the store repair remain. It does not recreate stock
Mesa or v3. The current provider port's controls are documented in the
[provider guide](driver-rc10.md). Preserve separate cache identities for changed
compiler behavior.

## Runtime changes

The retained RC6 tool uses GE-Proton 11-6's loader/prefix manager and one narrow
patch for a strict local component manifest. Test it against
`tests/fixtures/ge-proton11-6-upscalers.py`, keeping upstream notices.

`runtime/manifest.json` pins FSR 4.1.1 INT8 model 2, OptiScaler nightly 20260904,
OptiPatcher 0.41 and the AMD SDK 4.0.2 bridge. The bridge must be older than the
driver provider; an equally versioned bridge can hide it and select its own
model. Changed components require new identities and runtime checks. Keep
complete versions/inventories so rollback selects a consistent set.

Steam's Compatibility menu owns opt-in. Do not add game discovery, account/VDF
writers or game-directory proxy installation to this runtime. Keep `proton` in
the internal tool name and `BC250-FSR4` as an alias: Steam's save-folder setup
depends on the name. Runtime rollback must preserve repaired registration.
[RC6 save-path diagnosis](save-paths-rc6.md).

`scripts/runtime_bundle.py` serves both the installer and offline packager.
Public RC6 setup bundles include tools/manifest/notices and fetch pinned upstream
binaries; [legacy offline instructions](game-troubleshooting.md) cover retention.

## Checks and acceptance

Run the [contributor checks](../CONTRIBUTING.md#set-up-and-check). CI verifies
source hashes, links, recorded arithmetic, tests and source exports. A successful
build or userspace test is not a game-rendering qualification.

| Changed component | Required evidence |
| --- | --- |
| Driver | Compiler programs, GPU output and guarded fallbacks; loader, install/migration and rollback checks. |
| Runtime | Pinned patch accepts its fixture and rejects drift; unselected upstream behavior stays intact. Check install/reuse/update/rollback/offline/failure paths. |
| Game integration | Actual mapped driver/provider, INT8 selection and a correctly rendered frame for each claimed API/input route. |
| Recovery | Interrupted transaction steps preserve unrelated data and can recover the prior selection. |

Use existing logs and bounded checks; do not enable game GPU tracing. Identify
nonredistributable test inputs by hash and state the reproduction limit.
Interrupted-process tests do not prove sudden-power-loss durability; retain
normal backups and prior payloads.

## Preserve the evidence

The original upstream v3 commit is `6173651fa3a5a557cba2c2ff802e2d6f49881bc1`.
Keep its history and all published tags/assets. Package from a clean checkout
or intact snapshot. Current provenance uses schema 2; old rc1 build directories
need a fresh build. New driver bytes need new qualification; tool-only changes
can reuse a verified unchanged binary. [Release procedure](releases.md#publishing-changes).
