# Development and release process

The v4 branch starts at upstream v3 commit
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. `upstream` should remain
`https://github.com/dmorazasanchez/bc250-fsr4.git`; `origin` targets
`https://github.com/daniel-h-0/bc250-fsr4-fork.git`. Do not squash away the original
history or mix experimental upstream branches into the accepted source.

## Repository map

| Path | Responsibility |
| --- | --- |
| `v4/manifest.json`, `v4/patches/` | Pinned driver source identity and ordered Mesa changes |
| `v4/source-dependencies/` | Hash-pinned build input |
| `v4/games.json` | Pinned provider/runtime identities and explicit game profiles |
| `scripts/`, top-level shell entry points | Source preparation, build, packaging, installation, recovery and game setup |
| `tests/`, `.github/workflows/` | Active checks and CI |
| `docs/qualification.*`, `docs/performance.md`, `docs/data/`, `docs/assets/` | Recorded rc1 qualification and later performance evidence |
| `legacy/` | Archived upstream scripts, patches, documentation and disabled workflow; see its index |
| `.work/`, `dist/`, `.venv/` | Ignored local build, release and Python environment outputs |

The `v4` branch may improve tooling without changing the qualified Mesa source.
Keep that distinction visible in reviews and the [changelog](../CHANGELOG.md).
The [release guide](releases.md) identifies the immutable rc1 artifacts and
explains how to distribute a later source snapshot. Historical qualification
records describe the binaries actually tested, even after the tools change.

## Source inputs

`v4/manifest.json` pins the Mesa archive, three ordered patches, Wayland
protocols archive, fifteen final source hashes, lineage and provider identity.
The patches are a complete route from stock Mesa 26.2.2:

1. The accepted v3-derived arithmetic/composed optimization checkpoint.
2. Resolution family coverage and the independent large-bucket store guard.
3. Default-on production selection and cache identity.

Rebase patches onto a new Mesa source version explicitly; update all final
source hashes and qualify the resulting compiler programs and runtime. Never
apply patches with fuzz or present a fresh compiler binary as an old qualified
binary merely because the source file hashes match.

## Checks

```sh
python3 scripts/check-repo.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The repository check validates pinned hashes, active documentation links,
published performance arithmetic, syntax and tooling tests. These checks run
without a GPU and do not install a driver or launch a game.
See [contributor setup](../CONTRIBUTING.md) for the development dependencies.
CI runs the checks on pushes and PRs. A manually dispatched workflow also
builds the source in the container, packages it, checks the extracted bundle
and uploads an **unqualified** build artifact. CI does not automatically
publish, install, flash or run GPU tests.

Release qualification should establish:

- Clean source materialization and exact changed-file hashes.
- Complete allocated-program comparisons against the last accepted checkpoint,
  including unrecognized shader/weight/interface and optimization-off cases.
- Complete GPU tensor/image outputs, buffer integrity and guarded store cases.
- Private install, migrated v3 launch, exact rollback and reinstall.
- The package route against an actual original package, including rollback.
- A real game rendering with mapped driver/provider identities, INT8 model
  selection and native FSR4 initialization; restore test settings and saves.

Do not enable game GPU tracing to obtain proof. It was implicated (not proven)
in an earlier hard-reset incident. Source builds explicitly disable RADV
u_trace. Existing engine logs, mapped identities, a visible frame, and bounded
isolated correctness harnesses provide the relevant evidence here.

The public repository contains no proprietary game shader corpus or provider
DLLs. Some complete-program/output qualification uses locally obtained game
artifacts and therefore cannot be reproduced from this repository alone.
Reports must state that limitation and identify tested inputs by hash.

## Packaging and publication

The two artifact types have different purposes:

- `scripts/package.py` creates an installable binary archive from a completed
  pinned build and includes its source/tooling records.
- `scripts/source-release.py` exports all tracked files at a reviewed Git
  revision, with a commit/file manifest and an adjacent checksum. It does not
  build a driver or include ignored local artifacts.

After committing and reviewing a clean checkout:

```sh
python3 scripts/source-release.py --output dist/source
```

Use `--ref TAG` to export a specific recorded revision. A default HEAD export
requires a clean working tree; an explicit ref exports Git object contents,
including when the working tree has unrelated edits. See [release guidance](releases.md)
for filenames, verification and publication boundaries.

Generate archives only after source checks; `scripts/package.py` records
compiler/flags, shared-library dependencies, ELF symbol-version requirements,
source manifest SHA256 and every archive file's SHA256. The packaging step
strips a copy of the driver; the unstripped build evidence stays unchanged.
Qualify the exact stripped release artifact before publication.

Current build provenance uses schema 2. Work directories created by the
original rc1 tooling need a fresh build directory; they cannot be resumed or
repackaged with these stronger checks. The original qualified archives remain
installable. Binary packaging requires a clean Git checkout, so newly added
helpers cannot be silently omitted from the tracked source inventory. Commit
reviewed changes first, or build from a verified source snapshot. Provenance
records the available source revision; GitHub-generated source downloads have
no Git metadata and report that limitation explicitly.

Before a GitHub release, update the qualification report, review notices,
run the documented installation on a v3-style baseline, and attach the tested
archive plus checksum. A checksum fetched beside its archive is an integrity
check, not an independent signature. Tag the reviewed commit and retain the
original source/package evidence for rollback and future compiler comparisons.

Never move the `v4.0.0-rc1` tag, replace its original attached archives, or copy
its accepted-binary claim onto a new build. Later tooling validation belongs
in its own report and commit. A future binary release needs a new reviewed
release identity and qualification of the exact distributed ELF.
