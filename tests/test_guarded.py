# SPDX-License-Identifier: MIT
"""Guarded passes 7-9 (dll/port/guarded.py) and the SIMT interpreter (dll/port/simt.py).

Synthetic guarded programs (a model vote, a literal fast path, a dynamic fallback) run through
each stage and then, before and after, through whole 64-thread groups of the interpreter on
random and extreme scratch contents: every store must be identical. With BC250_FSR4_SDK_CACHE
and BC250_FSR4_DXC set, the RC11 recipes of passes 7-9 regenerate RC11's 18 programs and the C10
recipes give the 1080p and 8K families the 1440p-4K lowering, checked against the SDK programs.
"""

import json
import os
import random
import tempfile
import unittest
from pathlib import Path

from dll.port import guarded, ir, lower_pass, simt
from dll.port import simt_equivalence as equivalence

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"
METADATA = """
!dx.entryPoints = !{!1}
!1 = !{void ()* @main, !"main", null, null, !2}
!2 = !{i32 0, i64 16, i32 4, !3}
!3 = !{i32 64, i32 1, i32 1}
"""


def packed(weights):
    word = sum((w & 255) << (8 * k) for k, w in enumerate(weights))
    return word - (1 << 32) if word >= 1 << 31 else word


class Guarded:
    """A canonical guarded program: inputs loaded per thread, one vote, fast literal chains,
    a fallback with a dynamic dot4, one store per output."""

    def __init__(self, words):
        self.lines, self.serial, self.words = [], 0, words

    def new(self, rhs):
        self.serial += 1
        self.lines.append(f"  %n{self.serial} = {rhs}")
        return f"%n{self.serial}"

    def build(self, chains, fallback_literal=False, leak=None):
        """chains: list of (input word indices, weights per dot, bias literal).

        `leak` = (chain, dot): that intermediate dot4 value is stored too (a second use)."""
        out = ["define void @main() {", "entry:"]
        self.lines = out
        handle = self.new(
            "call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
            "{ i32 11, i32 11, i32 0, i8 1 }, i32 11, i1 false)"
        )
        tid = self.new("call i32 @dx.op.threadId.i32(i32 93, i32 0)")
        base = self.new(f"mul i32 {tid}, {4 * self.words}")
        words = []
        for k in range(0, self.words, 4):
            at = self.new(f"add i32 {base}, {4 * k}")
            load = self.new(
                f"call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle "
                f"{handle}, i32 {at}, i32 undef, i8 15, i32 4)"
            )
            words += [self.new(f"extractvalue %dx.types.ResRet.i32 {load}, {j}") for j in range(4)]
        dest = self.new(f"mul i32 {tid}, {4 * len(chains)}")
        dest = self.new(f"add i32 {dest}, 16777216")
        ok = self.new(f"icmp ult i32 {tid}, 100000")
        vote = self.new(f"call i1 @dx.op.waveAllTrue(i32 114, i1 {ok})")
        out.append(f"  br i1 {vote}, label %fast, label %slow")
        out.append("fast:")
        tails, leaked = [], []
        for c, (inputs, weights, bias) in enumerate(chains):
            acc = str(bias)
            for d, (word, w) in enumerate(zip(inputs, weights)):
                acc = self.new(
                    f"call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 {acc}, i32 {words[word]}, i32 {packed(w)})"
                )
                if leak == (c, d):
                    leaked.append(acc)
            tails.append(acc)
        self._stores(handle, dest, tails + leaked)
        out.append("  br label %done")
        out.append("slow:")
        second = words[1] if fallback_literal is False else str(packed([1, 2, 3, 4]))
        # A loaded accumulator: no interval, so the dynamic dot stays a dot4 (as in the SDK body).
        dyn = self.new(
            f"call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 {words[2]}, i32 {words[0]}, i32 {second})"
        )
        self._stores(handle, dest, [dyn])
        out.append("  br label %done")
        out.append("done:")
        out.append("  ret void")
        out.append("}")
        text = "\n".join(out) + "\n\ndeclare i32 @dx.op.dot4AddPacked.i32(i32, i32, i32, i32) #0\n"
        return ir.canonical_ssa(text + METADATA)

    def _stores(self, handle, dest, values):
        for o, value in enumerate(values):
            at = self.new(f"add i32 {dest}, {4 * o}")
            self.lines.append(
                f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle {handle}, i32 {at}, "
                f"i32 undef, i32 {value}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)"
            )


