# Development and release process

The v4 branch starts at upstream v3 commit
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`. `upstream` should remain
`https://github.com/dmorazasanchez/bc250-fsr4.git`; `origin` targets
`https://github.com/daniel-h-0/bc250-fsr4.git`. Do not squash away the original
history or mix experimental upstream branches into the accepted source.

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
python3 -m unittest discover -s tests -v
for script in ./*.sh scripts/*.sh; do bash -n "$script" || exit; done
```

CI runs tooling tests on pushes and PRs. A manually dispatched workflow also
builds the source in the container and uploads an **unqualified** build
artifact. CI does not automatically publish, install, flash or run GPU tests.

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

Generate archives only after source checks; `scripts/package.py` records
compiler/flags, shared-library dependencies, ELF symbol-version requirements,
source manifest SHA256 and every archive file's SHA256. The packaging step
strips a copy of the driver; the unstripped build evidence stays unchanged.
Qualify the exact stripped release artifact before publication.

Before a GitHub release, update the qualification report, review notices,
run the documented installation on a v3-style baseline, and attach the tested
archive plus checksum. A checksum fetched beside its archive is an integrity
check, not an independent signature. Tag the reviewed commit and retain the
original source/package evidence for rollback and future compiler comparisons.
