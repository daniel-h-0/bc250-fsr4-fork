# RC9 shader port — experimental driver source

This capsule preserves the exact additional sources for the second RC10 driver
prototype. It is included in complete-source exports; it is not another
release-page download or a qualified replacement for the retained RC1 driver.
The production `v4/manifest.json` and RC6 recovery runtime remain unchanged.

`source-overlay.tar.xz` contains three files for a fresh Mesa 26.2.2 tree already
prepared with the production v4 patches. `manifest.json` records their original
and resulting hashes, the archive hash, all 42 matched shader pairs and the
private ELF identity. The generated header contains the complete original and
RC9 SPIR-V programs, compressed with Zstandard, plus the selector implementation.
Original and target programs are matched byte-for-byte after a hash prefilter.
The driver retains the original path for unknown inputs and incompatible
subgroup or specialization requests. RC9's runtime model-weight checks remain
inside the replacement shaders.

The captured programs originate from the pinned SDK and RC9 DLL through
GE-Proton 11-6. Original SDK DXIL and maintained RC9 LLVM sources are identified
by [the DLL manifest](../../../dll/manifest.json). The
[AMD SDK notice](../../../dll/notices/AMD-SDK-LICENSE.md) applies to that material;
Mesa's existing source notices and the [project license](../../../LICENSE)
remain applicable. This capsule does not contain or alter the AMD driver-provider
DLL or the OptiScaler hook required to select its INT8 model.

## Verify and inspect

From this directory, run `python3 verify.py` with Python 3.11 or newer. It verifies the complete capsule
without a GPU, network access or filesystem writes. To compare an already
prepared prototype tree, add `--mesa-source /absolute/path/to/mesa-26.2.2`.
The source hashes must match the capsule exactly.

Extract into a new prepared build tree when experimenting. Do not extract it
into an installed driver location or over unrelated source work. The existing
portable recipe in `scripts/build-compat.py` provides the pinned Debian 12
sysroot and Mesa options. For an experimental rebuild, prepare that private
build first, apply these three source files, rebuild its
`src/amd/vulkan/libvulkan_radeon.so` target with the same environment, and repeat
`verify_target_abi` from that script. The earlier build receipt then describes
the earlier source; it must not be used to package or label this new binary.

The prototype targets glibc 2.36 / GLIBCXX 3.4.30 and statically links libdrm.
A portable ABI floor is not proof of every distro's complete graphics stack.
The experimental `BC250_FSR4_RC9_DISABLE` switch has a distinct Vulkan pipeline
cache UUID, verified through the actual driver. `BC250_FSR4_RC9_LOG=true` logs
recognized shaders when they are compiled; cache hits need not print a message.

Current image, runtime, mode and cache-key qualification belongs in the
[RC10 development record](../../../docs/rc10-development.md). Translator changes
may prevent an exact match and use the older path. Release packaging and
installation/recovery qualification are separate steps; this source capsule
does not authorize RC10 publication.
