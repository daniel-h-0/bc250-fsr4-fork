# SPDX-License-Identifier: MIT
"""Lowering stages (dll/port): exactness on synthetic programs, refusals, and the RC11 pass 12."""

import json
import os
import random
import re
import tempfile
import unittest
from pathlib import Path

from dll.port import bins, ir, lower_pass, packing, unroll, winograd

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"


def wrap(value, bits):
    half = 1 << (bits - 1)
    return ((value + half) & ((1 << bits) - 1)) - half


class Interpreter:
    """Straight-line evaluator for the integer IR the lowering stages read and write."""

    def __init__(self, inputs):
        self.inputs, self.env, self.sinks = inputs, {}, []

    def value(self, token):
        token = token.strip()
        if token in ("zeroinitializer", "undef"):
            return (0, 0)
        if m := re.fullmatch(r"<i(16|32) (-?\d+), i\1 (-?\d+)>", token):
            return (int(m[2]), int(m[3]))
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        return self.env[token]

    def run(self, lines):
        for line in lines:
            line = line.split(";")[0].rstrip()
            if m := re.fullmatch(r"  call void @sink\(i32 (\S+)\)", line):
                self.sinks.append(self.value(m[1]))
                continue
            m = re.fullmatch(r"  (%[\w.]+) = (.*)", line)
            if not m:
                continue
            self.env[m[1]] = self.op(m[2])
        return self.sinks

    def op(self, rhs):
        v = self.value
        if m := re.fullmatch(r"call i32 @input\(i32 (\d+)\)", rhs):
            return self.inputs[int(m[1])]
        if m := re.fullmatch(
            r"call i32 @dx\.op\.dot4AddPacked\.i32\(i32 163, i32 ([^,]+), i32 ([^,]+), i32 ([^)]+)\)",
            rhs,
        ):
            a, b = ir.signed_bytes(v(m[2])), ir.signed_bytes(v(m[3]))
            return wrap(v(m[1]) + sum(x * y for x, y in zip(a, b)), 32)
        if m := re.fullmatch(r"bitcast i32 (\S+) to <2 x i16>", rhs):
            word = v(m[1]) & 0xFFFFFFFF
            return (wrap(word & 0xFFFF, 16), wrap(word >> 16, 16))
        if m := re.fullmatch(r"(shl|ashr) <2 x i16> (\S+), <i16 (\d+), i16 (\d+)>", rhs):
            x, k = v(m[2]), (int(m[3]), int(m[4]))
            return tuple(wrap(a << s, 16) if m[1] == "shl" else a >> s for a, s in zip(x, k))
        if m := re.fullmatch(r"extractelement <2 x i(?:16|32)> (\S+), i32 ([01])", rhs):
            return v(m[1])[int(m[2])]
        if m := re.fullmatch(r"insertelement <2 x i16> (\S+), i16 (\S+), i32 ([01])", rhs):
            x = list(v(m[1]))
            x[int(m[3])] = v(m[2])
            return tuple(x)
        if m := re.fullmatch(r"(add|sub|mul) <2 x i(16|32)> (\S+), (<[^>]+>|\S+)", rhs):
            f = {"add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b}[
                m[1]
            ]
            return tuple(wrap(f(a, b), int(m[2])) for a, b in zip(v(m[3]), v(m[4])))
        if m := re.fullmatch(r"(sext|zext) <2 x i16> (\S+) to <2 x i32>", rhs):
            return tuple(a if m[1] == "sext" else a & 0xFFFF for a in v(m[2]))
        if m := re.fullmatch(r"(add|sub|mul|shl|ashr)(?: nuw| nsw)* i32 (\S+), (\S+)", rhs):
            a, b = v(m[2]), v(m[3])
            if m[1] in ("shl", "ashr"):
                return wrap(a << b if m[1] == "shl" else a >> b, 32)
            return wrap(dict(add=a + b, sub=a - b, mul=a * b)[m[1]], 32)
        if m := re.fullmatch(r"icmp (eq|ne) i32 (\S+), (\S+)", rhs):
            return (v(m[2]) == v(m[3])) == (m[1] == "eq")
        if m := re.fullmatch(r"sext i16 (\S+) to i32", rhs):
            return v(m[1])
        raise AssertionError("Unsupported instruction: " + rhs)


