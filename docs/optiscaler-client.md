# Install across your games

**Import the DLL once, select games, and install or update them together.**
Use the BC250 project build of OptiScaler Client, **1.0.7-bc250.1**, on Linux x64.
It supplies the FFX/INT8 settings and manages each game's copy. Keep your normal
graphics driver and Proton; no custom shader-cache setup is needed.

## 1. Open the client

Download the [Linux client archive](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/download/opticlient-v1.0.7-bc250.1/bc250-opticlient-1.0.7-bc250.1-linux-x64.tar.gz),
extract it, and run **`Start-BC250-OptiClient.sh`**. First setup downloads and checks its
OptiScaler dependencies. RC11 is included and selected automatically.

Choose **Scan Games**, then **Install across your games**. Steam and Heroic
libraries are discovered by the client; use **Add Manually** for other folders.

## 2. Select games and install

Close the games you select, then choose **Install / update selected**.
The client places the DLL and applies FFX/INT8 with frame generation off and the
watermark on Auto. Leave frame generation off in the game too.

- **Working OptiScaler installation:** keep its launch options. The first BC250
  installation applies the settings above and retains other game-specific settings.
  This build supports the September 4 OptiScaler 10.0.0-pre1 adapter.
- **First installation:** the client prepares the files for Cyberpunk 2077,
  Control, System Shock, No Man's Sky and DOOM: The Dark Ages. Each row shows
  the game's upscaler choice and Linux loading instruction.
- **Other games or custom shared paths:** follow the
  [manual guide and game recipes](beginner-guide.md) first. Rescan a supported
  local OptiScaler installation to manage its DLL here.

For a fresh Steam installation, merge the displayed loading instruction into
**Properties → General → Launch Options**, preserving other settings and one
`%command%`. In Heroic, put environment variables and game arguments in their
separate fields. Existing working OptiScaler installations skip this step.

Launch normally and choose the upscaler named in the game row. It may say DLSS
or FSR3; that is the input OptiScaler uses to run FSR4. First use can pause while
shaders compile. The optional [watermark check](beginner-guide.md#3-play-and-check-once)
confirms the active FSR4 release and INT8 model.

## Update or restore

**Update:** download a compatible BC250 FSR4 **DLL ZIP** from the
[project releases](https://github.com/daniel-h-0/bc250-fsr4-fork/releases), choose **Import DLL ZIP** once,
select installed games, and choose **Install / update selected**. Updates replace
only their upscaler DLL; existing game settings stay in place. New games use the
selected release too.

**Restore:** select games and choose **Restore / recover selected**. The client
restores the files saved before its first BC250 installation and removes the
files it added. Keep its application data until you have restored your games.
Any launch-option changes you made remain yours to undo. If you edited files
after installation, restore pauses for that game before changing any files;
keep the backups and [report the displayed result](../CONTRIBUTING.md#report-a-problem)
if you need help reconciling the edits.

An interrupted operation uses the same **Restore / recover** action to return to
the previous installation. The result is shown for each game, so a game needing attention
does not prevent the others from completing.

## Scope and help

The client manages selected games, not the entire system. Its **Files installed**
result confirms file setup; use the game's loading instruction and verification
check to confirm it is rendering. Supported native FidelityFX games can use the
[direct DLL recipes](beginner-guide.md#native-fidelityfx-games) without OptiScaler.
Those special native targets remain a manual route in this first client build.

[Troubleshooting](beginner-guide.md#if-the-check-fails) ·
[Shader compilation](first-run-shader-compilation.md) ·
[Client build and validation](../integrations/optiscaler-client/README.md)

This is a BC250 project build of the third-party
[OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client), with
an integrated installation/update screen. Its settings and restore records live
under `~/.config/OptiscalerClient-BC250/` (or your `XDG_CONFIG_HOME`). Application
updates come from this project; ordinary upstream Client 1.0.7 does not include
this route.
