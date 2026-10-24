# SPDX-License-Identifier: MIT
"""Group-level equivalence checker (dll/port/equivalence.py) and the C10 recipes."""

import json
import math
import os
import random
import re
import tempfile
import unittest
from pathlib import Path

from dll.port import equivalence as eq
from dll.port import ir, lower_pass

ROOT = Path(__file__).resolve().parents[1]
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_FSR4_DXC")
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"
UAV = "%dx.types.ResBind { i32 11, i32 11, i32 0, i8 1 }"


def module(body, shared=""):
    return (
        shared + "define void @main() {\n" + body + "}\n\n"
        "!dx.entryPoints = !{!1}\n"
        '!1 = !{void ()* @main, !"main", null, null, !2}\n'
        "!2 = !{i32 0, i64 0, i32 4, !3}\n!3 = !{i32 64, i32 1, i32 1}\n"
    )


def store(address, value):
    return (
        "  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h, i32 "
        f"{address}, i32 undef, i32 {value}, i32 undef, i32 undef, i32 undef, i8 1, i32 4)\n"
    )


HANDLE = f"  %h = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, {UAV}, i32 11, i1 false)\n"
SHARED = "@s = external addrspace(3) global [64 x i32], align 4\n\n"
EXCHANGE = (
    "v0:\n" + HANDLE + "  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
    "  %x = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n"
    "  %lane = call i32 @dx.op.waveGetLaneIndex(i32 111)\n"
    "  %z = icmp eq i32 %t, 0\n"
    "  %any = call i1 @dx.op.waveAnyTrue(i32 113, i1 %z)\n"
    "  %role = select i1 %any, i32 0, i32 1\n"
    "  %p = getelementptr [64 x i32], [64 x i32] addrspace(3)* @s, i32 0, i32 %t\n"
    "  store i32 %x, i32 addrspace(3)* %p, align 4\n"
    "  call void @dx.op.barrier(i32 80, i32 9)\n"
    "  %u = add i32 %t, 32\n  %w = and i32 %u, 63\n"
    "  %q = getelementptr [64 x i32], [64 x i32] addrspace(3)* @s, i32 0, i32 %w\n"
    "  %v = load i32, i32 addrspace(3)* %q, align 4\n"
    "  %r = mul i32 %role, 1000\n  %s1 = add i32 %v, %r\n  %s2 = add i32 %s1, %lane\n"
    "  %a = shl i32 %x, 2\n" + store("%a", "%s2") + "  ret void\n"
)


def run(text, group=(0, 0, 0), mapping=None, profile="random"):
    memory = eq.Memory(profile, 1, eq._cbuffer((64, 1)))
    stores, _ = eq.run_group(eq.Function(text), group, memory, mapping)
    return eq.store_map(stores)[0]


