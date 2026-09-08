# Move from rc1 to the shared Steam runtime

The current distribution retains the **rc1 Mesa source** and uses RC4’s portable
private build by default. It reuses a verified working system driver, replaces
the original private binary with the portable build, and manages how games
receive the upscaler integration. Keep prior releases for rollback; no Mesa
rebuild or repeat of a completed v3 migration is required.

## 1. Keep your recovery records and obtain the current tools

Download the RC5 setup archive and its checksum from the
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc5).
In the download directory:

```sh
sha256sum -c bc250-fsr4-setup-4.0.0-rc5.tar.gz.sha256
tar -xzf bc250-fsr4-setup-4.0.0-rc5.tar.gz
cd bc250-fsr4-setup-4.0.0-rc5
```

Extract into a fresh directory. Keep your previous installer, game-runtime
transactions and payloads, private driver releases, and any system-package
rollback archives. The rc1 driver archive and standalone `install-v4.sh`
do not contain the unified command. The maintained `v4` checkout is also
suitable; avoid running commands from an old checkout by mistake.

Use **`./bc250-fsr4` from the new directory**, as your desktop user without
sudo. If you installed the optional system packages, the older
`bc250-fsr4` command on PATH operates on the system driver alone.

## 2. Retire the old integration for each game you are moving

Close Steam and all games before recovery. Preserve the game's existing
launch options so you can distinguish FSR-specific entries from unrelated
arguments, mods and wrappers.

| Your rc1 setup | Preparation |
| --- | --- |
| Driver only, no game-local upscaler mod | Proceed to installation below. |
| This fork's old `setup-game.sh` wizard or `scripts/game-setup.py` | Undo the game's original transactions using the recovery commands below. |
| Manually installed OptiScaler or another mod/manager | Use that integration's own uninstall or rollback records. The unified installer does not remove competing game-local hooks. |

The old tools normally stored game records in
`~/.local/share/bc250-fsr4/game-runtime/transactions/`. A custom `--state`
location has its own `transactions/` directory. Keep records in their original
directory with the retained payloads. To inspect the default location:

```sh
ls -1r "$HOME/.local/share/bc250-fsr4/game-runtime/transactions/"
python3 -m json.tool /path/to/transactions/RECORD.json
```

Inspect `profile`, `changes` and, where present, `steam_changes` to identify
the game and account. Recover `prepared` or `rolling-back` records first:

```sh
./setup-game.sh recover /path/to/transactions/RECORD.json
```

Then undo the game's `active` records, newest first:

```sh
./setup-game.sh rollback /path/to/transactions/RECORD.json
```

Already `rolled-back`, `recovered` or `aborted` records need no action. The
wizard's recorded Steam fields are restored with its game files; the older
manual tool may have no Steam fields to restore. If recovery reports an
independent edit, preserve it and reconcile against the record before
continuing. Do not delete the journal or force past the conflict.

Rollback restores what existed before that transaction, which can include an
even older mod. Ensure the outgoing upscaler integration is actually retired.
Do not blanket-delete game DLLs, the game's own FSR SDK, its Proton prefix,
saves, or shared GE-Proton installations.

## 3. Install and verify driver selection

For the standard private location or a compatible system driver:

```sh
./bc250-fsr4 install
./bc250-fsr4 status
```

Automatic selection prefers a compatible system driver, then a compatible
private driver. To retain a particular private installation, specify its
existing root (the directory containing `current.json` and `releases/`):

```sh
./bc250-fsr4 install --driver private --prefix /path/to/existing/private-root
./bc250-fsr4 status --prefix /path/to/existing/private-root
```

Use the same `--prefix` for subsequent commands. For custom native Steam,
also pass the same `--steam-root PATH` throughout. `--driver system` requires
a verified system driver and refuses a private fallback. Otherwise, when no
compatible driver is found, installation obtains and validates a private one.
Check the reported driver mode/path and `unfinished_operations` before play.

**Do not use `--upgrade-v3` just because you originally came from v3.** It is
for a v3 ICD that still needs migration when installing a private driver.
An interrupted old private-driver transaction must be recovered first with
`python3 scripts/driver.py --prefix /path/to/existing/private-root recover`;
the new unified rollback does not own that earlier transaction.

## 4. Select the tool and clean up the old launch options

Restart Steam. For each prepared game, choose **BC250 FSR4 (4.1.1 INT8)** in
Properties → Compatibility. In Launch Options, remove only the outgoing
integration's entries: for example its `VK_DRIVER_FILES=...`,
`PROTON_FSR4_UPGRADE=...`, OptiScaler selectors, or the `dxgi=n,b` entry in
`WINEDLLOVERRIDES`. Preserve unrelated override entries and arguments such as
`-dx12`. If a launcher script supplied those settings, remove its FSR-specific
part rather than discarding unrelated launcher behavior.

With no other options needed, leave Launch Options empty. The new tool owns
the provider, model and driver selection. Keep the established game renderer and select
its FSR or DLSS input; use the [game verification guide](game-troubleshooting.md#verify-a-real-game)
if engagement is unclear. Do not combine it with the newer 4.1.1b mod.

## Undo this transition

Close Steam and games, then run `./bc250-fsr4 rollback` with the same custom
paths, if any. On the first unified installation, this removes the new Steam
entry. A reused driver is left in place; a replaced private driver returns to
its preceding selection. Later updates restore the previous managed selection. Retained runtime payloads support recovery.

Restart Steam and select the previous compatibility tool for affected games.
Unified rollback **does not reinstall the old game-local mod or restore
manually edited launch options**. Returning to the complete previous setup
requires its retained installer/backups and saved launch options. Keep these
until you have accepted the new path.
