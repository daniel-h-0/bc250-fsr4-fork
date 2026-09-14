# RC10 Linux driver compatibility option

This is a private **Mesa 26.2.2 / RADV driver for AMD BC250**, carrying the
RC9 shader optimizations. The portable DLL remains the primary installation.
Use this option for an existing AMD-provider integration that needs its
optimizations supplied by the driver. It is Linux x86-64 only.

The [updated development installer](driver-cache-setup.md) can install this binary
with an integrated shared-cache launcher and permanent tools. The instructions
below describe the original RC10 archive's installer.

The driver recognizes exact translated shader programs from the pinned FSR
4.1.1 INT8 provider and GE-Proton 11-6. A full byte comparison follows the hash
prefilter; unrelated or changed inputs retain the earlier driver path. This
makes the optimization dependent on the provider and translator versions.
Changing Proton can lose RC9 optimization coverage even if a game still works.
The installed Valve Proton 11.0-2c trial removed the manually supplied provider
and selected FSR 3.1.5; it did not pass the provider-route check. The primary
RC10 DLL passed that same Proton build. Use the verified GE-Proton selection
for this driver recipe. Experimental 11.0-20260910b also passed one 1440p
provider check with all 14 expected substitutions; its full matrix is unqualified.
The driver includes all three resolution families and preserves model-weight
checks and fallback computation. It does not add accelerated dot-product hardware.

## Install the private driver

Requirements: BC250, Linux x86-64, Python 3.11+, a Vulkan loader and the usual
X11/Wayland, xcb, zstd and C++ runtime dependencies. The binary targets glibc
2.36 and GLIBCXX 3.4.30 or later. A startup probe checks its dependencies and
Vulkan initialization before selecting it. This is an ABI target, not a claim
that every distribution or sandbox has passed a graphics test.

Download the Linux driver archive and release `SHA256SUMS`. Keep them together,
then run from that download directory:

```sh
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf bc250-fsr4-v4.0.0-rc10-linux-glibc236-x86_64.tar.gz
cd bc250-fsr4-v4.0.0-rc10-linux-glibc236-x86_64
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-rc10" install \
  ../bc250-fsr4-v4.0.0-rc10-linux-glibc236-x86_64.tar.gz --sha256 ARCHIVE_SHA256_FROM_SHA256SUMS
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-rc10" status
```

Replace the last token with this archive's 64-character digest from
`SHA256SUMS`. Run as your desktop user without sudo. The installer records a
transaction and leaves the system driver unchanged. Keep the extracted tools
in a permanent location. The dedicated RC10 directory also keeps a retained
RC6 setup independent; the old `setup.sh` and `install-v4.sh` still target
that historical distribution and should not be used for RC10.

To select this driver for one launch, prepend the command below to the
existing launch command and retain its arguments and environment settings:

```sh
python3 "/path/to/extracted-driver/scripts/driver.py" \
  --prefix "$HOME/.local/share/bc250-fsr4-rc10" run -- ORIGINAL-COMMAND ARGUMENTS
```

In Steam replace `ORIGINAL-COMMAND ARGUMENTS` with the game's existing command,
including exactly one `%command%`. The script, installation and ICD paths
must be visible inside any Steam/Flatpak/container namespace used by that game.
The wrapper chooses the private Vulkan ICD for that process; it does not edit
Steam, Heroic, prefixes, game files, saves or system packages.

## The AMD-provider path

Installing RADV alone does not make an application request FSR4. The tested
provider route uses ordinary GE-Proton **11-6**, upstream OptiScaler
**10.0.0-pre1 (September 4)** with **OptiPatcher 0.41**, the original AMD
**4.1.1** provider and the older SDK **4.0.2** bridge. The exact upstream
URLs and hashes are retained in
[the runtime input manifest](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/runtime/manifest.json).
The retained runtime's driver selection is a separate RC1 recovery pin.

The two AMD downloads for this recipe are:

