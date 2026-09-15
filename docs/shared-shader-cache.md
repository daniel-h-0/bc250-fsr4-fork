# Shared shader caching on Linux

Install the optional launcher once, then use it with the games you choose.
It shares compatible Mesa shader compilations and preserves existing Steam caches.

These setup and status commands ship in **RC11**. The original RC10 archives
retain their earlier helper and [original instructions](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/shared-shader-cache.md).

## DLL users: install once, copy one launch command

From the RC11 DLL package's extracted directory, run:

```sh
sh linux/shared-cache.sh install
```

From a source checkout, use `sh scripts/shared-cache.sh install` instead.
In a terminal, setup asks for the game's current Steam launch options. Paste the
entire line, or press Enter if empty. Then copy the complete generated line back
to Steam. It preserves your existing settings and inserts the cache launcher just
before `%command%`.

Setup installs both helper files together and prints a permanent absolute launcher
path. You can move or delete the downloaded folder afterward. No sudo, system
service, PATH edit, cache backend choice or global game enrollment is needed.

For another game, run the installed command with `steam` and paste its existing
launch options when asked. For automation, use `install --launch-options 'TEXT'`
or `steam --launch-options 'TEXT'`; noninteractive calls otherwise assume empty
launch options. Keep exactly one unquoted `%command%` placeholder.

To update the DLL cache helper, run `install` from the **new download**, using
the same `--prefix` if you chose a custom installation directory. The permanent
launcher path stays the same, so existing game launch options keep working.

If switching from the old portable RC10 wrapper, remove that old wrapper from
the launch-option text before giving it to setup. Retain unrelated variables,
wrappers and game arguments; carry any custom `--cache-dir` option onto the new
cache launcher. Keeping the old invocation would still require its downloaded
files, even after the new launcher is installed.

**First use can still compile shaders.** Existing ordinary caches are not imported,
and hits in a game's preserved Steam cache do not automatically fill the shared
store for other games. Keep the new shared cache between launches.

## Driver users: caching is part of the installed launcher

The [updated driver installer](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/driver-cache-setup.md)
installs one permanent driver launcher with shared caching enabled by default for
new command-line installations. Use its generated launch options; a separate cache
wrapper is unnecessary. Updates retain the selected cache preference. The driver
and its cache preference are covered by the same upgrade/rollback transaction.

## Check status or undo

Use the exact installed command printed by setup:

```sh
/path/to/installed/bc250-fsr4-cache status
/path/to/installed/bc250-fsr4-cache doctor
```

`status` is read-only. It shows storage, the size limit and the last recorded launch
preparation or fallback **for this user**, which may be from another game or
launcher. A present directory is not a successful write check. `doctor` creates
and removes small temporary files to test
writing in the current environment. Neither command claims a game used the cache
or observed hits. Steam or a sandbox can supply a different launch environment;
the last-launch record includes the view prepared for that invocation. Add `--json`
for structured diagnostics.

Damaged diagnostic records produce a readable explanation. Invalid installed-tool
metadata is reported as an installation error and gives a failing status; it is
not silently repaired or mistaken for a working installation.

If storage is unwritable or a write fails, the game launches with its original
cache settings and the reason is recorded when possible. This is a check at launch,
not a guarantee that space or permissions cannot change later.

To stop using shared caching, remove only the cache wrapper from the game's launch
options. Leave existing environment variables, other wrappers and arguments intact.
Add `--disable` before `--` for a temporary bypass. `uninstall` removes the managed
cache launcher and retains shader caches. Driver users instead use their installed
driver launcher's `--no-shared-cache` switch or its rollback command.

If helper removal is interrupted, rerun `uninstall --prefix /original/install/path`
from the download; `install` from the download can also restore missing launcher
links. Removed tool sets are detached before file cleanup, so a partial deletion
does not block reinstalling the same version. Unselected partial staging/removal
directories are preserved. If the managed `launcher-tools` directory has been
moved or replaced by a symlink, setup and removal refuse to follow it; restore
the original directory layout before retrying.

