# First launch: shader compilation

Proton and the driver compile the DLL's shaders for your GPU on first use.
Enabling FSR or loading a scene can pause the game for tens of seconds or longer.
Later launches can reuse that work through normal shader caches.

1. Allow the first compilation to finish and keep caches enabled.
2. If the game exits, save the error and retry once with the same files and cache.
3. For a repeat failure, [report the problem](../CONTRIBUTING.md#report-a-problem)
   with the game/API, DLL, GPU, driver and Proton versions plus a short error log.

Updates can require recompilation. The [GPU-cost chart](gpu-cost.md) measures
upscaling after compilation. Optional [shared caching](shared-shader-cache.md)
can reuse compatible work across games.

[Return to installation](beginner-guide.md).
