# SPDX-License-Identifier: MIT
"""sdiv_shift: interval proofs for power-of-two sdivs, checked by an IR interpreter."""

import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from dll.port import sdiv_shift as s

CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
DXC = os.environ.get("BC250_DXC")
I32 = s.bounds(32)
EXTREMES = sorted(
    {0, 1, 2, 3, -1, -2, -3, I32[0], I32[0] + 1, I32[1], I32[1] - 1, 1 << 30, -(1 << 30)}
)
HEADER = """%dx.types.Handle = type { i8* }
%dx.types.ResBind = type { i32, i32, i32, i8 }
%dx.types.CBufRet.i32 = type { i32, i32, i32, i32 }

define void @main() {
  %h = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind { i32 1, i32 1, i32 0, i8 2 }, i32 1, i1 false)
  %cb = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %h, i32 4)
  %w = extractvalue %dx.types.CBufRet.i32 %cb, 0
  %ht = extractvalue %dx.types.CBufRet.i32 %cb, 1
  %tid = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)
  %gid = call i32 @dx.op.groupId.i32(i32 94, i32 0)
"""
FOOTER = """}

!dx.entryPoints = !{!1}
!1 = !{void ()* @main, !"main", null, null, !2}
!2 = !{i32 4, !3}
!3 = !{i32 64, i32 1, i32 1}
"""


def program(body):
    return HEADER + body + FOOTER


def same_runs(test, a, b, seeds, assume=None, pins=()):
    """Both programs compute the same values and path for every seed (and pinned inputs)."""
    for seed in seeds:
        for pin in [{}, *pins]:
            try:
                ta = s.interpret(a, s.Oracle(seed, pin), assume)
            except s.Poison:
                with test.assertRaises(s.Poison):
                    s.interpret(b, s.Oracle(seed, pin), assume)
                continue
            test.assertEqual(ta, s.interpret(b, s.Oracle(seed, pin), assume), (seed, pin))


def contained(test, text, seeds, assume=None):
    """Every value of every seeded run lies inside its analysed interval."""
    analysis = s.Analysis(s.Function(text), assume)
    checked = 0
    for seed in seeds:
        try:
            trace, _ = s.interpret(text, s.Oracle(seed), assume)
        except s.Poison:
            continue
        for name, value in trace:
            r = analysis.ranges.get(name)
            test.assertIsNotNone(r, name)
            test.assertTrue(r.lo <= value <= r.hi, (seed, name, value, r))
            checked += 1
    return checked


class Semantics(unittest.TestCase):
    def test_shift_equals_signed_division_exactly_for_non_negative_dividends(self):
        rng = random.Random(1)
        values = EXTREMES + [rng.randrange(*I32) for _ in range(20000)]
        for k in range(1, 31):
            for x in values:
                sdiv = s.binop("sdiv", [], x, 1 << k, 32)
                lshr = s.binop("lshr", [], x, k, 32)
                self.assertEqual(sdiv == lshr, x >= 0, (x, k))

    def test_minus_one_is_the_witness(self):
        self.assertEqual(s.binop("sdiv", [], -1, 2, 32), 0)
        self.assertEqual(s.binop("lshr", [], -1, 1, 32), I32[1])

    def test_interpreter_wraps_and_refuses_undefined_operations(self):
        self.assertEqual(s.binop("add", ["nsw"], I32[1], 1, 32), I32[0])
        self.assertEqual(s.binop("sdiv", [], -7, 2, 32), -3)
        self.assertEqual(s.binop("srem", [], -7, 2, 32), -1)
        self.assertEqual(s.binop("ashr", [], -7, 1, 32), -4)
        for op, a, b in [("sdiv", 1, 0), ("sdiv", I32[0], -1), ("shl", 1, 32), ("urem", 3, 0)]:
            with self.assertRaises(s.Poison):
                s.binop(op, [], a, b, 32)