For Heroic on Linux, add the installed launcher path in the game's **Wrapper**
field and `--` in **Arguments**. For the integrated driver launcher, use `run --`
as its arguments. Preserve existing wrappers and use the launcher inside the
environment that runs the game; do not put Steam's `%command%` in Heroic.
These fields follow [Heroic's wrapper interface](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/blob/main/src/frontend/screens/Settings/components/WrappersTable.tsx).

## What is shared

New compilations go into a shared, size-limited Mesa cache. Mesa still keys each
entry by its driver/compiler identity, GPU, architecture and compilation inputs.
Putting two games in the same cache directory does not make incompatible shader
binaries interchangeable. Driver or translator updates can require compilation
again; shaders with different pipeline layouts or options may not be reusable.
This affects all Mesa shaders in applications that opt in, including non-FSR work.

Each application keeps a separate view of its existing Steam/Fossilize caches.
Those files remain in place and are opened read-only. Newly compiled entries use
the shared cache. An entry read from an existing Fossilize cache is not
necessarily copied into the shared cache. Wine prefixes, saves and vkd3d's
application cache remain separate.

Existing multi-file and Mesa-DB directories are preserved but are not imported
into the new shared store. Opting an application in can therefore require an
initial compilation. Fossilize reads retain Mesa's source-count limit; if an
application already reserves every read-only slot, the helper does not replace
one of its sources to add the local `foz_cache`. A dynamic read-only list also
keeps its reserved slots.

The default uses Mesa's established multi-file backend. `--backend database`
selects the optional Mesa-DB backend for a Mesa build that supports it. Existing
Fossilize reuse also requires Mesa's combined read/write plus read-only Fossilize
support. An older or differently configured driver may ignore an unsupported
option and compile again. The launcher does not override compatibility keys.
See [Mesa's cache variables](https://docs.mesa3d.org/envvars.html#mesa-shader-cache-dir).

## Portable use and advanced storage options

Keep `shared-cache.sh` and `shared-cache.py` together. The DLL ZIP supplies
them under `linux/`; the source and driver archives use `scripts/`. Installation
keeps them together automatically. For portable use, keep both files in a permanent
directory yourself. Inspect the proposed environment without changing it:

```sh
sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" --show
```

`--show` is now read-only. To launch without installation:

```sh
sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" -- ORIGINAL-COMMAND ARGUMENTS
```

For a Steam game, prepend the wrapper to its existing launch command, retaining
existing environment variables and arguments. For example, if it currently has
`WINEDLLOVERRIDES="winmm=n,b" %command%`, use:

```sh
WINEDLLOVERRIDES="winmm=n,b" sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" -- %command%
```

The `steam` command handles existing launch text for you. It prints the result;
it does not edit Steam or Heroic settings or install a global launcher hook.

The default storage is `$XDG_CACHE_HOME/bc250-fsr4`, falling back to
`$HOME/.cache/bc250-fsr4`. Relative `XDG_CACHE_HOME` values are ignored as required
by the [XDG specification](https://specifications.freedesktop.org/basedir/latest/).
An immutable distro's `/var/home/...` layout needs no special handling. A custom
location is available through `--cache-dir "/absolute/path"`; use writable local
storage with symlink support. `MESA_SHADER_CACHE_MAX_SIZE` is retained when set,
otherwise the default is 10 GiB per architecture. The directory is disposable
cache data, and Mesa manages entry eviction; it is not a strict quota on every
file below the helper's root.

## Sandboxes and fallback behavior

The script, Python interpreter and cache paths must be visible inside the
process's filesystem namespace. Native Steam, Flatpak Steam, Heroic and custom
containers can expose different paths and different Mesa builds. Set up access
within the chosen launcher; this helper does not grant Flatpak permissions or
bypass a sandbox. Reuse across separate sandboxes requires both to see the same
storage and compatible compiler inputs. Network filesystems are unqualified.

The Python launcher is tested with Python 3.8 through 3.14. The shell bootstrap
runs the original command if Python or the adjacent helper is unavailable.
If cache preparation fails, the Python helper likewise launches with the
original environment and prints the reason. An explicit cache-disable setting
is respected. Existing cache files or conflicting links are never replaced.
Simultaneous launches can prepare the same cache view, and Steam caches created
between launches are picked up on the next launch.

These fallbacks keep the application launch intact; they do not guarantee a
cache hit or verify every possible Mesa build. The wrapper cannot catch a later
application/driver failure or make an inaccessible cache path accessible inside
a child sandbox.

## Qualification scope

The updated helper's [qualification](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4/docs/cache-setup-qualification.md)
covers its setup/status/fallback tests and real Control-to-System-Shock cache reuse.
The following original RC10 results remain separate historical evidence.

Filesystem and launch tests pass in Debian Bullseye/Python 3.8, Debian
Bookworm/Python 3.11, Alpine/Python 3.12 and the Arch-based builder/Python 3.14.
They cover argument boundaries, read-only storage, missing Python, concurrent
preparation, late Steam cache creation and existing-cache preservation. These
are isolated userspace tests, not full graphics qualification of those distros.

The separate [RC10 development record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/rc10-development.md) records actual BC250
GPU cache-reuse measurements, compiler experiments and their limits. Native
Windows and proprietary Vulkan drivers are outside this launcher's scope.
