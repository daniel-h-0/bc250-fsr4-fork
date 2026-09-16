# Cache recovery and removal review

This second review starts from `df5996d`, following the
[first installer review](cache-review.md). It found additional problems in
interrupted removal and restoration of retained launcher tools. The measured
cache preparation code and shader sources remain unchanged.

## Corrections

- **Validate the old launcher before rollback.** A previous driver library could
  be intact while its separate launcher tools were missing or modified. Rollback
  previously restored that broken launcher. Rollback and recovery now validate
  its complete tool set before changing the driver selection, launcher, settings
  or legacy ICDs. The error identifies which retained tools need restoration.
- **Retry an interrupted helper uninstall.** Removal could previously leave a
  valid selection with a missing launcher, causing both reinstall and uninstall
  to refuse. The two managed links are now checked independently, allowing setup
  to restore a missing link or removal to continue. Before deletion, each verified
  payload is moved into an unselected temporary directory. Interrupted cleanup
  cannot leave a partial immutable tool ID that blocks installing that version.
- **Preserve a replaced tool directory.** Uninstall previously followed a symlink
  substituted for `launcher-tools` and deleted the relocated files. Status,
  installation and removal now reject that layout before changing its contents
  or selection links. Existing partial staging/removal directories stay preserved.
- **Recognize actual launcher placement.** A cache-launcher path appearing after
  `%command%` is a game argument, not an existing wrapper. It no longer prevents
  the command generator from inserting the requested launcher.

The [driver guide](../../driver-cache-setup.md) and [DLL cache guide](../../shared-shader-cache.md)
explain recovery, retry and restoration requirements. The existing shared Mesa
store with separate Steam/Fossilize views remains appropriate; these fixes require
no extra service, global hook, cache backend or user-facing setup choice.

## Evidence and limits

The [new review record](../../data/cache-review2-20260914.json) pins the current source
and fresh tooling test results. The earlier review record is unchanged; its
runtime and tests are available at
[the reviewed source commit](https://github.com/daniel-h-0/bc250-fsr4-fork/tree/df5996d838a2cf65cd94dee9e2b6121ce9bc8e73).

Each of four isolated userspaces runs 27 cache-helper tests and 34 driver-installer
tests: Python 3.8/Debian Bullseye, 3.11/Debian Bookworm, 3.12/Alpine and 3.14/Arch.
All available tests pass, along with the independent read-only-mount fallback
check. Three minimal images lack a C compiler and skip the undefined-symbol ABI
fixture; that fixture passes on the host and Arch builder. Installer fixtures
mock the Vulkan probe, so these userspace results are not graphics-driver or
complete-distro qualification.

The new cases simulate removal stopping after either control link or partway
through payload cleanup, then retry removal or reinstall. They verify that a
relocated directory's bytes and selection links remain intact. Rollback/recovery
tests remove or modify previous launcher files, verify refusal without selection
changes, restore the files and successfully roll back. Installation paths with
spaces are included.

The checker continues to compare current cache preparation and launch functions
against the [measured game-to-game implementation](cache-setup-qualification.md).
No new gameplay, image-quality or loading/FPS result is claimed here. Published
RC10 downloads remain unchanged.
