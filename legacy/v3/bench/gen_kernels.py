import subprocess, re, sys

UNROLL = int(sys.argv[1]) if len(sys.argv) > 1 else 16
ITER = int(sys.argv[2]) if len(sys.argv) > 2 else 16384
SUF = sys.argv[3] if len(sys.argv) > 3 else ""


def gen(name, acc, init, dot):
    L = [
        "; SPIR-V", "; Version: 1.3",
        "OpCapability Shader", "OpCapability DotProduct", "OpCapability DotProductInput4x8BitPacked",
        "OpMemoryModel Logical GLSL450",
        'OpEntryPoint GLCompute %main "main" %glid %bufA %bufB %bufO',
        "OpExecutionMode %main LocalSize 64 1 1",
        "OpDecorate %arr_u32 ArrayStride 4", "OpMemberDecorate %buf 0 Offset 0", "OpDecorate %buf Block",
        "OpDecorate %bufA DescriptorSet 0", "OpDecorate %bufA Binding 0",
        "OpDecorate %bufB DescriptorSet 0", "OpDecorate %bufB Binding 1",
        "OpDecorate %bufO DescriptorSet 0", "OpDecorate %bufO Binding 2",
        "OpDecorate %glid BuiltIn GlobalInvocationId",
        "%void = OpTypeVoid", "%fn = OpTypeFunction %void",
        "%u32 = OpTypeInt 32 0", "%i32 = OpTypeInt 32 1", "%uvec3 = OpTypeVector %u32 3",
        "%ptr_in_uvec3 = OpTypePointer Input %uvec3", "%glid = OpVariable %ptr_in_uvec3 Input",
        "%arr_u32 = OpTypeRuntimeArray %u32", "%buf = OpTypeStruct %arr_u32",
        "%ptr_sb_buf = OpTypePointer StorageBuffer %buf",
        "%bufA = OpVariable %ptr_sb_buf StorageBuffer",
        "%bufB = OpVariable %ptr_sb_buf StorageBuffer",
        "%bufO = OpVariable %ptr_sb_buf StorageBuffer",
        "%ptr_sb_u32 = OpTypePointer StorageBuffer %u32",
        "%zero = OpConstant %i32 0",
    ]
    L += [f"%c{k} = OpConstant %u32 {k}" for k in range(UNROLL)]
    L += [
        "%sh8 = OpConstant %u32 8", "%sh16 = OpConstant %u32 16",
        f"%cu = OpConstant %u32 {UNROLL}", "%sh24 = OpConstant %u32 24",
        "%ff = OpConstant %u32 255", "%one = OpConstant %i32 1",
        f"%N = OpConstant %i32 {ITER}", "%true = OpTypeBool",
        "%main = OpFunction %void None %fn", "%entry = OpLabel",
        "%id3 = OpLoad %uvec3 %glid", "%idx = OpCompositeExtract %u32 %id3 0",
        "%base = OpIMul %u32 %idx %cu",
        "%po = OpAccessChain %ptr_sb_u32 %bufO %zero %idx",
    ]
    for k in range(UNROLL):
        L.append(f"%o{k} = OpIAdd %u32 %base %c{k}")
    for k in range(UNROLL):
        L += [
            f"%pa{k} = OpAccessChain %ptr_sb_u32 %bufA %zero %o{k}",
            f"%pb{k} = OpAccessChain %ptr_sb_u32 %bufB %zero %o{k}",
            f"%a{k} = OpLoad %u32 %pa{k}",
            f"%b{k} = OpLoad %u32 %pb{k}",
        ]
    L += [
        "OpBranch %loop", "%loop = OpLabel",
        "%i = OpPhi %i32 %zero %entry %inext %cont",
        f"%acc = OpPhi {acc} {init} %entry %t{UNROLL - 1} %cont",
        "%cond = OpSLessThan %true %i %N",
        "OpLoopMerge %done %cont None",
        "OpBranchConditional %cond %body %done", "%body = OpLabel",
        "%iu = OpBitcast %u32 %i",
        "%m0 = OpBitwiseAnd %u32 %iu %ff",
        "%m1 = OpShiftLeftLogical %u32 %m0 %sh8",
        "%m2 = OpBitwiseOr %u32 %m0 %m1",
        "%m3 = OpShiftLeftLogical %u32 %m0 %sh16",
        "%m4 = OpBitwiseOr %u32 %m2 %m3",
        "%m5 = OpShiftLeftLogical %u32 %m0 %sh24",
        "%mb = OpBitwiseOr %u32 %m4 %m5",
    ]
    prev = "%acc"
    for k in range(UNROLL):
        L.append(f"%x{k} = OpBitwiseXor %u32 %a{k} %mb")
        L.append(dot(k).replace("$P", prev))
        prev = f"%t{k}"
    L += [
        "OpBranch %cont", "%cont = OpLabel", "%inext = OpIAdd %i32 %i %one", "OpBranch %loop",
        "%done = OpLabel", "%accu = OpBitcast %u32 %acc", "OpStore %po %accu", "OpReturn", "OpFunctionEnd",
    ]

    asm = f"dot_{name}{SUF}.spvasm"
    spv = f"dot_{name}{SUF}.spv"
    open(asm, "w").write("\n".join(L) + "\n")
    subprocess.run(["spirv-as", "--target-env", "vulkan1.3", asm, "-o", spv], check=True)
    subprocess.run(["spirv-val", "--target-env", "vulkan1.3", spv], check=True)
    dis = subprocess.run(["spirv-dis", spv], capture_output=True, text=True, check=True).stdout
    n = len(re.findall(r"Op(S|U|SU)Dot\b", dis))
    print(f"OK {spv} dots={n}")


gen("sdot", "%i32", "%zero", lambda k: f"%d{k} = OpSDotKHR %i32 %x{k} %b{k} PackedVectorFormat4x8Bit\n%t{k} = OpIAdd %i32 $P %d{k}")
gen("udot", "%u32", "%c0", lambda k: f"%d{k} = OpUDotKHR %u32 %x{k} %b{k} PackedVectorFormat4x8Bit\n%t{k} = OpIAdd %u32 $P %d{k}")
gen("sudot", "%i32", "%zero", lambda k: f"%ai{k} = OpBitcast %i32 %x{k}\n%d{k} = OpSUDotKHR %i32 %ai{k} %b{k} PackedVectorFormat4x8Bit\n%t{k} = OpIAdd %i32 $P %d{k}")