class Builder:
    """A canonical synthetic program: input words, constant dot4 chains, sinks."""

    def __init__(self):
        self.lines, self.serial, self.inputs = [], 0, 0

    def new(self, rhs):
        self.serial += 1
        self.lines.append(f"  %v{self.serial} = {rhs}")
        return f"%v{self.serial}"

    def input(self):
        self.inputs += 1
        return self.new(f"call i32 @input(i32 {self.inputs - 1})")

    def chain(self, base, words, weights):
        acc = base
        for word, w in zip(words, weights):
            packed = sum((b & 255) << (8 * k) for k, b in enumerate(w))
            packed = wrap(packed, 32)
            acc = self.new(
                f"call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 {acc}, i32 {word}, i32 {packed})"
            )
        return acc

    def text(self, tails):
        body = "\n".join(self.lines + [f"  call void @sink(i32 {t})" for t in tails])
        return (
            "define void @main() {\nv0:\n" + body + "\n  ret void\n}\n\n"
            "declare i32 @dx.op.dot4AddPacked.i32(i32, i32, i32, i32) #0\n"
        )


def random_weights(rng, n, scale=128, extreme=0.0):
    """Packed dot4 weights: uniform in [-scale, scale - 1], a fraction set to -128/127."""
    return [
        [
            rng.choice((-128, 127)) if rng.random() < extreme else rng.randint(-scale, scale - 1)
            for _ in range(4)
        ]
        for _ in range(n)
    ]


def evaluate(text, inputs):
    return Interpreter(inputs).run(text.splitlines())


def input_sets(rng, count, words):
    sets = [[wrap(0x80808080, 32)] * words, [0x7F7F7F7F] * words, [wrap(0x807F807F, 32)] * words]
    sets += [[wrap(rng.getrandbits(32), 32) for _ in range(words)] for _ in range(count)]
    return sets


