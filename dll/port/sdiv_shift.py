#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Signed divisions by a power of two as logical shifts, only where the dividend is proven >= 0.

For x >= 0, `sdiv i32 x, 2**k` and `lshr i32 x, k` both give floor(x / 2**k). For x < 0 they never
agree: sdiv rounds toward zero (x = -1 gives 0) and lshr reads the sign bit as 2**31 (x = -1 gives
0x7fffffff). The stage rewrites a division only when an interval analysis over the function's SSA
values proves its dividend non-negative; every other division stays as the SDK compiled it.

The analysis is path-insensitive: each integer value gets one signed interval that holds on every
path. Literals are exact; each operation follows its integer semantics, and a result that could
wrap becomes the full range of its type (nuw/nsw/exact are not trusted: DXIL consumers drop them
and the GPU wraps). Phis are solved to a fixpoint with widening. Values from outside the program
get the facts named below and nothing else:

  numthreads  ThreadIdInGroup / FlattenedThreadIdInGroup lie below the entry's numthreads (DXIL)
  dispatch    GroupId < 65535 and ThreadId < 65535 * numthreads per dimension
              (D3D12_CS_DISPATCH_MAX_THREAD_GROUPS_PER_DIMENSION)
  assume:KEY  a range the caller supplies for a named constant-buffer field such as b1[4].x;
              none by default, and `apply` uses only the facts it is given

Constant-buffer, buffer and texture reads, float conversions, loads and undef can hold any value
of their type. `interpret` executes the same IR subset on concrete values; the tests use it to
check that every value computed lies inside its interval and that a rewritten program computes
the same values as the original on random and extreme inputs.
"""

import argparse
import collections
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

if __package__:
    from .ir import LoweringError, numthreads, require
else:
    from ir import LoweringError, numthreads, require

NAME = r"%[A-Za-z0-9_.$-]+"
LABEL = r"[A-Za-z0-9_.$-]+"
INT_TYPE = re.compile(r"i(\d+)")
FLAGS = r"((?: nuw| nsw| exact)*)"
BIN = re.compile(
    r"(add|sub|mul|and|or|xor|shl|lshr|ashr|udiv|sdiv|urem|srem)" + FLAGS + r" i(\d+) ([^,]+), (.+)"
)
ICMP = re.compile(r"icmp (eq|ne|ugt|uge|ult|ule|sgt|sge|slt|sle) i(\d+) ([^,]+), (.+)")
SELECT = re.compile(r"select i1 ([^,]+), i(\d+) ([^,]+), i\d+ (.+)")
CAST = re.compile(r"(zext|sext|trunc) i(\d+) (\S+) to i(\d+)")
PHI = re.compile(r"phi i(\d+) (.+)")
PHI_PAIR = re.compile(r"\[ ([^,]+), %(" + LABEL + r") \]")
EXTRACT = re.compile(r"extractvalue (%[A-Za-z0-9_.\"<>:, ]+?|\{[^}]*\}) (" + NAME + r"), (\d+)")
DXOP = re.compile(r"call (\S+) @dx\.op\.([A-Za-z0-9]+)\.?[A-Za-z0-9.]*\(i32 (\d+)(.*)\)")
OPAQUE = (
    re.compile(r"(?:fptosi|fptoui|bitcast|ptrtoint) .* to i(\d+)"),
    re.compile(r"load i(\d+), "),
    re.compile(r"atomicrmw \w+ i(\d+)[ *]"),
    re.compile(r"call i(\d+) "),
)
SDIV = re.compile(r"^  (" + NAME + r") = sdiv( exact)? i32 (" + NAME + r"), (\d+)$")
METADATA = re.compile(r"(?:, ![A-Za-z][A-Za-z0-9_.]* !\d+)+$")
TYPE_DEF = re.compile(r"^(%[A-Za-z0-9_.\"<>:, ]+?) = type \{ (.*) \}$", re.M)
CBUFFER_LOAD = re.compile(
    r"call %dx\.types\.CBufRet\.\w+ @dx\.op\.cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle ("
    + NAME
    + r"), i32 (\d+)\)"
)
BINDING = re.compile(
    r"call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\(i32 217, %dx\.types\.ResBind "
    r"\{ i32 (\d+), i32 (\d+), i32 (\d+), i8 (\d+) \}, i32 (\d+), i1 false\)"
)
OLD_HANDLE = re.compile(
    r"call %dx\.types\.Handle @dx\.op\.createHandle\(i32 57, i8 (\d+), i32 \d+, i32 (\d+), i1 false\)"
)
ANNOTATE = re.compile(
    r"call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle (" + NAME + r"),"
)
# DXIL opcodes (DXIL.rst operation table) with the function family each must be called through.
SOURCES = {93: "threadId", 94: "groupId", 95: "threadIdInGroup", 96: "flattenedThreadIdInGroup"}
MINMAX = {37: "IMax", 38: "IMin", 39: "UMax", 40: "UMin"}
BITS = {31: "Countbits", 32: "FirstbitLo", 33: "FirstbitHi", 34: "FirstbitSHi"}
MAX_GROUPS = 65535
WIDEN_AFTER = 3
COMPONENTS = "xyzw"


class Poison(Exception):
    """The concrete run reached LLVM poison or undefined behaviour (division by zero, ...)."""


def bounds(width):
    """Signed range of iN; i1 is held as 0/1."""
    if width == 1:
        return 0, 1
    return -(1 << (width - 1)), (1 << (width - 1)) - 1


def wrap(value, width):
    """Two's-complement value of iN (i1 as 0/1)."""
    value &= (1 << width) - 1
    if width > 1 and value >> (width - 1):
        value -= 1 << width
    return value


