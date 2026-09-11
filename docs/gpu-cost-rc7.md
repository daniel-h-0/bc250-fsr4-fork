# Direct FSR4 GPU cost — four implementations

This historical September 10 chart measures the complete FSR upscaler on an AMD BC250 at
1080p, 1440p and 4K Quality. Every bar comes from GPU timestamps in a fresh
September 10 campaign. No pixel-count extrapolation or FPS subtraction enters
these values.

![Direct GPU cost of four FSR implementations](assets/fsr4-four-way-gpu-cost.svg)

| Output / Quality input | FSR 4.1.1 original shaders | FSR 4.1.1b | BC250 v3 | BC250 v4r7 |
| --- | ---: | ---: | ---: | ---: |
| 1080p / 1280×720 | 7.13 ms | 7.13 ms | 5.18 ms | 3.84 ms |
| 1440p / 1706×960 | 11.51 ms | 11.77 ms | 8.24 ms | 6.61 ms |
| 4K / 2560×1440 | 25.72 ms | 25.71 ms | 18.40 ms | 13.94 ms |

Lower is better. The numbers are milliseconds per complete upscale dispatch,
not total game frame time. The workload is a controlled synthetic D3D12 scene.
Actual game inputs, context sizes, sharpening, overlap and integration can
change the cost.

## What was measured

- **FSR 4.1.1, original shaders:** the pinned AMD SDK, with only the existing
  18-byte host eligibility bypass needed for INT8 on BC250. Every original
  shader is retained; no v3/v4 compiler optimizations are active.
- **FSR 4.1.1b:** the exact previously supplied community DLL. Its modified
  final-pass shaders were identified by hash in the live D3D12 inputs.
- **BC250 v3:** original SDK shaders with the exact upstream v3 Mesa 26.2.0
  implementation, rebuilt for this host's LLVM ABI. Its
  [source and build audit](performance.md#what-the-baseline-represents) is
  retained. This is the v3 implementation, rather than disabling one v4 flag.
- **BC250 v4r7:** the exact published RC7 DLL, SHA256
  `730c175a38b0f0271ffaa201ca531825c6440a95fb71729d34566c66af8e4893`.

The first, second and fourth arms share the same standard Mesa 26.2.1 driver.
v3 retains its original driver. The chart compares these four implementations;
it does not isolate one source patch from Mesa-version or build differences.
All use ordinary GE-Proton 11-6 and Steam Linux Runtime 4. The optional
driver-provider and automatic OptiScaler integrations are disabled.

## Workload and sampling

The [full-dispatch probe](../dll/probe/README.md) is the same revision 3 used
for the RC7 GPU-time work. It runs the actual FSR shaders on the GPU, through
the native FidelityFX/D3D12 interface. Color is a linear gradient/checkerboard,
with depth and zero motion vectors; RGBA32F output is read back for validation.
The HDR-capable context uses automatic exposure. Sharpening is off.
Input dimensions are listed in the table; maximum context output dimensions
equal the requested output. The timestamp pair surrounds the complete FFX
dispatch, including SDK compute work and barriers. Input upload, output
readback, CPU time and shader compilation lie outside that interval.

Each cell has four independent launches. Every launch dispatches 600 frames,
discards the first 300, and contributes the median of the remaining 300 GPU
times. The plotted value is the median of those four launch medians. Whiskers
show their minimum and maximum; they are not confidence intervals. The
four-round Williams order puts each technique in each position once and
balances every ordered transition. No outliers were removed.

Observed run ranges are retained even where they are wider. Small differences
between FSR 4.1.1 and 4.1.1b overlap the measured run ranges; this workload
does not establish a performance gain for 4.1.1b.

There are **28,800 recorded timestamps and 14,400 scored frames**. All 48
launches completed their GPU fences and exited normally. At each resolution,
all sixteen final images are byte-identical. Separate 64-frame preflights
also match all four images and verify the complete active model family for
each artifact. Per-pass tracing and shader dumps are off during scoring.

The existing GPU governor and cooling policy were preserved.
Every run's scored-period median GPU clock was **1850 MHz**.
The highest sampled scored-period temperature is **82.0 °C**.
The experiment uses one board and one defined workload, with no Windows,
other-GPU or whole-game FPS claim.

## Data and reproduction

[Every timestamp](data/fsr-cost-20260910/samples.csv),
[run metadata and telemetry summaries](data/fsr-cost-20260910/runs.json),
[build identities and configuration](data/fsr-cost-20260910/configuration.json),
[model/image preflight](data/fsr-cost-20260910/preflight.json), and
[computed results](data/fsr-cost-20260910/results.json) accompany the figure.
The summarizer checks counts, identities, timing flags, finite positive
samples, complete images and every plotted statistic. The chart generator
derives bar heights, error bars and printed values from those results.

```sh
python3 docs/data/fsr-cost-20260910/summarize.py
python3 docs/data/fsr-cost-20260910/plot.py
```

The first command uses Python's standard library. Plotting uses Matplotlib
3.11.1 and Fira Sans. Exports are [SVG](assets/fsr4-four-way-gpu-cost.svg),
[3600×2040 PNG](assets/fsr4-four-way-gpu-cost.png) and
[PDF](assets/fsr4-four-way-gpu-cost.pdf). Vector geometry and displayed values
are also recorded in [chart-geometry.json](data/fsr-cost-20260910/chart-geometry.json).

The [September 8 reconstructed chart](fsr-cost.md) and
[September 7 whole-game v3/v4 results](performance.md) remain historical,
separate campaigns; their values are not mixed into this figure.