| Download | Use / SHA256 of the DLL |
| --- | --- |
| [SDK 4.0.2 bridge](https://raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/f4c1da8e92f3fe563b5c28c44e6267ce6b6b8eb2/Kits/FidelityFX/signedbin/amd_fidelityfx_upscaler_dx12.dll) | Save as `amd_fidelityfx_upscaler_dx12.dll`; `241e6e5e4d848424eb8ec9a6b22c43fe34cf0cf52d30002ca435ba42e53a9ca0` |
| [Original FSR 4.1.1 provider, xz-compressed](https://loathingKernel.github.io/proton-upscalers/amdxcffx64_v4.1.1_398EA93C15D554EFB7ECE1F4CD057554.xz) | Decompress and rename the result to `amdxcffx64.dll`; `4e7dc37aebea3a90e3d3cc43e24cb2b54176b2535315f20dbe63b3b7cfc56b1e` |

The compressed provider archive itself has SHA256
`5de9b6d9f5475a0f2622e4cbce88cde46c68929d9bd0bbc353c70056997bb771`. Keep the upstream notices linked in the input
manifest. Verify the extracted DLL hashes before replacing files. For Steam,
the game's prefix is usually under `steamapps/compatdata/APPID/pfx` in its
Steam library; Heroic uses the configured Wine-prefix path.

For that route, close the game, back up the current integration and use the
pinned SDK bridge as `OptiScaler/amd_fidelityfx_upscaler_dx12.dll`. The
unmodified `amdxcffx64.dll` provider belongs in that game's Wine prefix under
`drive_c/windows/system32/`. Keep OptiScaler's other required files. Its INT8
selection hook is required; the bare SDK bridge on ordinary GE selected FSR3
in our test. Configure the existing adapter with:

```ini
[FSR]
UpscalerIndex=0
Fsr4ForceModel=2
Fsr4EnableWatermark=auto

[FrameGen]
Enabled=false
```

Retain the appropriate `ffx` / `ffx_12` backend and input settings for the game.
For a `winmm.dll` proxy, the recorded environment contains:

```sh
PROTON_FSR4_UPGRADE=0 PROTON_USE_OPTISCALER=0 PROTON_USE_XALIA=0 WINEDLLOVERRIDES="winmm=n,b;amdxcffx64=n" VKD3D_DISABLE_EXTENSIONS="VK_NVX_binary_import,VK_NVX_image_view_handle"
```

These accompany the private-driver wrapper. Adapt only the proxy name for an
established different adapter installation. The portable DLL recipe disables
`amdxcffx64`; that override must change when deliberately selecting this provider
route. Keep a record of the previous files and launch options for undo.

Do not replace the bridge with the primary RC10 DLL when checking the driver
route: that would test the DLL's own optimized implementation. The original
provider continues to identify itself as **4.1.1**, with source **DRIVER**.
The primary DLL identifies itself as **4.1.1r10**, with source **LOCAL**.
For temporary confirmation, `BC250_FSR4_RC9_LOG=true` prints exact shader matches
during compilation. Warm cache hits can be silent. `BC250_FSR4_RC9_DISABLE=true`
disables the new port and has a separate cache identity. Remove diagnostic
variables after checking. Leave `MLSR-WATERMARK` absent for a hidden watermark.

## Qualification and limitations

Control passed a normal Steam driver-route gameplay check, with saved-scene
navigation, the source DRIVER watermark, exact loaded component hashes and
14 logged shader substitutions. System Shock's additional DX11 check reached
animated menu rendering with 13 substitutions, but did not establish gameplay.
[Gameplay evidence and limits](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/driver-gameplay-rc10.md).

The RC9 parity work includes exact shader/interface matching, native-code
comparisons, three-resolution sustained synthetic rendering, SDR/HDR, motion,
history reset and sharpening. A reserved 8K context exercises the third shader
family; this is not a full 8K gameplay result. The older provider route's dynamic
resolution output differs from the direct SDK route; the new driver preserves
the older provider's result exactly. Universal image identity between those
API routes is not claimed.

[Detailed results and source](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/rc10-development.md)
remain in the complete source/evidence archive. Real-game coverage is narrower
than the synthetic matrix. Windows, other GPUs, frame generation and arbitrary
provider/Proton versions are unqualified. The driver's generated header contains
exact translated programs, so this option does not promise the DLL's measured
cold-compilation reduction.

## Shared cache and undo

The included `scripts/shared-cache.sh` and adjacent Python helper optionally
share compatible Mesa compilations. They can wrap the existing launch command;
keep all driver and adapter settings. See the
[shared-cache guide](https://github.com/daniel-h-0/bc250-fsr4-fork/blob/v4.0.0-rc10/docs/shared-shader-cache.md)
for XDG paths, sandbox visibility, concurrency and fallbacks.

To stop using the private driver, remove its wrapper from the game's launch
options. Restore any provider/bridge files and overrides changed for this route
from your backups. To return the managed private selection to its prior version:

```sh
python3 scripts/driver.py --prefix "$HOME/.local/share/bc250-fsr4-rc10" rollback
```

A first-install rollback leaves no selected private release and retains the
payload for inspection. If an installation was interrupted, run `recover`
before attempting another install. Saves and Wine prefixes are not removed.

The compact driver download contains runtime tools, build provenance and
notices. The separate source archive contains the full Mesa modifications,
shader sources, reproducible build recipe and retained evidence.
