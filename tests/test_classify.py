# SPDX-License-Identifier: MIT
"""SDK drop classifier: skeletons and constant roles, classes A-E, host layout, gate, real drops."""

import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from dll.port import classify as cl
from dll.port import drop_profile as dp
from dll.port import sdk_intake as intake

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "dll/notices/AMD-SDK-LICENSE.md"
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
COMPILER = "dxcoob 1.8.2505.28 (9efbb6c32)"


def program(
    entry="fsr4_model_v07_fp8_no_scale_pass1",
    weights=(-33881605, 268697344),
    base=1152,
    scale="1.562500e-02",
    opcode=139,
    numthreads=(64, 1, 1),
    sm=(6, 6),
    alloca=16,
    lane=0,
    field=0,
    bindings=(
        "ScratchBuffer                         UAV    byte         r/w      U0            u11     1",
    ),
    compiler=COMPILER,
    shader_hash="00112233445566778899aabbccddeeff",
    table=("1.000000e+00", "2.000000e+00"),
    mask=1,
    extra="",
):
    """A program in the `dxc -dumpbin` layout the classifier reads."""
    rows = "\n".join("; " + b for b in bindings)
    stores = "\n".join(f"  store i32 {w}, i32* %{9 + i}, align 4" for i, w in enumerate(weights))
    return f""";
; shader hash: {shader_hash}
;
;PSVRuntimeInfo:
; Compute Shader
; NumThreads=({numthreads[0]},{numthreads[1]},{numthreads[2]})
; MinimumExpectedWaveLaneCount: 0
; MaximumExpectedWaveLaneCount: 4294967295
; EntryFunctionName: {entry}
;
; Resource Bindings:
;
; Name                                 Type  Format         Dim      ID      HLSL Bind  Count
; ------------------------------ ---------- ------- ----------- ------- -------------- ------
{rows}
;
target datalayout = "e-m:e-p:32:32-i1:32-i8:8-i16:16-i32:32-i64:64-f16:16-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-dx"

@icb = internal unnamed_addr constant [2 x float] [float {table[0]}, float {table[1]}], align 4

define void @{entry}() {{
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 11, i1 false)  ; CreateHandle(resourceClass,rangeId,index,nonUniformIndex)
  %2 = call i32 @dx.op.threadId.i32(i32 93, i32 0)  ; ThreadId(component)
  %3 = alloca [{alloca} x i32], align 4
  %4 = getelementptr [{alloca} x i32], [{alloca} x i32]* %3, i32 0, i32 {lane}
  %5 = add i32 %2, {base}
  %6 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 {opcode}, %dx.types.Handle %1, i32 %5, i32 undef, i8 {mask}, i32 4)  ; RawBufferLoad(srv,index,elementOffset,mask,alignment)
  %7 = extractvalue %dx.types.ResRet.i32 %6, {field}
  %8 = call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 0, i32 %7, i32 {weights[0]})  ; Dot4AddI8Packed(acc,a,b)
  %9 = getelementptr [{alloca} x i32], [{alloca} x i32]* %3, i32 0, i32 1
  %10 = getelementptr [{alloca} x i32], [{alloca} x i32]* %3, i32 0, i32 2
{stores}
  %11 = sitofp i32 %8 to float
  %12 = fmul fast float %11, {scale}
{extra}  br label %13

; <label>:13                                      ; preds = %0
  ret void
}}

; Function Attrs: nounwind readnone
declare i32 @dx.op.dot4AddPacked.i32(i32, i32, i32, i32) #1

!llvm.ident = !{{!0}}
!dx.shaderModel = !{{!3}}
!dx.entryPoints = !{{!11}}

!0 = !{{!"{compiler}"}}
!3 = !{{!"cs", i32 {sm[0]}, i32 {sm[1]}}}
!11 = !{{void ()* @{entry}, !"{entry}", null, null, !12}}
!12 = !{{i32 4, !13}}
!13 = !{{i32 {numthreads[0]}, i32 {numthreads[1]}, i32 {numthreads[2]}}}
"""