def unsigned(value, width):
    return value & ((1 << width) - 1)


def fit(lo, hi, width):
    """[lo, hi] if it lies inside iN, else the whole type (the result may have wrapped)."""
    tlo, thi = bounds(width)
    return (lo, hi) if tlo <= lo <= hi <= thi else (tlo, thi)


def tdiv(a, b):
    """Integer division rounding toward zero (LLVM sdiv)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def literal(token, width):
    token = token.strip()
    if token in ("true", "false"):
        return int(token == "true")
    if re.fullmatch(r"-?\d+", token):
        return wrap(int(token), width)
    return None


# ----------------------------------------------------------------------------------- parsing


Instr = collections.namedtuple("Instr", "name kind width args extra line")


class Function:
    """The single DXIL entry function: blocks in layout order and decoded instructions."""

    def __init__(self, text):
        self.text = text
        self.types = {
            name: [field.strip() for field in body.split(",")]
            for name, body in TYPE_DEF.findall(text)
        }
        lines = text.splitlines()
        starts = [n for n, line in enumerate(lines) if re.match(r"define \S+ @\S+\(.*\{$", line)]
        require(len(starts) == 1, "Expected exactly one function body")
        self.blocks = collections.OrderedDict()
        self.defs = {}
        self.handles, self.cbuffers = {}, {}
        block = None
        for n in range(starts[0] + 1, len(lines)):
            line = lines[n]
            if line == "}":
                break
            label = re.match(r"^; <label>:(\d+)", line) or re.match(r"^(" + LABEL + r"):", line)
            if label:
                block = label[1]
                self.blocks[block] = []
                continue
            code = METADATA.sub("", line.split(";")[0].rstrip())
            if not code.strip():
                continue
            if block is None:
                block = "0"
                self.blocks[block] = []
            instr = self.decode(code, n)
            self.blocks[block].append(instr)
            if instr.name:
                self.defs[instr.name] = (block, instr)
        try:
            self.numthreads = numthreads(text)
        except LoweringError:
            self.numthreads = None

    def decode(self, code, n):
        m = re.match(r"^  (" + NAME + r") = (.*)$", code)
        if not m:
            return Instr(None, "stmt", None, (code.strip(),), None, n)
        name, rhs = m[1], m[2].strip()
        if b := BIN.fullmatch(rhs):
            op, flags, width, a, b2 = b.groups()
            return Instr(name, "bin", int(width), (a, b2), (op, flags.split()), n)
        if c := ICMP.fullmatch(rhs):
            return Instr(name, "icmp", 1, (c[3], c[4]), (c[1], int(c[2])), n)
        if s := SELECT.fullmatch(rhs):
            return Instr(name, "select", int(s[2]), (s[1], s[3], s[4]), None, n)
        if c := CAST.fullmatch(rhs):
            return Instr(name, "cast", int(c[4]), (c[3],), (c[1], int(c[2])), n)
        if p := PHI.fullmatch(rhs):
            pairs = PHI_PAIR.findall(p[2])
            require(pairs, "Unparsed phi: " + rhs)
            return Instr(name, "phi", int(p[1]), tuple(pairs), None, n)
        if e := EXTRACT.fullmatch(rhs):
            fields = self.types.get(e[1]) or [f.strip() for f in e[1].strip("{} ").split(",")]
            index = int(e[3])
            ftype = fields[index] if index < len(fields) else ""
            if t := INT_TYPE.fullmatch(ftype):
                return Instr(
                    name, "extract", int(t[1]), (e[2],), (index, self.source(e[2], index)), n
                )
            return Instr(name, "other", None, (), None, n)
        if d := DXOP.fullmatch(rhs):
            rtype, family, opcode, rest = d[1], d[2], int(d[3]), d[4]
            raw = [a.strip().split(" ", 1) for a in rest.split(",")[1:] if a.strip()]
            args = tuple(value for _, value in raw)
            if h := BINDING.fullmatch(rhs):
                if h[1] == h[2]:
                    self.handles[name] = (int(h[4]), int(h[1]), int(h[3]))
            elif h := OLD_HANDLE.fullmatch(rhs):
                self.handles[name] = (int(h[1]), int(h[2]), 0)
            elif h := ANNOTATE.match(rhs):
                if h[1] in self.handles:
                    self.handles[name] = self.handles[h[1]]
            elif h := CBUFFER_LOAD.fullmatch(rhs):
                self.cbuffers[name] = (h[1], int(h[2]))
            width = INT_TYPE.fullmatch(rtype)
            if width:
                width = int(width[1])
                if opcode in SOURCES and family == SOURCES[opcode]:
                    return Instr(name, "source", width, args, (family,), n)
                if opcode in MINMAX and family == "binary":
                    return Instr(name, "minmax", width, args, (MINMAX[opcode],), n)
                if opcode in BITS and family == "unaryBits" and INT_TYPE.fullmatch(raw[0][0]):
                    source = int(INT_TYPE.fullmatch(raw[0][0])[1])
                    return Instr(name, "bits", width, args, (BITS[opcode], source), n)
                return Instr(name, "opaque", width, (), ("dx.op." + family,), n)
            return Instr(name, "other", None, (), None, n)
        for pattern in OPAQUE:
            if o := pattern.match(rhs):
                return Instr(name, "opaque", int(o[1]), (), (rhs.split(" ", 1)[0],), n)
        return Instr(name, "other", None, (), None, n)

    def source(self, aggregate, index):
        """Name of a constant-buffer field read through extractvalue, e.g. b1[4].x."""
        if aggregate not in self.cbuffers:
            return None
        handle, row = self.cbuffers[aggregate]
        cls, register, space = self.handles.get(handle, (None, None, None))
        if cls != 2:
            return None
        where = f"b{register}" + (f",space{space}" if space else "")
        return f"{where}[{row}].{COMPONENTS[index]}" if index < 4 else None

    def instructions(self):
        for block, body in self.blocks.items():
            for instr in body:
                yield block, instr


# ---------------------------------------------------------------------------------- analysis


Range = collections.namedtuple("Range", "lo hi facts rule")


class Analysis:
    """Signed interval of every integer SSA value of `Function`, with the facts each one uses."""

    def __init__(self, function, assume=None, dispatch=True, solve=True):
        self.f = function
        self.assume = dict(assume or {})
        self.dispatch = dispatch
        self.ranges = {}
        if solve:
            self.solve()

    def operand(self, token, width):
        """Range of an operand; None while a value is still unknown (bottom)."""
        value = literal(token, width)
        if value is not None:
            return Range(value, value, frozenset(), "literal")
        token = token.strip()
        if token in self.ranges:
            return self.ranges[token]
        if token.startswith("%") and token in self.f.defs:
            instr = self.f.defs[token][1]
            if instr.kind in ("other", "stmt"):
                return Range(*bounds(width), frozenset(), "untracked")
            return None
        return Range(*bounds(width), frozenset(), token if token == "undef" else "constant")

    def transfer(self, instr):
        w = instr.width
        full = bounds(w)
        if instr.kind == "opaque":
            return Range(*full, frozenset(), instr.extra[0] + ": any value")
        if instr.kind == "extract":
            key = instr.extra[1]
            if key in self.assume:
                lo, hi = self.assume[key]
                return Range(lo, hi, frozenset({"assume:" + key}), f"{key}: assumed [{lo}, {hi}]")
            what = f"constant-buffer field {key}" if key else "extractvalue of a call result"
            return Range(*full, frozenset(), what + ": any value")
        if instr.kind == "source":
            family = instr.extra[0]
            comp = literal(instr.args[0], 32) if instr.args else None
            nt = self.f.numthreads
            if family == "threadIdInGroup" and nt and comp in (0, 1, 2):
                return Range(0, nt[comp] - 1, frozenset({"numthreads"}), f"{family}.{comp}")
            if family == "flattenedThreadIdInGroup" and nt:
                return Range(0, nt[0] * nt[1] * nt[2] - 1, frozenset({"numthreads"}), family)
            if self.dispatch and family == "groupId" and comp in (0, 1, 2):
                return Range(0, MAX_GROUPS - 1, frozenset({"dispatch"}), f"{family}.{comp}")
            if self.dispatch and family == "threadId" and nt and comp in (0, 1, 2):
                facts = frozenset({"dispatch", "numthreads"})
                return Range(0, MAX_GROUPS * nt[comp] - 1, facts, f"{family}.{comp}")
            return Range(*full, frozenset(), family + ": any value")
        ops = [self.operand(a, w) for a in instr.args] if instr.kind != "phi" else []
        if instr.kind == "icmp":
            return Range(0, 1, frozenset(), "icmp")
        if instr.kind == "cast":
            op, src = instr.extra
            a = self.operand(instr.args[0], src)
            if a is None:
                return None
            if op == "trunc":
                return Range(*fit(a.lo, a.hi, w), a.facts, "trunc")
            if op == "zext":
                if src == 1 or a.lo >= 0:
                    return Range(a.lo, a.hi, a.facts, "zext")
                return Range(0, (1 << src) - 1, a.facts, "zext")
            if src == 1:
                return Range(-a.hi, -a.lo, a.facts, "sext")
            return Range(a.lo, a.hi, a.facts, "sext")
        if instr.kind == "phi":
            got = [self.operand(v, w) for v, _ in instr.args]
            got = [g for g in got if g is not None]
            if not got:
                return None
            facts = frozenset().union(*(g.facts for g in got))
            return Range(min(g.lo for g in got), max(g.hi for g in got), facts, "phi")
        if any(o is None for o in ops):
            return None
        facts = frozenset().union(*(o.facts for o in ops))
        if instr.kind == "select":
            _, a, b = ops
            return Range(min(a.lo, b.lo), max(a.hi, b.hi), facts, "select")
        if instr.kind == "minmax":
            a, b = ops
            name = instr.extra[0]
            if name.startswith("U") and (a.lo < 0 or b.lo < 0):
                return Range(*full, facts, name)
            pick = max if name.endswith("Max") else min
            return Range(pick(a.lo, b.lo), pick(a.hi, b.hi), facts, name)
        if instr.kind == "bits":
            name, source = instr.extra
            lo = 0 if name == "Countbits" else -1
            hi = source if name == "Countbits" else source - 1
            return Range(lo, hi, facts, name)
        if instr.kind == "bin":
            return self.binary(instr.extra[0], ops[0], ops[1], w, facts)
        return Range(*full, facts, instr.kind)

    def binary(self, op, a, b, w, facts):
        full = bounds(w)
        if w == 1:
            return Range(0, 1, facts, op)
        const = b.lo if b.lo == b.hi else None
        lo = hi = None
        if op == "add":
            lo, hi = a.lo + b.lo, a.hi + b.hi
        elif op == "sub":
            lo, hi = a.lo - b.hi, a.hi - b.lo
        elif op == "mul":
            products = [x * y for x in (a.lo, a.hi) for y in (b.lo, b.hi)]
            lo, hi = min(products), max(products)
        elif op == "and":
            if a.lo >= 0 and b.lo >= 0:
                lo, hi = 0, min(a.hi, b.hi)
            elif a.lo >= 0 or b.lo >= 0:
                lo, hi = 0, a.hi if a.lo >= 0 else b.hi
        elif op in ("or", "xor"):
            if a.lo >= 0 and b.lo >= 0:
                top = (1 << max(a.hi, b.hi).bit_length()) - 1
                lo, hi = (max(a.lo, b.lo) if op == "or" else 0), top
        elif op in ("shl", "lshr", "ashr"):
            if 0 <= b.lo <= b.hi < w and b.hi - b.lo <= 64:
                results = [self.shift(op, a, k, w) for k in range(b.lo, b.hi + 1)]
                lo, hi = min(r[0] for r in results), max(r[1] for r in results)
        elif op == "udiv":
            if const is not None and const > 0:
                if a.lo >= 0:
                    lo, hi = a.lo // const, a.hi // const
                else:
                    lo, hi = 0, ((1 << w) - 1) // const
        elif op == "sdiv":
            if const is not None and const > 0:
                lo, hi = tdiv(a.lo, const), tdiv(a.hi, const)
        elif op == "urem":
            if const is not None and const > 0:
                lo, hi = 0, (min(a.hi, const - 1) if a.lo >= 0 else const - 1)
        elif op == "srem":
            if const is not None and const != 0:
                m = abs(const) - 1
                lo, hi = (0, min(a.hi, m)) if a.lo >= 0 else (-m, m)
        if lo is None:
            return Range(*full, facts, op)
        return Range(*fit(lo, hi, w), facts, op)

    @staticmethod
    def shift(op, a, k, w):
        if op == "shl":
            return fit(a.lo << k, a.hi << k, w)
        if op == "ashr":
            return a.lo >> k, a.hi >> k
        if a.lo >= 0:
            return a.lo >> k, a.hi >> k
        return (0, ((1 << w) - 1) >> k) if k else bounds(w)

    def solve(self):
        changes = collections.Counter()
        tracked = [(b, i) for b, i in self.f.instructions() if i.name and i.width]
        for _ in range(1000):
            changed = False
            for _, instr in tracked:
                new = self.transfer(instr)
                if new is None:
                    continue
                old = self.ranges.get(instr.name)
                if old is not None:
                    lo, hi = min(old.lo, new.lo), max(old.hi, new.hi)
                    if (lo, hi) == (old.lo, old.hi):
                        continue
                    changes[instr.name] += 1
                    if changes[instr.name] > WIDEN_AFTER:
                        tlo, thi = bounds(instr.width)
                        lo = tlo if lo < old.lo else lo
                        hi = thi if hi > old.hi else hi
                    new = Range(lo, hi, old.facts | new.facts, new.rule)
                self.ranges[instr.name] = new
                changed = True
            if not changed:
                return
        raise LoweringError("Interval analysis did not converge")

    def derivation(self, name, depth=6):
        """Short text of how a value's interval follows from its operands, down to sources."""
        out, seen = [], set()

        def visit(value, level):
            if value in seen or value not in self.f.defs or level > depth:
                return
            seen.add(value)
            _, instr = self.f.defs[value]
            r = self.ranges.get(value)
            span = f"[{r.lo}, {r.hi}]" if r else "unreached"
            text = self.f.text.splitlines()[instr.line].split(";")[0].strip()
            out.append(f"{'  ' * level}{text}  => {span} ({r.rule if r else '-'})")
            if instr.kind not in ("extract", "source", "opaque"):
                for arg in instr.args:
                    arg = arg[0] if isinstance(arg, tuple) else arg
                    visit(arg.strip(), level + 1)

        visit(name, 0)
        return out