def chains(rng, outputs, inputs, groups=1, extreme=0.1, start=0):
    result = []
    for g in range(groups):
        words = list(range(start + g * inputs, start + (g + 1) * inputs))
        for _ in range(outputs):
            weights = [
                [
                    rng.choice((-128, 127)) if rng.random() < extreme else rng.randint(-128, 127)
                    for _ in range(4)
                ]
                for _ in words
            ]
            result.append((words, weights, rng.randint(-(2**20), 2**20)))
    return result


def stores(text, kind, seed, model=b""):
    program = simt.Program(text)
    resources = simt.Resources(equivalence.fill(kind, seed), model)
    votes = simt.run_group(program, resources, (0, 0, 0))
    return resources.writes, votes


class StageExactness(unittest.TestCase):
    def check(self, original, lowered):
        for kind, seed in [("random", 1), ("random", 2), ("extreme", 3), ("min", 0), ("max", 0)]:
            before, votes = stores(original, kind, seed)
            after, _ = stores(lowered, kind, seed)
            self.assertTrue(before)
            self.assertEqual(after, before, kind)
            self.assertEqual({v[2] for v in votes}, {1})

    def test_point_small_batches(self):
        rng = random.Random(1)
        text = Guarded(32).build(chains(rng, 16, 16, groups=2))
        lowered, proof = guarded.point_small(text, length=16, width=4, blocks="all", dots=512)
        self.assertEqual((proof["batches"], proof["outputs"]), (8, 32))
        self.assertEqual(sum(bool(guarded.literal_dot(x)) for x in lowered.splitlines()), 0)
        self.check(text, ir.canonical_ssa(lowered))
        # `first` keeps only the first block's groups; here every group is in one block.
        self.assertEqual(guarded.point_small(text, 16, 4, "first")[0], lowered)
        with self.assertRaisesRegex(ir.LoweringError, "Expected 2048"):
            guarded.point_small(text, 16, 4, "all", dots=2048)

    def test_native_stream_and_mixed_float(self):
        rng = random.Random(2)
        text = Guarded(68).build(
            chains(rng, 4, 32, extreme=0.3) + chains(rng, 4, 36, extreme=0.3, start=32)
        )
        streamed, proof = guarded.native_stream(text, width=4, extra_lengths=(32, 36))
        self.assertEqual(proof["streamed_dots"], 4 * 32 + 4 * 36)
        self.check(text, ir.canonical_ssa(streamed))
        mixed, record = guarded.mixed_float(text)
        self.assertEqual((record["converted_dots"], record["remaining_dots"]), (272, 1))
        self.assertLessEqual(record["maximum_magnitude"], 2**24)
        self.check(text, ir.canonical_ssa(mixed))

    def test_mixed_float_after_point_small_audit(self):
        rng = random.Random(3)
        text = Guarded(48).build(chains(rng, 16, 16) + chains(rng, 4, 32, start=16))
        point, _ = guarded.point_small(text, 16, 4, "all", dots=256)
        mixed, record = guarded.mixed_float(point)
        self.assertEqual(record["converted_dots"], 128)
        control, _ = guarded.mixed_float(text)
        remaining = {m[1] for x in point.splitlines() if (m := guarded.literal_dot(x))}
        audit = guarded.float_graph_audit(control, mixed, remaining)
        self.assertEqual(audit["graph_audit_roots"], 128)
        self.check(text, ir.canonical_ssa(mixed))

    def test_mixed_float_keeps_chains_beyond_exact_fp32(self):
        rng = random.Random(6)
        big = chains(rng, 2, 8)
        big[0] = (big[0][0], big[0][1], 2**25 + 1)  # literal base beyond 2**24
        big[1] = (big[1][0], [[127] * 4] * 8, 2**24 - 1000)  # sum leaves [-2**24, 2**24]
        text = Guarded(8).build(big)
        mixed, record = guarded.mixed_float(text)
        self.assertEqual(record["converted_dots"], 0)
        self.check(text, ir.canonical_ssa(mixed))

    def test_point_small_skips_chains_with_a_second_use(self):
        rng = random.Random(7)
        text = Guarded(16).build(chains(rng, 16, 16), leak=(0, 7))
        with self.assertRaisesRegex(ir.LoweringError, "multiple of 16"):
            guarded.point_small(text, 16, 4)
        rows, _ = guarded.literal_columns(text)
        self.assertEqual(sorted(len(r["chain"]) for r in rows)[:2], [8, 8])

    def test_refusals(self):
        rng = random.Random(4)
        good = Guarded(16).build(chains(rng, 16, 16))
        bad = Guarded(16).build(chains(rng, 16, 16), fallback_literal=True)
        with self.assertRaisesRegex(ir.LoweringError, "outside its guard region"):
            guarded.point_small(bad, 16, 4)
        with self.assertRaisesRegex(ir.LoweringError, "one model-guard vote"):
            guarded.point_small(good.replace("waveAllTrue(i32 114", "waveAnyTrue(i32 113"), 16, 4)
        with self.assertRaisesRegex(ir.LoweringError, "No pointwise groups"):
            guarded.point_small(good, 8, 4)
        with self.assertRaisesRegex(ir.LoweringError, "Reserved names"):
            guarded.point_small(good.replace("v0:", "v0:\n  %point1 = add i32 0, 0", 1), 16, 4)
        audit = guarded.guard_audit(good)
        tail = guarded.fallback_tail(good, audit["fallback_entry"])
        changed = good.replace(tail, tail + "\n  %extra = add i32 0, 0")
        self.assertNotEqual(changed, good)
        with self.assertRaisesRegex(ir.LoweringError, "dynamic fallback changed"):
            guarded.require_guard_kept(good, changed, audit)