class Stage(unittest.TestCase):
    def test_constant_buffer_dividend_is_refuted_and_left_alone(self):
        text = program(
            "  %q = sdiv i32 %w, 2\n"
            "  %x = or i32 %tid, 0\n"
            "  %out = icmp uge i32 %x, %q\n"
            "  br i1 %out, label %done, label %body\n"
            "body:\n  br label %done\ndone:\n  ret void\n"
        )
        new, records = s.apply(text)
        self.assertEqual(new, text)
        (r,) = records
        self.assertFalse(r["proven"])
        self.assertFalse(r["applied"])
        self.assertEqual(r["interval"], list(I32))
        self.assertEqual(r["witness"], dict(x=-1, sdiv=0, lshr=I32[1]))
        self.assertIn("b1[4].x", r["derivation"][0])
        forced = text.replace("sdiv i32 %w, 2", "lshr i32 %w, 1")
        _, path = s.interpret(text, s.Oracle(0, {"%w": -1, "%tid": 5}))
        _, shifted = s.interpret(forced, s.Oracle(0, {"%w": -1, "%tid": 5}))
        self.assertEqual(path, ["0", "done"])
        self.assertEqual(shifted, ["0", "body", "done"])

    def test_assumed_field_range_proves_and_rewrites(self):
        text = program("  %q = sdiv i32 %w, 2\n  %r = sdiv exact i32 %ht, 8\n  ret void\n")
        assume = {"b1[4].x": (0, 16384)}
        new, records = s.apply(text, assume)
        self.assertEqual([r["applied"] for r in records], [True, False])
        self.assertIn("  %q = lshr i32 %w, 1\n", new)
        self.assertIn("  %r = sdiv exact i32 %ht, 8\n", new)
        self.assertEqual(records[0]["facts"], ["assume:b1[4].x"])
        pins = [{"%w": v} for v in (0, 1, 16383, 16384)]
        same_runs(self, text, new, range(50), assume, pins)

    def test_masked_and_thread_derived_dividends_are_proven(self):
        text = program(
            "  %a = and i32 %w, 1023\n"
            "  %b = lshr i32 %ht, 1\n"
            "  %c = shl i32 %gid, 4\n"
            "  %d = or i32 %c, %tid\n"
            "  %e = add i32 %d, -1\n"
            "  %qa = sdiv i32 %a, 4\n"
            "  %qb = sdiv i32 %b, 2\n"
            "  %qd = sdiv i32 %d, 2\n"
            "  %qe = sdiv i32 %e, 2\n"
            "  ret void\n"
        )
        new, records = s.apply(text)
        verdicts = {r["value"]: (r["proven"], r["interval"]) for r in records}
        self.assertEqual(verdicts["%qa"], (True, [0, 1023]))
        self.assertEqual(verdicts["%qb"], (True, [0, I32[1]]))
        self.assertEqual(verdicts["%qd"], (True, [0, (65534 << 4) | 63]))
        self.assertEqual(verdicts["%qe"], (False, [-1, ((65534 << 4) | 63) - 1]))
        self.assertEqual(
            {r["value"]: r["facts"] for r in records}["%qd"], ["dispatch", "numthreads"]
        )
        self.assertIn("  %qa = lshr i32 %a, 2\n", new)
        self.assertIn("  %qe = sdiv i32 %e, 2\n", new)
        same_runs(self, text, new, range(200))
        contained(self, text, range(200))

    def test_without_dispatch_limit_group_ids_are_unbounded(self):
        text = program("  %c = shl i32 %gid, 4\n  %q = sdiv i32 %c, 2\n  ret void\n")
        self.assertTrue(s.analyze(text)[2][0]["proven"])
        self.assertFalse(s.analyze(text, dispatch=False)[2][0]["proven"])

    def test_loops_widen_to_a_sound_interval(self):
        text = program(
            "  br label %loop\n"
            "loop:\n"
            "  %i = phi i32 [ 0, %0 ], [ %n, %loop ]\n"
            "  %m = and i32 %i, 255\n"
            "  %n = add nuw nsw i32 %i, 16\n"
            "  %qi = sdiv i32 %i, 16\n"
            "  %qm = sdiv i32 %m, 16\n"
            "  %c = icmp ult i32 %n, 4096\n"
            "  br i1 %c, label %loop, label %exit\n"
            "exit:\n  ret void\n"
        )
        new, records = s.apply(text)
        self.assertEqual([r["proven"] for r in records], [False, True])
        same_runs(self, text, new, range(5))
        self.assertGreater(contained(self, text, range(5)), 1000)

    def test_unsupported_forms_are_reported_not_rewritten(self):
        text = program(
            "  %a = sdiv i32 %w, %ht\n  %b = sdiv i32 %w, 1\n  %c = sdiv i32 %w, -2\n"
            "  %d = sdiv i32 %w, 6\n  ret void\n"
        )
        new, records = s.apply(text)
        self.assertEqual(new, text)
        self.assertEqual([r["form"] for r in records], ["other"] * 4)


