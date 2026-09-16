# Legacy RC6 Steam compatibility tool

New installs use [OptiScaler DLL replacement](beginner-guide.md). This page is
for existing users of the RC6 compatibility tool.

## Install once

Use the [retained RC6 guide](legacy-rc6.md#start-a-steam-game) for its download,
prerequisites and installer. Migration instructions are specific to the old setup:
[v3](upgrading-v3.md), [rc1](upgrading-rc1.md), or [rc2–rc5](upgrading-rc2.md).

## Select it in Steam

Restart Steam, open the game's **Properties → Compatibility**, and select
**BC250 FSR4 (4.1.1 INT8)**. Keep the established renderer and select its FSR/DLSS
input. The tool supplies INT8 model 2 with frame generation off. Games needing
an input mod such as Luma still require that mod's setup.

## Update or undo

Close Steam and games, then use the matching RC6 tools:

```sh
./bc250-fsr4 update
./bc250-fsr4 status
./bc250-fsr4 rollback
```

Rollback restores the runtime/driver selection owned by its transaction; reused
system/private drivers stay in place. To stop using the tool for one game,
select its previous Proton version. After first-install rollback, switch affected
games away from the removed entry.

## Runtime compatibility

This tool uses pinned **FSR 4.1.1 INT8**. Before selecting it, undo competing
4.1.1b or game-local OptiScaler integrations using their records. Preserve
unrelated mods and backups. [Recorded runtime checks](runtime-qualification.md).

## Undo game setup

Use [transaction recovery](game-troubleshooting.md#recover-the-retired-game-wizard)
for the retired wizard. Restore manually changed launch options from your own
backup. To move to the current DLL, follow the
[RC6 handoff](legacy-rc6.md#upgrade-a-game-to-rc7).