class PackingExactness(unittest.TestCase):
    def check(self, text, stage, rounds=40, **options):
        lowered, proof = stage(text, **options)
        lowered = ir.canonical_ssa(lowered)
        rng = random.Random(7)
        words = text.count("@input(")
        for inputs in input_sets(rng, rounds, words):
            self.assertEqual(evaluate(lowered, inputs), evaluate(text, inputs))
        return lowered, proof

    def test_stream_outputs_groups_shared_inputs(self):
        rng = random.Random(1)
        b = Builder()
        words = [b.input() for _ in range(8)]
        bias = b.input()
        tails = [
            b.chain(bias, words, random_weights(rng, 8, extreme=0.3 * (i % 2))) for i in range(6)
        ]
        lowered, proof = self.check(b.text(tails), packing.stream_outputs, width=4)
        self.assertEqual(proof["streamed_dots"], 48)
        self.assertEqual(ir.count_dots(lowered), 0)

    def test_stream_primary_takes_36_input_chains(self):
        rng = random.Random(2)
        b = Builder()
        words = [b.input() for _ in range(36)]
        tails = [b.chain("0", words, random_weights(rng, 36)) for _ in range(4)]
        _, proof = self.check(
            b.text(tails), packing.stream_outputs, width=4, primary=False, rounds=10
        )
        self.assertEqual(proof["streamed_dots"], 0)
        _, proof = self.check(
            b.text(tails), packing.stream_outputs, width=4, primary=True, rounds=10
        )
        self.assertEqual(proof["streamed_dots"], 144)

    def test_prefix_pairs_then_half_groups_then_pack(self):
        rng = random.Random(3)
        b = Builder()
        words = [b.input() for _ in range(40)]
        bias = b.input()
        tails = [
            b.chain(bias, words, random_weights(rng, 40, scale=12, extreme=0.02)) for _ in range(2)
        ]
        tails.append(b.chain(bias, words[:20], random_weights(rng, 20, scale=40)))
        text = b.text(tails)
        text, proof = self.check(text, packing.prefix_pairs)
        self.assertGreater(proof["paired_dots"], 0)
        text, proof = self.check(text, packing.half_groups)
        self.assertGreater(proof["converted_dots"], 0)
        self.assertEqual(ir.count_dots(text), 0)

    def test_half_groups_biased_fallback(self):
        b = Builder()
        word = b.input()
        tail = b.chain(b.input(), [word], [[-128, -128, -128, -128]])
        _, proof = self.check(b.text([tail]), packing.half_groups)
        self.assertTrue(proof["groups"][0]["biased"])

    def test_packed_reductions_both_modes(self):
        rng = random.Random(4)
        b = Builder()
        words = [b.input() for _ in range(6)]
        bias = b.input()
        tails = [
            b.chain(bias, words, random_weights(rng, 6, scale=60, extreme=0.2)) for _ in range(3)
        ]
        # A single dot whose two-byte bound exceeds i16 takes the i32 path.
        tails.append(b.chain(bias, words[:1], [[-128, -128, 5, 6]]))
        for mode in ("paired", "halves"):
            lowered, proof = self.check(b.text(tails), packing.packed_reductions, mode=mode)
            self.assertEqual(ir.count_dots(lowered), 0)
        self.assertGreater(proof["converted_dots"], 0)
        with self.assertRaises(ir.LoweringError):
            packing.packed_reductions(b.text(tails), mode="float")


class PointwiseExactness(unittest.TestCase):
    def test_pointwise_groups_of_16(self):
        rng = random.Random(9)
        b = Builder()
        words = [b.input() for _ in range(8)]
        bias = b.input()
        tails = [
            b.chain(bias, words, random_weights(rng, 8, scale=40, extreme=0.05)) for _ in range(16)
        ]
        text = b.text(tails)
        lowered, proof = bins.pointwise(text, [8])
        lowered = ir.canonical_ssa(lowered)
        self.assertEqual((proof["groups"], proof["replaced_dots"]), (1, 128))
        self.assertEqual(ir.count_dots(lowered), 0)
        for inputs in input_sets(rng, 30, len(words) + 1):
            self.assertEqual(evaluate(lowered, inputs), evaluate(text, inputs))
        with self.assertRaisesRegex(ir.LoweringError, "No pointwise groups"):
            bins.pointwise(text, [4])

    def test_pointwise_needs_whole_groups(self):
        rng = random.Random(10)
        b = Builder()
        words = [b.input() for _ in range(4)]
        tails = [b.chain("0", words, random_weights(rng, 4)) for _ in range(15)]
        with self.assertRaisesRegex(ir.LoweringError, "multiple of 16"):
            bins.pointwise(b.text(tails), [4])


