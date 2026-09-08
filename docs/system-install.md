# System installation on Arch / CachyOS

**Already running the rc1 system packages?** The rc3 shared runtime reuses
that compatible driver; follow the [rc1 transition guide](upgrading-rc1.md).
There is no system-package replacement needed for that transition.

This page describes advanced driver-only operations. Here, `bc250-fsr4`
means the installed system-package helper; **`./bc250-fsr4` in a current
setup directory** is the separate unified driver/runtime interface.

This route packages the v4 library as `vulkan-radeon`, preserving the exact
base package's dependencies and other files. A second package,
`bc250-fsr4-v4`, installs `bc250-fsr4 status`, a post-update identity check and
an exact original-package rollback. `bc250-fsr4-driver` remains available as a
compatibility alias for the earlier local integration. There is no loose overwrite of an
untracked `/usr/lib` file and no global ICD environment override.

This is presently a **64-bit Mesa 26.2.2** integration. It leaves 32-bit RADV,
OpenGL, firmware, kernels, Proton, display settings and GPU tuning alone.
Already-running compositor/desktop processes keep the driver they loaded;
new game processes use the new package. A reboot is not required for game
proof. Keep your normal distribution recovery boot entry.

The qualified game runtime is **FSR 4.1.1 INT8, not the newer 4.1.1b mod**.
Do not co-install 4.1.1b with this setup; installing the system driver does
not remove a competing game-local runtime. Follow the
[runtime compatibility guidance](games.md#runtime-compatibility) before switching.

## Generate packages for your installation

Use the maintained `v4` checkout's packaging tools; see
[release identities](releases.md) for their relationship to the original rc1
driver. First build or obtain a compatible v4 archive using the main README. Locate
the exact installed `vulkan-radeon` package in `/var/cache/pacman/pkg/` and
verify its version with `pacman -Q vulkan-radeon`. If the exact archive is
missing, retrieve it from your distribution's trusted package archive. Do not
substitute a similarly named package or an archive from another distribution.

```sh
python3 scripts/system-package.py build \
  dist/bc250-fsr4-v4.0.0-rc1-cachyos-x86_64.tar.gz \
  --base-package /var/cache/pacman/pkg/EXACT-vulkan-radeon-PACKAGE.pkg.tar.zst \
  --output .work/system-packages
```

The builder verifies the release, checks the library against this host with
`vulkaninfo`, requires a matching Mesa version, and retains the complete base
package. Inspect `.work/system-packages/PKGBUILD` and `packages.json`; the
package keeps the base distribution version so the next normal repository
upgrade supersedes it. Its description and `bc250-fsr4 status` identify v4 by
release and driver SHA256. It is a local derivative of that base, not a
repository-wide Mesa upgrade.

Exit Steam and all games, then install the reviewed pair:

```sh
python3 scripts/system-package.py install .work/system-packages
bc250-fsr4 status
vulkaninfo --summary
```

The install command checks that the live package **and live library hash**
match the retained base before invoking your normal pacman confirmation.
It will not discard an unrecorded local modification. Existing private v3/v4
`VK_DRIVER_FILES` launch options override the system driver: remove those
options when switching a game to this route. Check
the real game process mapping; a successful desktop `vulkaninfo` alone does
not prove which driver the game loaded.

The earlier local development package `bc250-fsr4-integration` conflicts with
this helper because its old status metadata would become misleading. Preserve
its exact driver/helper archives and remove that old helper during a deliberate
migration. This does not apply to ordinary upstream v3 private installations.

## Updates and rollback

The package replacement procedure below applies when changing the driver ELF,
not when adding or updating the shared Steam runtime with an unchanged driver.

`bc250-fsr4 status` checks the installed driver's SHA256, rather than assuming
that a package name proves v4 is active. The pacman hook reports when a later
distribution update replaces v4. It never blocks package upgrades, rewrites a
new driver, adds `IgnorePkg`, or freezes Mesa. Rebase and qualify v4 against a
new Mesa version before rebuilding the package; do not force the old binary
back over a newer Mesa installation.

For a v4-to-v4 update on the same Mesa base, first prepare and review the new
package pair using the retained **original distribution package**, available
at `/usr/share/bc250-fsr4-v4/rollback/base.pkg.tar.zst`. Close Steam and games,
roll back the active v4 package to that original base, then install the new
reviewed pair. The install command requires the live base library to match its
rollback archive; it deliberately refuses to replace an active v4 driver
directly. Keep both generations of package output until the update is verified.

With Steam and games closed:

```sh
sudo bc250-fsr4 rollback
```

Rollback validates the original archive and reinstalls it with pacman. It
refuses to downgrade if the current driver has changed since installation.
The helper remains installed but reports inactive. You may remove it with
`sudo pacman -R bc250-fsr4-v4`, or reinstall the v4 package pair with the same
reviewed install command to activate v4 again. 32-bit RADV is untouched in
both directions.

Retain the generated directory and original package archive until rollback
and reinstallation have been verified. A package directory built for another
host or distribution is not a substitute for this machine's exact base.
When requesting help, include the package versions, `bc250-fsr4 status`
output and actual driver SHA256 after reviewing the output for private paths.

On other distributions use the private install, or supply a native package
integration with equivalent ownership, compatibility and rollback checks.
Copying the Arch library into another distribution's `/usr/lib` is unsupported.
