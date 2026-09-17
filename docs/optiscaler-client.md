# Install across your games

**Select compatible games, install once, and update them together.** The BC250
build of OptiScaler Client manages each game's DLL and FFX/INT8 settings. Keep
your normal graphics driver and Proton; no custom paths or shader-cache setup
are needed.

## 1. Open the client

Download [OptiScaler Client 1.0.7-bc250.2 for Linux x64](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/opticlient-v1.0.7-bc250.2/bc250-opticlient-1.0.7-bc250.2-linux-x64.tar.gz),
extract it, and run **`Start-BC250-OptiClient.sh`**. First setup downloads its
OptiScaler dependencies. The RC11 DLL is included and selected automatically.

Choose **Scan Games**, then **Install across your games**. Steam and Heroic
libraries are discovered automatically. **Add Manually** lets you select a
game's executable yourself.

## 2. Select games and install

Select the games you want, close them, and choose **Install / update selected**.
For a new installation, check the executable shown in its row. If the client
asks you to locate it, use **Add Manually** to select the actual 64-bit Windows
`.exe`; Unreal games usually keep it under `Binaries/Win64`.

Every new game uses the same OptiScaler setup. The client installs its files
beside the executable and selects FFX/INT8. Existing OptiScaler installations
keep their input settings and working launch options. Leave frame generation
off in the game.

**First OptiScaler installation on Linux?** Copy the row's loading instruction
into Steam **Properties → General → Launch Options**. Merge it with any existing
settings, keeping one `%command%`. In Heroic, add `WINEDLLOVERRIDES` as an
environment variable with value `dxgi=n,b`. Do this once per newly configured
game; DLL updates need no launch-option changes.

Launch normally and select **DLSS**, or an FSR/XeSS input supported by that
game's OptiScaler integration. OptiScaler uses that input to run FSR4. Press
**Insert** to open its overlay; the optional
[watermark check](beginner-guide.md#3-play-and-check-once) confirms RC11 and INT8.
First use can pause while shaders compile.

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

This is a project build of [OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client).
Application updates come from this project; upstream Client 1.0.7 does not
include this installation/update screen.
