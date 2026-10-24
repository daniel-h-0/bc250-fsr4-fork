#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Group-level equivalence of two compute programs on sampled thread groups (CPU only).

The lowering stages are checked stage by stage on synthetic programs (tests/test_lowering.py).
This module checks a finished program end to end against the SDK's own program for the same
slot: it interprets the text IR (the SDK disassembly as `rename_ssa` prints it, and the stages'
outputs) for whole thread groups and compares every buffer store.

Model (documented choices, the same for every program compared):
- A group is `numthreads` threads; each wave holds `wave` lanes. The thread-to-(wave, lane)
  assignment is a parameter (identity, the other wave first, or a seeded permutation), so a
  program may not rely on any order the hardware does not promise.
- Control flow runs per thread; threads at the same point run together. `dx.op.barrier` is a
  group sync: every thread that has not returned must reach the same barrier. Wave operations
  need every live lane of the wave in the same step.
- Group shared memory is checked for races: within one barrier interval, an address written by
  one thread and read or written by another is an error, as is a read of an unwritten address.
- Buffer loads read the pre-dispatch contents (a deterministic function of the binding, the byte
  address and the test profile); stores go to a log. A program that read its own stores within
  the dispatch would be racy across groups already, so the log is the defined semantics.
- Integers wrap at their width. Floats are IEEE binary32 with round-to-nearest-even per
  operation (computed in binary64 and rounded once, which gives the correctly rounded binary32
  result of +, - and *); `fptosi` saturates to i32 and then wraps to the target width (NaN
  gives 0); `fast` flags are ignored. The lowering stages do not touch float code, so these
  choices apply identically to every program compared.

