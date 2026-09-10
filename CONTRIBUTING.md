# Contributing

For RC7 DLL development, start with [the DLL source and rebuild guide](dll/README.md).
The [standalone GPU probe](dll/probe/README.md) can qualify a new D3D12 environment
without a game installation. The driver commands below apply to the retained v4
driver component.

The portable DLL is the primary RC7 component. Retained driver and Steam
runtime [release identities](docs/releases.md) and
[source contracts](docs/development.md) remain separate.

## Set up and check

Use Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
python3 scripts/check-repo.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The repository check covers pinned inputs, documentation links, recorded
performance arithmetic and tooling tests. It needs no GPU. Driver builds
use the separate [build instructions](docs/legacy-rc6.md#build-from-source) and
`requirements-build.txt`.

## Keep the scope small

- DLL changes belong in `dll/` with complete editable shader sources, input
  hashes and a newly qualified output identity. Keep the user download small.
- Driver changes belong in the manifest and ordered Mesa patches. Preserve
  provenance and qualify changed compiler output.
- Runtime changes belong in its manifest, launcher or narrow upstream patch.
  Use the shared assembly code; keep GE-Proton's loader and prefix ownership.
- In the retained runtime, Steam controls per-game opt-in. Do not add game catalogs, executable scans,
  Steam-account writers or game-directory injection to the active runtime.
- Keep the retired wizard available for recovery only. Preserve existing
  transactions and historical qualification; add dated evidence for new work.
- Retain attribution and licenses. New SPDX-marked MIT tools do not relicense
  inherited code; see [THIRD_PARTY.md](THIRD_PARTY.md).

For installation/recovery fixes, test the failure and preservation of the prior
state. For runtime changes, test the pinned upstream fixture and report the
native-FSR/DLSS qualification actually performed. GPU-free tests alone cannot
establish rendering correctness.

In a pull request, describe the problem, final behavior, relevant checks and
remaining limits. Update commands and the [changelog](CHANGELOG.md) when user
behavior changes. Keep build outputs, local transactions and host data out of
source control.

## Report a problem

Use the [issue tracker](https://github.com/daniel-h-0/bc250-fsr4-fork/issues).
Include the release/commit, distribution, installation route, failing command
and error. For runtime issues, include its version, driver hash, game/API and
upscaler selection, and whether ordinary Proton still works.

Share a small redacted log excerpt. Do not attach game/provider binaries,
shader dumps, saves, account configuration or secrets.
