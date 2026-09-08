# Use BC250 FSR4 in Steam

Install the v4 driver and compatibility tool once. Then choose **BC250 FSR4
(4.1.1 INT8)** in Steam for each compatible DX12 game you want to use it with.

The shared runtime passed [FSR-input and DLSS-input gameplay checks](runtime-qualification.md).
There is no game allowlist or automatic library scan; these checks do not
establish compatibility with every game.

## Install once

Use `bc250-fsr4-setup-4.0.0-rc2.tar.gz` and its checksum from the
[releases page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases), or the
[maintained source checkout](../README.md#obtain-the-source). The original
driver rc1 archive predates this tool. Check the [prerequisites](../README.md#prerequisites),
then close Steam and games and run as your desktop user:

```sh
./bc250-fsr4 install
```

For a standard v3 installation, use `./bc250-fsr4 install --upgrade-v3`.
See the [v3 upgrade notes](upgrading-v3.md) for custom paths.

The installer reuses a compatible verified system or private driver. If one
is needed, it installs a private driver after checking its dependencies and
Vulkan loading. It then downloads the pinned runtime components, verifies
them and assembles the compatibility tool locally. It does not change Steam
account settings or copy proxies into game directories.

## Select it in Steam

1. Restart Steam and open the game's **Properties → Compatibility**.
2. Enable **Force the use of a specific Steam Play compatibility tool** and
   select **BC250 FSR4 (4.1.1 INT8)**.
3. Launch the game's DX12 version. Select **FSR** or **DLSS** as the input through OptiScaler, in the game's graphics menu.

Your resolution and quality settings remain yours to choose. This runtime
selects FSR 4.1.1 INT8 model 2 and disables frame generation. A compatibility
tool cannot add an upscaler input that the game does not support; the presence
of a DLL alone does not prove compatibility.

## Update or undo

Close Steam and games. Extract the newer distribution and run:

```sh
./bc250-fsr4 update
./bc250-fsr4 status
./bc250-fsr4 rollback
```

Update uses the new distribution's pinned components and reuses a compatible
verified driver. Rollback restores the preceding runtime and driver selection
owned by that operation; it does not roll back a system or private driver
that the installer reused. Retained component transactions support recovery
after an interrupted operation.

To stop using BC250 FSR4 for one game, select its previous compatibility tool
in Steam. A rollback of the first installation removes the new Steam entry;
switch affected games back to their previous tool.

## Runtime compatibility

**Use FSR 4.1.1 INT8 only. Do not combine this tool with the newer 4.1.1b mod
or a second OptiScaler deployment.** Before switching, use the outgoing mod's
own uninstall or rollback procedure to restore the game files and launch
options it changed. Preserve unrelated mods and backups.

## Undo game setup

If you used this project's retired per-game wizard, close Steam and games and
undo its transactions before selecting the new tool:

```sh
./setup-game.sh rollback /path/to/transactions/RECORD.json
```

For an interrupted transaction, use `recover` instead of `rollback`. The old
entry point supports recovery only. See [legacy recovery and troubleshooting](game-troubleshooting.md).
