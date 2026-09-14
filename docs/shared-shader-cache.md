# Shared shader caching on Linux

The optional launcher lets applications reuse compatible Mesa shader compilations.
RC10 includes the launcher as an opt-in feature. The launcher
works through Mesa's cache environment variables and ordinary user directories,
without distro-specific package managers, services or changes to Wine prefixes.

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

## Use

Keep `shared-cache.sh` and `shared-cache.py` together. The DLL ZIP supplies
them under `linux/`; the source and driver archives use `scripts/`. Inspect the proposed cache settings with:

```sh
sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" --show
```

`--show` creates only the launcher's own cache directories and links. To launch:

```sh
sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" -- ORIGINAL-COMMAND ARGUMENTS
```

For a Steam game, prepend the wrapper to its existing launch command, retaining
existing environment variables and arguments. For example, if it currently has
`WINEDLLOVERRIDES="winmm=n,b" %command%`, use:

```sh
WINEDLLOVERRIDES="winmm=n,b" sh "/path/to/bc250-fsr4/scripts/shared-cache.sh" -- %command%
```

Opt each application in deliberately. This helper does not edit Steam or Heroic
settings or install a global launcher hook. If another wrapper is already
present, preserve its ordering and verify the effective cache path inside the
launched process.

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

## Undo

Remove the wrapper from the launch command. The application's previous cache
locations and files are still available. Keep the shared directory while any
application is using it. No prefix, save or game-file restoration is necessary.

## Qualification scope

Filesystem and launch tests pass in Debian Bullseye/Python 3.8, Debian
Bookworm/Python 3.11, Alpine/Python 3.12 and the Arch-based builder/Python 3.14.
They cover argument boundaries, read-only storage, missing Python, concurrent
preparation, late Steam cache creation and existing-cache preservation. These
are isolated userspace tests, not full graphics qualification of those distros.

The separate [RC10 development record](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/rc10-development.md) records actual BC250
GPU cache-reuse measurements, compiler experiments and their limits. Native
Windows and proprietary Vulkan drivers are outside this launcher's scope.