class WinogradAlgebra(unittest.TestCase):
    def setUp(self):
        rng = random.Random(5)
        self.weights = [
            [[[rng.randint(-128, 127) for _ in range(16)] for _ in range(3)] for _ in range(3)]
            for _ in range(16)
        ]
        self.plan = dict(transformed=winograd.transform(self.weights))

    def test_identity_and_domains(self):
        winograd.check_identity()
        self.assertEqual(winograd.domains(), [(-255, 255), (-256, 254), (-255, 255), (-255, 255)])
        self.assertEqual([winograd.postscale_factor(d) for d in range(4)], [2, 1, 1, 2])

    def test_two_pixel_convolution(self):
        """Left/right numerators from the four domains equal twice the direct 3x3 outputs."""
        rng = random.Random(6)
        for _ in range(20):
            pixels = [
                [[rng.randint(-128, 127) for _ in range(16)] for _ in range(4)] for _ in range(3)
            ]
            direct = [
                [
                    sum(
                        self.weights[o][ky][kx][c] * pixels[ky][x + kx][c]
                        for ky in range(3)
                        for kx in range(3)
                        for c in range(16)
                    )
                    for o in range(16)
                ]
                for x in range(2)
            ]
            dots = {}
            for _, _, domain, sign in winograd.ROLES:
                transformed = [
                    sum(winograd.BT[domain][j] * pixels[ky][j][c] for j in range(4))
                    for ky in range(3)
                    for c in range(16)
                ]
                matrix = winograd.domain_matrix(self.plan, domain, sign)
                dots[domain] = [sum(w * x for w, x in zip(row, transformed)) for row in matrix]
            for o in range(16):
                left = dots[0][o] + dots[1][o] + dots[2][o]
                right = dots[1][o] - dots[2][o] + dots[3][o]  # d3 entered negated
                self.assertEqual([left, right], [2 * direct[0][o], 2 * direct[1][o]])

    def test_domain_accumulators_are_exact(self):
        rng = random.Random(8)
        signs = {0: 1, 1: 1, 2: 1, 3: -1}
        # Every domain sequentially; the matchings (120 pair costs each) on a factor-1 and a
        # factor-2 domain.
        cases = [(d, "sequential", p) for d in range(4) for p in (False, True)]
        cases += [(d, a, True) for d in (1, 3) for a in ("matched", "binned")] + [
            (0, "binned", False)
        ]
        for domain, accumulate, postscale in cases:
            matrix = winograd.domain_matrix(self.plan, domain, signs[domain])
            bounds = winograd.domains()[domain]
            inputs = [f"%in{t}" for t in range(48)]
            e = bins.Emitter()
            outputs, record = winograd._domain_dots(
                e, inputs, matrix, bounds, domain, accumulate, postscale
            )
            for trial in range(6):
                values = [
                    rng.choice(bounds) if trial < 2 else rng.randint(*bounds) for _ in range(48)
                ]
                interp = Interpreter([])
                interp.env.update({f"%in{t}": values[t] for t in range(48)})
                interp.run(e.lines)
                expected = [sum(w * x for w, x in zip(row, values)) for row in matrix]
                self.assertEqual(
                    [interp.env[o] for o in outputs],
                    expected,
                    (domain, accumulate, postscale),
                )

    def test_matching_is_optimal_and_deterministic(self):
        matrix = winograd.domain_matrix(self.plan, 1, 1)
        first = bins.matching(matrix, (-256, 254), bins.binned_pair_cost)
        second = bins.matching(matrix, (-256, 254), bins.binned_pair_cost)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first["output_order"]), list(range(16)))
        self.assertLessEqual(first["selected_cost"], first["adjacent_cost"])

    def test_pieces_split_large_coefficients(self):
        self.assertEqual(bins.pieces(300), [127, 127, 46])
        self.assertEqual(bins.pieces(-254), [-127, -127])
        self.assertEqual(bins.pieces(0), [0])


