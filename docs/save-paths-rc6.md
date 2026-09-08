# RC6: Steam save-folder registration

RC5 users reported that Split Fiction and Cyberpunk started without their
existing saves under BC250 FSR4. The Split Fiction report said that selecting
regular Proton restored the saves.
The investigation reproduced a Steam integration defect: Steam assigns the
standard Windows save-folder mappings only when a compatibility tool's internal
name contains `proton`, without regard to case. Our `BC250-FSR4` key did not.
The launcher filename and `compatmanager_layer_name=proton` were insufficient.

RC6 registers `proton-bc250-fsr4`, with `BC250-FSR4` as its compatibility alias.
The displayed name and installation directory remain the same. Existing Steam
selections resolve through the alias; the installer does not edit accounts,
game prefixes, save payloads or cloud indexes.

## Update

Close games and Steam. Download the RC6 setup archive and checksum from the
[release page](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc6),
verify and extract them, then run the new folder's `./bc250-fsr4 update`.
Repeat any custom `--prefix` or `--steam-root` options. **Restart Steam** so it
reloads the registration, then launch the same game entry.

`./bc250-fsr4 status` reports `steam_registration.save_paths_supported=true` for
the corrected on-disk registration. `doctor` reports the old registration as
unhealthy and points to this update. These commands inspect the installed
files; restarting the Steam client is still required.

If saves still appear absent, keep the original prefix and both sets of saves.
Select the previously working Proton tool to confirm the original progress,
and collect the relevant app's entries from Steam's `logs/cloud_log.txt`.
Do not delete the prefix or blindly copy one save tree over another. This fix
addresses the reproduced registration error; it does not reconcile independent
save conflicts or establish the state of a remote user's files.

## Verification and recovery

An isolated Windows program checked standard folders through regular
Proton 10/11, GE-Proton11-6 and RC5. Synthetic save canaries seeded into the
stock prefixes remained visible after switching tools, including successful
file reads in a Proton 10 → RC5 → Proton 10 cycle. Windows exposed the same
folders and retained the canaries. This ruled out a universal prefix
relocation in those checks.

Calling the installed Steam client's actual registration routine independently
reproduced empty Documents, AppData, Saved Games and profile mappings for
`BC250-FSR4`, and the standard Proton mappings for `proton-bc250-fsr4` and
ordinary GE-Proton. The diagnostic uses no game, Steam login or cloud writer.
It is tied to the recorded Steam client binary, not a substitute implementation
of the name check. The local evidence also retains the prefix comparisons and
the initial probe's unrelated directory-scan failure.
The [recorded results](data/save-paths-20260908.json),
[Steam registration probe](data/save-paths-20260908/steam-registration-probe.py)
and [Windows folder probe](data/save-paths-20260908/folder-probe.c) are retained.

An RC5 installation upgraded to RC6, rolled back to the retained RC5 runtime,
and returned to RC6 offline with an empty download cache. Host and Steam
Runtime diagnostics passed throughout. All 196 repository tests passed,
including upgrade, interrupted publication, foreign-file preservation and
registration-only repair.

After the production update and Steam restart, all 17 existing game selections
resolved to the corrected internal name, with one BC250 entry in the tool menu.
Steam's new cloud log resolved previously failing Windows roots and recognized
the existing local saves. All 2,377 snapshotted save/settings files and links
were unchanged, with no missing or added files. Steam updated two of its
17 cloud indexes normally; those indexes were not restored or rewritten by
the installer. The 51 managed Steam fields, 51 retired mod paths and system
driver hash also remained unchanged. These are local integration checks;
the reported Split Fiction/Cyberpunk cases still need confirmation on the
affected users' installations.

The installer accepts only the exact old or new owned registration. Before
atomic replacement it retains `compatibilitytool.rc5.vdf.backup` in the tool's
directory. Modified registrations or conflicting backup paths are preserved
and rejected. Interrupted publication can be retried. Runtime rollback with
the current installer keeps the corrected registration and alias; use the
current commands to manage retained older runtimes.

The Mesa driver, GE-Proton, provider, OptiScaler, OptiPatcher and graphics
preset are unchanged. Previous renderer/performance evidence keeps its scope;
this maintenance release adds no new FPS or universal game-compatibility claim.
