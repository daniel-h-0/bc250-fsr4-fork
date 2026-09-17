# Install across your games

**Select compatible games, install once, and update them together.** The BC250
build of OptiScaler Client manages each game's DLL and FFX/INT8 settings. Keep
your normal graphics driver and Proton; no custom paths or shader-cache setup
are needed.

This is a BC250-maintained build of [OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client),
created by [Agustín Montaña (Agustinm28)](https://github.com/Agustinm28) and contributors.
Their application supplies the desktop interface, game discovery and component
management; BC250 adds the dedicated FSR4 installation/update/restore workflow.

## 1. Open the client

Download [OptiScaler Client 1.0.7-bc250.2 for Linux x64](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/opticlient-v1.0.7-bc250.2/bc250-opticlient-1.0.7-bc250.2-linux-x64.tar.gz),
extract it, and run **`Start-BC250-OptiClient.sh`**. First setup downloads its
OptiScaler dependencies. The RC11 DLL is included and selected automatically.

Choose **Scan Games**, then **Install across your games**. On Linux, the client
scans Steam, Heroic's installed Windows Epic/GOG games, and Lutris entries in
their standard locations.

For a missing game, Heroic Amazon/sideloaded entry, Bottles installation or another
storefront, choose **Add Manually** and select the installed game's actual 64-bit
Windows `.exe`. Use the game executable, not the store launcher or a shortcut.
Keep playing through your existing launcher, Wine/Proton runner and prefix.

## 2. Select games and install

Select the games you want, close them, and choose **Install / update selected**.
For a new installation, check the executable shown in its row. If the client
asks you to locate it, use **Add Manually** to select the actual 64-bit Windows
`.exe`; Unreal games usually keep it under `Binaries/Win64`.

Every new game uses the same OptiScaler setup. The client installs its files
beside the executable and selects FFX/INT8. Existing OptiScaler installations
keep their input settings and working launch options. Leave frame generation
off in the game.

**First OptiScaler installation on Linux?** Complete the
[launcher setup below](#launcher-setup) once for each newly configured game.
DLL updates need no launch-option changes.

Launch normally and select **DLSS**, or an FSR/XeSS input supported by that
game's OptiScaler integration. OptiScaler uses that input to run FSR4. Press
**Insert** to open its overlay; the optional
[watermark check](beginner-guide.md#3-play-and-check-once) confirms RC11 and INT8.
First use can pause while shaders compile.

## Launcher setup

**Skip this when OptiScaler already loads.** The client installs game files;
you set the loading option in the launcher that runs the game. These entries
match the client's default `dxgi.dll` adapter. For an existing `winmm.dll`
adapter, use `winmm` in place of `dxgi`.

| Launcher | Where | Enter |
| --- | --- | --- |
| Steam running the game through Proton | Game **Properties → General → Launch Options** | `WINEDLLOVERRIDES="dxgi=n,b" %command%` |
| [Heroic](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/wiki/Environment-Variables) | Game **Settings → Advanced → Environment Variables** | Name: `WINEDLLOVERRIDES`; value: `dxgi=n,b`. Click **+** to save the row. |
| [Lutris](https://github.com/lutris/lutris/blob/master/lutris/runners/wine.py) | Game **Configure → Runner options → DLL overrides** | Key: `dxgi`; value: `n,b`. Save. |
| [Bottles](https://docs.usebottles.com/bottles/preferences) | Open the existing bottle → **Preferences → System → Environment variables** | Name: `WINEDLLOVERRIDES`; value: `dxgi=n,b`. This setting applies to the bottle's programs. |

Heroic/Bottles values use plain text without shell quotes or `%command%`.
Other Wine frontends can use the same environment-variable name and value.
Put actual game arguments in the launcher's separate arguments field.

Preserve existing settings. If `WINEDLLOVERRIDES` already has other DLL entries,
merge them into one value, for example `dxgi=n,b;dinput8=n,b`. In Lutris, add or
edit the `dxgi` row while retaining other rows. In Steam, keep other options
and exactly one `%command%`.

**Using a launcher-generated Steam shortcut?** If it opens Heroic, Lutris or
Bottles, configure loading in that launcher and keep the shortcut's existing
target. Continue launching through it so the same prefix and launcher settings
are used.

<details>
<summary>Missing games, Flatpak and external drives</summary>

Enable the appropriate scan source in the client and rescan after installing a
game. Heroic discovery covers Epic/GOG metadata in its standard native and
Flatpak locations; Lutris discovery reads entries with an existing Windows
executable in its standard native and Flatpak locations. Custom data locations
and other entries can use **Add Manually**.

Use the game's real host path when choosing its `.exe`. The native Linux client
needs write access there, and the game's launcher needs access to that same
folder. For Flatpak launchers, grant access to the game folder or external-drive
mount when needed through your desktop's Flatpak permissions or Flatseal.
[Heroic folder-access guidance](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/wiki/Linux-Quick-Start-Guide) ·
[Bottles folder-access guidance](https://docs.usebottles.com/flatpak/expose-directories).

Install beside the game's executable, even when it lives inside a Wine prefix.
Keep the existing prefix, saves and runner selection. Use one client library
entry per installation when updating or restoring it.

</details>

## Which games can I select?

Use games [compatible with OptiScaler](https://github.com/optiscaler/OptiScaler/wiki/Compatibility-List).
Follow upstream's compatibility notes if a game's input or loading method needs
adjustment.
OptiScaler advises against using it in online games with anti-cheat.

This build uses OptiScaler 10.0.0-pre1 from September 4. It can also manage an
existing installation of that version with a local FSR DLL. For other adapter
versions, shared paths or native FidelityFX replacement, use the
[manual guide](beginner-guide.md).

## Update or restore

**Update:** download a compatible BC250 FSR4 **DLL ZIP** from the
[project releases](https://github.com/daniel-h-0/bc250-fsr4-fork/releases), choose
**Import DLL ZIP** once, select installed games, and choose **Install / update
selected**. Updates replace only their upscaler DLL; existing game settings stay
in place. New games use the selected release too.

**Update the client:** extract the new client archive and run its launcher.
Keep `~/.config/OptiscalerClient-BC250/` (or its location under `XDG_CONFIG_HOME`);
it holds your selected DLL, installation records and original-file backups.

**Restore:** select games and choose **Restore / recover selected**. The client
restores files saved before its first BC250 installation and removes files it
added. Undo any launch-option edits yourself. If a file has later changes,
restore pauses that game for review and leaves its files in place.

Use the same action to recover an interrupted operation. Results appear per
game, so one game needing attention does not prevent the others from completing.

[Troubleshooting](beginner-guide.md#if-the-check-fails) ·
[Shader compilation](first-run-shader-compilation.md) ·
[Client build and validation](../integrations/optiscaler-client/README.md)

[OptiScaler](https://github.com/optiscaler/OptiScaler) and
[OptiPatcher](https://github.com/optiscaler/OptiPatcher), by the OptiScaler team and
contributors, provide the in-game adapter and input-compatibility plugin.
OptiScaler Client is an independent manager project. BC250 maintains this
modified build and supplies its application updates and
[installation support](../CONTRIBUTING.md#report-a-problem).
[Full credits and licenses](../THIRD_PARTY.md#optiscaler-client-addon).
