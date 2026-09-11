# Update an existing rc2–rc5 runtime

This updates the retained Steam compatibility tool to RC6. New RC9 DLL
installations use the [beginner walkthrough](beginner-guide.md); migrating an
old game integration is covered by the [RC6 handoff](legacy-rc6.md#upgrade-a-game-to-rc7).

RC4 adds a portable driver, automatic repair of the old private driver's ABI
mismatches, fewer installer prerequisites and host/Steam Runtime diagnostics.
RC6 fixes [Steam save-folder registration](save-paths-rc6.md), including a
compatibility alias for existing selections. It includes RC5’s
[installer and recovery corrections](review-rc5.md). It preserves RC3’s
DX11/DX12/Vulkan routes, component versions and game preset.
A verified working system driver can still be reused. A private driver built
from the original CachyOS binary is upgraded to the portable build.

Download the RC6 setup archive and checksum from the
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc6).
Extract into a new directory and retain the previous installer and recovery
records. With Steam and games closed, run as your desktop user:

```sh
sha256sum -c bc250-fsr4-setup-4.0.0-rc6.tar.gz.sha256
tar -xzf bc250-fsr4-setup-4.0.0-rc6.tar.gz
cd bc250-fsr4-setup-4.0.0-rc6
./bc250-fsr4 update
./bc250-fsr4 doctor
```

Repeat any custom `--prefix` and `--steam-root` options from your installation.
Use `./bc250-fsr4` in this directory; the older system-package command on PATH
operates only on the driver. No driver rebuild or v3 migration is required.

Restart Steam. Existing games selected for **BC250 FSR4 (4.1.1 INT8)** use the
new runtime on their next launch. GE replaces its own tracked prefix files,
including an older proxy where needed, without changing game directories or saves.
Keep the game's established renderer and choose its FSR or DLSS input.

For a game still using a manual OptiScaler deployment, retire that deployment
using its own recovery records before selecting the shared tool. Preserve
unrelated mods and renderer arguments. Luma can provide an input for some
DX11 games; it remains a separate mod and is not installed by this tool.
The [RC4 qualification record](rc4-compatibility.md) states the tested scope.

To undo the update, close Steam and games and run `./bc250-fsr4 rollback`
with the same custom paths. Restart Steam. The preceding managed runtime and driver binding are restored. GE reconciles
its tracked files on the next launch. Retain runtime versions and transaction
records until you accept the update. Rollback does not reinstall a retired game-local mod.
The corrected Steam registration and compatibility alias remain in place
across runtime rollback. Use the current installer to manage retained runtimes.