def random_program(rng):
    """A random integer program: entry, a diamond with phis, a counted loop and an exit."""
    lines, values, serial = [], {"%w": 32, "%ht": 32, "%tid": 32, "%gid": 32}, [0]

    def new():
        serial[0] += 1
        return f"%r{serial[0]}"

    def pick(width, pool):
        options = [v for v, w in pool.items() if w == width]
        if not options or rng.random() < 0.15:
            return str(
                rng.choice([0, 1, 2, 3, 7, 255, -1, -2, 1 << 20, -(1 << 30), *s.bounds(width)])
            )
        return rng.choice(options)

    def emit(pool, count):
        for _ in range(count):
            name = new()
            width = rng.choice([32, 32, 32, 16, 8])
            kind = rng.random()
            if kind < 0.45:
                op = rng.choice(["add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr"])
                flags = (
                    rng.choice(["", "", " nsw", " nuw nsw"])
                    if op in ("add", "sub", "mul", "shl")
                    else ""
                )
                b = (
                    str(rng.randrange(width))
                    if op in ("shl", "lshr", "ashr")
                    else pick(width, pool)
                )
                lines.append(f"  {name} = {op}{flags} i{width} {pick(width, pool)}, {b}")
            elif kind < 0.6:
                op = rng.choice(["sdiv", "sdiv", "udiv", "srem", "urem"])
                d = rng.choice([2, 4, 16, 1 << 20, 3, 5]) if width == 32 else rng.choice([2, 4, 3])
                lines.append(f"  {name} = {op} i{width} {pick(width, pool)}, {d}")
            elif kind < 0.7:
                c = new()
                pred = rng.choice(["slt", "ult", "sgt", "eq", "uge"])
                lines.append(
                    f"  {c} = icmp {pred} i{width} {pick(width, pool)}, {pick(width, pool)}"
                )
                lines.append(
                    f"  {name} = select i1 {c}, i{width} {pick(width, pool)}, i{width} {pick(width, pool)}"
                )
                pool[c] = 1
            elif kind < 0.85:
                src = rng.choice([w for w in (8, 16, 32) if w != width] or [32])
                op = "trunc" if src > width else rng.choice(["zext", "sext"])
                lines.append(f"  {name} = {op} i{src} {pick(src, pool)} to i{width}")
            elif width == 32:
                op, code = rng.choice([("IMax", 37), ("IMin", 38), ("UMax", 39), ("UMin", 40)])
                lines.append(
                    f"  {name} = call i32 @dx.op.binary.i32(i32 {code}, i32 {pick(32, pool)}, i32 {pick(32, pool)})"
                )
            else:
                lines.append(f"  {name} = and i{width} {pick(width, pool)}, {pick(width, pool)}")
            pool[name] = width

    entry = dict(values)
    emit(entry, rng.randrange(4, 14))
    cond = new()
    lines.append(f"  {cond} = icmp slt i32 {pick(32, entry)}, {pick(32, entry)}")
    lines.append(f"  br i1 {cond}, label %left, label %right")
    sides = {}
    for side in ("left", "right"):
        lines.append(f"{side}:")
        pool = dict(entry)
        emit(pool, rng.randrange(1, 8))
        sides[side] = pool
        lines.append("  br label %join")
    lines.append("join:")
    merged = dict(entry)
    for width in (32, 16, 8):
        name = new()
        lines.append(
            f"  {name} = phi i{width} [ {pick(width, sides['left'])}, %left ], "
            f"[ {pick(width, sides['right'])}, %right ]"
        )
        merged[name] = width
    lines.append("  br label %loop")
    lines.append("loop:")
    i, step = new(), new()
    acc, acc_next = new(), new()
    trips = rng.randrange(1, 6)
    lines.append(f"  {i} = phi i32 [ 0, %join ], [ {step}, %loop ]")
    lines.append(f"  {acc} = phi i32 [ {pick(32, merged)}, %join ], [ {acc_next}, %loop ]")
    loop = dict(merged, **{i: 32, acc: 32})
    emit(loop, rng.randrange(1, 6))
    op = rng.choice(["add", "mul", "xor", "sub", "shl"])
    amount = rng.randrange(1, 4) if op == "shl" else pick(32, loop)
    lines.append(f"  {acc_next} = {op} i32 {acc}, {amount}")
    lines.append(f"  {step} = add i32 {i}, 1")
    done = new()
    lines.append(f"  {done} = icmp eq i32 {step}, {trips}")
    lines.append(f"  br i1 {done}, label %exit, label %loop")
    lines.append("exit:")
    tail = dict(merged, **{i: 32, acc: 32, acc_next: 32})
    emit(tail, rng.randrange(2, 10))
    for _ in range(3):
        lines.append(f"  {new()} = sdiv i32 {pick(32, tail)}, {rng.choice([2, 4, 8, 1 << 16])}")
    lines.append("  ret void")
    return program("\n".join(lines) + "\n")


