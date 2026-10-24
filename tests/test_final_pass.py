# SPDX-License-Identifier: MIT
"""Final-pass stages (dll/port/final_pass.py) and the whole-program interpreter (execute.py).

Synthetic programs check every stage against its input with the interpreter on random and
extreme resource contents; with BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC the rc12-final recipe
regenerates all 96 final-pass programs and the three family slots are run against the SDK's.
"""

import json
import math
import os
import random
import tempfile
import unittest
from pathlib import Path

from dll.port import execute, final_pass, ir, lower_pass, packing

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"
FAMILY = ("0x5e7990", "0x248220", "0x7abdd0")
PROFILES = ("random", "extreme", "min", "max", "mixed")


class Builder:
    """A canonical single-thread program: buffer words in, dot4 chains, buffer words out."""

    def __init__(self):
        self.lines, self.serial = [], 0
        self.handle = self.new(
            "call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind"
            " { i32 0, i32 0, i32 0, i8 1 }, i32 0, i1 false)"
        )
        tid = self.new("call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)")
        self.base = self.new(f"shl i32 {tid}, 6")
        self.tid, self.loads, self.stores = tid, 0, 0

    def new(self, rhs):
        self.serial += 1
        self.lines.append(f"  %v{self.serial} = {rhs}")
        return f"%v{self.serial}"

    def words(self, n):
        address = self.new(f"add i32 {self.base}, {16 * self.loads}")
        self.loads += 1
        ret = self.new(
            f"call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle"
            f" {self.handle}, i32 {address}, i32 undef, i8 15, i32 4)"
        )
        return [self.new(f"extractvalue %dx.types.ResRet.i32 {ret}, {k}") for k in range(n)]

    def chain(self, base, words, weights):
        acc = base
        for word, w in zip(words, weights, strict=True):
            packed = sum((b & 255) << (8 * k) for k, b in enumerate(w))
            packed = execute.wrap(packed, 32)
            acc = self.new(
                f"call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 {acc}, i32 {word}, i32 {packed})"
            )
        return acc

    def store(self, values):
        values = list(values) + ["0"] * (4 - len(values))
        self.stores += 1
        self.lines.append(
            f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle {self.handle},"
            f" i32 {16 * self.stores}, i32 undef, i32 {values[0]}, i32 {values[1]}, i32 {values[2]},"
            f" i32 {values[3]}, i8 15, i32 4)"
        )

    def text(self):
        return (
            "define void @main() {\nv0:\n" + "\n".join(self.lines) + "\n  ret void\n}\n\n"
            "declare i32 @dx.op.dot4AddPacked.i32(i32, i32, i32, i32) #0\n"
        )


def weights(rng, n, extreme=0.2):
    return [
        [
            rng.choice((-128, 127)) if rng.random() < extreme else rng.randint(-128, 127)
            for _ in range(4)
        ]
        for _ in range(n)
    ]


def same_effects(test, a, b, threads=24, modes=("fused", "separate")):
    pa, pb = execute.Program(a), execute.Program(b)
    for seed, profile in enumerate(PROFILES):
        world = execute.World(seed, profile)
        for t in range(threads):
            thread = dict(gid=(0, 0, 0), gtid=(t, 0, 0), tid=(t, 0, 0), flat=t)
            want, _, values = pa.run(world, thread, values=True)
            for mode in modes:
                got, _, got_values = pb.run(world, thread, fmad=mode, values=True)
                test.assertEqual(got, want, (profile, t, mode))
                test.assertEqual(execute.common_differences(values, got_values), [])


def branchy(rng):
    """Chains in two branches joined by phis; float and integer uses of the results."""
    b = Builder()
    words = b.words(4)
    first = [b.chain(str(rng.randint(-300, 300)), words, weights(rng, 4)) for _ in range(4)]
    cond = b.new(f"icmp ult i32 {b.tid}, 128")
    yes, no, join = "vYES", "vNO", "vJOIN"
    b.lines.append(f"  br i1 {cond}, label %{yes}, label %{no}")
    b.lines += ["", yes + ":"]
    more = b.words(4)
    left = [b.chain(t, more, weights(rng, 4)) for t in first]
    b.lines.append(f"  br label %{join}")
    b.lines += ["", no + ":", f"  br label %{join}", "", join + ":"]
    phis = [
        b.new(f"phi i32 [ {lv}, %{yes} ], [ {fv}, %{no} ]")
        for lv, fv in zip(left, first, strict=True)
    ]
    # As in the SDK's final pass, the merged partial sums accumulate further dots.
    last = b.words(4)
    tails = [b.chain(p, last, weights(rng, 4)) for p in phis]
    fl = b.new(f"sitofp i32 {tails[0]} to float")
    scaled = b.new(f"fmul fast float {fl}, 2.500000e-01")
    b.store([b.new(f"bitcast float {scaled} to i32"), tails[1], tails[2], tails[3]])
    return ir.canonical_ssa(b.text())