# ---------------------------------------------------------------------------------- the stage


def divisions(function, analysis):
    """Every `sdiv i32 x, 2**k` (k >= 1) with its dividend's interval and verdict."""
    records = []
    for block, instr in function.instructions():
        if instr.kind != "bin" or instr.extra[0] != "sdiv":
            continue
        line = function.text.splitlines()[instr.line]
        m = SDIV.match(line.split(";")[0].rstrip())
        divisor = literal(instr.args[1], instr.width)
        power = divisor is not None and divisor >= 2 and divisor & (divisor - 1) == 0
        record = dict(
            line=instr.line + 1,
            value=instr.name,
            block=block,
            divisor=divisor,
            form="sdiv i32 x, 2**k" if (m and power) else "other",
        )
        if not (m and power):
            record.update(proven=False, reason="not a scalar i32 division by a literal 2**k")
            records.append(record)
            continue
        dividend = m[3]
        r = analysis.operand(dividend, 32)
        record.update(
            dividend=dividend,
            shift=divisor.bit_length() - 1,
            exact=bool(m[2]),
            interval=[r.lo, r.hi] if r else None,
            facts=sorted(r.facts) if r else [],
            proven=bool(r and r.lo >= 0),
            derivation=analysis.derivation(dividend),
        )
        if r is None:
            record["reason"] = "dividend never computed (unreachable)"
        elif r.lo < 0:
            witness = -1 if r.lo <= -1 <= r.hi else r.hi
            record["reason"] = f"dividend may be negative: interval [{r.lo}, {r.hi}]"
            record["witness"] = dict(
                x=witness,
                sdiv=tdiv(witness, divisor),
                lshr=wrap(unsigned(witness, 32) >> (divisor.bit_length() - 1), 32),
            )
        else:
            record["reason"] = f"dividend in [{r.lo}, {r.hi}]"
        records.append(record)
    return records


