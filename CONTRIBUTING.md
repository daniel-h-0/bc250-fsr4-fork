# Contributing

Start with the [DLL source and rebuild guide](dll/README.md). RC11 is the current
release. The [GPU probe](dll/probe/README.md) can check a new D3D12 environment
without a game. Driver and old runtime components have separate
[source contracts](docs/development.md) and recovery requirements.

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
use the separate [build instructions](docs/development.md#build-the-current-driver) and
`requirements-build.txt`.

## Keep the scope small

User documentation starts with [Install across your games](docs/optiscaler-client.md).
Keep manual DLL replacement, native recipes and custom paths in the
[manual guide](docs/beginner-guide.md). The client supplies common FFX/INT8
settings and preserves per-game configuration on updates. Keep first-time Linux
loading instructions with their actual games, and link optional cache/driver tools separately.
Each topic has one maintained guide; other pages link to it instead of copying
its steps. Keep current guides at the top of `docs/`; put superseded runtime
guides in `docs/legacy/runtime/` and dated experiments in `docs/legacy/research/`. Keep release measurements dated and separate from current instructions.
When editing packaged docs, update their inventory/checksums and check links from
both the repository and the extracted archive.

- DLL changes belong in `dll/` with complete editable shader sources, input
  hashes and a newly qualified output identity. Keep the user download small.
- Client integration belongs in `integrations/optiscaler-client/`: pin the upstream
  source, retain its license, ship complete modified source with the application,
  and run the transaction and packaged-client checks in its build guide. Its
  per-game file ownership is separate from the retained RC6 runtime below.
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
and error. For the client route, include the client build and the game's displayed
result. For either DLL route, include the game/API, OptiScaler and DLL versions,
GPU/driver, Proton and in-game upscaler choice. Add the custom driver/runtime
identity only if you use that optional route.

Share a small redacted log excerpt. Do not attach game/provider binaries,
shader dumps, saves, account configuration or secrets.