class FloatGraphs(unittest.TestCase):
    def test_float_graph_is_exact(self):
        rng = random.Random(3)
        for _ in range(3):
            text = branchy(rng)
            lowered, proof = final_pass.float_graph(text)
            self.assertEqual(proof["converted_dots"], proof["dots"])
            self.assertEqual(proof["converted_phis"], 4)
            self.assertNotIn("dot4AddPacked.i32(i32 163", lowered)
            self.assertIn("fadd float %floatv", lowered)  # the sitofp use reads the FP32 value
            same_effects(self, text, lowered)

    def test_unbounded_chains_stay_integer(self):
        b = Builder()
        words = b.words(4)
        big = b.chain(str(1 << 24), words, weights(random.Random(1), 4))
        small = b.chain("7", words, weights(random.Random(2), 4))
        b.store([big, small])
        text = ir.canonical_ssa(b.text())
        lowered, proof = final_pass.float_graph(text)
        self.assertEqual(proof["converted_dots"], 4)
        self.assertEqual(lowered.count("call i32 @dx.op.dot4AddPacked.i32(i32 163"), 4)
        self.assertIn("declare i32 @dx.op.dot4AddPacked.i32", lowered)
        same_effects(self, text, lowered)

    def test_mixed_float_from_external_roots(self):
        rng = random.Random(5)
        b = Builder()
        words, more = b.words(4), b.words(4)
        # Roots of known range computed outside the dot graph (in native_blocks: streamed tails).
        roots = [b.new(f"and i32 {w}, 1023") for w in words[:2]]
        tails = [b.chain(r, more, weights(rng, 4)) for r in roots]
        b.store(tails + roots)
        text = ir.canonical_ssa(b.text())
        program = ir.Program(text)
        root_names = sorted({c["base"] for c in program.columns() if c["base"].startswith("%")})
        self.assertEqual(len(root_names), 2)
        external = {r: (0, 1023) for r in root_names}
        lowered, proof = final_pass.mixed_float(text, external)
        self.assertEqual(proof["converted_dots"], 8)
        self.assertEqual(proof["external_float_roots"], 2)
        self.assertIn("%nativefp", lowered)
        same_effects(self, text, lowered)
        with self.assertRaisesRegex(ir.LoweringError, "exact FP32"):
            final_pass.mixed_float(text, {root_names[0]: (0, 1 << 25)})
        with self.assertRaisesRegex(ir.LoweringError, "Reserved"):
            final_pass.float_graph(lowered)

    def test_stream_filters(self):
        rng = random.Random(9)
        b = Builder()
        words = b.words(4)
        chains = [b.chain(str(rng.randint(-50, 50)), words, weights(rng, 4)) for _ in range(8)]
        b.store(chains[:4])
        b.store(chains[4:])
        text = ir.canonical_ssa(b.text())
        tails = set(chains[:4])
        streamed, proof = packing.stream_outputs(text, width=4, allowed_tails=tails)
        self.assertEqual(proof["streamed_dots"], 16)
        self.assertEqual(sorted(proof["groups"][0]["tails"]), sorted(tails))
        self.assertEqual(streamed.count("dot4AddPacked.i32(i32 163"), 16)
        same_effects(self, text, streamed, modes=("fused",))
        none, proof = packing.stream_outputs(text, width=4, allowed_blocks={"v99"})
        self.assertEqual((none, proof["streamed_dots"]), (text, 0))

    def test_shape_refusals(self):
        b = Builder()
        b.store([b.chain("1", b.words(4), weights(random.Random(1), 4))])
        text = ir.canonical_ssa(b.text())
        with self.assertRaisesRegex(ir.LoweringError, "13 dot blocks"):
            final_pass.native_blocks(text)
        with self.assertRaisesRegex(ir.LoweringError, "No colour samples"):
            final_pass.color_reuse(text)


