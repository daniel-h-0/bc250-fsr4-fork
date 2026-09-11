# Standalone FFX API and GPU probe

[provider_probe.c](provider_probe.c) is the synthetic D3D12 workload used for
the DLL API checks and whole-upscaler timing comparison. It needs no game
installation, saves or OptiScaler. It creates color, depth and motion-vector
inputs, dispatches FSR, waits for GPU completion and reads every output pixel.

The default build retains probe revision 3 from the recorded campaign. Its
include path and attribution comment are portable, and an optional compile-time
barrier audit is available. The normal build below reproduces
the campaign executable SHA256
`1ea66d09b2ca8863fbabf18adf0ebf10423a8e1f66bc22399e44ec7f76749d76`
with the validation host's toolchain. Other compiler versions can produce
different executable bytes; preserve the source/workload identity when comparing.

## Build

Obtain the SDK source headers at commit
`60f4ea81909200d8542eca14dccb2628b763a9a3`, the same SDK revision pinned by the
DLL manifest. The following Clang/LLD cross-build uses Wine development headers
and PE import libraries. Adjust their directories for your distribution.

```sh
clang --target=x86_64-w64-windows-gnu -fms-extensions -D__WINE_USE_MSVCRT \
  -isystem /usr/include/wine/msvcrt -isystem /usr/include/wine/windows \
  -I /path/to/FidelityFX-SDK/Kits/FidelityFX/upscalers/include \
  -O2 -Wall -Wextra -Werror -nostdlib -fuse-ld=lld \
  -Wl,--entry,mainCRTStartup -Wl,--subsystem,console -Wl,--no-insert-timestamp \
  -L/usr/lib/wine/x86_64-windows dll/probe/provider_probe.c \
  -lkernel32 -lucrtbase -o .work/provider-probe.exe
```

These are optional probe-build dependencies. They are not needed by the
ordinary DLL builder or end-user installation.

## Run a small check

Choose a fresh scratch directory. Set these environment variables to absolute
Windows paths and run the executable through your chosen D3D12 environment:

| Variable | Meaning |
| --- | --- |
| `BC250_FFX_DLL` | Exact upscaler DLL to load |
| `BC250_FFX_OUTPUT` | New text-log path |
| `BC250_FFX_PIXELS` | New raw RGBA32F output path |

For example, from PowerShell in a fresh scratch directory:

```powershell
$env:BC250_FFX_DLL = 'C:\test\amd_fidelityfx_upscaler_dx12.dll'
$env:BC250_FFX_OUTPUT = 'C:\test\run-01.txt'
$env:BC250_FFX_PIXELS = 'C:\test\run-01.rgba32f'
.\provider-probe.exe
```

On Proton/Wine, use the equivalent `Z:\...` path when referring to Linux
files. Use a separate test prefix, ordinary Proton and the intended driver.
Keep other upscaler injection disabled. The probe writes the two explicitly
selected output files; choose fresh paths rather than existing data.

For a visible SDK watermark on a documentation reference, set the process
environment variable `MLSR-WATERMARK` to `1`. In PowerShell, the hyphenated name
can be set with:

```powershell
[Environment]::SetEnvironmentVariable('MLSR-WATERMARK', '1', 'Process')
```

The [RC9 reference image](../../docs/assets/rc9-watermark-reference.png) uses
the unchanged probe, SDR input, 1280×720 render / 1920×1080 output and eight
frames. Its [capture record](../../docs/data/beginner-watermark-rc9.json) identifies
the DLL, executable and output hashes. It is a synthetic documentation render,
not a game screenshot or timing result. It leaves the scored release images
and performance records unchanged. Unset this variable for normal image comparisons.

The default workload renders 128×96 to 192×144 for four frames. Require
successful create, four dispatch/fence completions, readback and destroy.
The raw output is 442,368 bytes. With RC7's corrected SDK synchronization,
the BC250 reference SHA256 is
`d52cb63f378dc0eb3f90044884534f91c6fec208919c9060ab5618d5bf107c9b`.
Inspect finite pixel values as well as the API statuses. A DLL load or context
creation alone is not a rendering pass.

## Observe the SDK barrier repair

Add `-DBC250_PROBE_BARRIER_AUDIT` to the build command and use a different
output executable name. Run that build with `BC250_FFX_TRACE=1`. The
[optional audit](barrier_audit.h) records the UAV barriers issued by the SDK
before each dispatch; it adds no barriers or other GPU commands.

The default four-frame FSR4 workload has 28 dispatches per frame. Padding
clears occupy indices 2, 4, ..., 26. RC7 issues a UAV barrier before every one
of those clears, as well as retaining the barriers before model dispatches.
The preceding SDK skipped the padding-clear barriers. Its formerly common
`d6b3c5...` small-image hash belongs to that unsynchronized path and is not the
corrected oracle. This diagnostic build is for verification, not scored timing.

## Workload and timing controls

`BC250_FFX_RENDER_W/H` and `BC250_FFX_OUTPUT_W/H` select input/output sizes.
`BC250_FFX_MAX_OUTPUT_W/H` independently reserve context output dimensions.
`BC250_FFX_FRAMES` accepts 1–600 frames. `BC250_FFX_SCENARIO` selects `static`,
`sdr`, `hdr`, `motion`, `reset`, `resize` or `rcas`. Defaults and bounds are in
the source; the recorded timing campaign used the static scenario.

For matched timing, use 240 frames and the median GPU time from frames
120–239 of each run. Repeat in v4/DLL/DLL/v4 order and average the two medians
per arm. Compare full output hashes at each size. Retain individual runs and
report the actual runtime, driver, DLL and probe identities.

`BC250_FFX_TRACE=1` enables optional per-dispatch timestamps inside this probe
only. Keep it at its default zero for whole-upscaler timing. Keep shader dumps
and game tracing off in scored runs; log writes do not flush on every line.
Do not compare the earlier flush-heavy probe's numbers with this campaign.

The [compatibility record](../../docs/portable-dll-rc7.md) lists the measured
outputs and inconclusive arbitrary-size cases. Reproducing an API check on a
new GPU or native Windows is useful new qualification, not an automatic claim
of byte-identical floating-point results or BC250 performance on that device.
