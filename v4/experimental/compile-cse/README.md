# Compilation cleanup — proposed source changes

This capsule preserves the private `4.1.1d5` proposal used during RC10 development.
The maintained DLL incorporates the selected cleanup; build it through
[the DLL source guide](../../../dll/README.md).

`source-proposal.tar.xz` contains the 48 proposed LLVM files. `manifest.json`
records their input/output hashes, candidate DLL and native-code evidence.
The other 300 slots retained RC9 bytes in this experiment.

The experiment ran the pinned DXC LLVM optimizer's `early-cse`, `dce` and
`strip-dead-prototypes` passes. This removes redundant computations and unused
instructions/declarations before the application hands the shaders to its
runtime compiler. It preserves the existing model, weights, runtime weight
checks and fallback computations. The 48 selected slots represent 36 distinct
bytecodes exercised across the three resolution families. Alternate variants
that were not exercised retain their original RC9 source and bytecode.

The [development record](../../../docs/legacy/research/rc10-development.md) and
[final candidate data](../../../docs/data/rc10-selected-compiler-20260914.json)
keep measured compilation, image, GPU-cost and scope information together.
Small GPU-time differences are not claimed as speedups. The aim is less
compilation work with the same executable GPU program.

These source files assemble with the DXC library pinned by
[the main DLL manifest](../../../dll/manifest.json). The
[AMD SDK notice](../../../dll/notices/AMD-SDK-LICENSE.md),
[LLVM notice](../../../dll/notices/LICENSE-LLVM.txt) and
[new-tool license](../../../LICENSE.new-code) remain applicable. The sources retain
optimizer-emitted module comments; those comments are not instructions to a
build tool or an agent.

Use a separate development tree when reproducing the capsule. The maintained
builder validates its own complete inventory and release identities.

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
