# SPDX-License-Identifier: MIT
"""Whole-program differential check of a lowered program against the SDK program of its slot.

The lowering stages prove each rewrite locally (interval bounds, IR-interpreter tests on
synthetic programs). This module checks a finished replacement program as a whole: it runs the
SDK program, the RC release program and the new program of one slot on the same deterministic
inputs and requires identical results.

`Program` interprets the DXIL text IR the stages read and write (DXC 1.9.2607 printer forms):
integer, float and vector arithmetic, control flow with phis, local arrays, constant globals and
the dx.op intrinsics the model passes use. Everything a thread cannot compute comes from an
`Inputs` oracle: thread and group IDs, constant-buffer rows and raw-buffer words. The oracle is
a pure function of (seed, key), so every program of a run sees the same inputs whatever order it
reads them in. Each run's result is the ordered log of buffer stores.

Wave intrinsics are evaluated over the whole wave: `waveAllTrue` replays the program for every
other lane of the wave up to the same instruction and combines the operands of the lanes that
reach it, so a guard that splits a table across lanes is checked as the GPU evaluates it.

Floating point is IEEE binary32: each operation is computed in binary64 and rounded once to
binary32, which is the correctly rounded binary32 result for +, -, * and / (53 >= 2 * 24 + 2).
Operations whose GPU rounding is implementation-defined get one fixed model (FMad unfused). Both programs execute
the same float operations on the same values unless a stage changed them, so any fixed model is
enough for a differential check; a stage that changes float code needs its own proof.

The SDK writes masked-out results to byte address 128 MiB; RC7's `masked_stores` moved that
discard address to 0xfffffffc. Stores to either address compare as one discard address when a
program is compared with the SDK program; release and new programs compare exactly.
"""

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

if __package__:
    from .ir import LoweringError, rename_ssa, require
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ir import LoweringError, rename_ssa, require

DISCARD_ADDRESSES = (134217728, -4)
# The SDK 2.3.0 model initializer (t18): file offset, size and SHA256 (dll/port README).
MODEL = dict(
    offset=12601856,
    bytes=131072,
    sha256="a54e552ff69a3f7199861417f7ae461d1f67f636ab532c6ec9d8ea5d2963735a",
)
INITIALIZER = (0, 0, 18)  # (resource class SRV, space 0, register 18)


class Poison(Exception):
    """An operation the IR leaves undefined (division by zero, oversized shift)."""


class Stop(Exception):
    """A lane replay reached the instruction it was asked to stop at."""

    def __init__(self, value):
        self.value = value


# ------------------------------------------------------------------------------------ values


def wrap(x, bits):
    if bits == 1:
        return x & 1
    half = 1 << (bits - 1)
    return ((x + half) & ((1 << bits) - 1)) - half


def unsigned(x, bits):
    return x & ((1 << bits) - 1)


