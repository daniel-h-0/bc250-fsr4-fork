# OptiScaler Client integration

User instructions: [Install across your games](../../docs/optiscaler-client.md).
Project build: **1.0.7-bc250.2**, Linux x64, based on
[OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client) by
[Agustín Montaña (Agustinm28)](https://github.com/Agustinm28) and contributors,
under GPL-3.0-or-later. The upstream desktop interface, scanner and component
services remain the foundation of this build.

We pin upstream Client 1.0.7 at `dd534b7d1cb8a0edf174a6917f5179791603d364`.
BC250 adds the dedicated FSR4 installation/update/restore screen, common FFX/INT8
settings, dependency pins and release packaging. BC250 maintains these changes
and handles support for this build. [Component credits and licenses](../../THIRD_PARTY.md#optiscaler-client-addon).

## Build

Requirements: Python 3.11+, .NET 10 SDK, `bsdtar`, network access to the pinned
sources and NuGet. From the repository root:

```sh
python3 scripts/package-opticlient.py --dotnet /path/to/dotnet \
  --dll-zip /path/to/bc250-fsr4-dll-4.0.0-rc11-docs2.zip
```

Use a fresh `--work` directory for another build. The output under
`dist/opticlient/` contains the self-contained application, unchanged RC11 ZIP,
complete modified client source, licenses, dependency pins and checksums.
End users need no SDK. At first launch a progress window downloads OptiScaler,
OptiPatcher and the DLSS helper from their hash-pinned upstream locations. A
completed setup works offline. The client archive does not redistribute those
adapter/helper binaries.

The packaged build pins `Tmds.DBus.Protocol` to its 0.21.3 backport and retains a
NuGet lock file; the remaining client dependencies follow the pinned upstream
project. The SDK used for qualification and CI is 10.0.401.

`prepare.py` checks the exact upstream integration points, connects the library
buttons to the BC250 screen and isolates application data under
`OptiscalerClient-BC250`. The project build's application update check is disabled;
updates to this build come from BC250 FSR4. The upstream scanner and library view
remain responsible for game discovery. Game files change only when games are selected
and an installation or restore action is invoked.

## Ownership and supported setup

- `src/Bc250StartupWindow.cs` retrieves and verifies first-run dependencies.
- `src/Bc250Window.cs` provides import, game selection, installation/update and
  restore/recovery actions. Results stay separate for each game.
- `src/Bc250RouteService.cs` supplies common FFX/INT8 settings and the fresh-install
  setup for any compatible 64-bit Windows game, with no title whitelist or
  per-game overrides. Existing supported local configurations retain
  their input/spoofing settings; subsequent updates touch only the FSR DLL.
- `src/Bc250Transaction.cs` owns the file changes made by this route. Durable
  pending records and file snapshots precede writes; replacements use temporary
  files and rename, with hash verification and recoverable commit state.

The BC250 receipt and original files live under `BC250/games/<path-hash>/` in the
client's application data. They are separate from upstream Client's installation
manifests and the project's retired RC6 runtime. All library install/manage
buttons in this build enter the same BC250 screen, including for existing games.
Use this screen to update or restore files it owns.

Fresh installations use `dxgi.dll`, upstream input/spoofing defaults and the common
FFX/INT8 preset. The scanner locates the executable directory; an ambiguous or
non-x64 selection asks the user to choose the executable through Add Manually.
The installation row displays that path before files change. Existing adapters
must match the pinned OptiScaler proxy hash. Relative game-local DLL overrides
are resolved; absolute/shared paths and symbolic links use the manual route.
Native game DLL replacements also remain manual in this build. Scan results
alone are not a game-compatibility claim.

The client displays Linux loading instructions and leaves Steam/Heroic settings
under the user's control. It does not edit launcher settings, Proton prefixes,
saves, drivers or shader-cache policy. Restoration therefore only restores game
files. Updates check the DLL, adapter identity and configured target. Restore
checks every owned file and pauses that game if a later edit needs review;
other selected games can still complete.

## Tests

After preparing/building the pinned source above:

```sh
dotnet run -c Release --project integrations/optiscaler-client/tests/ClientTests.csproj \
  -p:ClientSource=/absolute/path/to/work/source/Optiscaler-Client-dd534b7d1cb8a0edf174a6917f5179791603d364
python3 scripts/check-repo.py
python3 scripts/check-opticlient.py \
  --archive dist/opticlient/bc250-opticlient-1.0.7-bc250.2-linux-x64.tar.gz
```

The C# harness creates temporary game trees and isolated application data through
its injected service paths. It exercises installation, adoption, repeat updates,
preservation of INI edits, original-file restoration, symlink/path rejection,
corrupt imports, simulated running-game deferral, rollback on write failure and recovery
after a child process exits midway through a transaction.
The package checker verifies every archived file, the bundled DLL identity,
complete integration source, licenses and links in the extracted guide.

The optional `--real` harness mode takes a prepared payload directory, release ZIP,
new scratch output directory and a Windows DLL-load probe executable. It installs
the actual payload for flat, nested and Unreal-style executable layouts and records the results of repeat
updates. Use only synthetic game directories for this check.

## Validation

The integration adds installation behavior; it does not change the released DLL.
Its transaction tests and real-payload installation/loading checks are recorded
in [the integration validation record](../../docs/data/optiscaler-client-2.json).
The [RC11 validation](../../docs/portable-dll-rc11.md) remains the rendering evidence
for that DLL. Client setup, DLL loading and actual game rendering are distinct
checks; this build does not claim a new full gameplay campaign or support for
Windows/other GPUs.

## Release maintenance

Keep the C# additions and upstream patch under GPL-3.0-or-later, with complete
modified client source in every binary download. See [NOTICE.md](NOTICE.md).
The preparation/build scripts have their own MIT markers.

When updating upstream Client or OptiScaler, revise pins deliberately, rerun the
transaction and packaged-app checks, and qualify any configuration/model changes
in rendering. Keep the plain DLL ZIP useful independently. Do not repurpose an
old client filename for different bytes; bump the project suffix for a changed
client package. A new DLL ZIP can be imported without changing the application
when its FFX/INT8 interface remains compatible.

The package is a separate optional release asset. The source archive inside it
covers the modified client; the project's normal source release covers the DLL,
documentation and packaging tools. Publicly publish both with matching checksums.
