# Game setup and proof of engagement

There are two separate requirements: v4 RADV must be the driver the game loads,
and the game must actually request the supported FSR4 INT8 model. Driver
installation and the game's launch configuration address the first. A
game-compatible FSR4 provider/model hook handles
the second. Native FSR4 availability alone is insufficient on GFX1013: a tested
Proton-only upgrade without the model hook fell back to FSR 3.1.5.

## Runtime compatibility

**This guide uses the pinned FSR 4.1.1 INT8 runtime. It does not use the
newer 4.1.1b mod, and the two setups should not be co-installed.** Do not
copy 4.1.1b DLLs into a game configured by this guide, replace files inside
the managed runtime, or combine their proxy DLLs and launch overrides.
The tested provider SHA256 is
`4e7dc37aebea3a90e3d3cc43e24cb2b54176b2535315f20dbe63b3b7cfc56b1e`;
version labels alone do not establish the same binary or shader family.

To switch setups, close Steam and the game, undo the existing integration
with its own rollback procedure, and restore any original game DLLs it
replaced. For this helper, use [Undo game setup](#undo-game-setup). Remove
the outgoing integration's launch overrides before following the other
guide. Preserve backups and unrelated mods; this helper does not uninstall
an unknown 4.1.1b deployment for you.

4.1.1b and mixed-runtime configurations have not been qualified here.
The correctness and performance results in this repository apply only to
the recorded provider and driver hashes. The project's `v4` release name
is separate from the FSR provider's `4.1.1` version.

## Known profiles

The optional helper currently covers these explicit game routes. It does not
automatically inject the whole Steam library or choose unsupported online /
anti-cheat games. These are the known integration paths from the predecessor
host deployment; the fresh v4 release's actual test scope is recorded in
[qualification](qualification.md).

Use the maintained `v4` checkout for current game tooling. The original rc1
archive retains its bundled tool revision; later recovery and validation
changes do not extend the original driver/game qualification. See
[release identities](releases.md).

| Profile | Game | In-game selection | Route |
| --- | --- | --- | --- |
| `deadzone` | Deadzone: Rogue | FSR, choose your quality level | Native FSR4 context with INT8 model hook; FFX and DLSS interception off |
| `kcd2` | Kingdom Come: Deliverance II | FSR | Native FSR4 context with INT8 model hook; FFX interception off |
| `control` | Control Ultimate Edition, DX12 | DLSS | OptiScaler replacement upscaler |

The native profiles hash-check the shipped FSR SDK before changing files.
A game update that changes it stops setup and needs requalification. Other
games can use their own correctly configured OptiScaler integration; the
underlying driver optimizations are shader-based, not restricted to these
three game names. DLL presence is only a candidate for support, not proof.

## Prepare the runtime

The qualified combination is
[GE-Proton11-6](https://github.com/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton11-6),
FSR 4.1.1, and
[OptiScaler nightly 20260904](https://github.com/optiscaler/OptiScaler-nightly/releases/tag/nightly-20260904).
Install the appropriate GE-Proton archive into Steam's `compatibilitytools.d`
as documented by its author, restart Steam, and select
`GE-Proton11-6-x86_64` in the game's Properties → Compatibility. Changing the
system driver does not itself change the selected Proton version.

The helper uses the named OptiPatcher **v0.41** release, not a mutable rolling
asset. The original host used an older rolling artifact whose URL was later
replaced; the new helper intentionally refuses such checksum changes.
It downloads the pinned OptiScaler and OptiPatcher artifacts directly
from their authors and checks SHA256. You need `bsdtar`/libarchive for the 7z
archive. It does not run the archive's Windows setup scripts. Runtime files
are kept under `~/.local/share/bc250-fsr4/game-runtime/` by default; driver
archives do not redistribute these third-party binaries.

```sh
python3 scripts/game-setup.py fetch
```

Close Steam and games, then pass the **game installation directory**, not the
Wine prefix and not the nested executable directory:

```sh
python3 scripts/game-setup.py install --profile deadzone \
  --game "/your/SteamLibrary/steamapps/common/Deadzone Rogue"
```

It validates the known executable/SDK, installs links to the pinned runtime,
merges the important settings into `OptiScaler.ini`, and records exact original
files and symlinks. Existing unrelated proxy DLLs or real runtime directories
are preserved and require manual reconciliation. The helper prints a Steam
launch fragment; merge it with your existing options rather than discarding
them. For the Deadzone native route, the essential runtime fragment is:

```text
PROTON_FSR4_UPGRADE=4.1.1 WINEDLLOVERRIDES=dxgi=n,b %command%
```

An existing installation managed by this helper can be updated only while its
recorded links and retained runtime still pass verification. Unrelated runtime
files remain outside that operation. Keep the printed transaction records:
repeated changes to the same game must be rolled back newest first.

Keep the private driver's printed `VK_DRIVER_FILES=...` assignment as well if
using the private route. With the system package route, do not add a private
ICD override. Preserve unrelated launcher variables, Wine overrides and game
arguments. Do not add `PROTON_USE_OPTISCALER` on top of this game-local proxy;
that introduces another deployment path which this profile does not manage.

Launch the game and select the in-game upscaler listed above. Quality,
Balanced, Performance and Native AA have different costs; the helper leaves
your resolution and quality preferences alone. Frame generation stays off.

## Important configuration details

- `FSR.Fsr4ForceModel=2` selects INT8 for the pinned OptiScaler version.
- The tested linear-input paths use `FsrNonLinearColorSpace=false`, while
  **both** `FsrNonLinearSRGB` and `FsrNonLinearPQ` are `auto`. In this pinned
  version, explicitly assigning either optional child, even `false`, can
  activate the nonlinear flag and damage reconstruction. Do not bulk-copy
  that old false/false configuration.
- Production uses `Fsr4EnableWatermark=auto` and `LogToFile=false`. Add
  `--watermark` only for a temporary visual check, then roll back that
  transaction or reinstall the quiet configuration. The provider tests the
  watermark environment variable's presence: setting it to `0` is not a
  reliable way to turn the watermark off.
- The Deadzone/KCD2 native route intentionally bypasses OptiScaler's own
  replacement context. Its generic “select DLSS or XeSS” or “no FSR hooks”
  panel can therefore be empty while the game's native FSR4 works. Do not
  enable FFX interception just to populate that panel.

## Verify a real game

For a native profile, check all of the following in the current launch:

1. A game-owned log records successful FSR Upscaling provider **4.1.1**
   initialization, with FSR selected in the game.
2. The game process maps the release's exact `libvulkan_radeon.so`, the
   qualified `amdxcffx64.dll` provider and the intended OptiScaler model hook.
3. INT8 model 2 is selected and frame generation is disabled.
4. A current rendered frame looks correct; an optional temporary watermark
   identifies FSR 4.1.1 INT8 and its render/output dimensions.

The helper below checks the namespace-resolved file identity, mapped inode and
SHA256, the model configuration and supplied native initialization evidence.
It adds no tracing. Btrfs can report different devices in `maps` and `stat`;
the helper verifies the actual file through the game process's mount namespace
instead of rejecting that normal difference.
For the installed private release, use its `current/release.json`; for a system
install use `release.json` extracted from the exact installed archive.

```sh
python3 scripts/prove-game.py --pid GAME_PID \
  --release-manifest /path/to/release.json \
  --engine-log /path/to/current/game.log \
  --config /path/to/game/OptiScaler.ini
```

For Deadzone, the engine log is ordinarily under its Proton prefix at
`drive_c/users/steamuser/AppData/Local/Valhalla/Saved/Logs/`. Its Linux process
is ordinarily named `GameThread`. Check that the PID and log belong to the
current launch. The maintained helper records the process start and log
modification time, rejects a log older than the process, and checks that the
PID did not change during capture. A recently appended log can still contain
old initialization lines: select this launch's log and check its timestamps.
Save the JSON output and a current screenshot. This native-log
helper is not an engagement detector for the different Control/DLSS route.

A clean desktop `vulkaninfo`, a library file existing on disk, or a generic
OptiScaler banner alone is not sufficient game proof. A new provider version,
different shader family, sandbox-hidden library or stale v3 override needs
separate investigation.

## Undo game setup

With Steam and games closed, use the exact record printed during install:

```sh
python3 scripts/game-setup.py rollback /path/to/transactions/RECORD.json
```

Rollback restores original file bytes and symlink targets. It refuses to
clobber files edited after setup; inspect those differences before restoring
manually. Undo any launch-option/Proton changes you made in Steam separately.
Game saves, graphics preferences and Steam account files are not written by
this helper.

If setup or rollback was interrupted, the maintained helper refuses another
install or rollback while recovery is pending. Close Steam and games and use
the original transaction record:

```sh
python3 scripts/game-setup.py recover /path/to/transactions/RECORD.json
```

Recovery restores the pretransaction files only when every current file still
matches a recorded before or after state. Independent edits are preserved and
need manual reconciliation. Rollback and recovery derive the state directory
from the record; an explicitly supplied `--state` must match it. These commands
recover file changes managed by the helper, not manual Steam launch-option or
Proton selections.
