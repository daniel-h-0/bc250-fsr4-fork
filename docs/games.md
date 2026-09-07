# Set up a Steam game

Install the v4 driver once, then run the guided setup for each supported game.
It finds your Steam libraries and accounts, downloads the required runtime,
and configures the selected game's Steam settings with backups.

**Do not co-install the newer FSR 4.1.1b mod.** This setup uses the qualified
FSR **4.1.1 INT8** runtime. See [switching from another setup](#runtime-compatibility).

## 1. Install the driver once

Use the [maintained v4 checkout](../README.md#obtain-the-source). From that
folder, run as your desktop user:

```sh
./install-v4.sh
```

Already installed v4 privately or through the verified system-package route?
Continue to step 2. Coming from v3? Use `./install-v4.sh --upgrade-v3` for its
standard installation, or follow the [v3 upgrade checklist](upgrading-v3.md).

The tools need Python **3.12+**, `vulkan-tools`, and `bsdtar` from libarchive.
The original rc1 archive predates the guided setup; use the current `v4`
checkout for the commands on this page.

## 2. Close Steam, then run game setup

Install a supported game in Steam and sign in with the account you will use
at least once. Then exit Steam completely and close your games. In the same
folder, run:

```sh
./setup-game.sh
```

Choose an installed game and, if prompted, the Steam account you use to play
it. Review the proposed changes and continue. Setup installs the pinned
GE-Proton11-6, OptiScaler and OptiPatcher components when needed, selects
Proton for the game, and sets its launch options. You do not need to find
folders, enter launch strings or change Steam's Compatibility menu yourself.

Launch options are changed for the account you choose. Steam shares that
game's Proton selection across its accounts, so the chosen version applies
to the game's other accounts too.

Keep the undo command printed when setup finishes. If setup reports another
mod or an unsupported game update, follow its message before retrying.

## 3. Start Steam and choose the game's upscaler

Launch the game normally from Steam and use this in-game selection. Keep an
internet connection for the first launch so Proton can download FSR 4.1.1
if it is not already cached.

| Game | Choose in the game's graphics menu |
| --- | --- |
| Deadzone: Rogue | **FSR**, then your preferred quality level |
| Kingdom Come: Deliverance II | **FSR** |
| Control Ultimate Edition | **DLSS**; setup launches the DX12 version |

Frame generation stays off. Your resolution and graphics preferences remain
yours to choose.

Deadzone has actual rc1 gameplay qualification. KCD2 and Control have known
integration profiles but were not newly played for that release. Setup targets
native Linux Steam installations; Steam Flatpak and other sandboxes are not
yet qualified. See [qualification](qualification.md) for the full scope.

## Undo game setup

Close Steam and games, then copy the undo command that setup printed:

```sh
./setup-game.sh rollback /path/to/RECORD.json
```

This restores the recorded game and Steam settings. If setup was interrupted,
use the reported recovery record with `recover` instead:

```sh
./setup-game.sh recover /path/to/RECORD.json
```

Keep the record and backups. Changes you made afterward are preserved rather
than overwritten; resolve any reported conflict before retrying. GE-Proton
remains installed for reuse by other games.

## Runtime compatibility

This setup uses **FSR 4.1.1 INT8**, not the newer **4.1.1b** mod. Do not install
both into the same game or copy one setup's DLLs over the other.

To switch, close Steam and the game, undo the outgoing integration using its
own rollback procedure, and restore any original game files it replaced.
Preserve unrelated mods and backups. Guided setup does not uninstall an
unknown 4.1.1b deployment for you. Neither 4.1.1b nor mixed runtimes have been
qualified here.

## If something needs attention

Use `./setup-game.sh --list` to see detected supported games, or
`./setup-game.sh --dry-run` to preview setup without downloads or changes;
Steam can stay open for those checks. You can select a profile directly
with `./setup-game.sh --profile deadzone`.

For missing games, conflicting mods, manual installations or proof that FSR4
is engaged, see [game troubleshooting](game-troubleshooting.md). An empty
OptiScaler panel alone does not mean the native Deadzone/KCD2 route failed.
