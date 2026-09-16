# First launch: shader compilation

First use can pause for tens of seconds or longer while Proton and the driver
compile the DLL's shaders for your GPU. It can happen when enabling FSR or
loading a scene, without a progress bar. An already-built DLL still needs this
step; no custom cache helper is required.

## What to do

1. Give the first compilation time to finish. There is no universal wait time.
2. Keep normal shader caches enabled. Clearing them can repeat the work.
3. If the game exits or times out, save its error and try one restart with the
   same DLL, settings and cache.
4. If it fails again, report the game/API, DLL release, GPU, driver, Proton and
   error. Repeated stalls, device errors and system lockups need investigation.

Updates to the GPU, driver, Proton, game or DLL can require recompilation.
Cache reuse depends on compatible inputs, not just the GPU name.
[vkd3d-proton cache details](https://github.com/HansKristian-Work/vkd3d-proton#shader-cache).

## What has been observed

An RC7 No Man's Sky diagnostic run hit its hang detector after a 65.96-second
first dispatch; restarting with the same cache rendered successfully. Logging
and shader dumping were enabled, so this is an example, not an expected wait
time or a guarantee. [Original record](portable-dll-rc7.md#supported-scope).

The [performance chart](gpu-cost.md) measures ongoing GPU cost after compilation.
Its millisecond values do not describe first-launch waiting time.
Optional [shared caching](shared-shader-cache.md) may reuse compatible work
across games; sharing the DLL alone does not share those compiled caches.

[Back to installation](beginner-guide.md).
