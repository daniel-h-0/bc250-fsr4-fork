# Contributing

Use the `v4` branch for maintained BC250 FSR4 work. Start with the
[repository map and source contract](docs/development.md), then the
[release identities](docs/releases.md). Changes to tooling and documentation
can be reviewed without changing or retesting the qualified driver binary.

## Set up and check a change

Use Python 3.12 or newer. The build environment and development checks have
separate dependency lists:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
python3 scripts/check-repo.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The repository check includes pinned input hashes, active local documentation
links, published performance arithmetic, syntax and tooling tests. Ruff checks
maintained Python code; historical evidence and legacy files retain their
recorded form. Use `.venv/bin/ruff format .` when formatting a code change.

For a Mesa build, follow the [native or container instructions](README.md#build-from-source)
and `requirements-build.txt`. GPU-free tooling checks cannot establish shader
correctness, ABI compatibility on another distribution, or real game behavior.
Describe any additional qualification and its exact artifact hashes.

Keep changes focused enough to review. For an installer or recovery bug,
include a regression test that exercises the failure and preservation of the
previous state. For a source or build change, preserve the pinned inputs and
explain which build/provenance checks need to be repeated. Update commands and
the [changelog](CHANGELOG.md) when user-visible behavior changes.

## Report a problem

Use the fork's [issue tracker](https://github.com/daniel-h-0/bc250-fsr4-fork/issues).
Include the commit or release, installation route, distribution, architecture,
Python version, the command and its error, and whether the prior installation
still works. Driver reports also need the actual driver SHA256 and Vulkan
device/loader details; game reports need the profile, provider version and a
description of the in-game selection and current behavior.

Review logs before attaching them. Share the smallest relevant excerpt and
redact account identifiers, private paths and secrets. Do not upload provider
DLLs, game files, shader dumps or personal saves to issues or pull requests.
The public qualification data contains hashes and summaries for inputs that
cannot be redistributed here.

## Preserve the boundaries

- Keep v4 driver changes in the manifest and ordered patches. Do not patch a
  live system library or silently change build inputs to make a test pass.
- Preserve all historical qualification data and original rc1 release assets.
  Add a dated correction or qualification record when evidence changes.
- Keep archived v3 material under `legacy/`; its old commands and performance
  figures describe the historical project and may need their original tree.
- Keep generated build trees, binaries, transaction records and host-specific
  data out of source control. Distribute reviewed artifacts separately.
- Preserve attribution and existing notices. New v4 tooling uses an explicit
  SPDX MIT marker; [THIRD_PARTY.md](THIRD_PARTY.md) explains the limited scope
  and the unresolved license status of inherited material.

In a pull request, state the concrete problem, resulting behavior, checks run
and remaining limits. Describe any change to file ownership, rollback,
supported input or release identity so a reviewer can assess it directly.