def analyze(text, assume=None, dispatch=True):
    function = Function(text)
    analysis = Analysis(function, assume, dispatch)
    return function, analysis, divisions(function, analysis)


def apply(text, assume=None, dispatch=True):
    """Rewrite each proven `sdiv i32 x, 2**k` to `lshr i32 x, k`; return (text, records)."""
    _, _, records = analyze(text, assume, dispatch)
    lines = text.splitlines()
    for record in records:
        record["applied"] = record["proven"]
        if not record["proven"]:
            continue
        n = record["line"] - 1
        exact = " exact" if record["exact"] else ""
        new = f"  {record['value']} = lshr{exact} i32 {record['dividend']}, {record['shift']}"
        comment = lines[n][len(lines[n].split(";")[0]) :]
        lines[n] = new + comment
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), records


# ------------------------------------------------------------------------------- interpreter


class Oracle:
    """Deterministic inputs keyed by (name, visit): extremes first, then seeded random values."""

    def __init__(self, seed, overrides=None, extremes=0.35):
        self.seed, self.overrides, self.extremes = seed, dict(overrides or {}), extremes
        self.visits = collections.Counter()

    def __call__(self, key, lo, hi):
        if key in self.overrides:
            return self.overrides[key]
        visit = self.visits[key]
        self.visits[key] += 1
        h = zlib.crc32(f"{self.seed}|{key}|{visit}".encode())
        pick = (h & 0xFFFF) / 0x10000
        r = (h >> 16) | (zlib.crc32(f"{key}|{visit}|{self.seed}".encode()) << 16)
        if pick < self.extremes:
            choices = sorted(
                {
                    v
                    for v in (lo, hi, 0, 1, -1, 2, lo + 1, hi - 1, -2, 1 << 30, -(1 << 30))
                    if lo <= v <= hi
                }
            )
            return choices[r % len(choices)]
        return lo + r % (hi - lo + 1)