class Model(unittest.TestCase):
    def test_exchange_with_any_thread_order(self):
        maps = eq.mappings(64, 32, 4)
        for name, mapping in maps.items():
            words = run(module(EXCHANGE, SHARED), (3, 0, 0), mapping)
            wave = {t: slot // 32 for slot, t in enumerate(mapping)}
            lane = {t: slot % 32 for slot, t in enumerate(mapping)}
            for t in range(64):
                role = 0 if wave[t] == wave[0] else 1
                expected = 3 * 64 + ((t + 32) & 63) + 1000 * role + lane[t]
                self.assertEqual(words[(1, 0, 11), 4 * (3 * 64 + t)], expected, name)

    def test_shared_race_and_unwritten_reads(self):
        racy = EXCHANGE.replace("  call void @dx.op.barrier(i32 80, i32 9)\n", "")
        with self.assertRaisesRegex(eq.ProgramError, "race"):
            run(module(racy, SHARED))
        unwritten = EXCHANGE.replace("  store i32 %x, i32 addrspace(3)* %p, align 4\n", "")
        with self.assertRaisesRegex(eq.ProgramError, "unwritten"):
            run(module(unwritten, SHARED))

    def test_barrier_and_wave_rules(self):
        divergent = (
            "v0:\n  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
            "  %c = icmp ult i32 %t, 16\n  br i1 %c, label %v1, label %v2\n\n"
            "v1:\n  ret void\n\nv2:\n  call void @dx.op.barrier(i32 80, i32 9)\n  ret void\n"
        )
        with self.assertRaisesRegex(eq.ProgramError, "barrier"):
            run(module(divergent))
        partial = (
            "v0:\n  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
            "  %b = and i32 %t, 1\n  %c = icmp eq i32 %b, 0\n  br i1 %c, label %v1, label %v2\n\n"
            "v1:\n  %a = call i1 @dx.op.waveAnyTrue(i32 113, i1 %c)\n  ret void\n\n"
            "v2:\n  ret void\n"
        )
        with self.assertRaisesRegex(eq.ProgramError, "every live lane"):
            run(module(partial))

    def test_loop_alloca_phi(self):
        body = "v0:\n" + HANDLE + "  %arr = alloca [4 x i32], align 4\n"
        for k, value in enumerate([5, -7, 2147483647, 3]):
            body += (
                f"  %p{k} = getelementptr [4 x i32], [4 x i32]* %arr, i32 0, i32 {k}\n"
                f"  store i32 {value}, i32* %p{k}, align 4\n"
            )
        body += (
            "  br label %v1\n\nv1:\n"
            "  %i = phi i32 [ 0, %v0 ], [ %n, %v1 ]\n  %acc = phi i32 [ 0, %v0 ], [ %acc2, %v1 ]\n"
            "  %p = getelementptr [4 x i32], [4 x i32]* %arr, i32 0, i32 %i\n"
            "  %e = load i32, i32* %p, align 4\n  %acc2 = add i32 %acc, %e\n"
            "  %n = add nuw nsw i32 %i, 1\n  %c = icmp eq i32 %n, 4\n"
            "  br i1 %c, label %v2, label %v1\n\nv2:\n" + store("0", "%acc2") + "  ret void\n"
        )
        self.assertEqual(run(module(body))[(1, 0, 11), 0], eq.w32(5 - 7 + 2147483647 + 3))
        with self.assertRaisesRegex(eq.ProgramError, "unwritten"):
            run(module(body.replace("  store i32 3, i32* %p3, align 4\n", "")))

    def test_buffer_loads_dot4_and_discards(self):
        srv = "%dx.types.ResBind { i32 18, i32 18, i32 0, i8 0 }"
        body = (
            "v0:\n"
            + HANDLE
            + f"  %g = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, {srv}, i32 18, i1 false)\n"
            "  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n  %a = shl i32 %t, 4\n"
            "  %r = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h, i32 %a, i32 undef, i8 15, i32 4)\n"
            "  %w0 = extractvalue %dx.types.ResRet.i32 %r, 0\n  %w3 = extractvalue %dx.types.ResRet.i32 %r, 3\n"
            "  %b = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %g, i32 %a, i32 undef, i8 1, i32 4)\n"
            "  %bias = extractvalue %dx.types.ResRet.i32 %b, 0\n"
            "  %d = call i32 @dx.op.dot4AddPacked.i32(i32 163, i32 %bias, i32 %w0, i32 %w3)\n"
            "  %c = icmp ult i32 %t, 40\n  %o = shl i32 %t, 2\n  %m = select i1 %c, i32 %o, i32 -4\n"
            + store("%m", "%d")
            + "  ret void\n"
        )
        memory = eq.Memory("random", 9, {})
        stores, _ = eq.run_group(eq.Function(module(body)), (0, 0, 0), memory)
        words, discarded = eq.store_map(stores)
        self.assertEqual((len(words), discarded), (40, 24))
        uav, srvh = (1, 0, 11), (0, 0, 18)
        for t in range(40):
            w0, w3 = memory.word(uav, 16 * t), memory.word(uav, 16 * t + 12)
            bias = memory.word(srvh, 16 * t)
            expected = eq.w32(
                bias + sum(x * y for x, y in zip(ir.signed_bytes(w0), ir.signed_bytes(w3)))
            )
            self.assertEqual(words[uav, 4 * t], expected)

    def test_conflicting_stores_refused(self):
        body = "v0:\n" + HANDLE + "  %t = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
        body += store("0", "%t") + "  ret void\n"
        with self.assertRaisesRegex(eq.ProgramError, "two stores"):
            run(module(body))

    def test_scalar_semantics(self):
        self.assertEqual(eq.f32(0.1), 0.10000000149011612)
        self.assertEqual(eq.f32(3.5e38), math.inf)
        self.assertEqual([eq.round_ne(v) for v in (2.5, 3.5, -2.5, 0.49999997)], [2, 4, -2, 0])
        self.assertEqual(eq.fptosi(1e10, 16), -1)  # saturate to i32, then wrap
        self.assertEqual((eq.fptosi(math.nan, 32), eq.fptosi(-3.9, 32)), (0, -3))
        self.assertEqual(eq.float_literal("0x3F40000000000000"), 2.0**-11)
        self.assertEqual(eq.w16(0x8000), -32768)
        rng = random.Random(3)
        mul = eq.Function._binary("mul", "<2 x i16>")
        lshr = eq.Function._binary("lshr", "i32")
        for _ in range(200):
            a, b = rng.randint(-32768, 32767), rng.randint(-32768, 32767)
            self.assertEqual(mul((a, b), (b, a))[0], eq.w16(a * b))
            v = eq.w32(rng.getrandbits(32))
            self.assertEqual(lshr(v, 7), (v & 0xFFFFFFFF) >> 7)
        with self.assertRaises(eq.ProgramError):
            eq.Function._binary("shl", "i32")(1, 32)

    def test_unsupported_instructions_refused(self):
        for line in ("  %a = sdiv i32 %b, 2\n", "  %a = fdiv float %b, %c\n"):
            with self.assertRaisesRegex(ir.LoweringError, "Unsupported"):
                eq.Function(module("v0:\n" + line + "  ret void\n"))


class Recipes(unittest.TestCase):
    def test_tree(self):
        summary = lower_pass.check_tree()
        for name in ("c10-pass1", "c10-pass2", "c10-pass12"):
            self.assertIn(f"{name}: 6 slots", summary)
        self.assertIn("(2 new)", summary)
        self.assertIn("rc11-pass2: 6 slots", summary)

    def test_c10_recipes_change_only_two_family_slots(self):
        families = {
            1: ("0x2cce40", "0x4bee10"),
            2: ("0x278960", "0x9739d0"),
            12: ("0x647fd0", "0xaed1d0"),
        }
        for number, slots in families.items():
            base = json.loads((ROOT / f"dll/port/recipes/rc11-pass{number}.json").read_text())
            c10 = json.loads((ROOT / f"dll/port/recipes/c10-pass{number}.json").read_text())
            self.assertEqual(c10["sequences"], base["sequences"])
            self.assertEqual(sorted(c10["expected"]), sorted(slots))
            hot = [k for k, v in base["slots"].items() if "winograd" in v]
            self.assertEqual(len(hot), 1)
            for key, sequences in c10["slots"].items():
                self.assertEqual(
                    sequences, base["slots"][hot[0]] if key in slots else base["slots"][key]
                )

    def write(self, recipe):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(recipe, handle)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_pin_refusals(self):
        good = json.loads((ROOT / "dll/port/recipes/c10-pass12.json").read_text())
        lower_pass.load_recipe(self.write(good))
        for change in [
            lambda r: r["expected"].update({"0x123": r["expected"]["0x647fd0"]}),
            lambda r: r["expected"]["0x647fd0"].update(source_sha256="x" * 64),
            lambda r: r["expected"]["0x647fd0"].pop("source_sha256"),
        ]:
            recipe = json.loads(json.dumps(good))
            change(recipe)
            with self.assertRaises(ir.LoweringError):
                lower_pass.load_recipe(self.write(recipe))
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        profile = json.loads((ROOT / "dll/port/profiles/sdk-2.3.0.json").read_text())
        recipe = json.loads(json.dumps(good))
        row = next(r for r in manifest["replacements"] if r["original_offset"] == 0x647FD0)
        recipe["expected"]["0x647fd0"]["replacement_sha256"] = row["replacement_sha256"]
        with self.assertRaisesRegex(ir.LoweringError, "redundant"):
            lower_pass.check_recipe(recipe, profile, manifest)


@unittest.skipUnless(
    CACHE and DXC, "needs BC250_FSR4_SDK_CACHE and BC250_FSR4_DXC (extracted DXC 1.9.2607)"
)
class RealFamilies(unittest.TestCase):
    """The C10 recipes regenerate their pins; the new programs equal the SDK's on sampled groups."""

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
            for name in ("rc11-pass2", "c10-pass1", "c10-pass2", "c10-pass12")
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_generate_matches_release_and_pins(self):
        for name, summary in self.summaries.items():
            self.assertEqual(summary["containers_equal"], 6, name)
            self.assertEqual(summary["sources_equal_except_module_id"], 6, name)
            self.assertEqual(summary["new_programs"], 0 if name.startswith("rc11") else 2, name)

    def test_new_program_equals_sdk_program(self):
        output = Path(self.tmp.name) / "prove.json"
        summary = eq.prove(
            ROOT / "dll/port/recipes/c10-pass12.json",
            self.sdk,
            DXC,
            Path(self.tmp.name) / "c10-pass12",
            [0x647FD0],
            output,
            1,
            2,
            ["small"],
        )
        record = summary["records"][0]
        self.assertTrue(record["exact"], record["failures"][:2])
        self.assertEqual(record["comparisons"]["new vs sdk"]["equal"], 48)
        self.assertGreater(record["distinct_stored_values"], 1000)

    def test_coefficient_mutant_is_detected(self):
        text = (Path(self.tmp.name) / "c10-pass12/shaders/00647fd0.ll").read_text()
        lines = text.split("\n")
        barrier = next(i for i, line in enumerate(lines) if "@dx.op.barrier(" in line)
        pattern = re.compile(r"(  %v\d+ = mul <2 x i16> %v\d+, <i16 )(-?\d+)(, i16 -?\d+>)")
        i = next(i for i in range(barrier, len(lines)) if pattern.fullmatch(lines[i]))
        m = pattern.fullmatch(lines[i])
        value = int(m[2])
        lines[i] = m[1] + str(value + 1 if value < 127 else value - 1) + m[3]
        mutated = eq.Function("\n".join(lines))
        original = eq.Function(text)
        width, height = 960, 540
        memory = lambda: eq.Memory("small", 4, eq._cbuffer((width, height)))  # noqa: E731
        group = (7, 200, 0)
        a = eq.store_map(eq.run_group(original, group, memory())[0])
        b = eq.store_map(eq.run_group(mutated, group, memory())[0])
        self.assertFalse(eq.compare(a, b)["equal"])


if __name__ == "__main__":
    unittest.main()