class RandomPrograms(unittest.TestCase):
    """The analysis must contain every concrete value; proven rewrites must not change any."""

    def test_random_programs(self):
        rng = random.Random(20261023)
        proven = refuted = values = 0
        for n in range(150):
            text = random_program(rng)
            with self.subTest(program=n):
                values += contained(self, text, range(n * 8, n * 8 + 8))
                new, records = s.apply(text)
                same_runs(self, text, new, range(n * 8, n * 8 + 8))
                for r in records:
                    if r["form"] == "other":
                        continue
                    proven += r["proven"]
                    refuted += not r["proven"]
                    if not r["proven"] and r["interval"]:
                        w = r["witness"]
                        self.assertTrue(r["interval"][0] <= w["x"] < 0)
                        self.assertNotEqual(w["sdiv"], w["lshr"])
        self.assertGreater(proven, 50)
        self.assertGreater(refuted, 50)
        self.assertGreater(values, 20000)


@unittest.skipUnless(CACHE and DXC, "set BC250_FSR4_SDK_CACHE and BC250_DXC (pinned dxc)")
class PublicFinalPass(unittest.TestCase):
    """SDK 2.3.0's final pass: each power-of-two sdiv reads a constant-buffer field."""

    def test_final_pass_divisions_are_not_provable(self):
        from dll.port import sdk_intake as intake

        record = intake.lookup(intake.read_allowlist()[0], "v2.3.0")
        data = intake.Cache(CACHE).get(record["dll"]["sha256"])
        intake.allowlisted(data)
        blobs = [b for _, b in s.carve(data) if b"_postpass" in b]
        self.assertEqual(len(blobs), 144)
        found = 0
        with tempfile.TemporaryDirectory() as tmp:
            for blob in blobs[::12]:
                path = Path(tmp) / "p.dxbc"
                path.write_bytes(blob)
                text = subprocess.run(
                    [DXC, "-dumpbin", str(path)], check=True, capture_output=True, text=True
                ).stdout
                new, records = s.apply(text)
                self.assertEqual(new, text)
                for r in records:
                    found += 1
                    self.assertFalse(r["proven"])
                    self.assertRegex(r["derivation"][0], r"constant-buffer field b1\[4\]\.[xy]")
                contained(self, text, range(2))
        self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
