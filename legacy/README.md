# Legacy material and recovery

This directory preserves the earlier BC250 FSR4 project as historical
reference. Active v4 installation, build and recovery instructions are in the
[main README](../README.md) and [development guide](../docs/development.md).
The original upstream history remains in Git; the imported v3 checkpoint is
`6173651fa3a5a557cba2c2ff802e2d6f49881bc1`.

| Archived path | Contents |
| --- | --- |
| `game-setup/` | Recovery only for this fork's retired per-game wizard; use `../setup-game.sh rollback RECORD` or `recover RECORD` |
| `v3/README.md`, `v3/V2.md`, `v3/V3.md`, `v3/release-notes-v3.md` | Original documentation, measurements and release instructions |
| `v3/*.patch`, `v3/v2-patches/` | Earlier Mesa patches, including the former root-level v2 fragments |
| `v3/*.sh`, `v3/Dockerfile.cts`, `v3/cts/`, `v3/bench/` | Earlier install/build/conformance/benchmark scripts |
| `v3/tools/` | Historical relay tools and their instructions |
| `v3/workflows/upstream-linux-73-workflow.yml.disabled` | Inactive experimental upstream kernel workflow |

The archived `v3/` files retain their original bytes. Some commands assume their
original repository layout or external files, use old URLs, or describe
hardware and dependencies from the earlier investigation. Their benchmarks,
license status and qualification claims remain those of the original project.
Relocating them here does not make them active v4 instructions.

Reproduce historical behavior from a separate checkout of the original
revision with its documented inputs. Do not run the v3 installers, repair
scripts or kernel workflows as part of a normal v4 build or upgrade. In
particular, the disabled kernel workflow refers to an absent experimental
kernel tree and is not a supported v4 build target.

Active builds use only the source inputs declared in `v4/manifest.json`;
archived v2 fragments are not additional patches to apply. Full source
snapshots preserve this archive for provenance. See
[THIRD_PARTY.md](../THIRD_PARTY.md) before reusing or distributing inherited
files; the fork does not claim a blanket license grant for them.
