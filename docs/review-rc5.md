# RC5 code and documentation review

RC5 corrects installation, recovery and Steam utility behavior while reusing
RC4’s exact portable driver and RC3’s upstream components and game preset.
Use the [current setup](legacy-rc6.md#start-a-steam-game) and
[update guide](upgrading-rc2.md).

## Corrections

- Explicit v3 migration now creates a reversible private-driver transaction
  even when a compatible driver is already present. Earlier unified tools
  could silently skip that request. `--driver system` rejects migration before
  mutation; ordinary installs can still reuse a verified system driver.
- Private-driver selections and transaction directories reject external or
  replaced symlinks. Rollback preserves independently replaced records.
- Python 3.11’s compatibility extractor rejects hardlinks that reach an
  external file through an existing directory symlink and preserves existing
  directory permissions. Normal installation extracts into fresh staging;
  the reproduced hardlink case required a nonempty destination.
- Steam path conversion, prefix utilities and zero-ID calls do not receive
  game upscaler injection. The pinned GE entry point runs upscaler setup before
  dispatching its verb, so checking a nonempty game ID alone was insufficient.
- System-package records now carry the Mesa source-manifest hash. Rebuilt
  drivers cannot establish runtime compatibility from version labels alone;
  the known qualified system binary remains reusable. `doctor` also reports
  unfinished component transactions instead of a healthy result.
- Portable/SteamOS build provenance now pins imported builder and extraction
  helpers. Editing an imported build recipe invalidates a completed build.
- Private offline runtime packaging stages complete output before publication,
  refuses an existing archive or checksum, and preserves concurrent output.
- Current installation guides agree on Python 3.11, portable driver selection,
  RC5 setup and rollback behavior. Repository checks now recompute the historical
  FFX cost reconstruction and reject stale setup filenames in current guides.

## Review coverage and limits

The review follows the maintained launch/install/rollback paths, archive and
component identities, source preparation/build/packaging, CI, legacy transaction
recovery and maintained documentation. Mesa review focuses on source identity,
profile/subgroup gates, fallback paths, resolution-store repair and cache keys.
The archived v3 tools retain their historical bytes and remain inactive.

The driver source manifest, all three Mesa patches and fifteen resulting files
remain unchanged. RC5 downloads the same portable archive from the immutable
RC4 release; its ELF SHA256 remains
`13163d1350d346f54d58b82863e5fc2f31ddfab0a0dd6495886c3fec286315d2`.
The earlier [correctness and ABI checks](rc4-compatibility.md),
[renderer observations](runtime-qualification.md) and
[performance results](performance.md) retain their original scope and identity.

Process-interruption recovery is tested; abrupt power-loss durability is not.
A file fsync alone does not persist its containing directory entry; see
[Linux fsync semantics](https://man7.org/linux/man-pages/man2/fsync.2.html).
Build provenance identifies recorded inputs, without claiming a hermetic host
toolchain. No separately booted SteamOS, Flatpak or new performance qualification
is claimed by this maintenance release.

## Validation

All 186 tests pass on the host’s Python 3.14 and the actual Debian Python 3.11
package. Seven targeted regression cases fail against RC4 and pass with the
corrections. Fresh source preparation reproduces all fifteen pinned Mesa files;
all 360 performance rows and the historical FFX cost reconstruction agree with
the published data. Lint, formatting, source hashes and documentation links pass.

A real isolated RC4 → RC5 → RC4 → RC5 cycle passes with paths containing spaces,
including explicit migration with an already selected private driver, exact ICD
and selection rollback, and reinstallation from retained runtime files without
a download cache. The migration uses a legacy-shaped ICD backed by a real
Vulkan library; it is not a new v3 performance baseline.

A Windows probe imports the native OptiScaler WinMM proxy and creates a D3D12
device through RC5 inside Steam Runtime 4 with a SteamOS 3.8 graphics provider.
The result is `HRESULT=0x00000000`; maps and hashes confirm the exact portable
driver and native proxy. This uses a private virtual X display, no game or
swapchain. The [machine-readable record](data/runtime-v4.0.0-rc5.json) records
identities and scope.