def dxbc(entry, model=0x50066):
    """A DXBC container with a PSV0 entry name and a DXIL part of the given program version."""
    names = b"\0" + entry.encode() + b"\0"
    names += bytes(-len(names) % 4)
    info = bytearray(52)
    struct.pack_into("<I", info, 48, 1)
    psv = struct.pack("<I", 52) + info + struct.pack("<II", 0, len(names)) + names
    parts = [(b"PSV0", psv), (b"DXIL", struct.pack("<II", model, 0))]
    header = 32 + 4 * len(parts)
    offsets, body = [], b""
    for tag, payload in parts:
        offsets.append(header + len(body))
        body += tag + struct.pack("<I", len(payload)) + payload
    size = header + len(body)
    table = b"".join(struct.pack("<I", o) for o in offsets)
    return b"DXBC" + bytes(16) + struct.pack("<III", 1, size, len(parts)) + table + body


def row(offset, text, sm="6.6", pointers=(8,), sha=None):
    analysis = cl.analyze(text)
    return dict(
        offset=offset,
        bytes=len(text),
        sha256=sha or hashlib.sha256(text.encode()).hexdigest(),
        entry=analysis["entry"],
        sm=sm,
        kind=5,
        pointers=list(pointers),
        analysis=analysis,
    )


class AnalyzeTests(unittest.TestCase):
    def test_literal_constants_leave_the_skeleton(self):
        a = cl.analyze(program())
        b = cl.analyze(program(weights=(5, 6), base=2048, scale="2.500000e-01"))
        self.assertEqual(a["skeleton"], b["skeleton"])
        self.assertNotEqual(a["constants"], b["constants"])
        self.assertEqual(cl.constant_differences(a, b), {"f": 1, "i": 1, "s": 2, "w": 1})

    def test_roles(self):
        a = cl.analyze(program())
        literals = a["constants"].split(cl.SEP)
        pairs = list(zip(literals, a["roles"]))
        self.assertIn(("-33881605", "w"), pairs)  # dot4 literal operand
        self.assertIn(("268697344", "s"), pairs)  # stored weight
        self.assertIn(("1152", "i"), pairs)
        self.assertIn(("1.562500e-02", "f"), pairs)
        self.assertIn(("1.000000e+00", "g"), pairs)
        self.assertIn(("4", "x"), pairs)  # rawBufferLoad alignment
        self.assertNotIn(("57", "x"), pairs)  # dx.op opcodes stay in the skeleton
        self.assertEqual(a["numthreads"], [64, 1, 1])
        self.assertEqual(a["wave"], [0, 4294967295])
        self.assertEqual(a["compiler"], COMPILER)
        self.assertEqual(len(a["bindings"]), 1)
        self.assertEqual(a["ops"]["dot4AddPacked"], 1)
        self.assertEqual(a["blocks"], 2)

    def test_entry_name_shader_hash_and_compiler_are_normalized(self):
        a = cl.analyze(program())
        b = cl.analyze(
            program(entry="fsr_rcas_pass", shader_hash="f" * 32, compiler="dxcoob 1.9.1 (abc)")
        )
        self.assertEqual(a["skeleton"], b["skeleton"])
        self.assertEqual(a["interface"], b["interface"])
        self.assertEqual(b["entry"], "fsr_rcas_pass")
        self.assertEqual(b["compiler"], "dxcoob 1.9.1 (abc)")

    def test_structural_literals_stay_in_the_skeleton(self):
        base = cl.analyze(program())["skeleton"]
        for change in (
            dict(opcode=68),
            dict(alloca=32),
            dict(lane=3),
            dict(field=1),
            dict(numthreads=(32, 1, 1)),
            dict(sm=(6, 4)),
            dict(bindings=("InitializerBuffer texture byte r/o T0 t18 1",)),
            dict(weights=(1, 2, 3)),
            dict(extra="  %14 = mul i32 %5, 3\n"),
        ):
            with self.subTest(change=change):
                self.assertNotEqual(cl.analyze(program(**change))["skeleton"], base)

    def test_interface_follows_bindings_and_threads(self):
        a = cl.analyze(program())
        self.assertEqual(a["interface"], cl.analyze(program(weights=(1, 2), base=7))["interface"])
        self.assertNotEqual(a["interface"], cl.analyze(program(numthreads=(32, 1, 1)))["interface"])

    def test_shader_hash_and_entry_comments_are_ignored_anywhere(self):
        a = cl.analyze(program())
        later = "; shader hash: " + "e" * 32 + "\n; EntryFunctionName: other\ntarget triple"
        self.assertEqual(
            cl.analyze(program().replace("target triple", later))["skeleton"],
            cl.analyze(program().replace("target triple", later.replace("e" * 32, "f" * 32)))[
                "skeleton"
            ],
        )
        self.assertNotEqual(a["skeleton"], cl.analyze(program(extra="; note\n"))["skeleton"])

    def test_refusals(self):
        with self.assertRaises(cl.ClassifyError):
            cl.analyze("; nothing\n")
        text = program() + "define void @second() {\n}\n"
        with self.assertRaises(cl.ClassifyError):
            cl.analyze(text)
        a, b = cl.analyze(program()), cl.analyze(program(weights=(1, 2, 3)))
        with self.assertRaises(cl.ClassifyError):
            cl.constant_differences(a, b)


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        P1, POST = "fsr4_model_v07_fp8_no_scale_pass1", "fsr4_model_v07_fp8_no_scale_pass1_post"
        old = [
            row(0x100, program()),
            row(0x200, program(entry=POST, weights=(1, 2))),
            row(0x300, program(entry="main")),
            row(0x400, program(entry="gone", alloca=8)),
            row(0x500, program(entry="CS", numthreads=(8, 8, 1))),
            row(0x600, program(entry="CS", numthreads=(8, 8, 1), base=9)),
        ]
        new = [
            row(0x1000, program()),  # A
            row(0x1100, program()),  # A again (multiplicity)
            row(0x1200, program(entry=POST, weights=(1, 3))),  # B data
            row(0x1300, program(entry=POST, weights=(1, 2), base=99)),  # B constants
            row(0x1400, program(entry="fsr_rcas_pass")),  # B rename (from main or pass1)
            row(0x1500, program(), sha="c" * 64),  # B container: same text, other bytes
            row(0x1600, program(entry="CS", numthreads=(8, 8, 1), extra="  %14 = mul i32 %5, 3\n")),
            row(0x1700, program(entry=P1, sm=(6, 4)), sm="6.4"),  # D: only SM 6.6 in the old drop
            row(0x1800, program(entry="brand_new", alloca=64)),  # D (no interface pairing)
        ]
        rows, removed = cl.classify(old, new)
        by = {r["offset"]: r for r in rows}
        self.assertEqual([by[o]["cls"] for o in (0x1000, 0x1100)], ["A", "A"])
        self.assertEqual(by[0x1100]["base"], [0x100])
        self.assertEqual((by[0x1200]["cls"], by[0x1200]["kind"]), ("B", "data"))
        self.assertEqual(by[0x1200]["base"], [0x200])
        self.assertEqual(by[0x1200]["differing"], {"s": 1})
        self.assertEqual(by[0x1300]["kind"], "constants")
        self.assertEqual(by[0x1400]["kind"], "rename")
        self.assertTrue(by[0x1400]["renamed"])
        self.assertEqual(by[0x1500]["kind"], "container")
        self.assertEqual(by[0x1500]["base"], [0x100])  # same entry preferred
        self.assertEqual(by[0x1600]["cls"], "C")
        self.assertEqual(
            by[0x1600]["change"]["instructions"][1] - 1, by[0x1600]["change"]["instructions"][0]
        )
        self.assertEqual(by[0x1700]["cls"], "D")
        self.assertEqual(by[0x1700]["old_shader_models"], ["6.6"])
        self.assertEqual(by[0x1800]["cls"], "D")
        self.assertEqual(by[0x1600]["base"], [0x500])
        self.assertEqual({r["offset"] for r in removed}, {0x300, 0x400, 0x600})
        summary = cl.summarize(rows, removed, old)
        self.assertEqual(summary["classes"]["A"], 2)
        self.assertEqual(summary["unique_programs"]["A"], 1)
        self.assertEqual(
            summary["b_kinds"], {"constants": 1, "container": 1, "data": 1, "rename": 1}
        )
        rebuilt = [row(0x2000, program(compiler="dxcoob 1.9.2607 (x)"))]
        self.assertEqual(cl.classify(old, rebuilt)[0][0]["kind"], "compiler")

    def test_b_takes_the_closest_base_of_the_same_entry(self):
        old = [
            row(0x10, program(weights=(1, 2), base=5)),
            row(0x20, program(weights=(7, 8), base=5)),
            row(0x30, program(entry="other", weights=(7, 9), base=5)),
        ]
        rows, _ = cl.classify(old, [row(0x40, program(weights=(7, 9), base=5))])
        self.assertEqual((rows[0]["cls"], rows[0]["base"], rows[0]["candidates"]), ("B", [0x20], 2))

    def test_c_prefers_unused_old_programs(self):
        old = [
            row(0x10, program(entry="x", alloca=8)),
            row(0x20, program(entry="x", alloca=9)),
            row(0x30, program(entry="main", alloca=4)),
        ]
        new = [
            row(0x40, program(entry="x", alloca=10)),
            row(0x50, program(entry="x", alloca=11)),
            row(0x60, program(entry="fsr_rcas_pass", alloca=5)),  # renamed and changed: D + E
        ]
        rows, removed = cl.classify(old, new)
        self.assertEqual([r["cls"] for r in rows], ["C", "C", "D"])
        self.assertEqual(sorted(r["base"][0] for r in rows[:2]), [0x10, 0x20])
        self.assertEqual([r["offset"] for r in removed], [0x30])

    def test_rc_view_on_either_side(self):
        old = [
            row(0x10, program()),
            row(0x20, program(entry="y", weights=(3, 4))),
            row(0x30, program(entry="z")),
        ]
        new = [row(0x40, program()), row(0x50, program(entry="y", weights=(3, 5)))]
        rows, removed = cl.classify(old, new)
        manifest = dict(
            label="rc",
            sdk_sha256="o" * 64,
            replacements=[dict(original_offset=o) for o in (0x10, 0x20, 0x30, 0x99)],
        )
        view = cl.rc_rows(manifest, "o" * 64, "n" * 64, rows, {0x10, 0x20, 0x30})
        self.assertEqual(view["side"], "old")
        self.assertEqual((view["rows"], view["rows_total"]), (3, 4))
        self.assertEqual(view["classes"], {"A": 1, "B": 1, "E": 1})
        self.assertEqual(view["slots"]["0x20"], {"cls": "B", "new": [0x50]})
        manifest["replacements"] = [dict(original_offset=0x40), dict(original_offset=0x50)]
        view = cl.rc_rows(manifest, "x" * 64, "o" * 64, rows, set())
        self.assertEqual((view["side"], view["classes"]), ("new", {"A": 1, "B": 1}))
        self.assertIsNone(cl.rc_rows(manifest, "x" * 64, "y" * 64, rows, set())["side"])


