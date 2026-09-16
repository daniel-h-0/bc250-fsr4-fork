# Historical documentation

For the current install, use the [OptiScaler DLL guide](../beginner-guide.md).
These records preserve earlier releases, measurements and recovery procedures.
Choose the section that matches the component or release you are inspecting.

## Old Steam tool and system-driver setups

Start with the [RC6 guide](runtime/legacy-rc6.md) to migrate an existing game to
the current DLL, or maintain that older tool.

| Task | Guide |
| --- | --- |
| Select/update/undo the RC6 tool | [Steam tool](runtime/games.md) |
| Diagnose or recover old transactions | [Troubleshooting and recovery](runtime/game-troubleshooting.md) |
| Upgrade an older tool | [v3](runtime/upgrading-v3.md), [rc1](runtime/upgrading-rc1.md), [rc2–rc5](runtime/upgrading-rc2.md) |
| Maintain old system packages | [Arch/CachyOS integration](runtime/system-install.md) |
| Inspect the old tool's save registration | [RC6 save paths](runtime/save-paths-rc6.md) |
| Review compatibility/testing | [Original driver](runtime/qualification.md), [runtime RC2](runtime/runtime-qualification-rc2.md), [runtime RC3](runtime/runtime-qualification.md), [RC4](runtime/rc4-compatibility.md), [RC5 review](runtime/review-rc5.md), [SteamOS investigation](runtime/steamos-compatibility.md) |

## Earlier DLL releases and research

| Topic | Records |
| --- | --- |
| DLL checkpoints | [RC7](research/portable-dll-rc7.md), [RC8](research/portable-dll-rc8.md), [RC9](research/portable-dll-rc9.md), [RC10](research/portable-dll-rc10.md) |
| Earlier installation | [RC9 walkthrough archive](research/beginner-guide-rc9.md) |
| RC10 work | [Development](research/rc10-development.md), [release notes](research/release-notes-rc10.md), [driver gameplay](research/driver-gameplay-rc10.md) |
| Performance | [RC7 GPU costs](research/gpu-cost-rc7.md), [whole-game comparison](research/performance.md), [reconstructed pass costs](research/fsr-cost.md) |
| Cache/tooling | [Cache reuse](research/cache-setup-qualification.md), [installer review](research/cache-review.md), [recovery review](research/cache-review2.md) |

The [current GPU-cost chart](../gpu-cost.md) keeps the later RC9 measurements.
Raw data and figures remain in [data](../data) and [assets](../assets), preserving
their recorded filenames and hashes.

## Source archives

[Upstream v3 and old tools](../../legacy/README.md) live outside this documentation
tree. Experimental source capsules remain alongside their code under
[v4/experimental](../../v4/experimental).

Published tags and source downloads keep the layout from their release. A small
[compatibility pointer](../cache-setup-qualification.md) preserves the qualification
link used by the original RC11 downloads. For a
pre-reorganization URL, the [September 16 snapshot](https://github.com/daniel-h-0/bc250-fsr4-fork/tree/b00e02fae424601d54399d04d5ead242ae88a896/docs)
provides the previous paths.