def f32(x):
    """Round a binary64 value to binary32 (nearest even); overflow gives an infinity."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


F32 = f32


def f32_bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f32_from_bits(word):
    return struct.unpack("<f", struct.pack("<I", word & 0xFFFFFFFF))[0]


def fdiv(x, y):
    if y == 0:
        if x == 0 or math.isnan(x):
            return math.nan
        return math.copysign(math.inf, x) * math.copysign(1.0, y)
    return f32(x / y)


def f32_round(x, mode):
    """rint (half to even), floor, ceil, trunc of a binary32 value."""
    if not math.isfinite(x):
        return x
    return f32(float({"ne": round, "ni": math.floor, "pi": math.ceil, "z": math.trunc}[mode](x)))


def float_literal(token):
    if token.startswith("0x"):
        return f32(struct.unpack(">d", bytes.fromhex(token[2:].rjust(16, "0")))[0])
    return f32(float(token))


VECTOR = re.compile(r"<(\d+) x ([^>]+)>$")


def scalar_bits(ty):
    m = re.fullmatch(r"i(\d+)", ty)
    return int(m[1]) if m else None


def split_top(text, sep=","):
    """Split at top-level separators (outside <>, [], {}, ())."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "<[{(":
            depth += 1
        elif ch in ">]})":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def typed(token):
    """'i32 %v1' -> ('i32', '%v1'); '<2 x i16> <i16 1, i16 2>' -> ('<2 x i16>', '<...>')."""
    token = token.strip()
    if token.startswith("<"):
        depth = 0
        for i, ch in enumerate(token):
            depth += ch == "<"
            depth -= ch == ">"
            if depth == 0:
                end = i + 1
                while end < len(token) and token[end] == "*":
                    end += 1
                return token[:end], token[end:].strip()
    if token.startswith("%dx.types."):
        m = re.match(r"(%dx\.types\.[\w.]+)\s+(.*)", token)
        return m[1], m[2]
    m = re.match(r"(\[[^\]]+\]\*|[\w.]+\**)\s+(.*)", token)
    require(m, "Unparsed operand: " + token)
    return m[1], m[2]


# ----------------------------------------------------------------------------------- program


class Instr:
    """kind: 0 value function, 1 call/dx.op tuple, 2 terminator tuple, 3 phi table."""

    __slots__ = ("name", "op", "args", "line", "uid", "fn", "kind", "observe")

    def __init__(self, name, op, args, line):
        self.name, self.op, self.args, self.line = name, op, args, line
        self.uid = None
        self.fn = None
        self.kind = 0
        self.observe = None


class Program:
    """Parsed module: constant globals, functions with blocks of compiled instructions."""

    def __init__(self, text):
        self.text = text
        self.globals = {}
        self.functions = {}
        self.entry = None
        self.numthreads = (64, 1, 1)
        self.wave = None
        self._instructions = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("@") and " constant " in line:
                self._global(line)
            elif line.startswith("define "):
                m = re.match(r"define (?:internal )?void @([\w.$]+)\(\)", line)
                require(m, "Unsupported function: " + line[:80])
                j = i + 1
                while lines[j] != "}":
                    j += 1
                self.functions[m[1]] = self._function(lines[i + 1 : j], i + 1)
                if not line.startswith("define internal") and self.entry is None:
                    self.entry = m[1]
                i = j
            i += 1
        require(self.entry, "No entry function")
        self._metadata()
        for n, instr in enumerate(self._instructions):
            instr.uid = n

    # -- module parts
    def _global(self, line):
        m = re.match(
            r"@([\w.$]+) = (?:internal )?(?:unnamed_addr )?constant \[(\d+) x ([^\]]+)\] (.*?), align \d+$",
            line,
        )
        if not m:
            return  # refused only if an instruction references it (operand)
        name, count, ty, body = m[1], int(m[2]), m[3], m[4]
        if body == "zeroinitializer":
            values = [self.constant(ty, "zeroinitializer")] * count
        else:
            require(body.startswith("[") and body.endswith("]"), "Unsupported initializer")
            values = [self.constant(*typed(item)) for item in split_top(body[1:-1])]
        require(len(values) == count, "Global size mismatch")
        self.globals[name] = values

    def _metadata(self):
        text = self.text
        entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", text)
        if not entry:
            return
        node = re.search(r"^!" + entry[1] + r" = !\{(.*)\}$", text, re.M)
        props = re.findall(r"!(\d+)", node[1])[-1] if node else None
        body = re.search(r"^!" + props + r" = !\{(.*)\}$", text, re.M) if props else None
        if not body:
            return
        if m := re.search(r"i32 4, !(\d+)", body[1]):
            sizes = re.search(
                r"^!" + m[1] + r" = !\{i32 (\d+), i32 (\d+), i32 (\d+)\}$", text, re.M
            )
            self.numthreads = tuple(int(v) for v in sizes.groups())
        if m := re.search(r"i32 (?:11|23), !(\d+)", body[1]):
            self.wave = int(re.search(r"^!" + m[1] + r" = !\{i32 (\d+)", text, re.M)[1])

    def _function(self, lines, first):
        blocks, order, current = {}, [], None
        for k, raw in enumerate(lines):
            line = raw.split(";")[0].rstrip() if not raw.lstrip().startswith(";") else ""
            if not line:
                if m := re.match(r"^; <label>:(\d+)", raw):
                    current = "v" + m[1]
                    blocks[current] = []
                    order.append(current)
                continue
            if m := re.match(r"^([\w.$-]+):", line):
                current = m[1]
                blocks[current] = []
                order.append(current)
                continue
            if current is None:
                current = "entry"
                blocks[current] = []
                order.append(current)
            line = re.sub(r"(, !\w+ !\d+)+$", "", line)
            line = re.sub(r"\s+#\d+$", "", line)
            instr = self._parse(line.strip(), first + k)
            blocks[current].append(instr)
            self._instructions.append(instr)
        return dict(blocks=blocks, order=order)

    def _parse(self, line, number):
        m = re.match(r"(%[\w.$-]+) = (.*)$", line)
        name, rhs = (m[1], m[2]) if m else (None, line)
        op = rhs.split(" ", 1)[0]
        instr = Instr(name, op, rhs, number)
        instr.fn = self._compile(instr, rhs)
        if op == "phi":
            instr.kind = 3
        elif op in ("br", "ret", "switch", "unreachable"):
            instr.kind = 2
        elif isinstance(instr.fn, tuple):
            instr.kind = 1
        return instr

    # -- constants and operands
    def constant(self, ty, token):
        token = token.strip()
        if v := VECTOR.match(ty):
            n, ety = int(v[1]), v[2]
            if token in ("zeroinitializer", "undef"):
                return tuple(
                    self.constant(ety, "0" if ety.startswith("i") else "0.0") for _ in range(n)
                )
            require(token.startswith("<") and token.endswith(">"), "Vector literal: " + token)
            items = [typed(t) for t in split_top(token[1:-1])]
            return tuple(self.constant(t, val) for t, val in items)
        if ty in ("float", "half"):
            if token in ("undef", "zeroinitializer"):
                return 0.0
            return float_literal(token)
        if ty == "i1":
            return (
                {"true": 1, "false": 0, "undef": 0, "zeroinitializer": 0}.get(token, None)
                if not re.fullmatch(r"-?\d+", token)
                else int(token) & 1
            )
        bits = scalar_bits(ty)
        if bits:
            if token in ("undef", "zeroinitializer"):
                return 0
            require(re.fullmatch(r"-?\d+", token), f"Integer literal expected: {ty} {token}")
            return wrap(int(token), bits)
        if ty.startswith("%dx.types.ResBind"):
            items = [typed(t) for t in split_top(token.strip("{} "))]
            return tuple(self.constant(t, v) for t, v in items)
        if ty.startswith("%dx.types.ResourceProperties"):
            return ("props", token)
        raise LoweringError(f"Unsupported constant {ty} {token}")

    def operand(self, ty, token):
        token = token.strip()
        if token.startswith("%"):
            name = token
            return lambda env: env[name]
        if token.startswith("@"):
            name = token[1:]
            require(name in self.globals, "Unknown global " + token)
            return lambda env, ref=("global", name): (self.globals[ref[1]], 0)
        value = self.constant(ty, token)
        return lambda env: value

    # -- compiler
    def _compile(self, instr, rhs):
        op = instr.op
        operand = self.operand
        if op in (
            "add",
            "sub",
            "mul",
            "shl",
            "lshr",
            "ashr",
            "and",
            "or",
            "xor",
            "udiv",
            "sdiv",
            "urem",
            "srem",
        ):
            m = re.match(r"\w+(?: nuw| nsw| exact)* (.*)$", rhs)
            ty, rest = typed(m[1])
            a_tok, b_tok = split_top(rest)
            a, b = operand(ty, a_tok), operand(ty, b_tok)
            if v := VECTOR.match(ty):
                bits = scalar_bits(v[2])
                return lambda env: tuple(intop(op, x, y, bits) for x, y in zip(a(env), b(env)))
            bits = scalar_bits(ty)
            return lambda env: intop(op, a(env), b(env), bits)
        if op in ("fadd", "fsub", "fmul", "fdiv", "frem"):
            m = re.match(r"\w+(?: fast| nnan| ninf| nsz| arcp)* (.*)$", rhs)
            ty, rest = typed(m[1])
            a_tok, b_tok = split_top(rest)
            a, b = operand(ty, a_tok), operand(ty, b_tok)
            f = {
                "fadd": lambda x, y: F32(x + y),
                "fsub": lambda x, y: F32(x - y),
                "fmul": lambda x, y: F32(x * y),
                "fdiv": fdiv,
                "frem": lambda x, y: F32(math.fmod(x, y)) if y and math.isfinite(x) else math.nan,
            }[op]
            if VECTOR.match(ty):
                return lambda env: tuple(f(x, y) for x, y in zip(a(env), b(env)))
            return lambda env: f(a(env), b(env))
        if op == "icmp":
            m = re.match(r"icmp (\w+) (.*)$", rhs)
            pred = m[1]
            ty, rest = typed(m[2])
            a_tok, b_tok = split_top(rest)
            a, b = operand(ty, a_tok), operand(ty, b_tok)
            bits = scalar_bits(ty)
            return lambda env: icmp(pred, a(env), b(env), bits)
        if op == "fcmp":
            m = re.match(r"fcmp(?: fast| nnan| ninf| nsz| arcp)* (\w+) (.*)$", rhs)
            pred = m[1]
            ty, rest = typed(m[2])
            a_tok, b_tok = split_top(rest)
            a, b = operand(ty, a_tok), operand(ty, b_tok)
            return lambda env: fcmp(pred, a(env), b(env))
        if op == "select":
            parts = split_top(rhs[len("select ") :])
            c = operand(*typed(parts[0]))
            x, y = operand(*typed(parts[1])), operand(*typed(parts[2]))
            return lambda env: x(env) if c(env) else y(env)
        if op in (
            "sext",
            "zext",
            "trunc",
            "bitcast",
            "sitofp",
            "uitofp",
            "fptosi",
            "fptoui",
            "fpext",
            "fptrunc",
        ):
            m = re.match(r"\w+ (.*) to (.*)$", rhs)
            src, tok = typed(m[1])
            dst = m[2].strip()
            a = operand(src, tok)
            return cast(op, src, dst, a)
        if op == "extractelement":
            parts = split_top(rhs[len("extractelement ") :])
            v = operand(*typed(parts[0]))
            i = operand(*typed(parts[1]))
            return lambda env: v(env)[i(env)]
        if op == "insertelement":
            parts = split_top(rhs[len("insertelement ") :])
            v = operand(*typed(parts[0]))
            x = operand(*typed(parts[1]))
            i = operand(*typed(parts[2]))

            def insert(env):
                out = list(v(env))
                out[i(env)] = x(env)
                return tuple(out)

            return insert
        if op == "shufflevector":
            parts = split_top(rhs[len("shufflevector ") :])
            a, b = operand(*typed(parts[0])), operand(*typed(parts[1]))
            mask = self.constant(*typed(parts[2]))

            def shuffle(env):
                both = tuple(a(env)) + tuple(b(env))
                return tuple(both[k] for k in mask)

            return shuffle
        if op == "extractvalue":
            parts = split_top(rhs[len("extractvalue ") :])
            agg = operand(*typed(parts[0]))
            idx = int(parts[1])
            return lambda env: agg(env)[idx]
        if op == "phi":
            m = re.match(r"phi (\S+) (.*)$", rhs)
            ty = m[1]
            pairs = re.findall(r"\[ ([^,\]]+(?:<[^>]*>)?), %([\w.$-]+) \]", m[2])
            return dict((block, operand(ty, value)) for value, block in pairs)
        if op == "alloca":
            m = re.match(r"alloca \[(\d+) x ([^\]]+)\]", rhs)
            require(m, "Unsupported alloca: " + rhs)
            count = int(m[1])
            return lambda env: ([0] * count, 0)
        if op == "getelementptr":
            body = re.sub(r"^getelementptr (inbounds )?", "", rhs)
            parts = split_top(body)
            base = operand(*typed(parts[1]))
            indices = [operand(*typed(p)) for p in parts[2:]]
            require(len(indices) in (1, 2), "Unsupported getelementptr: " + rhs)
            if len(indices) == 2:
                first, second = indices

                def gep2(env):
                    storage, at = base(env)
                    require(first(env) == 0, "Non-zero first GEP index")
                    return storage, at + second(env)

                return gep2
            (only,) = indices
            return lambda env: (base(env)[0], base(env)[1] + only(env))
        if op == "load":
            parts = split_top(rhs[len("load ") :])
            ptr = operand(*typed(parts[1]))

            def load(env):
                storage, at = ptr(env)
                if not 0 <= at < len(storage):
                    raise Poison(f"load outside its array (line {instr.line})")
                return storage[at]

            return load
        if op == "store":
            parts = split_top(rhs[len("store ") :])
            value = operand(*typed(parts[0]))
            ptr = operand(*typed(parts[1]))

            def store(env):
                storage, at = ptr(env)
                if not 0 <= at < len(storage):
                    raise Poison(f"store outside its array (line {instr.line})")
                storage[at] = value(env)

            return store
        if op == "call":
            return self._call(rhs)
        if op == "ret":
            return ("ret",)
        if op == "br":
            if m := re.fullmatch(r"br label %([\w.$-]+)", rhs):
                return ("br", m[1])
            m = re.fullmatch(r"br i1 (\S+), label %([\w.$-]+), label %([\w.$-]+)", rhs)
            require(m, "Unsupported branch: " + rhs)
            return ("cbr", operand("i1", m[1]), m[2], m[3])
        if op == "switch":
            m = re.match(r"switch (\w+) (\S+), label %([\w.$-]+) \[(.*)\]", rhs)
            require(m, "Unsupported switch: " + rhs[:120])
            bits = scalar_bits(m[1])
            cases = {
                wrap(int(v), bits): label
                for v, label in re.findall(r"\w+ (-?\d+), label %([\w.$-]+)", m[4])
            }
            return ("switch", operand(m[1], m[2]), cases, m[3])
        raise LoweringError("Unsupported instruction: " + rhs[:120])

    def _call(self, rhs):
        m = re.match(r"call (\S+(?: \{[^}]*\})?|%dx\.types\.[\w.]+) @([\w.$]+)\((.*)\)$", rhs)
        require(m, "Unsupported call: " + rhs[:120])
        callee, args = m[2], split_top(m[3])
        if callee.startswith("llvm.lifetime."):
            return lambda env: None
        if not callee.startswith("dx.op."):
            return ("call", callee)
        opcode = int(typed(args[0])[1])
        ops = [self.operand(*typed(a)) for a in args[1:]]
        return ("dxop", opcode, callee, ops, args)


def intop(op, a, b, bits):
    if op == "add":
        return wrap(a + b, bits)
    if op == "sub":
        return wrap(a - b, bits)
    if op == "mul":
        return wrap(a * b, bits)
    if op == "and":
        return wrap(a & b, bits)
    if op == "or":
        return wrap(a | b, bits)
    if op == "xor":
        return wrap(a ^ b, bits)
    ub = unsigned(b, bits)
    if op in ("shl", "lshr", "ashr"):
        if ub >= bits:
            raise Poison(f"{op} by {ub}")
        if op == "shl":
            return wrap(a << ub, bits)
        if op == "lshr":
            return wrap(unsigned(a, bits) >> ub, bits)
        return wrap(a >> ub, bits)
    if op in ("udiv", "urem"):
        if ub == 0:
            raise Poison("division by zero")
        ua = unsigned(a, bits)
        return wrap(ua // ub if op == "udiv" else ua % ub, bits)
    if b == 0 or (a == -(1 << (bits - 1)) and b == -1):
        raise Poison("signed division undefined")
    q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
    return wrap(q if op == "sdiv" else a - q * b, bits)


def icmp(pred, a, b, bits):
    if pred[0] == "u":
        a, b = unsigned(a, bits), unsigned(b, bits)
    p = pred[1:] if pred[0] in "us" else pred
    return int(
        {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[p]
    )


def fcmp(pred, a, b):
    unordered = math.isnan(a) or math.isnan(b)
    if pred == "true":
        return 1
    if pred == "false":
        return 0
    if pred == "ord":
        return int(not unordered)
    if pred == "uno":
        return int(unordered)
    if unordered:
        return int(pred[0] == "u")
    rel = {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[
        pred[1:]
    ]
    return int(bool(rel))


def to_int32_from_float(x, signed=True):
    """GPU float -> int conversion: NaN -> 0, saturating, truncating toward zero."""
    if math.isnan(x):
        return 0
    lo, hi = (-(1 << 31), (1 << 31) - 1) if signed else (0, (1 << 32) - 1)
    if math.isinf(x):
        return hi if x > 0 else lo
    v = math.trunc(x)
    return min(max(v, lo), hi)


def cast(op, src, dst, a):
    sv, dv = VECTOR.match(src), VECTOR.match(dst)
    if op == "bitcast":
        if src.endswith("*") or dst.endswith("*"):
            return a
        if src == "i32" and dv and dv[1] == "2" and dv[2] == "i16":
            return lambda env: (wrap(a(env) & 0xFFFF, 16), wrap((a(env) >> 16) & 0xFFFF, 16))
        if sv and sv[1] == "2" and sv[2] == "i16" and dst == "i32":
            return lambda env: wrap(unsigned(a(env)[0], 16) | (unsigned(a(env)[1], 16) << 16), 32)
        if src == "i32" and dst == "float":
            return lambda env: f32_from_bits(a(env))
        if src == "float" and dst == "i32":
            return lambda env: wrap(f32_bits(a(env)), 32)
        raise LoweringError(f"Unsupported bitcast {src} -> {dst}")
    if sv:
        ety = sv[2]
        dety = dv[2]
        one = cast(op, ety, dety, lambda env: env)
        return lambda env: tuple(one(x) for x in a(env))
    if op in ("sext", "zext", "trunc"):
        sb, db = scalar_bits(src), scalar_bits(dst)
        if op == "sext":
            if sb == 1:
                return lambda env: -a(env) if db != 1 else a(env)
            return lambda env: wrap(a(env), db)
        if op == "zext":
            return lambda env: unsigned(a(env), sb)
        return lambda env: wrap(a(env), db)
    if op == "sitofp":
        bits = scalar_bits(src)
        return lambda env: f32(float(wrap(a(env), bits)))
    if op == "uitofp":
        bits = scalar_bits(src)
        return lambda env: f32(float(unsigned(a(env), bits)))
    if op == "fptosi":
        bits = scalar_bits(dst)
        return lambda env: wrap(to_int32_from_float(a(env)), bits)
    if op == "fptoui":
        bits = scalar_bits(dst)
        return lambda env: wrap(to_int32_from_float(a(env), signed=False), bits)
    if op in ("fpext", "fptrunc"):
        return lambda env: F32(a(env))
    raise LoweringError(f"Unsupported cast {op}")


# ------------------------------------------------------------------------------------ inputs


class Inputs:
    """Deterministic thread inputs: a pure function of (seed, key) and the run's profile.

    profile: activations "random" | "extreme" (each byte -128 or 127) | "high" (all 0x7f) |
    "low" (all 0x80); model "real" | "perturbed" (a few initializer words changed) | "random".
    """

    def __init__(self, seed, thread, lane, wave, profile, model=None):
        self.seed, self.thread, self.lane, self.wave = seed, thread, lane, wave
        self.profile, self.model = profile, model

    def word(self, *key):
        h = hashlib.blake2b(repr((self.seed,) + key).encode(), digest_size=8).digest()
        return int.from_bytes(h, "little")

    def activation(self, handle, address):
        mode = self.profile["activations"]
        h = self.word("mem", handle, address)
        if mode == "random":
            return wrap(h & 0xFFFFFFFF, 32)
        if mode == "high":
            return 0x7F7F7F7F
        if mode == "low":
            return wrap(0x80808080, 32)
        return wrap(sum((0x7F if (h >> k) & 1 else 0x80) << (8 * k) for k in range(4)), 32)

    def memory(self, handle, address):
        if handle[1:] == INITIALIZER and self.model is not None:
            mode = self.profile["model"]
            if mode != "random" and 0 <= address <= len(self.model) - 4 and address % 4 == 0:
                value = struct.unpack_from("<i", self.model, address)[0]
                if mode == "perturbed" and address // 4 in self.profile["perturbed_words"]:
                    value = wrap(value ^ (1 << (self.word("perturb", address) % 32)), 32)
                return value
        return self.activation(handle, address)

    def cbuffer(self, handle, row, component):
        return wrap(self.word("cb", handle, row, component) & 0xFFFFFFFF, 32)


class Run:
    """One thread's execution of a program: environment, memory, store log."""

    def __init__(self, program, inputs, stop_at=None, limit=20_000_000):
        self.p, self.inputs, self.stop_at, self.limit = program, inputs, stop_at, limit
        self.memory, self.stores, self.steps = {}, [], 0
        self.wave_results, self.initializer_reads, self.trace = [], set(), []

    def execute(self):
        try:
            self.call(self.p.entry)
        except Stop as stop:
            return stop.value
        return None

    def call(self, function):
        f = self.p.functions[function]
        blocks, env = f["blocks"], {}
        block, previous = f["order"][0], None
        while True:
            body = blocks[block]
            k = 0
            incoming = {}
            while k < len(body) and body[k].kind == 3:
                phi = body[k]
                require(previous in phi.fn, f"phi {phi.name} has no value for {previous}")
                incoming[phi.name] = phi.fn[previous](env)
                k += 1
            env.update(incoming)
            self.steps += len(body)
            if self.steps > self.limit:
                raise LoweringError("Step limit")
            target = None
            for instr in body[k:]:
                kind = instr.kind
                if kind == 0:
                    value = instr.fn(env)
                elif kind == 1:
                    value = self.special(instr, instr.fn, env)
                else:
                    target = self.terminator(instr.fn, env)
                    break
                if instr.name:
                    env[instr.name] = value
            if target is None:
                return
            previous, block = block, target

    def terminator(self, fn, env):
        if fn[0] == "br":
            return fn[1]
        if fn[0] == "cbr":
            return fn[2] if fn[1](env) else fn[3]
        if fn[0] == "switch":
            return fn[2].get(fn[1](env), fn[3])
        if fn[0] == "ret":
            return None
        raise LoweringError("Unsupported terminator")

    def special(self, instr, fn, env):
        if fn[0] == "call":
            self.call(fn[1])
            return None
        _, opcode, callee, ops, args = fn
        v = [o(env) for o in ops]
        thread = self.inputs.thread
        if opcode == 163:  # Dot4AddI8Packed
            a = [((v[1] >> (8 * k)) & 255 ^ 128) - 128 for k in range(4)]
            b = [((v[2] >> (8 * k)) & 255 ^ 128) - 128 for k in range(4)]
            return wrap(v[0] + sum(x * y for x, y in zip(a, b)), 32)
        if opcode == 93:  # ThreadId
            return thread["id"][v[0]]
        if opcode == 94:  # GroupId
            return thread["group"][v[0]]
        if opcode == 95:  # ThreadIdInGroup
            return thread["local"][v[0]]
        if opcode == 96:  # FlattenedThreadIdInGroup
            return thread["flat"]
        if opcode == 217:  # CreateHandleFromBinding
            lower, _, space, klass = v[0]
            return ("res", klass, space, v[1])
        if opcode == 216:  # AnnotateHandle
            return v[0]
        if opcode == 59:  # CBufferLoadLegacy
            return tuple(self.inputs.cbuffer(v[0], v[1], c) for c in range(4))
        if opcode == 139:  # RawBufferLoad
            handle, index = v[0], unsigned(v[1], 32)
            words = [self.load(handle, index + 4 * c) for c in range(4)]
            return tuple(words) + (1,)
        if opcode == 140:  # RawBufferStore
            handle, index, values, mask = v[0], v[1], v[3:7], v[7]
            written = []
            for c in range(4):
                if mask >> c & 1:
                    self.memory[(handle, unsigned(index + 4 * c, 32))] = values[c]
                    written.append(values[c])
            self.stores.append((handle, index, mask, tuple(written)))
            return None
        if opcode == 49 or opcode == 48:  # UMad / IMad
            return wrap(v[0] * v[1] + v[2], 32)
        if opcode == 46:  # FMad (unfused model)
            return F32(F32(v[0] * v[1]) + v[2])
        if opcode in (37, 38, 39, 40):  # IMax IMin UMax UMin
            a, b = v
            if opcode >= 39:
                a, b = unsigned(a, 32), unsigned(b, 32)
                return wrap(max(a, b) if opcode == 39 else min(a, b), 32)
            return max(a, b) if opcode == 37 else min(a, b)
        if opcode in (35, 36):  # FMax FMin
            a, b = v
            if math.isnan(a):
                return b
            if math.isnan(b):
                return a
            return F32(max(a, b) if opcode == 35 else min(a, b))
        if opcode == 26:  # Round_ne: its operand is a scaled accumulator, observed bit for bit
            self.trace.append(("round", f32_bits(v[0])))
            return f32_round(v[0], "ne")
        if opcode == 27:
            return f32_round(v[0], "ni")
        if opcode == 28:
            return f32_round(v[0], "pi")
        if opcode == 29:
            return f32_round(v[0], "z")
        if opcode == 7:  # Saturate
            return 0.0 if math.isnan(v[0]) else min(max(v[0], 0.0), 1.0)
        if opcode == 6:
            return F32(abs(v[0]))
        if opcode == 220:  # Pack4x8: 0 trunc, 1 unsigned clamp, 2 signed clamp
            mode, xs = v[0], v[1:5]
            self.trace.append(("pack", mode, tuple(xs)))
            if mode == 1:
                xs = [min(max(x, 0), 255) for x in xs]
            elif mode == 2:
                xs = [min(max(x, -128), 127) for x in xs]
            return wrap(sum((x & 255) << (8 * k) for k, x in enumerate(xs)), 32)
        if opcode == 219:  # Unpack4x8: 0 unsigned, 1 signed
            mode, word = v[0], v[1]
            out = []
            for k in range(4):
                byte = (word >> (8 * k)) & 255
                out.append(byte - 256 if mode == 1 and byte >= 128 else byte)
            return tuple(out)
        if opcode == 111:  # WaveGetLaneIndex
            return self.inputs.lane
        if opcode == 112:  # WaveGetLaneCount
            return self.inputs.wave
        if opcode == 114:  # WaveAllTrue
            own = v[0]
            if self.stop_at == instr.uid:
                raise Stop(own)
            values = [own]
            for lane in range(self.inputs.wave):
                if lane == self.inputs.lane:
                    continue
                other = lane_inputs(self.inputs, lane)
                if other is None:
                    continue
                value = Run(self.p, other, stop_at=instr.uid).execute()
                if value is not None:
                    values.append(value)
            result = int(all(values))
            self.wave_results.append(result)
            return result
        raise LoweringError(f"Unsupported dx.op {callee} ({opcode})")

    def load(self, handle, address):
        if handle[1:] == INITIALIZER:
            self.initializer_reads.add(unsigned(address, 32))
        key = (handle, unsigned(address, 32))
        if key in self.memory:
            return self.memory[key]
        return self.inputs.memory(handle, wrap(key[1], 32) if key[1] >= 1 << 31 else key[1])


def thread_inputs(group, local, numthreads):
    gx, gy, gz = group
    nx, ny, nz = numthreads
    lx, ly, lz = local
    return dict(
        group=(gx, gy, gz),
        local=(lx, ly, lz),
        id=(gx * nx + lx, gy * ny + ly, gz * nz + lz),
        flat=lx + nx * (ly + ny * lz),
    )


def lane_inputs(inputs, lane):
    """The inputs of another lane of the same wave (same group, same oracle)."""
    t = inputs.thread
    flat = t["flat"] - inputs.lane + lane
    nx, ny, nz = t["numthreads"]
    if flat >= nx * ny * nz:
        return None
    local = (flat % nx, (flat // nx) % ny, flat // (nx * ny))
    thread = thread_inputs(t["group"], local, (nx, ny, nz))
    thread["numthreads"] = (nx, ny, nz)
    return Inputs(inputs.seed, thread, lane, inputs.wave, inputs.profile, inputs.model)


# ---------------------------------------------------------------------------------- the check


def grid_bounds(program):
    """(x, y) bounds from the entry's first `icmp ugt` of thread IDs 0 and 1, else None."""
    ids = {}
    for f in program.functions.values():
        for block in f["blocks"].values():
            for i in block:
                if (
                    i.op == "call"
                    and isinstance(i.fn, tuple)
                    and i.fn[0] == "dxop"
                    and i.fn[1] == 93
                ):
                    comp = int(typed(i.fn[4][1])[1])
                    ids[i.name] = comp
    bounds = {}
    for m in re.finditer(r"icmp ugt i32 (%[\w.$-]+), (\d+)", program.text):
        if m[1] in ids and ids[m[1]] not in bounds:
            bounds[ids[m[1]]] = int(m[2])
    return bounds.get(0), bounds.get(1)


def normalize(stores, discard):
    if not discard:
        return stores
    return [
        (h, "discard" if wrap(i, 32) in DISCARD_ADDRESSES else i, m, v) for h, i, m, v in stores
    ]


def plan_runs(trials, seed, bounds, numthreads, read_words, waves):
    """Deterministic run plans covering fast/fallback paths, edges and extreme activations."""
    import random

    rng = random.Random(seed)
    bx, by = bounds
    bx = bx if bx is not None else 1023
    by = by if by is not None else 1023
    plans = []
    profiles = [
        ("random", "real"),
        ("extreme", "real"),
        ("high", "real"),
        ("low", "real"),
        ("extreme", "perturbed"),
        ("random", "random"),
    ]
    for n in range(trials):
        activations, model = (
            profiles[n % len(profiles)] if n < 2 * len(profiles) else rng.choice(profiles)
        )
        kind = rng.random()
        if n % 7 == 6:
            x = rng.choice([bx, bx + 1, bx + rng.randint(2, 200)])
        elif kind < 0.1:
            x = rng.choice([0, 1, bx - 1])
        else:
            x = rng.randint(0, bx)
        y = rng.choice([0, by, by + 1]) if n % 11 == 10 else rng.randint(0, by)
        z = 0 if (n % 9 != 8) else rng.choice([1, 2, 7])
        nx = numthreads[0]
        group = (x // nx, y, z)
        local = (x % nx, 0, 0)
        perturbed = set()
        if model == "perturbed" and read_words:
            # Words the SDK program reads, so the change reaches a guard or a weight.
            perturbed = set(rng.sample(read_words, min(3, len(read_words))))
        wave = rng.choice(waves)
        plans.append(
            dict(
                seed=f"{seed}:{n}",
                group=group,
                local=local,
                wave=wave,
                profile=dict(activations=activations, model=model, perturbed_words=perturbed),
            )
        )
    return plans


def compare(programs, trials=48, seed=1, model=None, names=None):
    """Run every program on the same plans; programs[0] is the SDK program.

    Returns a record; raises LoweringError on the first difference.
    """
    names = names or [f"p{k}" for k in range(len(programs))]
    first = programs[0]
    waves = sorted({p.wave for p in programs if p.wave}) or [32, 64]
    require(
        len(waves) == 1 or all(p.wave is None for p in programs),
        "Programs require different wave sizes",
    )
    bounds = grid_bounds(first)
    read_words = []
    if model is not None:
        # One probe thread inside the grid with the real model: which initializer words are read.
        probe = thread_inputs((0, 0, 0), (0, 0, 0), first.numthreads)
        probe["numthreads"] = first.numthreads
        run = Run(
            first,
            Inputs(
                f"{seed}:probe",
                probe,
                0,
                waves[0],
                dict(activations="random", model="real", perturbed_words=set()),
                model,
            ),
        )
        run.execute()
        read_words = sorted(a // 4 for a in run.initializer_reads if a % 4 == 0)
    plans = plan_runs(trials, seed, bounds, first.numthreads, read_words, waves)
    stats = dict(
        runs=0,
        stores=0,
        discard_stores=0,
        fast_path_runs=0,
        fallback_runs=0,
        no_store_runs=0,
        profiles={},
        steps={n: 0 for n in names},
        grid_bounds=list(bounds),
        initializer_words_read=len(read_words),
    )
    for plan in plans:
        thread = thread_inputs(plan["group"], plan["local"], first.numthreads)
        thread["numthreads"] = first.numthreads
        lane = thread["flat"] % plan["wave"]
        results = []
        for p, name in zip(programs, names):
            run = Run(p, Inputs(plan["seed"], thread, lane, plan["wave"], plan["profile"], model))
            try:
                run.execute()
                outcome = ("ok", run.trace, run.stores)
            except Poison:
                # Undefined behaviour (e.g. a local array index out of range when z != 0):
                # programs must agree up to that point and both reach it.
                outcome = ("poison", run.trace, run.stores)
            stats["steps"][name] += run.steps
            results.append((outcome, run))
        reference = results[0][0]
        for (outcome, run), name in zip(results[1:], names[1:]):
            a = normalize(reference[-1], True)
            b = normalize(outcome[-1], True)
            if reference[-2] != outcome[-2]:
                raise LoweringError(
                    f"{name} differs from {names[0]} in run {plan['seed']}: requantization inputs "
                    f"{describe(reference[-2], outcome[-2], 'value')}"
                )
            if reference[0] != outcome[0] or a != b:
                raise LoweringError(
                    f"{name} differs from {names[0]} in run {plan['seed']} "
                    f"(group {plan['group']}, lane {plan['local']}, {plan['profile']['activations']}/"
                    f"{plan['profile']['model']}): {describe(a, b)}"
                )
        # Release and new programs (both after masked_stores) compare exactly as well.
        tail = [r[0] for r in results[1:]]
        for k in range(1, len(tail)):
            require(
                tail[k] == tail[0],
                f"{names[k + 1]} differs from {names[1]} exactly in run {plan['seed']}",
            )
        stats["runs"] += 1
        stats["undefined_runs"] = stats.get("undefined_runs", 0) + (reference[0] == "poison")
        stats["observed_accumulators"] = stats.get("observed_accumulators", 0) + len(reference[-2])
        stores = reference[-1]
        stats["stores"] += len(stores)
        stats["discard_stores"] += sum(wrap(i, 32) in DISCARD_ADDRESSES for _, i, _, _ in stores)
        stats["no_store_runs"] += not stores
        key = f"{plan['profile']['activations']}/{plan['profile']['model']}"
        stats["profiles"][key] = stats["profiles"].get(key, 0) + 1
        guarded = [r[1].wave_results for r in results[1:] if r[1].wave_results]
        if guarded:
            if all(g and g[0] == 1 for g in guarded):
                stats["fast_path_runs"] += 1
            else:
                stats["fallback_runs"] += 1
    return stats


def describe(a, b, what="store"):
    if len(a) != len(b):
        return f"{len(a)} vs {len(b)} {what}s"
    for k, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"{what} {k}: {x} vs {y}"
    return "outcome differs"


# ------------------------------------------------------------------------------------- inputs


def sdk_programs(sdk, dxc, offsets):
    """Gate the SDK DLL, disassemble the given slots; also return the model initializer bytes."""
    import tempfile

    if __package__:
        from . import drop_profile, lower_pass
    else:
        import drop_profile
        import lower_pass
    data = Path(sdk).read_bytes()
    _, profile = drop_profile.select(hashlib.sha256(data).hexdigest())
    drop_profile.verify(data, profile)
    slots = {s["offset"]: s for s in profile["shaders"]["slots"]}
    manifest = json.loads((lower_pass.DLL / "manifest.json").read_text())
    model = data[MODEL["offset"] : MODEL["offset"] + MODEL["bytes"]]
    require(hashlib.sha256(model).hexdigest() == MODEL["sha256"], "Model initializer differs")
    out = {}
    with tempfile.TemporaryDirectory() as work:
        tc = lower_pass.Toolchain(dxc, work, manifest["dxcompiler_sha256"])
        for offset in offsets:
            s = slots[offset]
            blob = data[s["offset"] : s["offset"] + s["bytes"]]
            require(hashlib.sha256(blob).hexdigest() == s["sha256"], "SDK program differs")
            out[offset] = rename_ssa(tc.disassemble(blob, f"{offset:08x}"))
    return out, model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--dxc", type=Path, required=True)
    parser.add_argument("--slot", required=True, help="SDK file offset, e.g. 0xbd2c80")
    parser.add_argument(
        "--program",
        type=Path,
        action="append",
        required=True,
        help="program text to compare (release first, then new)",
    )
    parser.add_argument("--trials", type=int, default=48)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    offset = int(args.slot, 16)
    sources, model = sdk_programs(args.sdk, args.dxc, [offset])
    texts = [sources[offset]] + [p.read_text() for p in args.program]
    programs = [Program(t) for t in texts]
    names = ["sdk"] + [p.name for p in args.program]
    try:
        stats = compare(programs, args.trials, args.seed, model, names)
    except LoweringError as error:
        print("DIFFERS: " + str(error), file=sys.stderr)
        return 1
    record = dict(
        slot=args.slot,
        trials=args.trials,
        seed=args.seed,
        sdk_ir_sha256=hashlib.sha256(texts[0].encode()).hexdigest(),
        programs={n: hashlib.sha256(t.encode()).hexdigest() for n, t in zip(names[1:], texts[1:])},
        **stats,
    )
    if args.json:
        args.json.write_text(json.dumps(record, indent=1, default=list) + "\n")
    print(json.dumps(record, indent=1, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
