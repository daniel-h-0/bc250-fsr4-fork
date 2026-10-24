# SPDX-License-Identifier: MIT
"""C10 family coverage, misc group (dll/port): P5 stream pairing, transport, vector guards,
the whole-program differential check, and the five c10 recipes on the real SDK."""

import itertools
import json
import os
import random
import re
import struct
import tempfile
import unittest
from pathlib import Path

from dll.port import differential, guards, ir, lower_pass, packing, transport

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"
HANDLE = (
    "  %h0 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind"
    " { i32 0, i32 0, i32 0, i8 1 }, i32 0, i1 false)\n"
)


def wrap(value):
    return ((value + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)


class Program:
    """A synthetic compute program: words loaded from u0, constant dot4 chains, stores."""

    def __init__(self):
        self.lines, self.serial, self.words = [], 0, 0

    def new(self, rhs):
        self.serial += 1
        self.lines.append(f"  %v{self.serial} = {rhs}")
        return f"%v{self.serial}"

    def word(self):
        self.words += 1
        load = self.new(
            "call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h0,"
            f" i32 {4 * (self.words - 1)}, i32 undef, i8 1, i32 4)"
        )
        return self.new(f"extractvalue %dx.types.ResRet.i32 {load}, 0")

    def chain(self, base, words, weights):
        acc = base
        for word, w in zip(words, weights):
            packed = wrap(sum((b & 255) << (8 * k) for k, b in enumerate(w)))
            acc = self.new(
                f"call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 {acc}, i32 {word}, i32 {packed})"
            )
        return acc

    def text(self, tails):
        stores = [
            "  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h0, i32"
            f" {4096 + 4 * k}, i32 undef, i32 {t}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)"
            for k, t in enumerate(tails)
        ]
        return (
            "define void @main() {\nv0:\n"
            + HANDLE
            + "\n".join(self.lines + stores)
            + "\n  ret void\n}\n\ndeclare i32 @dx.op.dot4AddPacked.i32(i32, i32, i32, i32) #0\n"
        )


def weights(rng, n, scale=128, extreme=0.0):
    return [
        [
            rng.choice((-128, 127)) if rng.random() < extreme else rng.randint(-scale, scale - 1)
            for _ in range(4)
        ]
        for _ in range(n)
    ]


def same(test, a, b, trials=16, model=None):
    """Both programs give identical stores on the differential check's run plans."""
    stats = differential.compare(
        [differential.Program(a), differential.Program(b)], trials, 5, model, ["a", "b"]
    )
    test.assertEqual(stats["runs"], trials)
    return stats


class StreamPairing(unittest.TestCase):
    def spatial(self, rng, columns, big=()):
        p = Program()
        words = [p.word() for _ in range(36)]
        bias = p.word()
        tails = [
            p.chain(bias, words, weights(rng, 36, scale=127 if c in big else 6, extreme=0.1))
            for c in range(columns)
        ]
        return p.text(tails)

    def test_pairing_is_exact_and_never_costlier(self):
        rng = random.Random(11)
        for case in range(6):
            text = self.spatial(rng, 4, big={case % 4, (case + 1) % 4} if case % 2 else ())
            source, record = packing.stream_outputs(text, width=4, primary=True)
            paired, proof = packing.stream_outputs(text, width=4, primary=True, pairing="spatial")
            self.assertEqual(ir.count_dots(paired), 0)
            self.assertLessEqual(proof["spatial_chunks"], proof["source_spatial_chunks"])
            self.assertEqual(
                proof["source_spatial_chunks"], sum(len(g["chunks"]) for g in record["groups"])
            )
            same(self, text, ir.canonical_ssa(paired), trials=12)
            if proof["reordered_groups"]:
                self.assertNotEqual(paired, source)
            else:
                self.assertEqual(paired, source)

    def test_large_columns_share_a_pair(self):
        """Flushes are per pair: two large-weight outputs in one pair flush together."""
        rng = random.Random(12)
        text = self.spatial(rng, 4, big={0, 2})
        _, proof = packing.stream_outputs(text, width=4, primary=True, pairing="spatial")
        self.assertEqual(proof["reordered_groups"], 1)
        self.assertLess(proof["spatial_chunks"], proof["source_spatial_chunks"])
        self.assertEqual(proof["groups"][0]["pairing"]["order"], [0, 2, 1, 3])

    def test_matching_is_the_brute_force_optimum(self):
        rng = random.Random(13)
        cols = [
            dict(chain=[dict(w=w) for w in weights(rng, 9, scale=rng.choice((8, 127)))])
            for _ in range(6)
        ]
        order, record = packing.best_pairing(cols)
        cost = lambda o: sum(  # noqa: E731
            packing.pair_chunks(cols[o[i]], cols[o[i + 1]]) for i in range(0, len(o), 2)
        )
        best = min(cost(list(perm)) for perm in itertools.permutations(range(6)))
        self.assertEqual(cost(order), best)
        self.assertEqual(record["chunks"], best)
        if cost(list(range(6))) == best:
            self.assertEqual(order, list(range(6)))  # the source order is kept on ties

    def test_refusals(self):
        rng = random.Random(14)
        with self.assertRaisesRegex(ir.LoweringError, "four outputs"):
            packing.stream_outputs(self.spatial(rng, 2), primary=True, pairing="spatial")
        with self.assertRaisesRegex(ir.LoweringError, "Unsupported stream pairing"):
            packing.stream_outputs(self.spatial(rng, 4), primary=True, pairing="optimal")
        p = Program()
        words = [p.word() for _ in range(4)]
        text = p.text([p.chain("0", words, weights(rng, 4)) for _ in range(4)])
        with self.assertRaisesRegex(ir.LoweringError, "No spatial stream groups"):
            packing.stream_outputs(text, primary=True, pairing="spatial")


GUARDED = """@bc250_expected = internal unnamed_addr constant [16 x i32] [{table}], align 16

define void @main() {{
v0:
{handle}  %v1 = call i32 @dx.op.threadId.i32(i32 93, i32 2)
  %v2 = icmp eq i32 %v1, 0
  br i1 %v2, label %v3, label %v60

v3:
  %v4 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind {{ i32 18, i32 18, i32 0, i8 0 }}, i32 18, i1 false)
  %v5 = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %v4, %dx.types.ResourceProperties {{ i32 11, i32 0 }})
  %v6 = call i32 @dx.op.waveGetLaneIndex(i32 111)
  %v7 = call i32 @dx.op.waveGetLaneCount(i32 112)
  %v8 = shl i32 %v6, 2
  %v9 = shl i32 %v7, 2
  br label %v10

v10:
  %v11 = phi i32 [ %v8, %v3 ], [ %v42, %v13 ]
  %v12 = phi i1 [ true, %v3 ], [ %v41, %v13 ]
  %v50 = icmp ult i32 %v11, 16
  br i1 %v50, label %v13, label %v51

v13:
  %v14 = shl i32 %v11, 2
  %v15 = add i32 %v14, 64
  %v16 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %v5, i32 %v15, i32 undef, i8 15, i32 4)
  %v17 = getelementptr [16 x i32], [16 x i32]* @bc250_expected, i32 0, i32 %v11
  %v18 = load i32, i32* %v17, align 16
  %v19 = extractvalue %dx.types.ResRet.i32 %v16, 0
  %v20 = icmp eq i32 %v19, %v18
  %v21 = and i1 %v12, %v20
  %v22 = or i32 %v11, 1
  %v23 = getelementptr [16 x i32], [16 x i32]* @bc250_expected, i32 0, i32 %v22
  %v24 = load i32, i32* %v23, align 4
  %v25 = extractvalue %dx.types.ResRet.i32 %v16, 1
  %v26 = icmp eq i32 %v25, %v24
  %v27 = and i1 %v21, %v26
  %v28 = or i32 %v11, 2
  %v29 = getelementptr [16 x i32], [16 x i32]* @bc250_expected, i32 0, i32 %v28
  %v30 = load i32, i32* %v29, align 8
  %v31 = extractvalue %dx.types.ResRet.i32 %v16, 2
  %v32 = icmp eq i32 %v31, %v30
  %v33 = and i1 %v27, %v32
  %v34 = or i32 %v11, 3
  %v35 = getelementptr [16 x i32], [16 x i32]* @bc250_expected, i32 0, i32 %v34
  %v36 = load i32, i32* %v35, align 4
  %v37 = extractvalue %dx.types.ResRet.i32 %v16, 3
  %v38 = icmp eq i32 %v37, %v36
  %v41 = and i1 %v33, %v38
  %v42 = add i32 %v11, %v9
  br label %v10

v51:
  %v52 = call i1 @dx.op.waveAllTrue(i32 114, i1 %v12)
  br i1 %v52, label %v53, label %v60

v53:
{fast}  br label %v70

v60:
{slow}  br label %v70

v70:
  ret void
}}

!dx.version = !{{!1}}
!dx.shaderModel = !{{!2}}
!dx.entryPoints = !{{!3}}
!1 = !{{i32 1, i32 6}}
!2 = !{{!"cs", i32 6, i32 6}}
!3 = !{{void ()* @main, !"main", null, null, !4}}
!4 = !{{i32 0, i64 16, i32 4, !5}}
!5 = !{{i32 64, i32 1, i32 1}}
"""


def guarded_program(table, slow_only=False):
    """y = x * w[k] + w[k + 1]: the fast path uses table constants, the slow path the model."""
    x = (
        "  %x0 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h0,"
        " i32 0, i32 undef, i8 1, i32 4)\n  %x = extractvalue %dx.types.ResRet.i32 %x0, 0\n"
    )
    store = (
        "  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h0, i32 {at}, i32 undef,"
        " i32 {v}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)\n"
    )
    fast, slow = (
        [x],
        [
            x,
            "  %m0 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind"
            " { i32 18, i32 18, i32 0, i8 0 }, i32 18, i1 false)\n",
        ],
    )
    for k in range(0, 16, 2):
        fast.append(f"  %f{k} = mul i32 %x, {table[k]}\n  %g{k} = add i32 %f{k}, {table[k + 1]}\n")
        fast.append(store.format(at=4096 + 4 * k, v=f"%g{k}"))
        slow.append(
            f"  %w{k} = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle"
            f" %m0, i32 {64 + 4 * k}, i32 undef, i8 3, i32 4)\n"
            f"  %a{k} = extractvalue %dx.types.ResRet.i32 %w{k}, 0\n"
            f"  %b{k} = extractvalue %dx.types.ResRet.i32 %w{k}, 1\n"
            f"  %s{k} = mul i32 %x, %a{k}\n  %t{k} = add i32 %s{k}, %b{k}\n"
        )
        slow.append(store.format(at=4096 + 4 * k, v=f"%t{k}"))
    if slow_only:
        body = "".join(slow)
        return (
            "define void @main() {\nv0:\n" + HANDLE + body + "  ret void\n}\n\n"
            '!dx.entryPoints = !{!3}\n!3 = !{void ()* @main, !"main", null, null, !4}\n'
            "!4 = !{i32 0, i64 16, i32 4, !5}\n!5 = !{i32 64, i32 1, i32 1}\n"
        )
    return GUARDED.format(
        table=", ".join(f"i32 {v}" for v in table),
        handle=HANDLE,
        fast="".join(fast),
        slow="".join(slow),
    )


class VectorGuards(unittest.TestCase):
    def setUp(self):
        rng = random.Random(21)
        self.table = [rng.randint(-(1 << 31), (1 << 31) - 1) for _ in range(16)]
        self.model = bytes(64) + struct.pack("<16i", *self.table) + bytes(64)

    def test_transform_is_exact_and_keeps_both_paths(self):
        text = guarded_program(self.table)
        vector, proof = guards.vector_guards(text)
        self.assertNotIn("@bc250_expected", vector)
        self.assertIn(
            "@bc250_guard_vectors = internal unnamed_addr constant [4 x <4 x i32>]", vector
        )
        self.assertIn('!2 = !{!"cs", i32 6, i32 9}', vector)
        self.assertEqual((proof["words"], proof["groups"]), (16, 4))
        reference = guarded_program(self.table, slow_only=True)
        programs = [differential.Program(t) for t in (reference, text, vector)]
        stats = differential.compare(programs, 30, 3, self.model, ["slow", "scalar", "vector"])
        self.assertGreater(stats["fast_path_runs"], 0)
        self.assertGreater(stats["fallback_runs"], 0)

    def test_guard_and_fast_path_errors(self):
        reference = differential.Program(guarded_program(self.table, slow_only=True))
        # A table that differs from the model: the guard never takes the fast path.
        stale = list(self.table)
        stale[5] ^= 1
        vector, _ = guards.vector_guards(guarded_program(stale))
        stats = differential.compare([reference, differential.Program(vector)], 20, 3, self.model)
        self.assertEqual(stats["fast_path_runs"], 0)
        self.assertGreater(stats["fallback_runs"], 0)
        # A fast path that disagrees with its (correct) table is caught.
        vector, _ = guards.vector_guards(guarded_program(self.table))
        broken = vector.replace(
            f"mul i32 %x, {self.table[4]}\n", f"mul i32 %x, {self.table[4] ^ 4}\n"
        )
        self.assertNotEqual(broken, vector)
        with self.assertRaisesRegex(ir.LoweringError, "differs"):
            differential.compare([reference, differential.Program(broken)], 20, 3, self.model)

    def test_refusals(self):
        text = guarded_program(self.table)
        for old, new, message in [
            ("or i32 %v11, 2", "or i32 %v11, 3", "adjacent"),
            ("[16 x i32] [", "[15 x i32] [", "No guard table|whole groups"),
            ("%v9 = shl i32 %v7, 2", "%v9 = shl i32 %v7, 3", "lanes \\* 4"),
        ]:
            changed = text.replace(old, new, 1)
            self.assertNotEqual(changed, text)
            with self.assertRaisesRegex(ir.LoweringError, message):
                guards.vector_guards(changed)
        once, _ = guards.vector_guards(text)
        with self.assertRaisesRegex(ir.LoweringError, "already vectorised"):
            guards.vector_guards(once)


def family(bound, pitch, base):
    """A two-family SDK-like program: grid bound, row pitch, base address are grid literals."""
    return (
        "define void @main() {\nv0:\n"
        + HANDLE
        + "  %v1 = call i32 @dx.op.threadId.i32(i32 93, i32 0)  ; ThreadId(component)\n"
        f"  %v2 = icmp ugt i32 %v1, {bound}\n"
        "  br i1 %v2, label %v9, label %v3\n\nv3:\n"
        f"  %v4 = call i32 @dx.op.tertiary.i32(i32 49, i32 {pitch}, i32 %v1, i32 12)  ; UMad(a,b,c)\n"
        f"  %v5 = add i32 %v4, {base}\n"
        "  %v6 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h0, i32 %v5, i32 undef, i8 1, i32 4)  ; RawBufferLoad\n"
        "  %v7 = extractvalue %dx.types.ResRet.i32 %v6, 0\n"
        "  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h0, i32 %v5, i32 undef, i32 %v7, i32 undef, i32 undef, i32 undef, i8 1, i32 4)  ; RawBufferStore\n"
        "  br label %v9\n\nv9:\n  ret void\n}\n"
    )


def release(bound, pitch, base, extra=""):
    """The program after a lowering: a folded base + 4 load, a vector constant, a table."""
    text = family(bound, pitch, base).replace(
        "  br label %v9\n\nv9:",
        f"  %v8 = add i32 %v4, {base + 4}\n"
        "  %v10 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h0, i32 %v8, i32 undef, i8 1, i32 4)\n"
        "  %v11 = bitcast i32 %v7 to <2 x i16>\n"
        f"  %v12 = mul <2 x i16> %v11, <i16 {bound}, i16 3>\n" + extra + "  br label %v9\n\nv9:",
    )
    return (
        f"@bc250_expected = internal unnamed_addr constant [4 x i32] [i32 {bound}, i32 1, i32 2, i32 3], align 16\n"
        + text
    )


class Transport(unittest.TestCase):
    hot = (959, 15392, 41616128)
    small = (479, 7712, 10440128)

    def test_map_and_rewrite(self):
        sigma, contexts = transport.literal_map(family(*self.hot), family(*self.small))
        self.assertEqual(sigma, {959: 479, 15392: 7712, 41616128: 10440128})
        self.assertEqual(len(contexts), 3)
        out, proof = transport.transport(
            family(*self.hot), family(*self.small), release(*self.hot), release(*self.small)
        )
        self.assertEqual(proof["folded"], {"41616132": 10440132})
        expected = release(*self.small)
        # Vector constants and globals keep the donor's values (they are not grid literals).
        expected = expected.replace("<i16 479, i16 3>", "<i16 959, i16 3>").replace(
            "[i32 479, i32 1", "[i32 959, i32 1"
        )
        self.assertEqual(out, expected)
        same(self, family(*self.small), out, trials=8)

    def test_refusals(self):
        hot, small = family(*self.hot), family(*self.small)
        for target, message in [
            (small.replace("i32 12)", "i32 13)"), "counts differ"),
            (small.replace("icmp ugt", "icmp uge"), "more than literals"),
            (
                small.replace("i8 1, i32 4)  ; RawBufferLoad", "i8 3, i32 4)  ; RawBufferLoad"),
                "not an i32 operand",
            ),
            (small + "\n", "differ in length"),
            (
                family(479, 7712, 41616128).replace("%v1, 479", "%v1, 7712"),
                "not injective|two values",
            ),
        ]:
            with self.assertRaisesRegex(ir.LoweringError, message):
                transport.transport(hot, target, release(*self.hot), release(*self.small))
        with self.assertRaisesRegex(ir.LoweringError, "identical"):
            transport.literal_map(hot, hot)
        # A domain value in an instruction shape the SDK never uses for grid literals.
        odd = "  %v13 = xor i32 %v7, 15392\n"
        with self.assertRaisesRegex(ir.LoweringError, "unexpected instruction"):
            transport.transport(hot, small, release(*self.hot, odd), release(*self.small, odd))
        # A specialised constant that only looks like a grid literal: the counts disagree.
        weight = "  %v13 = add i32 959, 0\n"
        with self.assertRaisesRegex(ir.LoweringError, "counts differ"):
            transport.transport(
                hot, small, release(*self.hot, weight), release(*self.small, weight)
            )


class Interpreter(unittest.TestCase):
    def run_program(self, body, inputs=None, wave=32, lane=0):
        text = "define void @main() {\nv0:\n" + HANDLE + body + "}\n"
        p = differential.Program(text)
        thread = differential.thread_inputs((0, 0, 0), (lane, 0, 0), p.numthreads)
        thread["numthreads"] = p.numthreads
        profile = dict(activations="random", model="random", perturbed_words=set())
        run = differential.Run(p, differential.Inputs("t", thread, lane, wave, profile))
        run.execute()
        return [s[3] for s in run.stores], run

    def store(self, value, at=0):
        return (
            f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h0, i32 {at}, i32"
            f" undef, i32 {value}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)\n"
        )

    def test_loop_phi_alloca_and_select(self):
        body = (
            "  %a = alloca [4 x i32], align 4\n  br label %v1\n\nv1:\n"
            "  %i = phi i32 [ 0, %v0 ], [ %n, %v1 ]\n  %s = phi i32 [ 5, %v0 ], [ %t, %v1 ]\n"
            "  %p = getelementptr [4 x i32], [4 x i32]* %a, i32 0, i32 %i\n"
            "  store i32 %s, i32* %p, align 4\n  %t = add i32 %s, %i\n  %n = add nuw i32 %i, 1\n"
            "  %c = icmp eq i32 %n, 4\n  br i1 %c, label %v2, label %v1\n\nv2:\n"
            "  %q = getelementptr [4 x i32], [4 x i32]* %a, i32 0, i32 3\n"
            "  %r = load i32, i32* %q, align 4\n  %m = select i1 %c, i32 %r, i32 -1\n"
            + self.store("%t")
            + self.store("%m", 4)
            + "  ret void\n"
        )
        stores, _ = self.run_program(body)
        self.assertEqual(stores, [(11,), (8,)])  # 5 + 0 + 1 + 2 + 3; a[3] = 5 + 0 + 1 + 2

    def test_vectors_floats_and_intrinsics(self):
        body = (
            "  %w = bitcast i32 -2130673280 to <2 x i16>\n"  # 0x81008180: lanes -32384, -32512
            "  %e = extractelement <2 x i16> %w, i32 1\n  %x = sext i16 %e to i32\n"
            "  %f = sitofp i32 %x to float\n  %g = fmul fast float %f, 2.500000e-01\n"
            "  %h = call float @dx.op.unary.f32(i32 26, float %g)  ; Round_ne(value)\n"
            "  %k = fptosi float %h to i32\n"
            "  %d = call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 7, i32 -16843010, i32 33620225)\n"
            "  %u = call %dx.types.fouri32 @dx.op.unpack4x8.i32(i32 219, i8 1, i32 -16843010)\n"
            "  %u0 = extractvalue %dx.types.fouri32 %u, 0\n"
            "  %pk = call i32 @dx.op.pack4x8.i32(i32 220, i8 2, i32 300, i32 -300, i32 5, i32 %u0)\n"
            "  %mx = call i32 @dx.op.binary.i32(i32 37, i32 %k, i32 -9000)  ; IMax(a,b)\n"
            + self.store("%k")
            + self.store("%d", 4)
            + self.store("%pk", 8)
            + self.store("%mx", 12)
            + "  ret void\n"
        )
        stores, run = self.run_program(body)
        # -32512 / 4 = -8128; 7 + dot of bytes (-2, -2, -2, -2) and (1, 1, 1, 2); pack clamps.
        self.assertEqual([s[0] for s in stores], [-8128, -3, wrap(0xFE05807F), -8128])
        self.assertEqual(
            run.trace[0], ("round", struct.unpack("<I", struct.pack("<f", -8128.0))[0])
        )

    def test_binary32_rounding_and_poison(self):
        self.assertEqual(differential.f32(1 + 2**-30), 1.0)
        self.assertEqual(differential.f32(3.4e38 * 10), float("inf"))
        self.assertEqual(differential.f32_round(2.5, "ne"), 2.0)
        self.assertEqual(differential.to_int32_from_float(float("nan")), 0)
        self.assertEqual(differential.to_int32_from_float(1e20), (1 << 31) - 1)
        with self.assertRaises(differential.Poison):
            self.run_program("  %z = sdiv i32 7, 0\n  ret void\n")

    def test_wave_all_true_combines_lanes(self):
        body = (
            "  %l = call i32 @dx.op.waveGetLaneIndex(i32 111)\n  %c = icmp ne i32 %l, 17\n"
            "  %a = call i1 @dx.op.waveAllTrue(i32 114, i1 %c)\n  %z = zext i1 %a to i32\n"
            + self.store("%z")
            + "  ret void\n"
        )
        self.assertEqual(self.run_program(body, wave=32, lane=3)[0], [(0,)])  # lane 17 says no
        self.assertEqual(self.run_program(body, wave=16, lane=3)[0], [(1,)])

    def test_discard_addresses_compare_as_one(self):
        a = differential.normalize([(1, 134217728, 1, (5,))], True)
        b = differential.normalize([(1, -4, 1, (5,))], True)
        self.assertEqual(a, b)
        self.assertNotEqual(differential.normalize([(1, -4, 1, (5,))], False), a)

    def test_differences_are_reported(self):
        rng = random.Random(31)
        p = Program()
        words = [p.word() for _ in range(8)]
        text = p.text([p.chain("0", words, weights(rng, 8)) for _ in range(4)])
        lowered, _ = packing.stream_outputs(text, width=4)
        lowered = ir.canonical_ssa(lowered)
        same(self, text, lowered)
        m = re.search(r"<i16 (-?\d+), i16", lowered)
        broken = lowered[: m.start(1)] + str(int(m[1]) + 1) + lowered[m.end(1) :]
        with self.assertRaisesRegex(ir.LoweringError, "differs"):
            same(self, text, broken)


class Recipes(unittest.TestCase):
    def test_tree(self):
        summary = lower_pass.check_tree()
        for name, count in [
            ("c10-pass5", 6),
            ("c10-pass11", 6),
            ("c10-pass3", 6),
            ("c10-pass6", 6),
            ("c10-prepass", 6),
        ]:
            self.assertIn(f"recipe {name}: {count} slots", summary)

    def write(self, recipe):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(recipe, handle)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_refusals(self):
        good = json.loads((ROOT / "dll/port/recipes/c10-pass11.json").read_text())
        lower_pass.load_recipe(self.write(good))
        pin = good["expected"]["0x820540"]
        for change in [
            lambda r: r.update(cover="some"),
            lambda r: r["expected"].update({"0xdead00": pin}),
            lambda r: r["expected"].update({"0x820540": dict(pin, replacement_sha256="x")}),
            lambda r: r["expected"].update({"0x820540": dict(pin, extra=pin["source_sha256"])}),
            lambda r: r["sequences"]["transport"][0][1].update(donor=5),
        ]:
            recipe = json.loads(json.dumps(good))
            change(recipe)
            with self.assertRaises((ir.LoweringError, TypeError, ValueError)):
                lower_pass.load_recipe(self.write(recipe))
                context = dict(first=True, offset=0x820540, rows={}, slots={}, entry="e")
                lower_pass.step_transport("", context, **recipe["sequences"]["transport"][0][1])

    def test_stage_order_and_donor_checks(self):
        context = dict(first=False, row=None)
        with self.assertRaisesRegex(ir.LoweringError, "first stage"):
            lower_pass.step_release_source("", context)
        with self.assertRaisesRegex(ir.LoweringError, "first stage"):
            lower_pass.step_transport("", dict(first=False), donor="0x234860")
        with self.assertRaisesRegex(ir.LoweringError, "own donor"):
            lower_pass.step_transport("", dict(first=True, offset=0x234860), donor="0x234860")
        with self.assertRaisesRegex(ir.LoweringError, "not a manifest row"):
            lower_pass.step_transport("", dict(first=True, offset=0x820540, rows={}), donor="0x123")


NEW = {
    "c10-pass5": ("0xbd2c80", "0x3d9f20"),
    "c10-pass11": ("0x820540", "0x9b99a0"),
    "c10-pass3": ("0x66ed50", "0x166450"),
    "c10-pass6": ("0x98e620", "0x4f4de0", "0xbb0080", "0xa616b0"),
    "c10-prepass": ("0x45bae0", "0xa86cb0", "0x2a8ae0", "0x615390"),
}
DONORS = {
    "0xbd2c80": "0x7b6f00",
    "0x3d9f20": "0x7b6f00",
    "0x66ed50": "0x11a890",
    "0x166450": "0x11a890",
    "0x98e620": "0x8f3770",
    "0x4f4de0": "0x8f3770",
    "0xbb0080": "0xb512e0",
    "0xa616b0": "0xb512e0",
    "0x45bae0": "0x103100",
    "0xa86cb0": "0x3940b0",
    "0x2a8ae0": "0x103100",
    "0x615390": "0x3940b0",
}


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealRecipes(unittest.TestCase):
    """The five c10 recipes on SDK 2.3.0: release slots equal RC11, new slots equal their pins,
    two independent constructions agree, and every new slot passes a short differential check."""

    @classmethod
    def setUpClass(cls):
        cls.sdk = Path(CACHE) / "sha256" / SDK_SHA
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.summaries = {
            name: lower_pass.generate(
                ROOT / f"dll/port/recipes/{name}.json", cls.sdk, DXC, cls.out / name, jobs=3
            )
            for name in NEW
        }
        offsets = sorted(
            {int(x, 16) for pair in DONORS.items() for x in pair}
            | {int(x, 16) for slots in NEW.values() for x in slots}
        )
        cls.sources, cls.model = differential.sdk_programs(cls.sdk, DXC, offsets)
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        cls.rows = {r["original_offset"]: r for r in manifest["replacements"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_slot_equals_release_or_pin(self):
        for name, summary in self.summaries.items():
            self.assertEqual(summary["containers_equal"], summary["slots"], name)
            new = {r["offset"] for r in summary["records"] if r["baseline"] == "recipe pin"}
            self.assertEqual(new, set(NEW[name]), name)

    def test_transport_equals_the_stage_construction(self):
        for name, slots in NEW.items():
            if name == "c10-pass11":
                continue  # transport is this recipe's own construction
            for slot in slots:
                t, d = int(slot, 16), int(DONORS[slot], 16)
                text, _ = transport.transport(
                    self.sources[d],
                    self.sources[t],
                    lower_pass.release_source(self.rows[d]),
                    lower_pass.release_source(self.rows[t]),
                )
                ours = (self.out / name / "shaders" / f"{t:08x}.ll").read_text()
                strip = lambda s: lower_pass.MODULE_ID.sub("", s, count=1)  # noqa: E731
                self.assertEqual(strip(text), strip(ours), slot)

    def test_new_slots_are_exact(self):
        for name, slots in NEW.items():
            if name == "c10-prepass":
                continue  # metadata only; see test_prep_is_metadata_only
            for slot in slots[:2]:
                t = int(slot, 16)
                programs = [
                    differential.Program(self.sources[t]),
                    differential.Program(lower_pass.release_source(self.rows[t])),
                    differential.Program((self.out / name / "shaders" / f"{t:08x}.ll").read_text()),
                ]
                stats = differential.compare(programs, 8, 1, self.model, ["sdk", "rc11", "c10"])
                self.assertEqual(stats["runs"], 8)
                if name != "c10-pass5":
                    self.assertGreater(stats["fast_path_runs"], 0, slot)

    def test_prep_is_metadata_only(self):
        for slot in NEW["c10-prepass"]:
            t = int(slot, 16)
            ours = (self.out / "c10-prepass" / "shaders" / f"{t:08x}.ll").read_text()
            before = lower_pass.release_source(self.rows[t]).splitlines()
            after = ours.splitlines()
            changed = [(a, b) for a, b in zip(before, after) if a != b]
            self.assertEqual(len(changed), 1, slot)
            self.assertTrue(changed[0][1].startswith(changed[0][0][:-1] + ", i32 11, !"))
            self.assertEqual(after[len(before) :], ["", after[-1]])
            self.assertRegex(after[-1], r"^!\d+ = !\{i32 32\}$")
            # No operation whose result depends on the wave size (quad operations do not).
            self.assertIsNone(re.search(r"@dx\.op\.wave|@dx\.op\.barrier|addrspace\(3\)", ours))


if __name__ == "__main__":
    unittest.main()