class TextStages(unittest.TestCase):
    def test_canonical_ssa_is_idempotent(self):
        text = "define void @f() {\nentry:\n  %x = add i32 1, 2\n  br label %next\n\nnext:  ; preds = %entry\n  %y = add i32 %x, %x\n  ret void\n}\n"
        once = ir.canonical_ssa(text)
        self.assertIn("v0:", once)
        self.assertIn("%v3 = add i32 %v1, %v1", once)
        self.assertEqual(ir.canonical_ssa(once), once)

    def test_rename_ssa(self):
        text = "define void @f() {\n  %1 = add i32 0, 0\n  br label %2\n\n; <label>:2  ; preds\n  ret void\n}\n"
        self.assertEqual(
            ir.rename_ssa(text).splitlines()[1:6],
            ["v0:", "  %v1 = add i32 0, 0", "  br label %v2", "", "v2:"],
        )

    def test_fold_mads_wraps(self):
        text = "  %a = call i32 @dx.op.tertiary.i32(i32 48, i32 65536, i32 65536, i32 7) #0\n"
        self.assertEqual(unroll.fold_mads(text), ("  %a = add i32 7, 0\n", 1))
        text = "  %a = call i32 @dx.op.tertiary.i32(i32 49, i32 2147483647, i32 2, i32 3)\n"
        self.assertEqual(unroll.fold_mads(text)[0], "  %a = add i32 1, 0\n")
        text = "  %a = call i32 @dx.op.tertiary.i32(i32 49, i32 2147483647, i32 1, i32 1)\n"
        self.assertEqual(unroll.fold_mads(text)[0], "  %a = add i32 -2147483648, 0\n")

    def test_expand_two_block_loop(self):
        text = (
            "define void @f() {\nv0:\n  br label %v1\n\nv1:\n"
            "  %v2 = phi i32 [ 0, %v0 ], [ %v4, %v1 ]\n  %v3 = phi i32 [ 5, %v0 ], [ %v5, %v1 ]\n"
            "  %v5 = add i32 %v3, %v2\n  %v4 = add nuw nsw i32 %v2, 1\n  %v6 = icmp eq i32 %v4, 3\n"
            "  br i1 %v6, label %v7, label %v1\n\nv7:\n  call void @sink(i32 %v5)\n  ret void\n}\n"
        )
        expanded, loops = unroll.expand_loops(text)
        self.assertEqual([(lp["header"], lp["iterations"]) for lp in loops], [("v1", 3)])
        self.assertNotIn("phi", expanded)
        self.assertEqual(evaluate(expanded, []), [8])  # 5 + 0 + 1 + 2

    def test_expand_loop_with_latch_block(self):
        """The last iteration omits the latch, as the original exit branch does."""
        text = (
            "define void @f() {\nv0:\n  br label %v1\n\nv1:\n"
            "  %v2 = phi i32 [ 0, %v0 ], [ %v4, %v3 ]\n  %v5 = phi i32 [ 5, %v0 ], [ %v6, %v3 ]\n"
            "  %v7 = icmp eq i32 %v2, 3\n  br i1 %v7, label %v8, label %v3\n\n"
            "v3:\n  %v6 = add i32 %v5, %v2\n  %v4 = add i32 %v2, 1\n  br label %v1\n\n"
            "v8:\n  call void @sink(i32 %v5)\n  ret void\n}\n"
        )
        expanded, loops = unroll.expand_loops(text)
        self.assertEqual(
            [(lp["header"], lp["latch"], lp["iterations"]) for lp in loops], [("v1", "v3", 4)]
        )
        # Four header copies (icmp) and three latch copies (two adds each).
        self.assertEqual(len(re.findall(r"^  %bc250_unrolled\d+ = ", expanded, re.M)), 10)
        self.assertNotIn("v3:", expanded)
        self.assertEqual(evaluate(expanded, []), [8])

    def test_metadata(self):
        text = (
            "!dx.version = !{!1}\n!dx.shaderModel = !{!3}\n!dx.entryPoints = !{!11}\n!1 = !{i32 1, i32 6}\n"
            '!3 = !{!"cs", i32 6, i32 6}\n!11 = !{void ()* @f, !"f", null, null, !12}\n'
            "!12 = !{i32 0, i64 16, i32 4, !13}\n!13 = !{i32 64, i32 1, i32 1}\n"
        )
        text = ir.shader_model(text, 9)
        self.assertIn('!3 = !{!"cs", i32 6, i32 9}', text)
        self.assertEqual(ir.numthreads(text), (64, 1, 1))
        text, record = ir.wave_size(text, 32)
        self.assertEqual(record, dict(old=None, new="i32 32, i32 0, i32 0", changed=True))
        self.assertIn("!12 = !{i32 0, i64 16, i32 4, !13, i32 23, !14}", text)
        same, record = ir.wave_size(text, 32)
        self.assertEqual((same, record["changed"]), (text, False))
        wide, record = ir.wave_size(text, 64)
        self.assertIn("!14 = !{i32 64, i32 0, i32 0}", wide)

    def test_masked_stores(self):
        store = "  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %v1, i32 %v3, i32 undef, i32 %v4, i32 undef, i32 undef, i32 undef, i8 1, i32 4)"
        text = "  %v3 = select i1 %v2, i32 %v9, i32 134217728\n" + store + "\n"
        repaired, changed = ir.masked_stores(text)
        self.assertIn("i32 -4", repaired)
        self.assertEqual(len(changed), 1)
        with self.assertRaises(ir.LoweringError):
            ir.masked_stores(text + store + "\n")  # two uses
        with self.assertRaises(ir.LoweringError):
            ir.masked_stores(text.replace("i8 1, i32 4", "i8 3, i32 4"))  # not a scalar DWORD store
        with self.assertRaises(ir.LoweringError):
            ir.masked_stores(store + "\n")  # nothing to repair

    def test_drop_dot_declaration_refuses_remaining_dots(self):
        b = Builder()
        text = b.text([b.chain("0", [b.input()], [[1, 2, 3, 4]])])
        with self.assertRaises(ir.LoweringError):
            ir.drop_dot_declaration(text)