class Interpreter(unittest.TestCase):
    def run_one(self, body, world=None, fmad="fused"):
        text = "define void @main() {\nv0:\n" + body + "\n  ret void\n}\n"
        effects, _, values = execute.Program(text).run(
            world or execute.World(0),
            dict(gid=(0, 0, 0), gtid=(0, 0, 0), tid=(0, 0, 0), flat=0),
            fmad=fmad,
            values=True,
        )
        return effects, values

    def test_rounding_and_contraction(self):
        self.assertEqual(execute.f32(16777217.0), 16777216.0)
        self.assertEqual(execute.f32(1e39), math.inf)
        self.assertEqual(execute.f16(65520.0), math.inf)
        self.assertEqual(execute.f16(2049.0), 2048.0)
        body = (
            "  %v1 = call float @dx.op.tertiary.f32(i32 46, float 4.097000e+03,"
            " float 4.097000e+03, float -1.678541e+07)"
        )
        _, fused = self.run_one(body)
        _, separate = self.run_one(body, fmad="separate")
        self.assertEqual(fused["%v1"], 4097.0 * 4097.0 - 16785410.0)
        self.assertNotEqual(fused["%v1"], separate["%v1"])

    def test_integer_operations(self):
        _, v = self.run_one(
            "\n".join(
                [
                    "  %v1 = call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 5, i32 -2139062144,"
                    " i32 2139062143)",
                    "  %v2 = call i32 @dx.op.pack4x8.i32(i32 220, i8 2, i32 300, i32 -300, i32 5,"
                    " i32 -1)",
                    "  %v3 = call i32 @dx.op.pack4x8.i32(i32 220, i8 0, i32 300, i32 -300, i32 5,"
                    " i32 -1)",
                    "  %v4 = call %dx.types.fouri32 @dx.op.unpack4x8.i32(i32 219, i8 1, i32 %v2)",
                    "  %v5 = bitcast i32 -65535 to <2 x i16>",
                    "  %v6 = sext <2 x i16> %v5 to <2 x i32>",
                    "  %v7 = lshr i32 -1, 28",
                    "  %v8 = fptosi float 3.000000e+09 to i32",
                    "  %v9 = call i32 @dx.op.tertiary.i32(i32 49, i32 -1, i32 -1, i32 5)",
                ]
            )
        )
        self.assertEqual(v["%v1"], 5 + 4 * (-128 * 127))
        self.assertEqual(v["%v2"], execute.wrap(0x7F + (0x80 << 8) + (5 << 16) + (0xFF << 24), 32))
        self.assertEqual(v["%v3"], execute.wrap(0x2C + (0xD4 << 8) + (5 << 16) + (0xFF << 24), 32))
        self.assertEqual(v["%v4"], (127, -128, 5, -1))
        self.assertEqual(v["%v6"], (1, -1))
        self.assertEqual(v["%v7"], 15)
        self.assertEqual(v["%v8"], (1 << 31) - 1)
        self.assertEqual(v["%v9"], 6)
        with self.assertRaises(execute.Undefined):
            self.run_one("  %v1 = shl i32 1, 32")

    def test_world_is_a_function_of_the_address(self):
        world = execute.World(4, "mixed")
        self.assertEqual(world.word((1, 0), 64), world.word((1, 0), 64))
        self.assertEqual(
            world.texel((0, 3), 0, (1, 2, 0), "f32"), world.texel((0, 3), 0, (1, 2, 0), "f32")
        )
        self.assertEqual(execute.World(1, "min").word((1, 0), 0), execute.wrap(0x80808080, 32))
        fixed = execute.World(1, fields={"b1[4].x": 9, "b1[1].x": 0.5})
        self.assertEqual(fixed.cbuffer(1, 4, 0, "i32"), 9)
        self.assertEqual(fixed.cbuffer(1, 1, 0, "f32"), 0.5)
        self.assertEqual(fixed.cbuffer(1, 1, 0, "i32"), 0x3F000000)

    def test_single_thread_only(self):
        with self.assertRaisesRegex(ir.LoweringError, "single-thread"):
            execute.Program(
                "define void @main() {\nv0:\n  call void @dx.op.barrier(i32 80, i32 9)\n  ret void\n}\n"
            )


