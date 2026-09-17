# Optional shared shader cache

[Install across your games](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/optiscaler-client.md) and
[manual DLL installation](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/beginner-guide.md) need no cache helper. Use this RC11 tool only to share compatible Mesa shader
compilations across selected Linux games. It works with per-game DLL copies
and the normal driver; it does not change the FSR model or per-frame optimizations.

## DLL users: install once, copy one launch command

From the extracted RC11 DLL download:

```sh
sh linux/shared-cache.sh install
```

From a source checkout, use `sh scripts/shared-cache.sh install` instead.
Paste the game's **entire existing Steam launch-options line** when prompted,
then copy the generated line back into Steam. Setup preserves its variables
and arguments and inserts the helper before `%command%`.

The printed launcher path is permanent; the extracted download can be removed.
Run that installed command with `steam` to generate options for another game.
Only games using the wrapper opt in. For noninteractive use, pass
`--launch-options 'EXISTING TEXT'`; otherwise noninteractive input defaults to
empty. Keep exactly one unquoted `%command%`.

**First use may compile again.** Existing caches remain intact but are not
imported into the shared store. Keep normal caches enabled.

## Update

Run `install` from the new download, using the same `--prefix` if you chose a
custom location. The permanent launcher path stays the same.

When replacing an old portable RC10 wrapper, remove that wrapper from the text
you give setup, keeping other options. Carry forward any custom `--cache-dir`.
The original RC10 archives retain their
[older instructions](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/shared-shader-cache.md).

## Driver users: caching is part of the installed launcher

The [optional driver installer](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-cache-setup.md)
already supplies a cache-capable launcher. Use its generated command without
adding this separate wrapper. New CLI driver installs default to caching on;
updates preserve the previous choice.

## Check status or undo

Use the installed path printed by setup:

```sh
/path/to/installed/bc250-fsr4-cache status
/path/to/installed/bc250-fsr4-cache doctor
```

| Command | Meaning |
| --- | --- |
| `status` | Read-only configuration and last launch preparation for this user; the record may belong to another game. |
| `doctor` | Creates and removes small files to check current write access. |
| `steam` | Prints launch options for another game. |
| `uninstall` | Removes the managed helper; retains shader caches. |

`status` and `doctor` accept `--json`. Neither proves a game got cache hits,
and a sandbox can expose a different filesystem view from the one you checked.
Invalid installed-tool metadata is reported as an installation error.

To stop using the helper, remove only its wrapper from the game's launch options.
For a temporary bypass, add `--disable` before `--`. Driver users instead use
`run --no-shared-cache --` in their installed driver command.

If uninstall is interrupted, retry from the download with
`uninstall --prefix /original/install/path`; `install` can also restore missing
launcher links. Preserve transaction files. If `launcher-tools` was moved or
replaced by a symlink, restore its original layout before retrying.

## What is shared

New compilations go into a shared Mesa cache. Mesa's GPU, driver/compiler and
pipeline keys still decide whether an entry is reusable. This applies to all
Mesa shaders in opted-in games, including non-FSR work. Updates can invalidate
previous compilations.

Existing Steam/Fossilize sources remain read-only and retain their source-count
limits. Reading an old entry does not necessarily copy it into the shared store.
Old multi-file/Mesa-DB caches, vkd3d's application cache, Wine prefixes and saves
are not merged or moved.

## Portable use and advanced storage options

<details>
<summary>Portable wrapper, storage and Heroic</summary>

Keep `shared-cache.sh` and `shared-cache.py` together in a permanent directory.
The DLL archive stores them under `linux/`; source/driver archives use `scripts/`.

```sh
sh /path/to/shared-cache.sh --show
sh /path/to/shared-cache.sh -- ORIGINAL-COMMAND ARGUMENTS
```

`--show` is read-only. The installed `steam` command avoids manually composing
the wrapper with existing Steam options. It prints the result; it does not edit
Steam or Heroic configuration.

| Setting | Default / use |
| --- | --- |
| Storage | `$XDG_CACHE_HOME/bc250-fsr4`, or `$HOME/.cache/bc250-fsr4`; relative XDG paths are ignored. |
| `--cache-dir /absolute/path` | Writable local storage with symlink support. |
| Size | Existing `MESA_SHADER_CACHE_MAX_SIZE`, otherwise 10 GiB per architecture; Mesa evicts entries, not a strict quota on the whole helper directory. |
| Backend | Mesa multi-file; `--backend database` selects Mesa-DB when supported. |

Existing Fossilize reads require a Mesa build supporting combined read/write
and read-only Fossilize caches. Unsupported options may be ignored, causing
recompilation. [Mesa cache settings](https://docs.mesa3d.org/envvars.html#mesa-shader-cache-dir).

In Heroic, put the installed cache launcher in **Wrapper** and `--` in its
**Arguments**. For the driver launcher, use `run --`. Preserve other wrappers;
Heroic does not use Steam's `%command%` placeholder.

</details>

## Sandboxes and fallback behavior

The launcher, Python and cache paths must be visible where the game runs.
Flatpak/container access is configured in the launcher or sandbox; this helper
does not grant permissions. Sharing across sandboxes also needs compatible
compiler inputs. Network filesystems are unqualified.

Missing Python/helper files or failed cache preparation leave the game using its
original launch command and cache settings. Explicit cache-disable settings are
respected. Conflicting files/links are preserved. Concurrent preparation and
Steam caches created between launches are supported. These launch-time checks
cannot prevent a later game/driver failure or guarantee a cache hit.

## Qualification scope

[Cache qualification](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/legacy/research/cache-setup-qualification.md)
records Control-to-System-Shock reuse; System Shock reached its menu.
Filesystem/launch tests span Python 3.8–3.14 in four isolated Linux userspaces,
not complete distro graphics qualification. The
[RC10 record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/rc10-development.md)
retains the measurements. Native Windows and proprietary Vulkan drivers are
outside this helper's scope.