class Matching(unittest.TestCase):
    def test_exact_minimum_over_all_pairings(self):
        rng = random.Random(5)
        for trial in range(12):
            weights = [
                [rng.choice((-128, 127, rng.randint(-128, 127))) for _ in range(64)]
                for _ in range(4)
            ]
            match = guarded.matching(weights)
            options = []
            for first in range(1, 4):
                rest = [o for o in range(1, 4) if o != first]
                pairs = [(0, first), tuple(rest)]
                cost = [0, 0]
                for left, right in pairs:
                    layout = guarded.pack_pair(weights, [-128, 127], left, right)
                    cost = [cost[0] + len(layout["bins"]), cost[1] + layout["products"]]
                options.append((tuple(cost), tuple(pairs)))
            best = min(options)
            self.assertEqual(tuple(match["selected_cost"]), best[0], trial)
            self.assertEqual([tuple(p) for p in match["pairs"]], list(best[1]))

    def test_signed_byte_pieces_bounded(self):
        weights = [[-128] * 64, [127] * 64]
        layout = guarded.pack_pair(weights, [-128, 127], 0, 1)
        self.assertEqual(layout["products"], 64)
        for chunk in layout["bins"]:
            self.assertGreaterEqual(min(chunk["minimum"]), -32768)
            self.assertLessEqual(max(chunk["maximum"]), 32767)
        with self.assertRaises(ir.LoweringError):
            guarded.pack_pair([[200] * 4, [0] * 4], [-128, 127], 0, 1)