class Recipe(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / "dll/port/recipes/rc12-final.json").read_text())
        self.manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        self.profile = json.loads((ROOT / "dll/port/profiles/sdk-2.3.0.json").read_text())

    def test_tree_and_shape(self):
        self.assertIn("rc12-final: 96 slots", lower_pass.check_tree())
        families = [k for k, v in self.recipe["slots"].items() if v != ["rc7"]]
        self.assertEqual(sorted(families), sorted(FAMILY))
        self.assertEqual(sorted(self.recipe["expected"]), sorted(FAMILY))

    def test_pin_refusals(self):
        def check(change):
            recipe = json.loads(json.dumps(self.recipe))
            change(recipe)
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                json.dump(recipe, handle)
            self.addCleanup(os.unlink, handle.name)
            lower_pass.check_recipe(
                lower_pass.load_recipe(handle.name), self.profile, self.manifest
            )

        hot = self.recipe["expected"]["0x248220"]
        row = next(r for r in self.manifest["replacements"] if r["original_offset"] == 0x5E7990)
        for change, message in [
            (lambda r: r["expected"]["0x248220"].update(source_sha256="0" * 64), "rederived"),
            (lambda r: r["expected"].update({"0xdea00": dict(hot)}), "rederived"),
            (lambda r: r["expected"]["0x5e7990"].update(basis="other"), "Invalid expected"),
            (lambda r: r["expected"]["0x5e7990"].update(note="x"), "Invalid expected"),
            (lambda r: r["expected"].update({"0x1": dict(hot)}), "not a recipe slot"),
            (
                lambda r: r["expected"]["0x5e7990"].update(
                    replacement_sha256=row["replacement_sha256"]
                ),
                "redundant",
            ),
        ]:
            with self.assertRaisesRegex(ir.LoweringError, message):
                check(change)

    def test_compare_pinned_and_unstamped(self):
        pin = self.recipe["expected"]["0x5e7990"]
        result = lower_pass.compare_pinned(
            dict(replacement_sha256=pin["replacement_sha256"]), "x", pin
        )
        self.assertTrue(result["container_equal"])
        self.assertFalse(result["source_equal_except_module_id"])
        row = next(r for r in self.manifest["replacements"] if r["original_offset"] == 0xDEA00)
        text = (ROOT / "dll" / row["source"]).read_text()
        result = lower_pass.compare(dict(replacement_sha256=row["replacement_sha256"]), text, row)
        self.assertTrue(result["source_equal_except_module_id"])
        self.assertFalse(
            lower_pass.compare(dict(replacement_sha256="0" * 64), text + "\n", row)[
                "source_equal_except_module_id"
            ]
        )


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealFinalPass(unittest.TestCase):
    """All 96 final-pass programs regenerate; the family slots equal the SDK's on the interpreter."""

    @classmethod
    def setUpClass(cls):
        cls.sdk = Path(CACHE) / "sha256" / SDK_SHA
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "final"
        cls.summary = lower_pass.generate(
            ROOT / "dll/port/recipes/rc12-final.json", cls.sdk, DXC, cls.out, jobs=3
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_slots_identical(self):
        self.assertEqual(self.summary["slots"], 96)
        for record in self.summary["records"]:
            self.assertTrue(record["container_equal"], record["offset"])
            self.assertTrue(record["source_equal_except_module_id"], record["offset"])
        pinned = {
            r["offset"]: r["expected_basis"]
            for r in self.summary["records"]
            if "expected_basis" in r
        }
        self.assertEqual(pinned, {"0x248220": "rederived", "0x5e7990": "new", "0x7abdd0": "new"})

    def test_family_slots_equal_the_sdk(self):
        toolchain = lower_pass.Toolchain(
            DXC,
            self.tmp.name,
            json.loads((ROOT / "dll/manifest.json").read_text())["dxcompiler_sha256"],
        )
        data = self.sdk.read_bytes()
        profile = json.loads((ROOT / "dll/port/profiles/sdk-2.3.0.json").read_text())
        slots = {hex(s["offset"]): s for s in profile["shaders"]["slots"]}
        rng = random.Random(23)
        for key in FAMILY:
            s = slots[key]
            sdk = ir.rename_ssa(
                toolchain.disassemble(data[s["offset"] : s["offset"] + s["bytes"]], "x")
            )
            ours = (self.out / "shaders" / f"{s['offset']:08x}.ll").read_text()
            a, b = execute.Program(sdk), execute.Program(ours)
            kinds = execute.cbuffer_kinds(sdk)
            body = 0
            for seed, profile_name in enumerate(PROFILES):
                world = execute.World(
                    seed,
                    profile_name,
                    kinds,
                    fields={
                        "b1[4].x": 64,
                        "b1[4].y": 64,
                        "b1[4].w": rng.choice((1, 3, 64)),
                        "b1[5].x": 17,
                    },
                )
                for _ in range(6):
                    t = rng.randint(0, 255)
                    thread = dict(
                        gid=(rng.randint(0, 1), rng.randint(0, 1), 0),
                        gtid=(t, 0, 0),
                        tid=(t, 0, 0),
                        flat=t,
                    )
                    want, _, values = a.run(world, thread, values=True)
                    body += bool(want)
                    for mode in ("fused", "separate"):
                        got, _, got_values = b.run(world, thread, fmad=mode, values=True)
                        self.assertEqual(got, want, (key, seed, thread, mode))
                        self.assertEqual(execute.common_differences(values, got_values), [], key)
            self.assertGreater(body, 20, key)


if __name__ == "__main__":
    unittest.main()
