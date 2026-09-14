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

## Update an installation

For an existing installation made with the original RC10 tools or the first
development launcher, run the **new download's** `scripts/driver.py install`
command above once, using the same `--prefix`. This updates its permanent tools.
The earlier installed command cannot update its own helper code.
When replacing an old `python3 .../scripts/driver.py ... run --` invocation,
remove that invocation from the text you give setup, retaining unrelated variables,
wrappers and game arguments. Otherwise the generated line will still depend on
the old extracted files. Set any custom shared-cache location with `install --cache-dir`.

After installing these reviewed tools, future compatible driver packages can be
installed through the permanent command:

```sh
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" install \
  /path/to/new-driver-archive.tar.gz --sha256 ARCHIVE_SHA256_FROM_SHA256SUMS
```

The installer verifies the complete archive before adopting its driver and
launcher tools. Updates preserve the cache preference and storage location;
rollback restores the previous driver, launcher and preference together. Old
archives without bundled-update support retain the current tools. If a future
package needs a newer installer, the command tells you to use the new download.

## Status and controls

Use the exact installed path printed by setup. For the example prefix above:

```sh
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" status
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" steam
```

`status` is read-only and explains the driver selection, cache preference, storage
and last launch preparation recorded for this user, which may belong to another
game or launcher. Directory presence does not verify writability or cache hits.
`steam` generates another complete launch-option line.
Pass `status --json` for structured diagnostics.

To bypass shared caching for one game, put `--no-shared-cache` after `run` and before
`--` in its launch options. That retains the private driver. Cache setup failures
also fall back to the game's original cache settings while retaining the selected
driver. The rest of the game command is unchanged.

Malformed cache settings also leave the selected driver and original cache
environment available for launch. Setup and rollback still preserve independently
edited settings instead of overwriting them.

To roll back the driver, installed launcher and cache preference together:

```sh
"$HOME/.local/share/bc250-fsr4-rc10/bc250-fsr4-run" rollback
```

First-install rollback removes that launcher. Remove its invocation from Steam
before launching again, and restore any provider/bridge files changed for the
driver route. Shader caches and retained driver/tool payloads remain available.
If an install or rollback was interrupted, use `recover` before another install
or rollback. If first-install rollback already removed the launcher, use a fresh
copy of these tools with the same prefix:

```sh
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-rc10" recover
```

The retained `launcher-tools/<tool-set-id>/driver.py` also supports that command
without the original download. Recovery restores the prior selection; it does
not finish an interrupted upgrade. Do not delete its transaction records.
Independent edits to managed launcher/settings files are preserved and reported.
Rollback and recovery also verify the previous launcher's complete tool set
before changing the selection. If retained tools are missing or modified, restore
them from their matching verified source/download before retrying; the error names
the affected directory. Keeping only the old driver library is insufficient.

Cache sharing still requires compatible driver/compiler inputs and paths visible
inside the game's launcher or sandbox. See the [cache guide](shared-shader-cache.md)
for DLL setup, diagnostics and advanced settings, and the
[qualification record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/cache-setup-qualification.md)
for observed reuse and its limits.
