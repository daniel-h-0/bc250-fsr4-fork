# RC6 runtime troubleshooting and legacy recovery

For the current portable DLL, use [current DLL installation and troubleshooting](../../beginner-guide.md#if-the-check-fails).
This page covers the retained RC6 tool and retired game wizard. Start with
[the legacy Steam game guide](games.md). Installation can verify files and
driver loading; it cannot establish that a game's current frame uses FSR4.
For an existing rc1 installation, use the [transition guide](upgrading-rc1.md)
to distinguish driver reuse from removal of old game-local hooks.

## Installation and launch

For SteamOS driver loading, see the [retained compatibility record](steamos-compatibility.md).

| Symptom | Next step |
| --- | --- |
| The tool is missing from Steam | Exit Steam fully, run `./bc250-fsr4 status`, then restart Steam. Select it in the game's Properties → Compatibility. |
| A custom native Steam installation is not found | Pass `--steam-root PATH` to `./bc250-fsr4 install`. Steam Flatpak and other sandboxes are not qualified. |
| No verified driver is found | Run `./bc250-fsr4 install`. Use `--driver private --driver-prefix PATH` for a custom private installation, or `--driver system` for the verified system route. |
| A download or archive check fails | Keep the previous installation. Retry with the pinned archive or report the error; do not bypass its checksum. |
| The game has another upscaler mod | Undo that integration using its own records before selecting BC250 FSR4. Do not combine it with 4.1.1b. |
| The game starts but FSR4 is unclear | Confirm the established renderer and in-game upscaler choice, then collect the evidence below. Switching compatibility tools is not proof of INT8 engagement. |
| An existing ReShade/Luma setup captures menu input | Open and close its overlay (usually Home), then retry. Keep its configuration backed up; RC3 does not manage ReShade preferences. |

For offline reinstallation, retain the cache from a connected install:

```sh
./bc250-fsr4 install --cache /path/to/runtime-cache
./bc250-fsr4 install --cache /path/to/runtime-cache --offline
```

The second command uses that cache and fails if a required component is missing.
An optional privately built complete bundle can also be installed with its
SHA256:

```sh
./bc250-fsr4 install --runtime-archive /path/to/RUNTIME.tar.gz --runtime-sha256 EXPECTED_SHA256
```

The public setup bundle contains the installer, patch, manifest and notices;
upstream runtime binaries are downloaded separately. The unified installer
also obtains the driver if needed; for offline use, retain its cache or pass
`--driver-archive PATH` with the adjacent checksum. Game payloads are never
included. Use `./bc250-fsr4 install --help` for the current command options.

## Verify a real game

Check the actual mapped driver/provider, INT8 model 2 and a rendered frame.
The [runtime qualification](runtime-qualification.md) records the tested inputs.
For diagnostics, temporarily add this before `%command%` in Steam:

```sh
BC250_RUNTIME_DEBUG=1 %command%
```

Keep existing arguments. This enables the provider watermark, Proton logging
and `OptiScaler.log` under `pfx/drive_c/windows/system32/umu/`. Remove the debug
variable after collecting the runtime version, driver hash, game/API and selected
upscaler. Use current logs/screenshots and keep private game/account data out of
reports. Collect this through existing logs rather than game GPU tracing.

## Recover the retired game wizard

The [rc1 transition guide](upgrading-rc1.md#2-retire-the-old-integration-for-each-game-you-are-moving)
explains default record locations, transaction states, manual launch-option
cleanup and what to retain for a return to the old setup.

Keep the original transaction directory and retained payloads. Close Steam
and games, then undo completed transactions newest first:

```sh
./setup-game.sh rollback /path/to/transactions/RECORD.json
```

If setup or rollback was interrupted:

```sh
./setup-game.sh recover /path/to/transactions/RECORD.json
```

These commands forward to `legacy/game-setup/recover.py`. They restore the
game files and, when present in the record, the Steam settings owned by that
transaction. Changes made afterward are preserved and require reconciliation.
Manually changed launch options or Proton selections must be restored manually.

New per-game installs, scans and profile updates are retired. Recover the old
integration before selecting BC250 FSR4; never run both integration methods on
the same game. Shared GE-Proton installations remain available for other games.