def part(**fields):
    return dict(status="ok", **fields)


def layout(**changes):
    base = {name: part(site=0x10) for name in cl.PROFILE_PARTS}
    base.update(sections=[dict(name=".text", bytes=1, virtual_size=1)], strict_derive="ok")
    base.update(changes)
    return base


class LayoutTests(unittest.TestCase):
    def test_verdicts(self):
        old = layout(
            **{
                "int8-eligibility": dict(status="absent", reason="x"),
                "provider_label": dict(status="absent", reason="y"),
            }
        )
        new = layout(
            shaders=part(site=0x10, pointer_sections=[".data"]),
            **{
                "buffer-uav-barrier": part(site=0x20),
                "provider_label": dict(status="absent", reason="y"),
            },
        )
        new["pe"] = dict(status="absent", reason="certificate")
        verdicts = {p["part"]: p["verdict"] for p in cl.compare_layout(old, new)["parts"]}
        self.assertTrue(verdicts["pe"].startswith("lost"))
        self.assertTrue(verdicts["shaders"].startswith("changed"))
        self.assertEqual(verdicts["int8-eligibility"], "new in this drop")
        self.assertTrue(verdicts["buffer-uav-barrier"].startswith("moved"))
        self.assertEqual(verdicts["provider_label"], "absent in both")

    def test_sized_tables_follow_lea_and_size(self):
        data = bytearray(0x2000)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", data, 0x84, 0x8664, 2)
        struct.pack_into("<H", data, 0x94, 240)
        struct.pack_into("<H", data, 0x98, 0x20B)
        struct.pack_into("<Q", data, 0x98 + 24, 0x180000000)
        for index, (name, rva, raw, size, flags) in enumerate(
            [
                (b".text", 0x1000, 0x200, 0x200, 0x60000020),
                (b".rdata", 0x2000, 0x400, 0x1C00, 0x40000040),
            ]
        ):
            start = 0x98 + 240 + 40 * index
            data[start : start + 8] = name.ljust(8, b"\0")
            struct.pack_into("<IIII", data, start + 8, size, rva, size, raw)
            struct.pack_into("<I", data, start + 36, flags)
        code = 0x200
        data[code : code + 5] = b"\xb8" + struct.pack("<I", 0x1000)  # mov eax, 0x1000
        lea = code + 5  # lea rcx, [rip + table]: table at .rdata + 0x100 (RVA 0x2100)
        data[lea : lea + 3] = b"\x48\x8d\x0d"
        struct.pack_into("<i", data, lea + 3, 0x2100 - (0x1000 + 5 + 7))
        data[0x500:0x600] = bytes(range(256))
        tables = cl.sized_tables(dp.Image(bytes(data)), {})
        self.assertEqual([(t["offset"], t["bytes"]) for t in tables], [(0x500, 0x1000)])
        self.assertEqual(cl.sized_tables(dp.Image(bytes(data)), {0x500: dict(bytes=16)}), [])
        overlapping = bytearray(data)  # a second LEA into the first table's range: both dropped
        overlapping[0x210:0x215] = b"\xb8" + struct.pack("<I", 0x1000)
        overlapping[0x215:0x218] = b"\x48\x8d\x15"
        struct.pack_into("<i", overlapping, 0x218, 0x2180 - (0x1000 + 0x15 + 7))
        self.assertEqual(cl.sized_tables(dp.Image(bytes(overlapping)), {}), [])
        for bogus in (0x10000000, 0x1C00):  # above the maximum; past the end of .rdata
            moved = bytearray(data)
            moved[0x205:0x20C] = bytes(7)
            moved[0x220:0x223] = b"\x48\x8d\x0d"  # the LEA 32 bytes after the real size
            struct.pack_into("<i", moved, 0x223, 0x2100 - (0x1000 + 0x20 + 7))
            moved[0x227:0x22C] = b"\xb9" + struct.pack("<I", bogus)  # closer, but not a size
            found = cl.sized_tables(dp.Image(bytes(moved)), {})
            self.assertEqual([(t["offset"], t["bytes"]) for t in found], [(0x500, 0x1000)])
        # No relocation directory: every part that needs it is reported, the tables still are.
        record = dict(tag="v9.9.9", commit="1" * 40, dll=dict(sha256=cl.sha256(bytes(data))))
        record["dll"]["bytes"] = len(data)
        found = cl.host_layout(cl.Drop(record, bytes(data), None))
        self.assertIn("outside section", found["relocations"])
        for name in cl.PROFILE_PARTS:
            self.assertEqual(found[name]["status"], "absent", name)
        self.assertTrue(found["strict_derive"].startswith("refused"))
        self.assertEqual(len(found["sized_tables"]), 1)
        old = cl.Drop(dict(tag="o", dll=dict(sha256="")), bytes(data), None)
        moved = cl.Drop(
            dict(tag="n", dll=dict(sha256="")), b"\0" * 64 + bytes(data[0x500:0x1500]), None
        )
        result = cl.compare_tables(old, moved, tables, [])
        self.assertEqual((result["old_kept"], result["new_unique"]), (1, 0))