def interpret(text, oracle, assume=None, dispatch=True, limit=200000):
    """Run the function's integer slice once; return [(name, value)] in execution order + path.

    Values the IR does not compute come from `oracle(key, lo, hi)` within exactly the facts the
    analysis uses; branches on untracked conditions are chosen by the oracle as well.
    """
    f = Function(text)
    env, trace, path = {}, [], []
    probe = Analysis(f, assume, dispatch, solve=False)

    def val(token, width, key):
        lit = literal(token, width)
        if lit is not None:
            return lit
        token = token.strip()
        if token in env:
            return env[token]
        return oracle(key, *bounds(width))

    block, previous, steps = next(iter(f.blocks)), None, 0
    while True:
        path.append(block)
        body = f.blocks[block]
        phis = [i for i in body if i.kind == "phi"]
        incoming = {}
        for i in phis:
            pairs = dict((b, v) for v, b in i.args)
            require(previous in pairs, f"phi {i.name} has no value for {previous}")
            incoming[i.name] = val(pairs[previous], i.width, i.name + "#phi")
        env.update(incoming)
        trace.extend((n, v) for n, v in incoming.items())
        target = None
        for i in body:
            steps += 1
            if steps > limit:
                return trace, path
            if i.kind == "phi":
                continue
            if i.kind == "stmt":
                stmt = i.args[0]
                if stmt.startswith("ret"):
                    return trace, path
                if b := re.fullmatch(
                    r"br i1 (\S+), label %(" + LABEL + r"), label %(" + LABEL + r")", stmt
                ):
                    c = val(b[1], 1, "br:" + block)
                    target = b[2] if c else b[3]
                elif b := re.fullmatch(r"br label %(" + LABEL + r")", stmt):
                    target = b[1]
                elif stmt.startswith(("switch", "indirectbr", "unreachable")):
                    raise LoweringError("Unsupported terminator: " + stmt)
                continue
            if not i.width:
                continue
            value = execute(i, f, probe, val, oracle)
            env[i.name] = value
            trace.append((i.name, value))
        require(target is not None, f"Block {block} has no terminator")
        previous, block = block, target


