# Driver installation with shared caching

This is the updated installer in the development source after RC10. The published
RC10 driver binary works with these tools; its original archive still contains
the older installer. Shader/provider requirements remain in the
[RC10 driver guide](driver-rc10.md).

## Install once

Use the updated `scripts/driver.py` and its adjacent helper files from this source
tree. Download the verified driver archive and checksum, then run:

```sh
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-rc10" install \
  /path/to/bc250-fsr4-v4.0.0-rc10-linux-glibc236-x86_64.tar.gz \
  --sha256 ARCHIVE_SHA256_FROM_SHA256SUMS
```

When run in a terminal, setup asks for the game's existing Steam launch options.
Paste the entire line, or press Enter if empty. Copy the resulting complete line
back to Steam. Existing environment variables, wrapper ordering and game arguments
are preserved. For noninteractive setup, pass `--launch-options 'EXISTING TEXT'`.

Setup installs a permanent **`bc250-fsr4-run`** command inside the chosen directory.
It selects the private driver and prepares shared caching in one launch. The
original extracted download can be moved or deleted afterward. Use the printed
absolute command; no PATH or system-wide installation is required.

Shared caching defaults to **on for a new command-line installation**. Updates
retain an existing choice; `install --shared-cache` or `install --no-shared-cache`
explicitly changes it. Legacy runtime installers calling the driver tools keep
their earlier cache behavior. Existing direct `VK_DRIVER_FILES=...` launch options
continue to select the driver but bypass the new cache setup: use the installed
launcher to enable both.

The first enrolled game may still compile shaders. Keep the shared cache between
launches. Existing Steam caches are preserved; ordinary old caches are not imported.

## Status and controls

Use the exact installed path printed by setup. For the example prefix above:

```sh
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" status
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" steam
```

`status` is read-only and explains the driver selection, cache preference, storage
and last launch preparation. `steam` generates another complete launch-option line.
Pass `status --json` for structured diagnostics.

To bypass shared caching for one game, put `--no-shared-cache` after `run` and before
`--` in its launch options. That retains the private driver. Cache setup failures
also fall back to the game's original cache settings while retaining the selected
driver. The rest of the game command is unchanged.

To roll back the driver, installed launcher and cache preference together:

```sh
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" rollback
```

First-install rollback removes that launcher. Remove its invocation from Steam
before launching again, and restore any provider/bridge files changed for the
driver route. Shader caches and retained driver/tool payloads remain available.
If an install was interrupted, use `recover` before another install or rollback.
Independent edits to managed launcher/settings files are preserved and reported.

Cache sharing still requires compatible driver/compiler inputs and paths visible
inside the game's launcher or sandbox. See the [cache guide](shared-shader-cache.md)
for DLL setup, diagnostics and advanced settings, and the
[qualification record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/cache-setup-qualification.md)
for observed reuse and its limits.
