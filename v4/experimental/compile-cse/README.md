# Compilation cleanup — proposed source changes

This is the source proposal for private DLL `4.1.1d5`, not a release package.
The maintained DLL manifest, source inventory and published RC9 bytes remain
unchanged. `source-proposal.tar.xz` contains 48 changed LLVM source files;
`manifest.json` records every file/hash, original and resulting shader hashes,
the candidate DLL identity and the native-code evidence it was selected from.
The other 300 shader slots retain RC9 bytes.

The experiment ran the pinned DXC LLVM optimizer's `early-cse`, `dce` and
`strip-dead-prototypes` passes. This removes redundant computations and unused
instructions/declarations before the application hands the shaders to its
runtime compiler. It preserves the existing model, weights, runtime weight
checks and fallback computations. The 48 selected slots represent 36 distinct
bytecodes exercised across the three resolution families. Alternate variants
that were not exercised retain their original RC9 source and bytecode.

The [development record](../../../docs/rc10-development.md) and
[final candidate data](../../../docs/data/rc10-selected-compiler-20260914.json)
keep measured compilation, image, GPU-cost and scope information together.
Small GPU-time differences are not claimed as speedups. The aim is less
compilation work with the same executable GPU program.

These source files assemble with the DXC library pinned by
[the main DLL manifest](../../../dll/manifest.json). The
[AMD SDK notice](../../../dll/notices/AMD-SDK-LICENSE.md),
[LLVM notice](../../../dll/notices/LICENSE-LLVM.txt) and
[project license](../../../LICENSE) remain applicable. The sources retain
optimizer-emitted module comments; those comments are not instructions to a
build tool or an agent.

Integrate this proposal into a new development source tree when preparing a
release. Update that tree's complete source inventory, replacement hashes,
provider label, expected DLL identity and installation guide together, then
rebuild and qualify the exact release binary. Merely unpacking these files over
RC9 does not produce a coherent release manifest; the normal builder should
refuse such a mixed tree. Existing RC9 releases must keep their identities.

## Inspect executable code without a driver disassembler

The portable builds omit LLVM's disassembler. `inspect_pipeline.c` uses
[Vulkan pipeline-binary capture](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetPipelineBinaryDataKHR.html)
to obtain the actual serialized shader program without submitting GPU work.
It takes `shader.spv`, `default|required32|required64` and a new output filename. Build it against the
Mesa 26.2.2 Vulkan headers and the Vulkan loader. Use the intended
`VK_DRIVER_FILES` and `MESA_SHADER_CACHE_DISABLE=true` for each capture.

`inspect_layout.c` derives field offsets from the driver's own C headers and
compilation command. Its recorded result is `mesa-26.2.2-layout.json`; use that
layout only for the matching driver sources/ABI. `compare_binary.py` compares
all instructions, constant data, shader information and hardware configuration.
It excludes compiler-confirmed C alignment padding, which can vary without
changing a GPU field. It does not remove or ignore machine-code bytes.

The [custom-driver comparisons](../../../docs/data/rc10-custom-driver-code-20260914.json)
cover all 36 changed programs on both retained and new custom drivers. These
complement the standard-Mesa native disassembly comparisons.
