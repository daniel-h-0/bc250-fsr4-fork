# FSR4 GPU cost and game FPS

These are GPU times of the complete FSR upscaler on an AMD BC250, from GPU
timestamps of a synthetic D3D12 scene. The current numbers are the
**October 23, 2026** same-day pairs below. The September chart keeps its
original data.

For installation, use [Install across your games](optiscaler-client.md) or
[manual DLL replacement](beginner-guide.md).

## October 23, 2026

### RC11 against AMD's original shaders

Quality mode, context maximum = output. Both arms ran in one session on stock
Mesa 26.2.1 RADV, the September chart's driver.

| Output / Quality input | FSR 4.1.1 original shaders | RC11 | RC11 − original |
| --- | ---: | ---: | ---: |
| 1080p / 1280×720 | 6.657 ms | 3.671 ms | −2.986 ms (−44.9%) |
| 1440p / 1706×960 | 11.320 ms | 5.443 ms | −5.877 ms (−51.9%) |
| 4K / 2560×1440 | 25.243 ms | 11.980 ms | −13.263 ms (−52.5%) |

5, 6 and 4 rounds; standard errors ≤ 0.004 ms. [Record](data/fsr-cost-20261023.json).

### RC12 against RC11

System Mesa 26.2.5 RADV build with `RADV_DIRECTMESH=0`. RC12 ran as its
GPU-tested build: RC12's programs, host code and layout with another label
([identity](portable-dll-rc12.md#exact-identities)).

| Output (input) | Context maximum | RC11 | RC12 | RC12 − RC11 |
| --- | --- | ---: | ---: | ---: |
| 1080p Quality (1280×720) | 1920×1080 | 3.675 ms | 3.262 ms | −0.414 ms (−11.3%) |
| 1080p Performance (960×540) | 1920×1080 | 3.653 ms | 3.221 ms | −0.432 ms (−11.8%) |
| 1440p Performance (1280×720) | 2560×1440 | 5.381 ms | 5.383 ms | +0.002 ms (noise) |
| 1440p Performance (1280×720) | 7680×4320 | 6.105 ms | 5.383 ms | −0.723 ms (−11.8%) |

8, 5 and 8 rounds; paired standard errors ≤ 0.004 ms. Two copies of RC11
differed by up to 0.002 ms in the same runs. [Record](data/portable-dll-rc12.json).

- RC12's 1080p programs cost what RC11's 1440p–4K programs cost at the same
  output: RC11 with a 2560×1440 context maximum took 3.261 ms (Quality) and
  3.232 ms (Performance).
- RC12 was not timed at 1440p and 4K Quality. With those context maxima it runs
  RC11's programs except the final-pass slot `0x248220` (AMD's division),
  which the 2560×1440 row prices.

### Method

- The [full-dispatch probe](../dll/probe/README.md), revision 3, GE-Proton 11-6
  and Steam Linux Runtime 4, as in the September chart. Sharpening off.
- Each run: 600 frames, median of frames 300–599. Value: mean of the run
  medians; differences are paired by round. Arms rotate within each round, in
  reverse order every other round. Every scored sample ran at 1850 MHz.
- In each block every arm produced the same image.
- The September chart used the median of four launch medians and publishes
  every timestamp; these records publish run medians.
- With the same DLL, driver build, probe and Proton, the original shaders now
  measure 6.657 / 11.320 / 25.243 ms against 7.13 / 11.51 / 25.72 ms on
  September 10. Compare values within one date.

The chart was not redrawn: FSR 4.1.1b and v3 were not remeasured, and RC12 was
not timed at 1440p and 4K Quality.

## September 10–11 chart

This chart measures the complete FSR upscaler at 1080p, 1440p and 4K Quality.
**Only RC9 was remeasured on September 11.** The original FSR 4.1.1, supplied
4.1.1b and upstream v3 arms retain their September 10 data unchanged. Every
value comes from completed GPU timestamps.

![FSR4 GPU cost on September 10–11: RC9 versus unchanged baselines](assets/fsr4-four-way-gpu-cost-rc9.svg)

| Output / Quality input | FSR 4.1.1 original shaders | FSR 4.1.1b | BC250 v3 | BC250 RC9 |
| --- | ---: | ---: | ---: | ---: |
| 1080p / 1280×720 | 7.13 ms | 7.13 ms | 5.18 ms | 3.93 ms |
| 1440p / 1706×960 | 11.51 ms | 11.77 ms | 8.24 ms | 5.92 ms |
| 4K / 2560×1440 | 25.72 ms | 25.71 ms | 18.40 ms | 12.08 ms |

Lower is better. The numbers are milliseconds per complete upscale dispatch,
not total game frame time. The workload is a controlled synthetic D3D12 scene.
Actual game inputs, context sizes, sharpening, overlap and integration can
change the cost.

### What was measured

- **FSR 4.1.1, original shaders:** the pinned AMD SDK, with only the existing
  18-byte host eligibility bypass needed for INT8 on BC250. Every original
  shader is retained; no v3/v4 compiler optimizations are active.
- **FSR 4.1.1b:** the exact previously supplied community DLL. Its modified
  final-pass shaders were identified by hash in the live D3D12 inputs.
- **BC250 v3:** original SDK shaders with the exact upstream v3 Mesa 26.2.0
  implementation, rebuilt for this host's LLVM ABI. Its
  [source and build audit](legacy/research/performance.md#what-the-baseline-represents) is
  retained. This is the v3 implementation, rather than disabling one v4 flag.
- **BC250 RC9:** the final RC9 DLL, SHA256
  `eefcac03ab17b04a29a5bb16e3f3e9c3181ba9ea46b05a61cb49a5003e1516ef`.

The first, second and fourth arms share the same standard Mesa 26.2.1 driver.
v3 retains its original driver. The chart compares these four implementations;
it does not isolate one source patch from Mesa-version or build differences.
All use ordinary GE-Proton 11-6 and Steam Linux Runtime 4. The optional
driver-provider and automatic OptiScaler integrations are disabled.

### Workload and sampling

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
show their minimum and maximum; they are not confidence intervals. The original
baseline runs retain their Williams-order positions. RC9 uses
four separate rounds across the three output sizes; the exact order is
recorded in the new configuration. This is a refresh of one arm across dates,
not a new interleaved four-way campaign. No outliers were removed.

Observed run ranges are retained even where they are wider. Small differences
between FSR 4.1.1 and 4.1.1b overlap the measured run ranges; this workload
does not establish a performance gain for 4.1.1b.

The combined dataset has **28,800 timestamps and 14,400 scored frames**:
36 unchanged baseline launches plus twelve new RC9 launches. All completed
their GPU fences and exited normally. At each resolution,
all sixteen final images are byte-identical. Separate 64-frame preflights
match the complete images and verify each active model family. The RC9
preflights are fresh; the baseline preflights retain their original records.
Per-pass tracing and shader dumps are off during scoring.

The existing GPU governor and cooling policy were preserved.
Every run's scored-period median GPU clock was **1850 MHz**.
The experiment uses one board and one defined workload, with no Windows,
other-GPU or whole-game FPS claim.

### Data and reproduction

[Every timestamp](data/fsr-cost-20260911-rc9/samples.csv),
[run metadata and telemetry summaries](data/fsr-cost-20260911-rc9/runs.json),
[build identities and configuration](data/fsr-cost-20260911-rc9/configuration.json),
[model/image preflight](data/fsr-cost-20260911-rc9/preflight.json), and
[computed results](data/fsr-cost-20260911-rc9/results.json) accompany the figure.
The summarizer checks counts, identities, timing flags, finite positive
samples, scoring clocks, complete images and every plotted statistic. It also
checks the original dataset hashes and requires unchanged baseline metadata
and timestamp lines. The historical source dataset is included alongside it.
The chart generator
derives bar heights, error bars and printed values from those results.

```sh
python3 docs/data/fsr-cost-20260911-rc9/summarize.py
python3 docs/data/fsr-cost-20260911-rc9/plot.py
```

The first command uses Python's standard library. Plotting uses Matplotlib
3.11.1 and Fira Sans. Exports are [SVG](assets/fsr4-four-way-gpu-cost-rc9.svg),
[3600×2040 PNG](assets/fsr4-four-way-gpu-cost-rc9.png) and
[PDF](assets/fsr4-four-way-gpu-cost-rc9.pdf). Vector geometry and displayed values
are also recorded in [chart-geometry.json](data/fsr-cost-20260911-rc9/chart-geometry.json).

The [September 8 reconstructed chart](legacy/research/fsr-cost.md) and
[September 7 whole-game v3/v4 results](legacy/research/performance.md) remain historical,
separate campaigns; their values are not mixed into this figure.

The [September 10 RC7 chart](legacy/research/gpu-cost-rc7.md) remains unchanged. See the
[RC9 qualification](legacy/research/portable-dll-rc9.md) for its separate retained-checkpoint
comparison, additional image cases and the slightly higher 1080p result.

## Game FPS: Cyberpunk 2077

The [game matrix](assets/cyberpunk-performance-matrix.png) measures Cyberpunk
2077 2.31's built-in benchmark at 1920×1080 and 2560×1440. Each resolution has
FSR3 and FSR4 at Performance, Balanced, Quality and Native AA, plus native
rendering with upscaling and AA off: **18 scored runs**, recorded September 17,
2026 UTC. Each cell is one run after warm-up, using the game's reported
`Data.averageFps`. FSR3 and FSR4 runs alternate within each mode.

The High preset's graphics settings and textures stay fixed, with Medium crowd
density. Selecting the upscaling modes changes the game's preset label to Custom.
RT, path tracing, frame generation, dynamic resolution, VSync, FPS caps and game
HDR are off. The test uses a 40-CU BC250 with 6 GiB VRAM and up to 6 GiB GTT,
an 1850 MHz / 860 mV GPU governor target, a configured 3800 MHz CPU target,
GE-Proton 11-6 and system RADV 26.2.2. Existing cooling and clock policies remain
active. These are results for this configuration; one run per cell does not
establish run-to-run variation.

FSR3 uses the game's native FSR 3.0 path. `FSR4.1.1-BC250-v4` denotes the
released **RC11 INT8 DLL** through OptiScaler's September 4 build, using FSR3
input. The active output was confirmed in the OptiScaler overlay and the mapped
DLL hash checked for every run. The game summaries identify that FSR3 input API
in both columns. Native means 1:1 rendering; the No AA baseline also disables
the engine's `Developer/FeatureToggles/Antialiasing` option, verified in an
unscored run. Diagnostic logging and the OptiScaler menu are off during scoring.

For an arrow from A to B:

- FPS change: `100 × (FPS_B / FPS_A − 1)`.
- Added whole-frame time: `1000 / FPS_B − 1000 / FPS_A`, in milliseconds.

These frame-time differences include the game's complete workload. The
3.93 / 5.92 ms Quality figures above come from the separate synthetic GPU
measurement. A lower render resolution can save more work than reconstruction
adds, so an upscaler's millisecond cost alone does not predict the game's FPS.

[CSV](data/cyberpunk-20260917/outcomes.csv) ·
[Results and configuration](data/cyberpunk-20260917/outcomes.json) ·
[Original benchmark summaries](data/cyberpunk-20260917/runs) ·
[Validation record](data/cyberpunk-20260917/validation.json).

Regenerate the figure with Python, Matplotlib, NumPy, Pillow and Liberation Sans:

```sh
python3 docs/data/cyberpunk-20260917/plot.py
```

The generator checks all 18 averages against the original summaries, then
computes the arrow labels from full-precision values. Exports:
[PNG](assets/cyberpunk-performance-matrix.png),
[SVG](assets/cyberpunk-performance-matrix.svg),
[PDF](assets/cyberpunk-performance-matrix.pdf).
