# Update an existing rc2 runtime

RC3 reuses the **same qualified rc1 driver**. It expands the shared runtime to
DX11 and Vulkan inputs, keeps existing ReShade/Luma chains loadable, and adds
a signed NVIDIA DLSS helper for games that require NGX signature validation.
Rendering still uses the pinned AMD FSR 4.1.1 INT8 provider.

Download the RC3 setup archive and checksum from the
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc3).
Extract into a new directory and retain the previous installer and recovery
records. With Steam and games closed, run as your desktop user:

```sh
sha256sum -c bc250-fsr4-setup-4.0.0-rc3.tar.gz.sha256
tar -xzf bc250-fsr4-setup-4.0.0-rc3.tar.gz
cd bc250-fsr4-setup-4.0.0-rc3
./bc250-fsr4 update
./bc250-fsr4 status
```

Repeat any custom `--prefix` and `--steam-root` options from your installation.
Use `./bc250-fsr4` in this directory; the older system-package command on PATH
operates only on the driver. No driver rebuild or v3 migration is required.

Restart Steam. Existing games selected for **BC250 FSR4 (4.1.1 INT8)** use the
new runtime on their next launch. GE replaces its own tracked prefix files,
including the previous DXGI proxy, without changing game directories or saves.
Keep the game's established renderer and choose its FSR or DLSS input.

For a game still using a manual OptiScaler deployment, retire that deployment
using its own recovery records before selecting the shared tool. Preserve
unrelated mods and renderer arguments. Luma can provide an input for some
DX11 games; it remains a separate mod and is not installed by this tool.
The [qualification record](runtime-qualification.md) states the tested scope.

To undo the update, close Steam and games and run `./bc250-fsr4 rollback`
with the same custom paths. Restart Steam. The preceding managed runtime is
restored, and GE reconciles its tracked files on the next launch, removing
RC3-only files. Retain runtime versions and transaction records until you
accept the update. Rollback does not reinstall a retired game-local mod.