class Recipes(unittest.TestCase):
    def test_tree(self):
        self.assertIn("rc11-pass12: 6 slots", lower_pass.check_tree())

    def write(self, recipe):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(recipe, handle)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_refusals(self):
        good = json.loads((ROOT / "dll/port/recipes/rc11-pass12.json").read_text())
        lower_pass.load_recipe(self.write(good))
        for change in [
            lambda r: r.update(kind="other"),
            lambda r: r["sequences"]["pack"].append(["fold_everything"]),
            lambda r: r["sequences"]["pack"].append(["stream", 4]),
            lambda r: r["slots"].update({"0xb14220": ["missing"]}),
            lambda r: r["slots"].update({"11616800": ["unroll"]}),
            lambda r: r.update(slots={}),
        ]:
            recipe = json.loads(json.dumps(good))
            change(recipe)
            with self.assertRaises(ir.LoweringError):
                lower_pass.load_recipe(self.write(recipe))
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        profile = json.loads((ROOT / "dll/port/profiles/sdk-2.3.0.json").read_text())
        for change in [
            lambda r: r.update(sdk_tag="v2.2.0"),
            lambda r: r["slots"].update({"0xd85fa0": ["unroll"]}),  # pass-12 slot outside RC11
            lambda r: r["slots"].update({"0x7de1d0": ["unroll"]}),  # another entry's slot
        ]:
            recipe = json.loads(json.dumps(good))
            change(recipe)
            with self.assertRaises(ir.LoweringError):
                lower_pass.check_recipe(recipe, profile, manifest)

    def test_compare(self):
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        row = next(r for r in manifest["replacements"] if r["original_offset"] == 0xB14220)
        release = (ROOT / "dll" / row["source"]).read_text()
        ours = "; ModuleID = '00b14220.ll'\n" + release.split("\n", 1)[1]
        record = dict(replacement_sha256=row["replacement_sha256"])
        result = lower_pass.compare(record, ours, row)
        self.assertTrue(result["container_equal"])
        self.assertTrue(result["source_equal_except_module_id"])
        self.assertTrue(result["source_equal_with_release_module_id"])
        result = lower_pass.compare(dict(replacement_sha256="0" * 64), ours + "\n", row)
        self.assertFalse(result["container_equal"])
        self.assertFalse(result["source_equal_except_module_id"])
        self.assertFalse(result["source_equal_with_release_module_id"])

    def test_toolchain_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("bin/dxc", "bin/opt", "lib/libdxcompiler.so"):
                (root / name).parent.mkdir(exist_ok=True)
                (root / name).write_bytes(b"not dxc")
            with self.assertRaisesRegex(ValueError, "pinned DXC"):
                lower_pass.Toolchain(root, tmp, "0" * 64)


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealPasses(unittest.TestCase):
    """SDK 2.3.0's programs of passes 1 and 12 regenerate RC11's 12 replacements byte for byte."""

    @classmethod
    def setUpClass(cls):
        cls.sdk = Path(CACHE) / "sha256" / SDK_SHA
        cls.tmp = tempfile.TemporaryDirectory()
        cls.summaries = {
            name: lower_pass.generate(
                ROOT / f"dll/port/recipes/{name}.json",
                cls.sdk,
                DXC,
                Path(cls.tmp.name) / name,
                jobs=3,
            )
            for name in ("rc11-pass1", "rc11-pass12")
        }
        cls.toolchain = lower_pass.Toolchain(
            DXC,
            cls.tmp.name,
            json.loads((ROOT / "dll/manifest.json").read_text())["dxcompiler_sha256"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_slots_identical(self):
        for name, summary in self.summaries.items():
            self.assertEqual(summary["slots"], 6, name)
            for record in summary["records"]:
                self.assertTrue(record["container_equal"], record["offset"])
                self.assertTrue(record["source_equal_except_module_id"], record["offset"])
                self.assertTrue(record["source_equal_with_release_module_id"], record["offset"])

    def test_generate_refuses_partial_recipes(self):
        recipe = json.loads((ROOT / "dll/port/recipes/rc11-pass12.json").read_text())
        del recipe["slots"]["0xb14220"]
        path = Path(self.tmp.name) / "partial.json"
        path.write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ir.LoweringError, "INT8 slots"):
            lower_pass.generate(path, self.sdk, DXC, Path(self.tmp.name) / "partial", jobs=1)

    def unrolled(self):
        data = self.sdk.read_bytes()
        offset, size = 0xB14220, 38416
        text = ir.rename_ssa(self.toolchain.disassemble(data[offset : offset + size], "00b14220"))
        context = dict(
            toolchain=lower_pass.NamedToolchain(self.toolchain, "00b14220"),
            sdk_dots=ir.count_dots(text),
        )
        return lower_pass.step_unroll(text, context)[0]

    def test_winograd_refusals(self):
        text = self.unrolled()
        plan = winograd.plan(text)
        self.assertEqual((plan["row_pitch"], plan["input_buffer_base"]), (30752, 49958592))
        for old, new, message in [
            ("icmp ugt i32 %v5, 1919", "icmp ugt i32 %v5, 1899", "multiple of the group width"),
            ("!{i32 64, i32 1, i32 1}", "!{i32 32, i32 2, i32 1}", "numthreads"),
            (
                "define void @",
                "@wino_inputs = external global i32\ndefine void @",
                "Reserved names",
            ),
            ("v12:\n", "v12:\n  %wino1 = add i32 0, 0\n", "Reserved names"),
        ]:
            changed = text.replace(old, new, 1)
            self.assertNotEqual(changed, text)
            with self.assertRaisesRegex(ir.LoweringError, message):
                winograd.lower(changed, winograd.plan(changed))
        with self.assertRaisesRegex(ir.LoweringError, "another program"):
            winograd.lower(text.replace("v12:", "v12:\n  %extra = add i32 0, 0", 1), plan)
        # A spatial load at a different row pitch is not one 3x3 window.
        changed = text.replace("i32 49, i32 30752,", "i32 49, i32 30768,", 1)
        self.assertNotEqual(changed, text)
        with self.assertRaisesRegex(ir.LoweringError, "row pitches"):
            winograd.plan(changed)


if __name__ == "__main__":
    unittest.main()
