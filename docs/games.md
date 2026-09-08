# Use BC250 FSR4 in Steam

Install the v4 driver and compatibility tool once. Then choose **BC250 FSR4
(4.1.1 INT8)** in Steam for each compatible DX12 game you want to use it with.

**The new compatibility tool is pending gameplay qualification for native FSR
and DLSS routes.** Earlier [driver and game evidence](qualification.md) used
the previous integration. There is no game allowlist or automatic library scan.

## Install once

Use `bc250-fsr4-setup-1.0.0-rc1.tar.gz` and its checksum from the
[releases page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases), or the
[maintained source checkout](../README.md#obtain-the-source). The original
driver rc1 archive predates this tool. Check the [prerequisites](../README.md#prerequisites),
then run as your desktop user:

```sh
./install-v4.sh
```

For a standard v3 installation, use `./install-v4.sh --upgrade-v3` instead.
If v4 is already installed privately or through the verified system-package
route, keep that installation. See the [v3 upgrade notes](upgrading-v3.md)
for custom paths.

Close Steam and games, then install the compatibility tool:

```sh
./install-runtime.sh install
```

The installer downloads the pinned upstream components, checks their hashes
and assembles the tool locally.
It selects a verified system v4 driver first, then a verified private install.
It installs the tool without changing Steam account settings or copying
proxies into game directories.

## Select it in Steam

1. Restart Steam and open the game's **Properties → Compatibility**.
2. Enable **Force the use of a specific Steam Play compatibility tool** and
   select **BC250 FSR4 (4.1.1 INT8)**.
3. Launch the game's DX12 version. Select **FSR** for a native FSR route, or
   **DLSS** for an OptiScaler replacement route, in the game's graphics menu.

Your resolution and quality settings remain yours to choose. This runtime
selects FSR 4.1.1 INT8 model 2 and disables frame generation. A compatibility
tool cannot add an upscaler input that the game does not support; the presence
of a DLL alone does not prove compatibility.

## Update or undo

Close Steam and games before changing the installed runtime. With a newer
installer bundle, rerun `./install-runtime.sh install` to install its pinned
version. Use these commands to inspect or return to the previous version:

```sh
./install-runtime.sh status
./install-runtime.sh rollback
```

To stop using BC250 FSR4 for a game, select its previous compatibility tool in
Steam. A first installation has no previous runtime version to roll back to.
Driver rollback is separate; see [driver recovery](../README.md#private-archive-install-or-v3-upgrade).

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