class Interpreter(unittest.TestCase):
    def run_main(self, body, words=(), shared="", numthreads="64, 1, 1", wave=None, size=332352256):
        props = "!2 = !{i32 0, i64 16, i32 4, !3" + (", i32 11, !4}" if wave else "}")
        text = (
            shared
            + "define void @main() {\nv0:\n"
            + body
            + "\n}\n"
            + METADATA.replace("!2 = !{i32 0, i64 16, i32 4, !3}", props).replace(
                "64, 1, 1", numthreads
            )
            + (f"!4 = !{{i32 {wave}}}\n" if wave else "")
        )
        program = simt.Program(text)
        memory = dict(enumerate(words))
        resources = simt.Resources(lambda a: memory.get(a // 4, 0), b"", size=size)
        votes = simt.run_group(program, resources, (0, 0, 0))
        return resources, votes

    def store(self, value, at="0"):
        return (
            "  %h = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
            "{ i32 11, i32 11, i32 0, i8 1 }, i32 11, i1 false)\n"
            f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h, i32 {at}, i32 undef, "
            f"i32 {value}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)\n"
        )

    def test_scalar_semantics(self):
        cases = [
            (
                "call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 -5, i32 -2139062144, i32 2139062143)",
                (-5 + 4 * (-128 * 127)) & 0xFFFFFFFF,
            ),
            (
                "call i32 @dx.op.pack4x8.i32(i32 220, i8 2, i32 300, i32 -300, i32 5, i32 -5)",
                0x7F | 0x80 << 8 | 5 << 16 | 0xFB << 24,
            ),
            (
                "call i32 @dx.op.pack4x8.i32(i32 220, i8 1, i32 300, i32 -300, i32 5, i32 255)",
                0xFF | 0 << 8 | 5 << 16 | 0xFF << 24,
            ),
            ("call i32 @dx.op.binary.i32(i32 37, i32 -3, i32 2)", 2),
            ("ashr i32 -64, 3", (-8) & 0xFFFFFFFF),
            ("call i32 @dx.op.tertiary.i32(i32 49, i32 65536, i32 65536, i32 7)", 7),
        ]
        for rhs, expected in cases:
            resources, _ = self.run_main("  %x = " + rhs + "\n" + self.store("%x") + "  ret void")
            self.assertEqual(resources.writes, {0: expected}, rhs)

    def test_float_and_vectors(self):
        body = (
            "  %a = sitofp i32 5 to float\n  %b = fmul fast float %a, 5.000000e-01\n"
            "  %c = call float @dx.op.unary.f32(i32 26, float %b)\n"  # round half to even: 2.5 -> 2
            "  %d = fptosi float %c to i32\n"
            "  %e = bitcast i32 2130738944 to <2 x i16>\n"  # 0x7f007f00
            "  %f = ashr <2 x i16> %e, <i16 8, i16 8>\n"
            "  %g = mul <2 x i16> %f, <i16 300, i16 -3>\n"
            "  %h1 = extractelement <2 x i16> %g, i32 0\n  %h2 = sext i16 %h1 to i32\n"
            "  %s = add i32 %d, %h2\n" + self.store("%s") + "  ret void"
        )
        resources, _ = self.run_main(body)
        # 127 * 300 wraps in i16 lanes; the sum is 2 + (38100 - 65536).
        self.assertEqual(resources.writes, {0: (2 + 127 * 300 - 65536) & 0xFFFFFFFF})

    def test_loop_phis_are_parallel(self):
        body = (
            "  br label %v1\n\nv1:\n  %i = phi i32 [ 0, %v0 ], [ %j, %v1 ]\n"
            "  %a = phi i32 [ 1, %v0 ], [ %b, %v1 ]\n  %b = phi i32 [ 2, %v0 ], [ %a, %v1 ]\n"
            "  %j = add i32 %i, 1\n  %done = icmp eq i32 %j, 3\n  br i1 %done, label %v2, label %v1\n\n"
            "v2:\n  %s = mul i32 %a, 10\n  %t = add i32 %s, %b\n" + self.store("%t") + "  ret void"
        )
        resources, _ = self.run_main(body)
        # Three trips swap a and b twice: (1, 2) -> (2, 1) -> (1, 2); sequential phis would give 22.
        self.assertEqual(resources.writes, {0: 12})

    def test_votes_barrier_and_shared(self):
        shared = "@flags = external addrspace(3) global [64 x i32], align 4\n\n"
        body = (
            "  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
            "  %odd = and i32 %t, 1\n  %slot = xor i32 %t, 63\n"
            "  %p = getelementptr [64 x i32], [64 x i32] addrspace(3)* @flags, i32 0, i32 %t\n"
            "  store i32 %t, i32 addrspace(3)* %p, align 4\n"
            "  call void @dx.op.barrier(i32 80, i32 9)\n"
            "  %q = getelementptr [64 x i32], [64 x i32] addrspace(3)* @flags, i32 0, i32 %slot\n"
            "  %other = load i32, i32 addrspace(3)* %q, align 4\n"
            "  %low = icmp ult i32 %t, 32\n  %all = call i1 @dx.op.waveAllTrue(i32 114, i1 %low)\n"
            "  %flag = zext i1 %all to i32\n  %v = add i32 %other, %flag\n"
            "  %at = shl i32 %t, 2\n" + self.store("%v", "%at") + "  ret void"
        )
        resources, votes = self.run_main(body, shared=shared, wave=32)
        self.assertEqual(sorted(v[2] for v in votes), [0, 1])
        self.assertEqual(resources.writes, {4 * t: (63 - t) + (t < 32) for t in range(64)})

    def test_out_of_range_store_dropped_and_refusals(self):
        resources, _ = self.run_main(self.store("1", "-4") + "  ret void")
        self.assertEqual((resources.writes, resources.dropped), ({}, 64))
        body = self.store("1", "60").replace("%h", "%h1") + self.store("2", "64") + "  ret void"
        resources, _ = self.run_main(body, size=64)
        self.assertEqual((resources.writes, resources.dropped), ({60: 1}, 64))
        with self.assertRaises(simt.Unsupported):
            self.run_main("  %x = udiv i32 4, 2\n  ret void")
        with self.assertRaises(simt.Unsupported):
            self.run_main(
                "  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n  %c = icmp ult i32 %t, 3\n"
                "  br i1 %c, label %v1, label %v2\n\nv1:\n  %a = call i1 @dx.op.waveAllTrue(i32 114, i1 %c)\n"
                "  br label %v2\n\nv2:\n  ret void",
                wave=32,
            )


class RecipeFormat(unittest.TestCase):
    def test_pins_must_be_slots(self):
        recipe = json.loads((ROOT / "dll/port/recipes/c10-pass8.json").read_text())
        self.assertEqual(len(recipe["expected"]), 4)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            recipe["expected"]["0x1234"] = recipe["expected"]["0x6855c0"]
            json.dump(recipe, handle)
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaisesRegex(ir.LoweringError, "not a recipe slot"):
            lower_pass.load_recipe(handle.name)

    def test_c10_recipes_use_the_hot_sequence_for_every_slot(self):
        for p in (7, 8, 9):
            rc11 = json.loads((ROOT / f"dll/port/recipes/rc11-pass{p}.json").read_text())
            c10 = json.loads((ROOT / f"dll/port/recipes/c10-pass{p}.json").read_text())
            hot = [s for s, seq in rc11["slots"].items() if "rc7" not in seq]
            self.assertEqual(len(hot), 2)
            name = rc11["slots"][hot[0]][0]
            self.assertEqual(c10["sequences"][name], rc11["sequences"][name])
            self.assertEqual(set(c10["slots"]), set(rc11["slots"]))
            self.assertTrue(all(seq == [name, "cleanup"] for seq in c10["slots"].values()))
            self.assertEqual(sorted(c10["expected"]), sorted(set(rc11["slots"]) - set(hot)))


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealGuardedPasses(unittest.TestCase):
    """SDK 2.3.0: RC11's passes 7-9 regenerate byte for byte; C10 lowers the other families."""

    @classmethod
    def setUpClass(cls):
        cls.sdk = Path(CACHE) / "sha256" / SDK_SHA
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.summaries = {
            name: lower_pass.generate(
                ROOT / f"dll/port/recipes/{name}.json", cls.sdk, DXC, cls.out / name, jobs=3
            )
            for name in [f"{kind}-pass{p}" for p in (7, 8, 9) for kind in ("rc11", "c10")]
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rc11_recipes_reproduce_rc11(self):
        for p in (7, 8, 9):
            summary = self.summaries[f"rc11-pass{p}"]
            self.assertEqual(summary["slots"], 6)
            for record in summary["records"]:
                self.assertTrue(record["container_equal"], record["offset"])
                self.assertTrue(record["source_equal_with_release_module_id"], record["offset"])

    def test_c10_families_take_the_hot_lowering(self):
        for p in (7, 8, 9):
            summary = self.summaries[f"c10-pass{p}"]
            self.assertEqual(summary["containers_equal"], summary["slots"])
            self.assertEqual(summary["new_programs"], 4)
            records = {r["offset"]: r for r in summary["records"]}
            hot = next(r for r in records.values() if r["baseline"] == "release")

            def facts(record):
                return [{k: v for k, v in s.items() if k != "sha256"} for s in record["stages"]]

            for record in records.values():
                self.assertEqual(facts(record), facts(hot), record["offset"])
            # Alias slots (same SDK program) get the same program.
            by_sdk = {}
            for record in records.values():
                by_sdk.setdefault(record["sdk_sha256"], set()).add(record["replacement_sha256"])
            self.assertEqual(len(by_sdk), 3)
            self.assertTrue(all(len(v) == 1 for v in by_sdk.values()))

    def test_c10_program_equals_sdk_in_the_interpreter(self):
        data = self.sdk.read_bytes()
        model, identity = guarded.model_table(
            data, 0x8BE5, 131072, "a54e552ff69a3f7199861417f7ae461d1f67f636ab532c6ec9d8ea5d2963735a"
        )
        self.assertEqual(identity["offset"], 12601856)
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        toolchain = lower_pass.Toolchain(DXC, self.tmp.name, manifest["dxcompiler_sha256"])
        offset, size = 0x6855C0, 21644
        sdk_text = ir.rename_ssa(toolchain.disassemble(data[offset : offset + size], "006855c0"))
        generated = (self.out / "c10-pass8/shaders/006855c0.ll").read_text()
        report = equivalence.compare_slot(sdk_text, {"generated": generated}, model)
        self.assertTrue(report["all_equal"])
        self.assertTrue(report["mutation"]["detected"])
        self.assertEqual(report["grid"], [240, 135])


if __name__ == "__main__":
    unittest.main()
