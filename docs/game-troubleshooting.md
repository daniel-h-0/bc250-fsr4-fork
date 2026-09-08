# Runtime troubleshooting and legacy recovery

Start with [the Steam game guide](games.md). Installation can verify files and
driver loading; it cannot establish that a game's current frame uses FSR4.

## Installation and launch

| Symptom | Next step |
| --- | --- |
| The tool is missing from Steam | Exit Steam fully, run `./install-runtime.sh status`, then restart Steam. Select it in the game's Properties → Compatibility. |
| A custom native Steam installation is not found | Pass `--steam-root PATH` to `./install-runtime.sh install`. Steam Flatpak and other sandboxes are not qualified. |
| No verified driver is found | Install v4 with `./install-v4.sh`. Use `--driver private --driver-prefix PATH` for a custom private installation, or `--driver system` for the verified system route. |
| A download or archive check fails | Keep the previous installation. Retry with the pinned archive or report the error; do not bypass its checksum. |
| The game has another upscaler mod | Undo that integration using its own records before selecting BC250 FSR4. Do not combine it with 4.1.1b. |
| The game starts but FSR4 is unclear | Confirm DX12 and the in-game upscaler choice, then collect the evidence below. Switching compatibility tools is not proof of INT8 engagement. |

For offline reinstallation, retain the cache from a connected install:

```sh
./install-runtime.sh install --cache /path/to/runtime-cache
./install-runtime.sh install --cache /path/to/runtime-cache --offline
```

The second command uses that cache and fails if a required component is missing.
An optional privately built complete bundle can also be installed with its
SHA256:

```sh
./install-runtime.sh install --archive /path/to/RUNTIME.tar.gz --sha256 EXPECTED_SHA256
```

The public setup bundle contains the installer, patch, manifest and notices;
upstream runtime binaries are downloaded separately. Neither bundle supplies
a driver installation or game payloads. Use `./install-runtime.sh --help` for
the current command options.

## Verify a real game

The new native FSR and translated DLSS launch paths need their own gameplay
qualification. For the current launch, establish:

- The process maps the intended v4 driver and pinned FSR 4.1.1 provider.
- The runtime selects INT8 model 2, with frame generation off.
- A current frame renders correctly, and route-appropriate initialization
  evidence or a temporary watermark confirms FSR4 engagement.

Use current logs and screenshots; stale log lines, a generic OptiScaler panel
or a successful desktop `vulkaninfo` are insufficient. Do not enable game GPU
tracing to collect this evidence. Include the runtime version, driver hash,
game/API and upscaler selection in a report; omit game files and personal data.

Earlier observations remain in [driver qualification](qualification.md) and
[performance](performance.md). Deadzone supplied the original release's
native-FSR gameplay proof. KCD2 and Control supplied earlier native-FSR and
DLSS integration observations, respectively. Those titles are evidence,
not an installation allowlist, and their previous results do not qualify the
new compatibility tool.

## Recover the retired game wizard

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
