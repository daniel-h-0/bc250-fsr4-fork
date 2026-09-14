# Cache installer and documentation review

The September 14 review of `67870df` found fixes in setup, updates and diagnostics.
The shared Mesa store, per-game read-only Steam views, write probes and launch
fallback retain their measured preparation code. Shader sources and binaries
are unchanged. No replacement cache architecture is needed for these fixes.

## Corrections

- **Optional settings cannot block a game.** A JSON list or a missing optional
  directory field could previously stop the driver launcher. Settings now receive
  shape validation and defaults. Invalid settings leave the private driver
  selected and the original cache environment intact. Independent edits are still
  preserved when an installer or rollback would overwrite them.
- **Installed updates update the tools too.** Previously, an installed updater
  always staged its own helper files, ignoring newer bundled tools. Compatible
  archives now declare their launcher interface and supply the complete verified
  tool set and license. Updates retain old tools for exact rollback. Legacy
  archives keep the maintained caller's tools; an unsupported interface asks the
  user to run the new download's installer before changing the selection.
- **Status reports what was checked.** Directory presence, writability and game
  cache hits are distinct. The last-launch record is explicitly per user and shows
  its Steam AppID when available. Malformed records give readable diagnostics;
  invalid installed-tool metadata produces a failing status instead of a traceback
  or a successful installation result.
- **Paths and recovery are clearer.** Dedicated helper installation paths are
  normalized before validation. The older convenience launcher now uses a POSIX
  shell without requiring Bash. An interrupted first rollback can be recovered
  with the retained tools after its launcher has been removed.

The [driver guide](driver-cache-setup.md) covers upgrading earlier installers,
verified bundle updates and interrupted rollback. The [DLL cache guide](shared-shader-cache.md)
explains updating from a new download while keeping the permanent path. Both
explain removing an older portable invocation when migrating; nesting that old
invocation would retain a dependency on its downloaded files.

## Validation and limits

The [review record](data/cache-review-20260914.json) pins the reviewed runtime and
test sources. Each of four isolated userspaces runs 24 helper tests and 33 driver
installer tests: Python 3.8/Debian Bullseye, 3.11/Debian Bookworm, 3.12/Alpine and
3.14/the Arch builder. All available tests pass. The three minimal images lack a
C compiler and skip one undefined-symbol ABI fixture; that check passes on the
host and Arch builder. Each userspace also passes an independent read-only-mount
fallback check. Driver installer fixtures use a mocked Vulkan probe; these tests
do not qualify graphics drivers on those distributions or make the glibc driver
compatible with Alpine's musl.

Update tests use distinguishable old and new tool payloads, verify every installed
tool and its license, and check exact rollback. The interruption test leaves a
pending first rollback after launcher removal, then runs recovery from the retained
tool set. Invalid-settings tests execute a child process and verify the original
Mesa settings plus the selected private ICD.

The existing [Control-to-System-Shock reuse evidence](cache-setup-qualification.md)
keeps its original hashes and scope. Its full tools are retained under
`legacy/cache-setup-tools`; a parsed-code comparison continues to check the current
cache preparation and launch functions against that measured baseline. This review
adds no new gameplay, loading-time, image-quality or FPS measurements. Published
RC10 downloads remain unchanged.
