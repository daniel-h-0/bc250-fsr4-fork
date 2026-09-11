# First use: shader compilation can look like a freeze

**The first use of this FSR path can involve substantial shader compilation.**
Without a usable cache, the game may stop updating, ignore input or appear
unresponsive for tens of seconds or longer. This can happen when you first
enable the upscaler, or while loading a game that already has it selected.
There may be no progress bar. Allow time for compilation before force-closing
the game; an initial pause alone does not mean the installation failed.

## Why an already-built DLL still needs compilation

The DLL contains compiled DirectX shader bytecode. The graphics stack must still
turn that bytecode into executable GPU code for the hardware and driver in use.
On the tested Linux path, Proton translates DirectX shaders for Vulkan and Mesa
compiles them for the BC250. These inference shaders can take substantial work
to compile. You do not need to build the DLL yourself to complete this step.

The shader caches can reuse that work on later launches. The first launch may
be quicker if a compatible cache already exists; a long pause is not inevitable
for every user. Conversely, a GPU, driver, Proton, game or FSR DLL update can
require more compilation. Clearing or disabling caches, or selecting a mode
that needs another shader variant, can also bring the pause back. Cache reuse
depends on the graphics stack and the particular shader/pipeline, not just the
GPU model. [vkd3d-proton cache documentation](https://github.com/HansKristian-Work/vkd3d-proton#shader-cache).

## What to do

1. After first enabling the upscaler, give the game time to finish. A temporarily
   static image or an unresponsive application is not enough to identify a crash.
   Compilation time varies; there is no universal timeout for every system.
2. Keep shader caches enabled and intact between launches. Clearing them as a
   routine response to this first-use pause can make compilation start again.
3. If the game actually times out or exits, save its error message/log and try
   one restart with the same DLL, settings and cache. Some games' hang detectors
   can interrupt the first compilation even though the next launch succeeds.
4. If it fails again, collect the game/API, DLL release, GPU, driver and Proton
   versions plus the error/log. GPU/device errors, repeated stalls and a
   whole-system lockup should be investigated as failures, not dismissed as
   expected compilation. A pause does not establish compatibility on an
   otherwise untested platform.

## Recorded example and performance scope

During RC7 qualification, No Man's Sky recorded a **65.96-second first-dispatch
stall** after switching from Off to DLSS and produced `0x1106-HANG`. No kernel
GPU fault or reset was recorded. Restarting with the same DLL, Proton, driver
and compiled cache rendered the saved scene and exited normally. Shader
identification logging/dumping was enabled, so this is diagnostic evidence,
not a clean measurement of expected compilation time. It establishes one
successful restart, not a universal workaround or cold-start guarantee.
[Original game-check record](portable-dll-rc7.md#supported-scope).

The published GPU-cost chart measures steady per-frame upscaling work; its
millisecond values do not include this initial compilation delay. First-launch
waiting time and ongoing in-game performance are separate measurements.
[Chart method](gpu-cost.md).

Return to the [installation guide](../dll/INSTALL.md) or
[main README](../README.md#install).