Commands: `prove` checks generated programs against the SDK program and RC11's program for the
same slots and writes a JSON record; see dll/port/README.md.
"""

import argparse
import json
import math
import random
import re
import struct
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if __package__:
    from . import drop_profile, lower_pass, sdk_intake
    from .ir import LoweringError, masked_stores, numthreads, rename_ssa, require
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drop_profile
    import lower_pass
    import sdk_intake
    from ir import LoweringError, masked_stores, numthreads, rename_ssa, require

M32 = 0xFFFFFFFF
DLL = Path(__file__).resolve().parent.parent


class ProgramError(LoweringError):
    """The program did something the model defines as an error (race, divergent barrier, ...)."""


def w32(v):
    return ((v + 0x80000000) & M32) - 0x80000000


def w16(v):
    return ((v + 0x8000) & 0xFFFF) - 0x8000


def w8(v):
    return ((v + 0x80) & 0xFF) - 0x80


WRAP = {"i32": w32, "i16": w16, "i8": w8, "i1": bool}
BITS = {"i32": 32, "i16": 16, "i8": 8, "i1": 1}


def f32(x):
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def fptosi(x, bits):
    if math.isnan(x):
        return 0
    if math.isinf(x):
        value = (1 << 31) - 1 if x > 0 else -(1 << 31)
    else:
        value = max(-(1 << 31), min((1 << 31) - 1, math.trunc(x)))
    return WRAP[f"i{bits}"](value) if bits < 32 else value


def round_ne(x):
    return x if not math.isfinite(x) else f32(float(round(x)))


def split_args(text):
    """Top-level comma split (braces, brackets, angle brackets and parentheses nest)."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "({[<":
            depth += 1
        elif ch in ")}]>":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def float_literal(token):
    if token.startswith("0x"):
        return f32(struct.unpack(">d", bytes.fromhex(token[2:].rjust(16, "0")))[0])
    return f32(float(token))


TYPE = r"(?:i1|i8|i16|i32|i64|float|<2 x i16>|<2 x i32>|%[\w.]+)"


def constant(ty, token):
    """A literal operand of type ty, or None when the token is a value name."""
    if token.startswith("%") or token.startswith("@"):
        return None
    if token in ("undef", "zeroinitializer"):
        return (0, 0) if ty.startswith("<") else (0.0 if ty == "float" else 0)
    if ty.startswith("<"):
        m = re.fullmatch(r"<i(16|32) (-?\d+), i\1 (-?\d+)>", token)
        require(m, "Unsupported vector literal " + token)
        return (int(m[2]), int(m[3]))
    if ty == "float":
        return float_literal(token)
    if ty == "i1":
        require(token in ("true", "false"), "Unsupported i1 literal " + token)
        return token == "true"
    require(re.fullmatch(r"-?\d+", token), "Unsupported literal " + token)
    return int(token)


class Instruction:
    __slots__ = ("kind", "dest", "run", "text", "targets")

    def __init__(self, kind, dest, run, text, targets=None):
        self.kind, self.dest, self.run, self.text, self.targets = kind, dest, run, text, targets


def _clean(line):
    line = line.split("  ;")[0].rstrip()
    line = re.sub(r"(, !(?:tbaa|dx\.[\w.]+|llvm\.[\w.]+) !\d+)+$", "", line)
    return re.sub(r" #\d+$", "", line)


class Function:
    """One `define void @entry()` of a text-IR module, compiled to per-step closures."""

    def __init__(self, text):
        self.text = text
        self.numthreads = numthreads(text)
        self.shared = {
            m[1]: int(m[2])
            for m in re.finditer(
                r"^@([\w.]+) = (?:external )?addrspace\(3\) global \[(\d+) x i32\]", text, re.M
            )
        }
        body = re.search(r"^define void @([\w.]+)\(\) \{\n(.*?)^\}$", text, re.M | re.S)
        require(body, "No entry function")
        self.entry = body[1]
        self.blocks, self.order = {}, []
        label = None
        for raw in body[2].splitlines():
            if m := re.match(r"^([\w.$-]+):", raw):
                label = m[1]
                require(label not in self.blocks, "Duplicate block " + label)
                self.blocks[label] = dict(phis=[], segments=[[]], terminator=None)
                self.order.append(label)
                continue
            line = _clean(raw)
            if not line.strip():
                continue
            require(label is not None, "Instruction before the first label")
            block = self.blocks[label]
            require(block["terminator"] is None, "Instruction after a terminator in " + label)
            ins = self._compile(line.strip())
            if ins.kind == "phi":
                require(
                    not any(block["segments"][0]) and len(block["segments"]) == 1,
                    "phi after a non-phi instruction",
                )
                block["phis"].append(ins)
            elif ins.kind == "barrier":
                block["segments"].append([])
            elif ins.kind in ("br", "brc", "ret"):
                block["terminator"] = ins
            elif ins.kind != "nop":
                block["segments"][-1].append(ins)
        for name, block in self.blocks.items():
            require(block["terminator"] is not None, "Block without terminator " + name)
        self.wave_ops = any(
            ins.kind.startswith("wave")
            for block in self.blocks.values()
            for segment in block["segments"]
            for ins in segment
        )
        self.barriers = sum(len(b["segments"]) - 1 for b in self.blocks.values())

    # Operands -----------------------------------------------------------------------------

    @staticmethod
    def _getter(ty, token):
        value = constant(ty, token)
        if value is None:
            require(token.startswith("%"), "Unsupported operand " + token)
            return lambda st, name=token: st.env[name]
        broadcast = None

        def get(st, value=value):
            nonlocal broadcast
            if broadcast is None:
                broadcast = [value] * st.size
            return broadcast

        return get

    @staticmethod
    def _typed(arg):
        m = re.fullmatch(r"(" + TYPE + r"|i8\*|%dx\.types\.\w+) (.+)", arg)
        require(m, "Unsupported operand " + arg)
        return m[1], m[2]

    # Compilation --------------------------------------------------------------------------

    def _compile(self, line):
        dest = None
        if m := re.fullmatch(r"(%[\w.$-]+) = (.+)", line):
            dest, rhs = m[1], m[2]
        else:
            rhs = line
        g = self._getter
        if rhs == "ret void":
            return Instruction("ret", None, None, line)
        if m := re.fullmatch(r"br label %([\w.$-]+)", rhs):
            return Instruction("br", None, None, line, (m[1],))
        if m := re.fullmatch(r"br i1 (%[\w.$-]+), label %([\w.$-]+), label %([\w.$-]+)", rhs):
            cond = g("i1", m[1])
            return Instruction("brc", None, cond, line, (m[2], m[3]))
        if m := re.fullmatch(r"phi (" + TYPE + r") (.+)", rhs):
            incoming = {}
            for item in re.findall(r"\[ ([^,\]]+), %([\w.$-]+) \]", m[2]):
                incoming[item[1]] = g(m[1], item[0])
            require(len(incoming) == m[2].count("["), "Unsupported phi")
            return Instruction("phi", dest, incoming, line)
        if m := re.fullmatch(
            r"(add|sub|mul|shl|ashr|lshr|and|or|xor)((?: nuw| nsw| exact)*) (" + TYPE + r") (.+)",
            rhs,
        ):
            op, ty = m[1], m[3]
            a, b = split_args(m[4])
            return self._op(dest, self._binary(op, ty), [g(ty, a), g(ty, b)], line)
        if m := re.fullmatch(
            r"icmp (eq|ne|ugt|uge|ult|ule|sgt|sge|slt|sle) (i32|i16|i1) (.+)", rhs
        ):
            a, b = split_args(m[3])
            bits = BITS[m[2]]
            mask = (1 << bits) - 1
            pred = m[1]
            if pred in ("eq", "ne"):
                f = (lambda x, y: x == y) if pred == "eq" else (lambda x, y: x != y)
            else:
                cmp = {
                    "gt": lambda x, y: x > y,
                    "ge": lambda x, y: x >= y,
                    "lt": lambda x, y: x < y,
                    "le": lambda x, y: x <= y,
                }[pred[1:]]
                if pred[0] == "u":
                    f = lambda x, y, c=cmp: c(x & mask, y & mask)  # noqa: E731
                else:
                    f = cmp
            return self._op(dest, f, [g(m[2], a), g(m[2], b)], line)
        if m := re.fullmatch(r"select i1 ([^,]+), (.+)", rhs):
            a, b = split_args(m[2])
            ta, va = self._typed(a)
            tb, vb = self._typed(b)
            require(ta == tb, "select types differ")
            return self._op(
                dest, lambda c, x, y: x if c else y, [g("i1", m[1]), g(ta, va), g(tb, vb)], line
            )
        if m := re.fullmatch(
            r"(sext|zext|trunc|bitcast|sitofp|fptosi) ("
            + TYPE
            + r"|\[\d+ x i32\]\*) (\S+) to (.+)",
            rhs,
        ):
            return self._cast(dest, m[1], m[2], m[3], m[4], line)
        if m := re.fullmatch(r"extractelement <2 x i(?:16|32)> (\S+), i32 ([01])", rhs):
            k = int(m[2])
            return self._op(dest, lambda v: v[k], [g("<2 x i16>", m[1])], line)
        if m := re.fullmatch(
            r"insertelement <2 x (i16|i32)> (\S+), (?:i16|i32) (\S+), i32 ([01])", rhs
        ):
            k = int(m[4])
            vty = f"<2 x {m[1]}>"
            f = (lambda v, e: (e, v[1])) if k == 0 else (lambda v, e: (v[0], e))
            return self._op(dest, f, [g(vty, m[2]), g(m[1], m[3])], line)
        if m := re.fullmatch(r"extractvalue %dx\.types\.[\w.]+ (%[\w.$-]+), (\d+)", rhs):
            k = int(m[2])
            return self._op(dest, lambda v: v[k], [g("struct", m[1])], line)
        if m := re.fullmatch(r"(fadd|fsub|fmul)(?: fast)? float (.+)", rhs):
            a, b = split_args(m[2])
            f = {
                "fadd": lambda x, y: f32(x + y),
                "fsub": lambda x, y: f32(x - y),
                "fmul": lambda x, y: f32(x * y),
            }[m[1]]
            return self._op(dest, f, [g("float", a), g("float", b)], line)
        if m := re.fullmatch(r"alloca \[(\d+) x i32\], align 4", rhs):
            size = int(m[1])
            return self._op(dest, lambda: [None] * size, [], line)
        if m := re.fullmatch(
            r"getelementptr(?: inbounds)? \[(\d+) x i32\], \[\1 x i32\](?: addrspace\(3\))?\* (\S+), i32 0, i32 (\S+)",
            rhs,
        ):
            size, base, index = int(m[1]), m[2], g("i32", m[3])
            if base.startswith("@"):
                name = base[1:]
                require(self.shared.get(name) == size, "Unknown shared array " + base)
                return self._op(dest, lambda i: ("lds", name, i, size), [index], line)
            require(base.startswith("%"), "Unsupported pointer base")
            return self._op(
                dest, lambda arr, i: ("local", arr, i, size), [g("ptr", base), index], line
            )
        if m := re.fullmatch(r"load i32, i32(?: addrspace\(3\))?\* (\S+), align 4", rhs):
            return Instruction("load", dest, g("ptr", m[1]), line)
        if m := re.fullmatch(r"store i32 (\S+), i32(?: addrspace\(3\))?\* (\S+), align 4", rhs):
            return Instruction("store", None, (g("i32", m[1]), g("ptr", m[2])), line)
        if m := re.fullmatch(
            r"call (?:void|i32|i1|float|%dx\.types\.[\w.]+) @([\w.]+)\((.*)\)", rhs
        ):
            return self._call(dest, m[1], split_args(m[2]), line)
        raise LoweringError("Unsupported instruction: " + line)

    def _op(self, dest, f, getters, line):
        def run(st, idx):
            args = [get(st) for get in getters]
            if idx is None:
                st.env[dest] = list(map(f, *args)) if args else [f() for _ in range(st.size)]
                return
            out = st.env.get(dest)
            if out is None:
                out = st.env[dest] = [None] * st.size
            if args:
                values = map(f, *[[a[i] for i in idx] for a in args])
            else:
                values = (f() for _ in idx)
            for i, r in zip(idx, values):
                out[i] = r

        return Instruction("op", dest, run, line)

    @staticmethod
    def _binary(op, ty):
        if ty in ("<2 x i16>", "<2 x i32>"):
            wrap = w16 if "i16" in ty else w32
            bits = 16 if "i16" in ty else 32
            scalar = Function._binary_scalar(op, wrap, bits)
            return lambda a, b: (scalar(a[0], b[0]), scalar(a[1], b[1]))
        require(ty in WRAP, "Unsupported integer type " + ty)
        if ty == "i1":
            require(op in ("and", "or", "xor"), "Unsupported i1 operation")
            return {
                "and": lambda a, b: bool(a) & bool(b),
                "or": lambda a, b: bool(a) | bool(b),
                "xor": lambda a, b: bool(a) ^ bool(b),
            }[op]
        return Function._binary_scalar(op, WRAP[ty], BITS[ty])

    @staticmethod
    def _binary_scalar(op, wrap, bits):
        mask = (1 << bits) - 1

        def shift(b):
            if not 0 <= b < bits:
                raise ProgramError(f"shift by {b} at width {bits}")
            return b

        return {
            "add": lambda a, b: wrap(a + b),
            "sub": lambda a, b: wrap(a - b),
            "mul": lambda a, b: wrap(a * b),
            "shl": lambda a, b: wrap(a << shift(b)),
            "ashr": lambda a, b: a >> shift(b),
            "lshr": lambda a, b: wrap((a & mask) >> shift(b)),
            "and": lambda a, b: a & b,
            "or": lambda a, b: a | b,
            "xor": lambda a, b: a ^ b,
        }[op]

    def _cast(self, dest, op, src, token, target, line):
        g = self._getter
        if op == "bitcast" and src.endswith("*"):
            return Instruction("nop", dest, None, line)  # pointer casts feed only lifetime markers
        if op == "bitcast":
            require(src == "i32" and target == "<2 x i16>", "Unsupported bitcast")
            return self._op(
                dest, lambda v: (w16(v & 0xFFFF), w16((v >> 16) & 0xFFFF)), [g(src, token)], line
            )
        if op in ("sext", "zext"):
            if src == "<2 x i16>":
                require(target == "<2 x i32>", "Unsupported vector extension")
                f = (lambda v: v) if op == "sext" else (lambda v: (v[0] & 0xFFFF, v[1] & 0xFFFF))
                return self._op(dest, f, [g(src, token)], line)
            require(
                src in ("i1", "i8", "i16") and target in ("i16", "i32"), "Unsupported extension"
            )
            if op == "sext":
                f = (lambda v: -1 if v else 0) if src == "i1" else (lambda v: v)
            else:
                mask = (1 << BITS[src]) - 1
                f = (lambda v: 1 if v else 0) if src == "i1" else (lambda v: v & mask)
            return self._op(dest, f, [g(src, token)], line)
        if op == "trunc":
            require(src in WRAP and target in WRAP, "Unsupported trunc")
            wrap = WRAP[target]
            return self._op(dest, wrap, [g(src, token)], line)
        if op == "sitofp":
            require(src == "i32" and target == "float", "Unsupported sitofp")
            return self._op(dest, lambda v: f32(float(v)), [g(src, token)], line)
        require(src == "float" and target in ("i16", "i32"), "Unsupported fptosi")
        bits = BITS[target]
        return self._op(dest, lambda v: fptosi(v, bits), [g(src, token)], line)

    def _call(self, dest, name, args, line):
        g = self._getter
        typed = [self._typed(a) for a in args]

        def const(k):
            value = constant(typed[k][0], typed[k][1])
            require(value is not None, f"{name}: operand {k} must be a literal")
            return value

        if name.startswith("llvm.lifetime."):
            return Instruction("nop", None, None, line)
        require(name.startswith("dx.op."), "Unsupported call " + name)
        opcode = const(0)
        op = name[len("dx.op.") :]
        if op == "barrier":
            return Instruction("barrier", None, None, line)
        if op == "dot4AddPacked.i32":
            require(opcode == 163, "Unsupported dot4 variant")

            def dot(acc, a, b):
                return w32(
                    acc
                    + sum(
                        ((((a >> k) & 255) ^ 128) - 128) * ((((b >> k) & 255) ^ 128) - 128)
                        for k in (0, 8, 16, 24)
                    )
                )

            return self._op(dest, dot, [g("i32", typed[k][1]) for k in (1, 2, 3)], line)
        if op == "threadId.i32" or op == "threadIdInGroup.i32" or op == "groupId.i32":
            require(opcode in (93, 94, 95), "Unsupported thread ID")
            key = {93: "tid", 94: "gid", 95: "tig"}[opcode] + "xyz"[const(1)]
            return self._op(dest, lambda v: v, [lambda st, key=key: st.ids[key]], line)
        if op == "flattenedThreadIdInGroup.i32":
            return self._op(dest, lambda v: v, [lambda st: st.ids["tigx"]], line)
        if op == "waveGetLaneIndex":
            require(opcode == 111, "Unsupported wave op")
            return Instruction("wave_lane", dest, None, line)
        if op == "waveAnyTrue":
            require(opcode == 113, "Unsupported wave op")
            return Instruction("wave_any", dest, g("i1", typed[1][1]), line)
        if op == "createHandleFromBinding":
            m = re.fullmatch(r"\{ i32 (\d+), i32 (\d+), i32 (\d+), i8 (\d+) \}", typed[1][1])
            require(m, "Unsupported binding")
            require(int(m[1]) <= const(2) <= int(m[2]), "Binding index outside its range")
            handle = (int(m[4]), int(m[3]), const(2))  # (class, space, register)
            return self._op(dest, lambda: handle, [], line)
        if op == "annotateHandle":
            return self._op(dest, lambda h: h, [g("handle", typed[1][1])], line)
        if op == "rawBufferLoad.i32":
            require(opcode == 139 and typed[3][1] == "undef", "Unsupported buffer load")
            mask = const(4)
            return Instruction(
                "buffer_load", dest, (g("handle", typed[1][1]), g("i32", typed[2][1]), mask), line
            )
        if op == "rawBufferStore.i32":
            require(opcode == 140 and typed[3][1] == "undef", "Unsupported buffer store")
            mask = const(8)
            values = [g("i32", typed[k][1]) for k in range(4, 8)]
            return Instruction(
                "buffer_store",
                None,
                (g("handle", typed[1][1]), g("i32", typed[2][1]), values, mask),
                line,
            )
        if op == "cbufferLoadLegacy.i32":
            require(opcode == 59, "Unsupported cbuffer load")
            return Instruction("cbuffer", dest, (g("handle", typed[1][1]), const(2)), line)
        if op == "binary.i32":
            f = {
                37: max,
                38: min,
                39: lambda a, b: a if (a & M32) >= (b & M32) else b,
                40: lambda a, b: a if (a & M32) <= (b & M32) else b,
            }.get(opcode)
            require(f, f"Unsupported binary opcode {opcode}")
            return self._op(dest, f, [g("i32", typed[1][1]), g("i32", typed[2][1])], line)
        if op == "tertiary.i32":
            require(opcode in (48, 49), f"Unsupported tertiary opcode {opcode}")
            return self._op(
                dest,
                lambda a, b, c: w32(a * b + c),
                [g("i32", typed[k][1]) for k in (1, 2, 3)],
                line,
            )
        if op == "unary.f32":
            require(opcode == 26, f"Unsupported unary opcode {opcode}")
            return self._op(dest, round_ne, [g("float", typed[1][1])], line)
        if op == "pack4x8.i32":
            require(opcode == 220, "Unsupported pack")
            mode = const(1)
            clamp = {
                0: lambda v: v & 255,
                1: lambda v: max(0, min(255, v)),
                2: lambda v: max(-128, min(127, v)) & 255,
            }[mode]

            def pack(a, b, c, d):
                return w32(clamp(a) | clamp(b) << 8 | clamp(c) << 16 | clamp(d) << 24)

            return self._op(dest, pack, [g("i32", typed[k][1]) for k in (2, 3, 4, 5)], line)
        if op == "unpack4x8.i32":
            require(opcode == 219, "Unsupported unpack")
            signed = const(1) == 1

            def unpack(v):
                parts = [(v >> k) & 255 for k in (0, 8, 16, 24)]
                return tuple((p ^ 128) - 128 for p in parts) if signed else tuple(parts)

            return self._op(dest, unpack, [g("i32", typed[2][1])], line)
        raise LoweringError("Unsupported dx.op call " + name)


def _mix(x):
    x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


def _bytes(values):
    return w32(sum((v & 255) << (8 * k) for k, v in enumerate(values)))


# Test profiles: (word source for UAVs, word source for SRVs). Each takes the 64-bit hash.
PROFILES = {
    "random": (lambda h: w32(h & M32), lambda h: w32(h & M32)),
    "small": (
        lambda h: _bytes([((h >> (4 * k)) & 15) - 8 for k in range(4)]),
        lambda h: ((h >> 8) & 8191) - 4096,
    ),
    "extreme": (
        lambda h: _bytes([-128 if (h >> k) & 1 else 127 for k in range(4)]),
        lambda h: w32(h & M32),
    ),
    "sparse": (
        lambda h: _bytes(
            [((h >> (8 + 8 * k)) & 255) if (h >> (40 + 5 * k)) % 23 == 0 else 0 for k in range(4)]
        ),
        lambda h: 0,
    ),
    "min": (lambda h: w32(0x80808080), lambda h: w32(h & M32)),
    "max": (lambda h: 0x7F7F7F7F, lambda h: w32(h & M32)),
    "alternate": (lambda h: w32(0x807F807F), lambda h: ((h >> 8) & 8191) - 4096),
}


class Memory:
    """Pre-dispatch buffer contents as a function of (binding, byte address); stores logged."""

    def __init__(self, profile, seed, cbuffer):
        self.uav, self.srv = PROFILES[profile]
        self.seed = seed
        self.cbuffer = cbuffer  # register -> 4 i32
        self.stores = []

    def word(self, handle, address):
        kind, space, binding = handle
        h = _mix(_mix(self.seed * 1000003 + binding * 7919 + space) ^ (address & M32))
        return (self.srv if kind == 0 else self.uav)(h)


class GroupState:
    def __init__(self, function, group, mapping, memory, wave):
        nx, ny, nz = function.numthreads
        require(ny == nz == 1, "Only (N, 1, 1) groups are modelled")
        self.size = nx
        require(sorted(mapping) == list(range(nx)), "Mapping is not a permutation")
        gx, gy, gz = group
        self.ids = dict(
            tidx=[gx * nx + t for t in range(nx)],
            tidy=[gy] * nx,
            tidz=[gz] * nx,
            tigx=list(range(nx)),
            tigy=[0] * nx,
            tigz=[0] * nx,
            gidx=[gx] * nx,
            gidy=[gy] * nx,
            gidz=[gz] * nx,
        )
        # mapping[k] = thread at hardware slot k; slot k is lane k % wave of wave k // wave.
        self.wave_of, self.lane_of = [0] * nx, [0] * nx
        for slot, thread in enumerate(mapping):
            self.wave_of[thread], self.lane_of[thread] = divmod(slot, wave)
        self.env = {}
        self.memory = memory
        self.lds, self.epoch_writes, self.epoch_reads = {}, {}, {}
        self.steps = [0] * nx


def _shared_access(st, thread, pointer, value=None):
    _, name, index, size = pointer
    if not 0 <= index < size:
        raise ProgramError(f"shared index {index} outside @{name}")
    key = (name, index)
    writer = st.epoch_writes.get(key)
    if value is None:
        if writer is not None and writer != thread:
            raise ProgramError(f"shared race: thread {thread} reads @{name}[{index}] of {writer}")
        if key not in st.lds:
            raise ProgramError(f"shared read of unwritten @{name}[{index}]")
        st.epoch_reads.setdefault(key, set()).add(thread)
        return st.lds[key]
    if writer is not None and writer != thread:
        raise ProgramError(f"shared race: two writers of @{name}[{index}]")
    if st.epoch_reads.get(key, set()) - {thread}:
        raise ProgramError(f"shared race: @{name}[{index}] written after another thread read it")
    st.epoch_writes[key] = thread
    st.lds[key] = value
    return None


def _local(pointer, value=None):
    _, array, index, size = pointer
    if not 0 <= index < size:
        raise ProgramError(f"local index {index} outside [{size} x i32]")
    if value is None:
        if array[index] is None:
            raise ProgramError("local read of an unwritten element")
        return array[index]
    array[index] = value
    return None


def _execute(st, ins, idx):
    """Run one non-phi instruction for the threads idx (None = every thread)."""
    kind = ins.kind
    if kind == "op":
        ins.run(st, idx)
        return
    threads = range(st.size) if idx is None else idx
    env = st.env
    if kind in ("load", "store", "buffer_load", "buffer_store", "cbuffer", "wave_lane", "wave_any"):
        if ins.dest is not None:
            out = env.get(ins.dest)
            if out is None or idx is None:
                out = env[ins.dest] = [None] * st.size
    if kind == "load":
        pointers = ins.run(st)
        for i in threads:
            p = pointers[i]
            out[i] = _shared_access(st, i, p) if p[0] == "lds" else _local(p)
    elif kind == "store":
        values, pointers = ins.run[0](st), ins.run[1](st)
        for i in threads:
            p = pointers[i]
            if p[0] == "lds":
                _shared_access(st, i, p, values[i])
            else:
                _local(p, values[i])
    elif kind == "buffer_load":
        handles, addresses, mask = ins.run[0](st), ins.run[1](st), ins.run[2]
        memory = st.memory
        for i in threads:
            a = addresses[i]
            out[i] = tuple(
                memory.word(handles[i], a + 4 * k) if mask >> k & 1 else 0 for k in range(4)
            ) + (0,)
    elif kind == "buffer_store":
        handles, addresses, values, mask = (
            ins.run[0](st),
            ins.run[1](st),
            [v(st) for v in ins.run[2]],
            ins.run[3],
        )
        for i in threads:
            for k in range(4):
                if mask >> k & 1:
                    st.memory.stores.append(
                        (handles[i], (addresses[i] + 4 * k) & M32, values[k][i], i)
                    )
    elif kind == "cbuffer":
        regs = ins.run[1]
        for i in threads:
            out[i] = tuple(st.memory.cbuffer.get(regs, (0, 0, 0, 0)))
    elif kind == "wave_lane":
        for i in threads:
            out[i] = st.lane_of[i]
    elif kind == "wave_any":
        cond = ins.run(st)
        members = {}
        for i in threads:
            members.setdefault(st.wave_of[i], []).append(i)
        for wave, lanes in members.items():
            live = {t for t in range(st.size) if st.wave_of[t] == wave and st.status[t] != "done"}
            if live != set(lanes):
                raise ProgramError("wave operation without every live lane of the wave")
            result = any(cond[t] for t in lanes)
            for t in lanes:
                out[t] = result
    else:
        raise ProgramError("Unexpected instruction kind " + kind)


def run_group(function, group, memory, mapping=None, wave=32, limit=2_000_000):
    """Execute one thread group; returns (stores, per-thread step counts)."""
    size = function.numthreads[0]
    st = GroupState(function, group, mapping or list(range(size)), memory, wave)
    st.status = ["run"] * size
    position = [(function.order[0], 0)] * size
    previous = [None] * size
    barrier_at = [None] * size
    total = 0
    while True:
        runnable = [t for t in range(size) if st.status[t] == "run"]
        if not runnable:
            waiting = [t for t in range(size) if st.status[t] == "barrier"]
            if not waiting:
                break
            if len(waiting) != size:
                raise ProgramError("barrier not reached by every thread of the group")
            if len({barrier_at[t] for t in waiting}) != 1:
                raise ProgramError("threads wait at different barriers")
            for t in waiting:
                st.status[t] = "run"
            st.epoch_writes, st.epoch_reads = {}, {}
            continue
        here = position[runnable[0]]
        batch = [t for t in runnable if position[t] == here]
        idx = None if len(batch) == size else batch
        label, segment = here
        block = function.blocks[label]
        if segment == 0 and block["phis"]:
            new = []
            for phi in block["phis"]:
                values = {}
                for t in batch:
                    getter = phi.run.get(previous[t])
                    if getter is None:
                        raise ProgramError(f"phi in {label} has no edge from {previous[t]}")
                    values[t] = getter(st)[t]
                new.append((phi.dest, values))
            for dest, values in new:
                out = st.env.get(dest)
                if out is None or idx is None:
                    out = st.env[dest] = [None] * size
                for t, v in values.items():
                    out[t] = v
        for ins in block["segments"][segment]:
            _execute(st, ins, idx)
        count = len(block["segments"][segment]) + (len(block["phis"]) if segment == 0 else 0)
        total += count * len(batch)
        for t in batch:
            st.steps[t] += count
        if total > limit * size:
            raise ProgramError("step limit exceeded")
        if segment + 1 < len(block["segments"]):
            for t in batch:
                st.status[t] = "barrier"
                barrier_at[t] = here
                position[t] = (label, segment + 1)
            continue
        term = block["terminator"]
        if term.kind == "ret":
            for t in batch:
                st.status[t] = "done"
            continue
        if term.kind == "br":
            targets = [term.targets[0]] * size
        else:
            cond = term.run(st)
            targets = [term.targets[0] if cond[t] else term.targets[1] for t in range(size)]
        for t in batch:
            previous[t] = label
            position[t] = (targets[t], 0)
    return memory.stores, st.steps


def store_map(stores, discard=(M32 - 3,)):
    """Address -> value of the non-discarded stores, plus the discarded count; refuses conflicts."""
    words, discarded = {}, 0
    for handle, address, value, _ in stores:
        if address in discard:
            discarded += 1
            continue
        key = (handle, address)
        if key in words and words[key] != value:
            raise ProgramError(f"two stores to {address:#x} with different values")
        words[key] = value
    return words, discarded


def compare(a, b):
    """Differences between two (words, discarded) store maps."""
    (wa, da), (wb, db) = a, b
    keys = sorted(set(wa) | set(wb))
    diff = [
        dict(address=k[1], binding=k[0][2], a=wa.get(k), b=wb.get(k))
        for k in keys
        if wa.get(k) != wb.get(k)
    ]
    return dict(
        equal=not diff and da == db,
        words=len(keys),
        differing=len(diff),
        discarded=[da, db],
        first=diff[:4],
    )


# The prove driver ----------------------------------------------------------------------------


def grid_of(text):
    """(width, height) of the SDK program's grid bound check."""
    x = re.search(r"icmp ugt i32 (%v\d+), (\d+)\n  %v\d+ = icmp ugt i32 (%v\d+), (\d+)", text)
    require(x, "Grid bound check not found")
    return int(x[2]) + 1, int(x[4]) + 1


def cases(width, height, seed):
    """Sampled groups (corners, edges, seeded interior, one outside, one z = 1) and extents."""
    rng = random.Random(seed)
    columns = width // 64
    groups = [
        (0, 0, 0),
        (columns - 1, 0, 0),
        (0, height - 1, 0),
        (columns - 1, height - 1, 0),
        (columns // 2, height // 2, 0),
        (rng.randrange(columns), rng.randrange(height), 0),
        (rng.randrange(columns), rng.randrange(height), 1),
        (rng.randrange(columns), height, 0),  # outside the grid: no stores
    ]
    out = []
    for group in groups:
        out.append(dict(group=group, extents=(width, height)))
        # Extents cutting through the group (x) and through the row (y = last valid row).
        out.append(dict(group=group, extents=(group[0] * 64 + 37, min(height, group[1] + 1))))
    return out


def mappings(size, wave, seed):
    rng = random.Random(seed)
    permutation = list(range(size))
    rng.shuffle(permutation)
    other_first = list(range(wave, size)) + list(range(wave))
    return dict(identity=list(range(size)), other_wave_first=other_first, permutation=permutation)


def _cbuffer(extents):
    return {reg: (extents[0], extents[1], 0, 0) for reg in range(17)}


def check_slot(job):
    """Every case of one slot: the new program against the SDK program (with RC7's discard
    repair) and against RC11's program, and RC11's against the SDK's as the control."""
    texts, options = job["texts"], job["options"]
    functions = {name: Function(text) for name, text in texts.items()}
    width, height = grid_of(texts["sdk"])
    size = functions["sdk"].numthreads[0]
    maps = mappings(size, 32, options["seed"])
    record = dict(
        offset=job["offset"],
        grid=[width, height],
        cases=0,
        group_runs=0,
        failures=[],
        comparisons={},
        steps={},
        barriers={n: f.barriers for n, f in functions.items()},
        wave_ops={n: f.wave_ops for n, f in functions.items()},
        stored_words=0,
        distinct_stored_values=0,
    )
    seen = set()
    pairs = [("rc11", "sdk"), ("new", "sdk"), ("new", "rc11")]
    for case_number, case in enumerate(cases(width, height, options["seed"])):
        for profile in options["profiles"]:
            seed = options["seed"] * 7919 + case_number
            results = {}
            for name, function in functions.items():
                names = list(maps) if function.wave_ops else ["identity"]
                for mapping_name in names:
                    memory = Memory(profile, seed, _cbuffer(case["extents"]))
                    try:
                        stores, steps = run_group(
                            function, case["group"], memory, maps[mapping_name]
                        )
                        results[name, mapping_name] = store_map(stores)
                        record["steps"][name] = max(record["steps"].get(name, 0), max(steps))
                    except (ProgramError, LoweringError) as error:
                        results[name, mapping_name] = None
                        record["failures"].append(
                            dict(
                                case=case,
                                profile=profile,
                                program=f"{name}/{mapping_name}",
                                error=str(error),
                            )
                        )
                    record["group_runs"] += 1
            reference = results.get(("sdk", "identity"))
            if reference:
                seen.update(reference[0].values())
                record["stored_words"] += len(reference[0])
            for a, b in pairs:
                for (name, mapping_name), result in results.items():
                    if name != a:
                        continue
                    other = results.get((b, "identity"))
                    label = f"{a} vs {b}"
                    entry = record["comparisons"].setdefault(
                        label, dict(runs=0, equal=0, words=0, discarded=0)
                    )
                    entry["runs"] += 1
                    if result is None or other is None:
                        continue
                    c = compare(other, result)
                    entry["equal"] += c["equal"]
                    entry["words"] += c["words"]
                    entry["discarded"] += c["discarded"][1]
                    if not c["equal"]:
                        record["failures"].append(
                            dict(
                                case=case,
                                profile=profile,
                                comparison=label,
                                mapping=mapping_name,
                                first=c["first"],
                                differing=c["differing"],
                            )
                        )
            record["cases"] += 1
    record["distinct_stored_values"] = len(seen)
    record["exact"] = not record["failures"] and all(
        e["runs"] == e["equal"] for e in record["comparisons"].values()
    )
    return record


def _texts(offset, sdk_data, slot, toolchain, manifest, generated, pins):
    name = f"{offset:08x}"
    container = sdk_data[slot["offset"] : slot["offset"] + slot["bytes"]]
    require(
        lower_pass.sha(container) == slot["sha256"], f"{name}: SDK program differs from the profile"
    )
    sdk = rename_ssa(toolchain.disassemble(container, name))
    # RC7's discard-address repair (128 MiB -> 0xfffffffc), which every replacement carries.
    sdk, repaired = masked_stores(sdk)
    row = next(r for r in manifest["replacements"] if r["original_offset"] == offset)
    rc11 = (DLL / row["source"]).read_text()
    require(
        lower_pass.sha(rc11) == row["source_sha256"],
        f"{name}: RC11 source differs from the manifest",
    )
    new = (Path(generated) / "shaders" / f"{name}.ll").read_text()
    pin = pins.get(hex(offset))
    if pin is not None:
        require(
            lower_pass.sha(new) == pin["source_sha256"],
            f"{name}: generated source is not the pinned one",
        )
    return dict(sdk=sdk, rc11=rc11, new=new), dict(
        sdk_discard_repairs=len(repaired),
        sdk_sha256=slot["sha256"],
        rc11_replacement_sha256=row["replacement_sha256"],
        new_source_sha256=lower_pass.sha(new),
    )


def prove(recipe_path, sdk, dxc, generated, offsets, output, jobs, seed, profiles):
    output = sdk_intake.outside_source(Path(output))
    require(not output.exists(), "The record must be a new file")
    recipe = lower_pass.load_recipe(recipe_path)
    manifest = json.loads((DLL / "manifest.json").read_text())
    data = Path(sdk).read_bytes()
    _, profile = drop_profile.select(lower_pass.sha(data))
    drop_profile.verify(data, profile)
    slots = {s["offset"]: s for s in profile["shaders"]["slots"]}
    pins = recipe.get("expected", {})
    offsets = offsets or [int(k, 16) for k in recipe["slots"]]
    work_jobs, identities = [], {}
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        toolchain = lower_pass.Toolchain(dxc, work, manifest["dxcompiler_sha256"])
        for offset in offsets:
            require(hex(offset) in recipe["slots"], f"{offset:#x} is not a slot of the recipe")
            texts, ids = _texts(offset, data, slots[offset], toolchain, manifest, generated, pins)
            identities[hex(offset)] = ids
            work_jobs.append(
                dict(offset=hex(offset), texts=texts, options=dict(seed=seed, profiles=profiles))
            )
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        records = list(pool.map(check_slot, work_jobs))
    for record in records:
        record.update(identities[record["offset"]])
    summary = dict(
        kind="fsr4-group-equivalence",
        recipe=recipe["name"],
        recipe_sha256=lower_pass.sha(Path(recipe_path).read_bytes()),
        sdk_sha256=lower_pass.sha(data),
        checker_sha256=lower_pass.sha(Path(__file__).read_bytes()),
        seed=seed,
        profiles=profiles,
        slots=len(records),
        exact=sum(r["exact"] for r in records),
        records=records,
    )
    output.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prove", help="generated programs against SDK and RC11 programs")
    command.add_argument("--recipe", type=Path, required=True)
    command.add_argument("--sdk", type=Path, required=True)
    command.add_argument("--dxc", type=Path, required=True)
    command.add_argument("--generated", type=Path, required=True, help="a `generate` output")
    command.add_argument("--slot", action="append", default=[], help="slot offset (default: all)")
    command.add_argument("--output", type=Path, required=True, help="record JSON outside the tree")
    command.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    command.add_argument("--seed", type=int, default=1)
    command.add_argument("--profile", action="append", choices=sorted(PROFILES), default=[])
    args = parser.parse_args(argv)
    try:
        summary = prove(
            args.recipe,
            args.sdk,
            args.dxc,
            args.generated,
            [int(s, 16) for s in args.slot],
            args.output,
            args.jobs,
            args.seed,
            args.profile or sorted(PROFILES),
        )
    except (sdk_intake.Refused, drop_profile.ProfileError, LoweringError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    for record in summary["records"]:
        print(
            record["offset"],
            "exact" if record["exact"] else "DIFFERS",
            record["cases"],
            "cases",
            {k: f"{v['equal']}/{v['runs']}" for k, v in record["comparisons"].items()},
        )
    return 0 if summary["exact"] == summary["slots"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit("ERROR: " + str(error))