def fake_dll():
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 240)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    struct.pack_into("<II", data, 0x98 + 112, 0x1000, 40)
    table = 0x98 + 240
    data[table : table + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, table + 8, 0x200, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", data, 0x200 + 12, 0x1040)
    name = intake.DLL_NAME.encode() + b"\0"
    data[0x240 : 0x240 + len(name)] = name
    struct.pack_into("<II", data, 0x98 + 144, len(data), 16)
    return bytes(data + struct.pack("<IHH", 16, 0x200, 2) + b"\x30" * 8)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.dll, self.license = fake_dll(), NOTICE.read_bytes()
        record = intake.make_record("v9.9.9", "1" * 40, self.dll, self.license)
        self.allowlist = dict(schema=1, repository=intake.REPOSITORY, drops=[record])
        self.drop = self.folder / "v9.9.9"
        self.drop.mkdir()
        (self.drop / intake.DLL_NAME).write_bytes(self.dll)
        (self.drop / intake.LICENSE_NAME).write_bytes(self.license)
        (self.drop / "intake.json").write_text(json.dumps(dict(record=record)))

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_an_allowlisted_drop(self):
        drop = cl.Drop.load(self.drop, self.allowlist)
        self.assertEqual((drop.tag, drop.data), ("v9.9.9", self.dll))

    def test_refusals(self):
        cases = {
            "other DLL bytes": lambda: (self.drop / intake.DLL_NAME).write_bytes(self.dll + b"\0"),
            "other license": lambda: (self.drop / intake.LICENSE_NAME).write_bytes(
                self.license + b"x"
            ),
            "symlinked DLL": lambda: (
                (self.drop / intake.DLL_NAME).unlink(),
                (self.drop / intake.DLL_NAME).symlink_to(self.folder / "elsewhere"),
            ),
            "missing intake": lambda: (self.drop / "intake.json").unlink(),
            "edited intake": lambda: (self.drop / "intake.json").write_text(
                json.dumps(dict(record=dict(self.allowlist["drops"][0], commit="2" * 40)))
            ),
            "unlisted tag": lambda: (self.drop / "intake.json").write_text(
                json.dumps(dict(record=dict(tag="v0.0.1")))
            ),
        }
        for name, damage in cases.items():
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                damage()
                with self.assertRaises(intake.Refused):
                    cl.Drop.load(self.drop, self.allowlist)

    def test_only_gated_drops_are_disassembled_with_the_pinned_dxc(self):
        with self.assertRaises(cl.ClassifyError):
            cl.analyze_drop(dict(data=self.dll), lambda blob: "")
        root = self.folder / "dxc"
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        (root / "bin/dxc").write_bytes(b"not dxc")
        (root / "lib/libdxcompiler.so").write_bytes(b"")
        with self.assertRaises(cl.ClassifyError):
            cl.Disassembler(root, self.folder)

    def test_analyze_drop_checks_the_entry_and_uses_the_cache(self):
        data = bytearray(0x2000)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", data, 0x84, 0x8664, 1)
        struct.pack_into("<H", data, 0x94, 240)
        struct.pack_into("<H", data, 0x98, 0x20B)
        start = 0x98 + 240
        data[start : start + 8] = b".rdata\0\0"
        struct.pack_into("<IIII", data, start + 8, 0x1C00, 0x2000, 0x1C00, 0x400)
        struct.pack_into("<I", data, start + 36, 0x40000040)
        blob = dxbc("fsr4_model_v07_fp8_no_scale_pass1")
        data[0x600 : 0x600 + len(blob)] = blob
        drop = cl.Drop(dict(tag="v9.9.9"), bytes(data), None)
        calls = []

        def disassemble(container, entry="fsr4_model_v07_fp8_no_scale_pass1"):
            calls.append(container)
            return program(entry=entry)

        cache = cl.AnalysisCache(self.folder / "cache", "x")
        rows, error = cl.analyze_drop(drop, disassemble, cache)
        self.assertIn("outside section", error)  # no relocations: no slots, still classified
        self.assertEqual(
            [(r["offset"], r["sm"], r["pointers"]) for r in rows], [(0x600, "6.6", [])]
        )
        self.assertEqual(calls, [blob])
        cl.analyze_drop(drop, disassemble, cache)
        self.assertEqual(len(calls), 1)  # the second run reads the cache
        self.assertEqual(cl.analyze_drop(drop, disassemble, entries="other")[0], [])
        with self.assertRaises(cl.ClassifyError):
            cl.analyze_drop(drop, lambda container: program(entry="renamed"))

    def test_outputs_and_caches_stay_outside_the_tree(self):
        with self.assertRaises(ValueError):
            cl.AnalysisCache(ROOT / "dll", "x")
        cache = cl.AnalysisCache(self.folder / "cache", "x")
        cache.put("ab" * 32, dict(entry="e"))
        self.assertEqual(cache.get("ab" * 32), dict(entry="e", sha256="ab" * 32))
        self.assertIsNone(cache.get("cd" * 32))


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class PublicDropTests(unittest.TestCase):
    """SDK 2.2.0 -> 2.3.0 on passes 3 and 12, and the host layout of both drops."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        out = Path(cls.temp.name) / "drops"
        for tag in ("v2.2.0", "v2.3.0"):
            intake.ingest(tag, CACHE, out, offline=True)
        cls.old, cls.new = cl.Drop.load(out / "v2.2.0"), cl.Drop.load(out / "v2.3.0")
        work = Path(cls.temp.name) / "work"
        work.mkdir()
        cls.disassemble = cl.Disassembler(DXC, work)
        entries = r"fsr4_model_v07_fp8_no_scale_pass(3|12)(_post)?"
        cls.result = cl.diff(cls.old, cls.new, cls.disassemble, jobs=4, entries=entries)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def family(self, entry, sm):
        rows = [
            f for f in self.result["summary"]["families"] if (f["entry"], f["sm"]) == (entry, sm)
        ]
        self.assertEqual(len(rows), 1)
        return {k: v for k, v in rows[0].items() if k in "ABCDE" or k in ("old", "new")}

    def test_pass_classes(self):
        p = "fsr4_model_v07_fp8_no_scale_pass"
        self.assertEqual(self.family(p + "3", "6.4"), dict(old=3, new=3, A=3, B=0, C=0, D=0, E=0))
        self.assertEqual(self.family(p + "12", "6.4"), dict(old=3, new=3, A=0, B=0, C=3, D=0, E=0))
        self.assertEqual(self.family(p + "12", "6.6"), dict(old=0, new=6, A=0, B=0, C=0, D=6, E=0))
        self.assertEqual(self.family(p + "3", "6.6")["D"], 6)
        self.assertEqual(self.result["rc"]["side"], "new")
        self.assertEqual(self.result["rc"]["rows"], 12)
        self.assertEqual(self.result["rc"]["classes"], {"D": 12})

    def test_host_layout(self):
        parts = {p["part"]: p for p in self.result["layout"]["parts"]}
        self.assertEqual(parts["shaders"]["old"]["pointer_sections"], [".rdata"])
        self.assertEqual(parts["shaders"]["new"]["pointer_sections"], [".data"])
        self.assertEqual(parts["int8-eligibility"]["verdict"], "new in this drop")
        self.assertEqual(parts["provider_label"]["verdict"], "new in this drop")
        self.assertTrue(parts["buffer-uav-barrier"]["verdict"].startswith("moved"))
        self.assertTrue(self.result["layout"]["strict_derive"]["old"].startswith("refused"))
        self.assertEqual(self.result["layout"]["strict_derive"]["new"], "ok")
        weights = {row["bytes"]: row for row in self.result["tables"]["by_size"]}[0x20000]
        # Six FP8 initializers (two distinct) carried over byte for byte; six INT8 ones added.
        self.assertEqual(
            {
                k: weights[k]
                for k in ("old", "old_distinct", "old_kept", "new", "new_distinct", "new_in_old")
            },
            dict(old=6, old_distinct=2, old_kept=6, new=12, new_distinct=4, new_in_old=6),
        )

    def test_self_diff_is_all_a(self):
        result = cl.diff(
            self.new,
            self.new,
            self.disassemble,
            jobs=4,
            entries=r"fsr4_model_v07_fp8_no_scale_pass12",
        )
        self.assertEqual(result["summary"]["classes"], dict(A=9, B=0, C=0, D=0, E=0))
        self.assertEqual((result["rc"]["side"], result["rc"]["classes"]), ("old", {"A": 6}))
        self.assertTrue(all(p["verdict"] == "unchanged" for p in result["layout"]["parts"]))


if __name__ == "__main__":
    unittest.main()
