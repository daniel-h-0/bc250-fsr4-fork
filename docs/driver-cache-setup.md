# Optional Linux driver installation

For the usual install, [replace OptiScaler's DLL](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md).
This private driver is an alternative for an existing AMD-provider integration.
Read the [provider requirements](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-rc10.md)
first: installing the driver alone does not enable FSR4.

RC11 supplies the same driver binary as RC10 with newer installation tools.
These are the current install/update/recovery commands.

## Install once

Download the RC11 Linux driver archive and `SHA256SUMS` from the
[release](https://github.com/daniel-h-0/bc250-fsr4-fork/releases/tag/v4.0.0-rc11).
Verify with `sha256sum --ignore-missing -c SHA256SUMS`, extract it, and run from
its directory as your desktop user, without sudo:

```sh
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-driver" install \
  /path/to/bc250-fsr4-v4.0.0-rc11-linux-glibc236-x86_64.tar.gz \
  --sha256 ARCHIVE_SHA256_FROM_SHA256SUMS
```

Replace the last argument with the archive's 64-character hash. For an upgrade,
use your **existing prefix**, even if its name includes `rc10`.

Paste the game's entire existing Steam launch-options line when prompted.
Copy the generated line back to Steam. Setup installs a permanent
`bc250-fsr4-run` command in the prefix; the extracted download can then be removed.
Noninteractive setup accepts `--launch-options 'EXISTING TEXT'`.

Shared caching defaults on for new CLI installs; updates preserve the previous
choice. Use `install --no-shared-cache` or `install --shared-cache` to set it
explicitly. Existing Steam caches are preserved, not imported. First use may
compile again. Direct `VK_DRIVER_FILES=...` selection bypasses this cache helper.

## Update an installation

Original RC10/development tools must be updated using the **new download's**
install command above. Remove the old portable wrapper from the launch text
passed to setup, retaining unrelated variables, wrappers and game arguments.
Use `install --cache-dir` if choosing a custom cache location.

Once RC11 tools are installed, compatible packages can be updated through the
permanent launcher:

```sh
"$HOME/.local/share/bc250-fsr4-driver/bc250-fsr4-run" install \
  /path/to/new-driver-archive.tar.gz --sha256 ARCHIVE_SHA256_FROM_SHA256SUMS
```

Updates verify the archive and bundled tools and preserve cache preferences.
Older archives without tool-update support retain the current tools. If a newer
installer is needed, use the download named by the error.

## Status and controls

Use the actual installed prefix in these commands:

```sh
"$HOME/.local/share/bc250-fsr4-driver/bc250-fsr4-run" status
"$HOME/.local/share/bc250-fsr4-driver/bc250-fsr4-run" steam
```

`status` is read-only; `--json` gives structured output. It reports selection
and the last cache preparation for this user, not proof of cache hits.
`steam` generates launch options for another game.

For one launch without shared caching, use `run --no-shared-cache --` in the
installed command. Cache preparation failures likewise retain the selected
driver and original cache settings. Driver and cache paths must be visible
inside the game's launcher/sandbox.

## Rollback and interrupted operations

```sh
"$HOME/.local/share/bc250-fsr4-driver/bc250-fsr4-run" rollback
```

Rollback restores the prior driver, launcher and cache preference together.
First-install rollback removes the launcher: remove its invocation from Steam
and restore any provider/bridge files you changed. Caches and retained payloads
are kept. Game saves and prefixes are not removed.

If install or rollback was interrupted, run `recover` before another operation.
If the launcher is missing, use the matching prefix with a fresh download:

```sh
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-driver" recover
```

The retained `launcher-tools/<tool-set-id>/driver.py` can also recover it.
Recovery restores the prior selection; it does not finish an upgrade. Keep the
transaction records and complete retained tool sets. Modified/missing managed
files are reported rather than overwritten; restore the matching verified
files before retrying.

[Cache settings and diagnostics](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/shared-shader-cache.md) ·
[Provider setup and validation](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-rc10.md)