def execute(i, f, probe, val, oracle):
    w = i.width
    if i.kind in ("opaque", "extract", "source"):
        r = probe.transfer(i)
        return oracle(i.name, r.lo, r.hi)
    if i.kind == "bin":
        op, flags = i.extra
        a, b = val(i.args[0], w, i.name + "#0"), val(i.args[1], w, i.name + "#1")
        return binop(op, flags, a, b, w)
    if i.kind == "icmp":
        pred, ow = i.extra
        a, b = val(i.args[0], ow, i.name + "#0"), val(i.args[1], ow, i.name + "#1")
        if pred[0] == "u":
            a, b = unsigned(a, ow), unsigned(b, ow)
        return int(
            {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[
                pred[-2:] if pred not in ("eq", "ne") else pred
            ]
        )
    if i.kind == "select":
        c = val(i.args[0], 1, i.name + "#c")
        return val(i.args[1] if c else i.args[2], w, i.name + ("#t" if c else "#f"))
    if i.kind == "cast":
        op, src = i.extra
        a = val(i.args[0], src, i.name + "#0")
        if op == "zext":
            return unsigned(a, src)
        if op == "sext":
            return -a if src == 1 else a
        return wrap(a, w)
    if i.kind == "minmax":
        a, b = val(i.args[0], w, i.name + "#0"), val(i.args[1], w, i.name + "#1")
        name = i.extra[0]
        if name.startswith("U"):
            a2, b2 = unsigned(a, w), unsigned(b, w)
            return wrap((max if name == "UMax" else min)(a2, b2), w)
        return (max if name == "IMax" else min)(a, b)
    if i.kind == "bits":
        name, sw = i.extra
        a = unsigned(val(i.args[0], sw, i.name + "#0"), sw)
        if name == "Countbits":
            return bin(a).count("1")
        if name == "FirstbitSHi":
            s = wrap(a, sw)
            a = unsigned(~s, sw) if s < 0 else a
        if a == 0:
            return -1
        if name == "FirstbitLo":
            return (a & -a).bit_length() - 1
        return sw - a.bit_length()
    raise LoweringError("Cannot execute " + i.kind)


def binop(op, flags, a, b, w):
    """One integer operation as the GPU executes it: wrapping, with nuw/nsw/exact not trusted.

    DXIL consumers translate these flags away, so the concrete run wraps like the analysis
    assumes. Division by zero, INT_MIN / -1 and shift amounts outside [0, w) are undefined.
    """
    ua, ub = unsigned(a, w), unsigned(b, w)
    if op in ("add", "sub", "mul"):
        return wrap({"add": ua + ub, "sub": ua - ub, "mul": ua * ub}[op], w)
    if op in ("and", "or", "xor"):
        return wrap({"and": ua & ub, "or": ua | ub, "xor": ua ^ ub}[op], w)
    if op in ("shl", "lshr", "ashr"):
        if not 0 <= ub < w:
            raise Poison("shift amount out of range")
        if op == "shl":
            return wrap(ua << ub, w)
        return wrap(ua >> ub, w) if op == "lshr" else wrap(a >> ub, w)
    if op in ("udiv", "urem"):
        if ub == 0:
            raise Poison("division by zero")
        return wrap(ua // ub if op == "udiv" else ua % ub, w)
    if b == 0 or (w > 1 and a == bounds(w)[0] and b == -1):
        raise Poison("sdiv/srem undefined")
    q = tdiv(a, b)
    return wrap(q if op == "sdiv" else a - q * b, w)


# -------------------------------------------------------------------------------------- CLI


def carve(data):
    """Every DXBC container in a DLL (layout from DXC's DxilContainer.h): (offset, bytes)."""
    out, at = [], data.find(b"DXBC")
    while at >= 0:
        size, count = struct.unpack_from("<II", data, at + 24)
        require(at + size <= len(data), f"Container at {at:#x} runs past the file")
        for off in struct.unpack_from(f"<{count}I", data, at + 32):
            _, psize = struct.unpack_from("<4sI", data, at + off)
            require(off + 8 + psize <= size, f"Part outside the container at {at:#x}")
        out.append((at, data[at : at + size]))
        at = data.find(b"DXBC", at + size)
    return out


def census(dll, dxc, entry=None, out_dir=None):
    """Gate the DLL, disassemble every container and report each power-of-two sdiv."""
    if __package__:
        from .sdk_intake import require_allowlisted
    else:
        from sdk_intake import require_allowlisted
    drops = require_allowlisted(dll)
    data = Path(dll).read_bytes()
    programs = []
    with tempfile.TemporaryDirectory() as tmp:
        for n, (offset, blob) in enumerate(carve(data)):
            path = Path(tmp) / "p.dxbc"
            path.write_bytes(blob)
            text = subprocess.run(
                [dxc, "-dumpbin", str(path)], check=True, capture_output=True, text=True
            ).stdout
            name = re.search(r"^define void @([^(]+)\(", text, re.M)[1]
            if entry and not re.fullmatch(entry, name):
                continue
            new, records = apply(text)
            if out_dir:
                Path(out_dir, f"{n:04d}.ll").write_text(text)
            programs.append(
                dict(
                    index=n,
                    offset=offset,
                    entry=name,
                    sha256=hashlib.sha256(blob).hexdigest(),
                    ir_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    out_ir_sha256=hashlib.sha256(new.encode()).hexdigest(),
                    sdivs=records,
                )
            )
    return dict(drop=drops[0]["tag"], dll=hashlib.sha256(data).hexdigest(), programs=programs)


def parse_assume(items):
    out = {}
    for item in items or []:
        key, span = item.split("=", 1)
        lo, hi = (int(v) for v in span.split(":"))
        require(lo <= hi, "Empty assumed range: " + item)
        out[key] = (lo, hi)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("census", help="gate a DLL, disassemble it and report power-of-two sdivs")
    c.add_argument("dll")
    c.add_argument("--dxc", required=True)
    c.add_argument("--entry", help="regular expression for the entry names to keep")
    c.add_argument("--ir-dir", help="also write each kept program's disassembly here")
    c.add_argument("--json", required=True)
    a = sub.add_parser("apply", help="rewrite proven divisions in one disassembled program")
    a.add_argument("input")
    a.add_argument("output")
    a.add_argument("--assume", action="append", metavar="KEY=LO:HI")
    a.add_argument("--json")
    args = parser.parse_args(argv)
    if args.command == "census":
        report = census(args.dll, args.dxc, args.entry, args.ir_dir)
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n")
        kept = [r for p in report["programs"] for r in p["sdivs"]]
        print(
            f"{len(report['programs'])} programs, {len(kept)} sdiv, "
            f"{sum(r['proven'] for r in kept)} proven non-negative"
        )
        return 0
    text = Path(args.input).read_text()
    new, records = apply(text, parse_assume(args.assume))
    Path(args.output).write_text(new)
    if args.json:
        Path(args.json).write_text(json.dumps(records, indent=1) + "\n")
    print(f"{sum(r['applied'] for r in records)} of {len(records)} sdiv rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
