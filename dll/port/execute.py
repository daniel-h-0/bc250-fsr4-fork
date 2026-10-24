# SPDX-License-Identifier: MIT
"""Execute DXIL compute threads of a textual IR program on a synthetic resource world.

A lowered program is proven against the SDK program by running both on the same threads over
the same world and requiring every side effect (texture and buffer stores, in order, bit for
bit) to agree. Resource contents are deterministic functions of (seed, resource, address), so
two programs that read the same address see the same value, and a value one program caches
must equal what the other reloads.

Semantics: integers wrap (nuw/nsw/exact are not trusted). Floats are held as Python floats that
are exactly f32 (or f16) values: each operation is computed in double and rounded once to f32
(exact for +, -, *, / of f32 operands), conversions round to nearest even. `FMad` (opcode 46)
is the exact a*b+c rounded once (`fused`) or rounded after the product too (`separate`); a
lowering that is exact only under one contraction fails the other mode. Shift amounts out of
range, integer division by zero and unknown instructions raise `Undefined` / `LoweringError`.
`undef` is zero of its type. Only single-thread programs are accepted (no group-shared memory,
barriers, wave or quad operations).
"""

import hashlib
import math
import re
import struct
from fractions import Fraction

if __package__:
    from .ir import LoweringError, require
else:
    from ir import LoweringError, require

LABEL = r"[A-Za-z0-9_.$-]+"
NAME = r"%[A-Za-z0-9_.$-]+"
METADATA = re.compile(r"(?:, ![A-Za-z][A-Za-z0-9_.]* !\d+)+$")
INT_BINARY = ("add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr")
INT_DIVISION = ("udiv", "sdiv", "urem", "srem")
FLOAT_BINARY = ("fadd", "fsub", "fmul", "fdiv")
CASTS = ("zext", "sext", "trunc", "sitofp", "uitofp", "fptosi", "fptoui", "fptrunc", "fpext")
FORBIDDEN = ("addrspace(3)", "@dx.op.barrier", "@dx.op.wave", "@dx.op.quad")
F32_MAX = struct.unpack("<f", b"\xff\xff\x7f\x7f")[0]


class Undefined(Exception):
    """The run reached undefined behaviour (e.g. a shift amount out of range)."""


# ----------------------------------------------------------------------------- number formats


def wrap(value, width):
    value &= (1 << width) - 1
    if width > 1 and value >> (width - 1):
        value -= 1 << width
    return value


def f32(x):
    """Round a double to the nearest f32 (ties to even); overflow gives infinity."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def f16(x):
    try:
        return struct.unpack("<e", struct.pack("<e", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def f32_bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f16_bits(x):
    return struct.unpack("<H", struct.pack("<e", x))[0]


def bits_f32(word):
    return struct.unpack("<f", struct.pack("<I", word & 0xFFFFFFFF))[0]


def bits_f16(word):
    return struct.unpack("<e", struct.pack("<H", word & 0xFFFF))[0]


ROUND = {"float": f32, "half": f16}


def _divide(a, b):
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _fmad(a, b, c, mode):
    if mode == "separate":
        return f32(f32(a * b) + c)
    if all(math.isfinite(v) for v in (a, b, c)):
        if all(v == int(v) for v in (a, b, c)):
            exact = int(a) * int(b) + int(c)
            return f32(float(exact)) if exact else f32(a * b + c)
        exact = Fraction(a) * Fraction(b) + Fraction(c)
        if exact == 0:
            return f32(a * b + c)
        return f32(float(exact))  # Fraction -> float rounds once to double, then to f32
    return f32(a * b + c)


def _fmax(a, b):
    if math.isnan(a):
        return b
    if math.isnan(b):
        return a
    return a if a >= b else b


def _fmin(a, b):
    if math.isnan(a):
        return b
    if math.isnan(b):
        return a
    return a if a <= b else b


def _exp2(x):
    try:
        return f32(math.pow(2.0, x))
    except OverflowError:
        return math.inf


def _log2(x):
    if math.isnan(x) or x < 0:
        return math.nan
    if x == 0:
        return -math.inf
    if math.isinf(x):
        return math.inf
    return f32(math.log2(x))


def _round_ne(x):
    if not math.isfinite(x):
        return x
    return math.copysign(float(round(x)), x)


def _to_int(x, width, signed):
    """GPU conversion float -> integer: truncate, saturate, NaN -> 0."""
    if math.isnan(x):
        return 0
    lo, hi = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
    if math.isinf(x):
        v = hi if x > 0 else lo
    else:
        v = max(lo, min(hi, int(x)))
    return wrap(v, width)


UNARY_FLOAT = {
    6: abs,
    7: lambda x: x if math.isnan(x) else min(max(x, 0.0), 1.0),
    21: _exp2,
    22: lambda x: x - math.floor(x) if math.isfinite(x) else math.nan,
    23: _log2,
    24: lambda x: math.nan if x < 0 or math.isnan(x) else math.sqrt(x),
    26: _round_ne,
    27: lambda x: float(math.floor(x)) if math.isfinite(x) else x,
    28: lambda x: float(math.ceil(x)) if math.isfinite(x) else x,
    29: lambda x: math.copysign(float(math.trunc(x)), x) if math.isfinite(x) else x,
}


# --------------------------------------------------------------------------------- the world


class World:
    """Deterministic resource contents and thread IDs for one proof run.

    `profile` picks the value distributions: raw-buffer words (`random`, `extreme` bytes
    -128/127, `min`, `max`, `mixed`), and float data with a share of special values (zeros,
    infinities, NaN, denormals, f32/f16 extremes). Constant-buffer fields read as i32 get
    small sizes with occasional extremes, the others floats; `fields` overrides any of them
    by key "b<register>[row].<xyzw>".
    """

    SPECIAL = (
        0.0,
        -0.0,
        1.0,
        -1.0,
        0.5,
        math.inf,
        -math.inf,
        math.nan,
        F32_MAX,
        -F32_MAX,
        1e-40,
        -1e-40,
        1e-30,
        65504.0,
        3.0e9,
    )
    INT_EXTREMES = (0, -1, -2, 1, (1 << 31) - 1, -(1 << 31), 65535, 1 << 20)
    SCALES = (0.5, 1.0, 0.75, 0.25, 2.0, 0.0, 1.5, 0.6666667)

    def __init__(self, seed, profile="random", cbuffer_kinds=None, fields=None, special=0.08):
        self.seed, self.profile, self.special = seed, profile, special
        self.kinds = dict(cbuffer_kinds or {})
        self.fields = dict(fields or {})

    def _h(self, *key):
        digest = hashlib.blake2b(repr((self.seed, self.profile) + key).encode(), digest_size=8)
        return int.from_bytes(digest.digest(), "little")

    def _float(self, h, scale=4.0):
        pick = (h & 0xFFFF) / 0x10000
        if pick < self.special:
            return f32(self.SPECIAL[(h >> 16) % len(self.SPECIAL)])
        return f32(((h >> 16) & 0xFFFFFF) / 0x1000000 * scale)

    def word(self, resource, address):
        h = self._h("raw", resource, address)
        profile = self.profile
        if profile == "mixed":
            profile = ("random", "extreme", "min", "max")[h % 4]
            h >>= 2
        if profile == "random":
            return wrap(h, 32)
        if profile == "min":
            return wrap(0x80808080, 32)
        if profile == "max":
            return 0x7F7F7F7F
        return wrap(sum((0x80 if (h >> k) & 1 else 0x7F) << (8 * k) for k in range(4)), 32)

    def cbuffer(self, register, row, component, view):
        key = f"b{register}[{row}].{'xyzw'[component]}"
        if key in self.fields:
            value = self.fields[key]
            if isinstance(value, float):
                value = f32(value)
                return value if view == "f32" else wrap(f32_bits(value), 32)
            return wrap(value, 32) if view == "i32" else bits_f32(value)
        h = self._h("cb", key)
        if self.kinds.get(key, view) == "i32":
            pick = (h & 0xFFFF) / 0x10000
            value = (
                self.INT_EXTREMES[(h >> 16) % len(self.INT_EXTREMES)]
                if pick < self.special
                else 1 + (h >> 16) % 64
            )
            return value if view == "i32" else bits_f32(value)
        pick = (h & 0xFFFF) / 0x10000
        if pick < 0.5:
            value = f32(self.SCALES[(h >> 16) % len(self.SCALES)])
        elif pick < 0.5 + self.special:
            value = f32(self.SPECIAL[(h >> 16) % len(self.SPECIAL)])
        else:
            value = f32((((h >> 16) & 0xFFFFFF) / 0x1000000 - 0.5) * 4)
        return value if view == "f32" else wrap(f32_bits(value), 32)

    def texel(self, resource, mip, coords, kind):
        h = [self._h("tex", resource, mip, coords, c) for c in range(4)]
        if kind == "f32":
            return tuple(self._float(x) for x in h)
        if kind == "f16":
            return tuple(f16(self._float(x)) for x in h)
        return tuple(wrap(x, 32) for x in h)


# ------------------------------------------------------------------------------------ parsing


def split_top(text):
    """Split at commas outside {}, <> and [] brackets."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "{<[(":
            depth += 1
        elif ch in "}>])":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def _scalar_type(ty):
    if m := re.fullmatch(r"i(\d+)", ty):
        return ("int", int(m[1]))
    if ty in ("float", "half"):
        return ("float", ty)
    raise LoweringError("Unsupported type " + ty)


def _type_of(ty):
    """('int', w) / ('float', 'float'|'half') / ('vec', n, scalar) / ('other', name)."""
    ty = ty.strip()
    if m := re.fullmatch(r"<(\d+) x ([a-z0-9]+)>", ty):
        return ("vec", int(m[1]), _scalar_type(m[2]))
    if re.fullmatch(r"i\d+|float|half", ty):
        return _scalar_type(ty)
    return ("other", ty)


def _parse_float(token, kind):
    if token.startswith("0xH"):
        return bits_f16(int(token[3:], 16))
    if token.startswith("0x"):
        value = struct.unpack("<d", int(token[2:], 16).to_bytes(8, "little"))[0]
    else:
        value = float(token)
    return ROUND[kind](value)


def _constant(token, ty):
    """Python value of a literal operand of type `ty`, or None if it names a value."""
    token = token.strip()
    if token.startswith("%"):
        return None
    kind = ty[0]
    if kind == "other":  # struct-typed literal such as ResBind { ... }: kept as text
        return token
    if token in ("undef", "zeroinitializer"):
        if kind == "int":
            return 0
        if kind == "float":
            return 0.0
        if kind == "vec":
            return tuple(_constant("undef", ty[2]) for _ in range(ty[1]))
        return None
    if kind == "int":
        if token in ("true", "false"):
            return int(token == "true")
        return wrap(int(token), ty[1])
    if kind == "float":
        return _parse_float(token, ty[1])
    if kind == "vec":
        m = re.fullmatch(r"<(.*)>", token)
        require(m, "Unparsed vector constant " + token)
        items = [item.split(" ", 1)[1] for item in split_top(m[1])]
        require(len(items) == ty[1], "Vector constant size")
        return tuple(_constant(item, ty[2]) for item in items)
    raise LoweringError(f"Unsupported constant {token} of {ty}")


def _getter(token, ty):
    value = _constant(token, ty)
    if value is not None:
        return lambda env: value
    name = token.strip()
    return lambda env: env[name]


def _typed(arg):
    """'i32 %v1' -> (type, token); handles struct-typed constants like ResBind { ... }."""
    arg = arg.strip()
    if arg.startswith("%dx.types."):
        ty, rest = arg.split(" ", 1)
        return ty, rest.strip()
    if arg.startswith("<"):
        end = arg.index(">") + 1
        return arg[:end], arg[end:].strip()
    ty, rest = arg.split(" ", 1)
    return ty, rest.strip()


class Program:
    """A single-function program decoded into per-block closures."""

    def __init__(self, text):
        for item in FORBIDDEN:
            require(item not in text, f"{item} is not a single-thread program")
        lines = text.splitlines()
        starts = [n for n, line in enumerate(lines) if re.match(r"define void @\S+\(\) \{$", line)]
        require(len(starts) == 1, "Expected exactly one entry function")
        self.blocks, order, block = {}, [], None
        for line in lines[starts[0] + 1 :]:
            if line == "}":
                break
            if m := re.fullmatch(r"(" + LABEL + r"):(?:\s*;.*)?", line):
                block = m[1]
                self.blocks[block] = dict(phis=[], body=[], term=None)
                order.append(block)
                continue
            code = METADATA.sub("", line.split(";", 1)[0].rstrip())
            if not code.strip():
                continue
            require(block is not None, "Instruction before the first label (use rename_ssa)")
            self._decode(self.blocks[block], code.strip())
        self.entry = order[0]
        for name, b in self.blocks.items():
            require(b["term"] is not None, f"Block {name} has no terminator")

    # -- decoding -------------------------------------------------------------------------

    def _decode(self, block, code):
        if code.startswith("br "):
            if m := re.fullmatch(r"br label %(" + LABEL + r")", code):
                target = m[1]
                block["term"] = lambda env: target
                return
            m = re.fullmatch(r"br i1 (\S+), label %(" + LABEL + r"), label %(" + LABEL + r")", code)
            require(m, "Unparsed branch " + code)
            cond, yes, no = _getter(m[1], ("int", 1)), m[2], m[3]
            block["term"] = lambda env: yes if cond(env) else no
            return
        if code == "ret void":
            block["term"] = lambda env: None
            return
        m = re.fullmatch(r"(" + NAME + r") = (.*)", code)
        if not m:
            require(code.startswith("call void @dx.op."), "Unparsed statement " + code)
            block["body"].append((None, self._call(code)))
            return
        name, rhs = m[1], m[2]
        if rhs.startswith("phi "):
            ty, rest = rhs[4:].split(" ", 1)
            t = _type_of(ty)
            pairs = re.findall(r"\[ ([^\[\]]+?), %(" + LABEL + r") \]", rest)
            require(pairs, "Unparsed phi " + code)
            block["phis"].append((name, {label: _getter(v, t) for v, label in pairs}))
            return
        block["body"].append((name, self._instruction(rhs)))

    def _instruction(self, rhs):
        op = rhs.split(" ", 1)[0]
        if op in INT_BINARY or op in INT_DIVISION:
            m = re.fullmatch(op + r"((?: nuw| nsw| exact)*) (<\d+ x i\d+>|i\d+) (.*)", rhs)
            require(m, "Unparsed " + rhs)
            t = _type_of(m[2])
            a, b = [_getter(x, t) for x in split_top(m[3])]
            return self._int_binary(op, t, a, b)
        if op in FLOAT_BINARY:
            m = re.fullmatch(op + r"((?: [a-z]+)*?) (float|half) (.*)", rhs)
            require(m, "Unparsed " + rhs)
            t = ("float", m[2])
            a, b = [_getter(x, t) for x in split_top(m[3])]
            rnd = ROUND[m[2]]
            if op == "fadd":
                return lambda env: rnd(a(env) + b(env))
            if op == "fsub":
                return lambda env: rnd(a(env) - b(env))
            if op == "fmul":
                return lambda env: rnd(a(env) * b(env))
            return lambda env: rnd(_divide(a(env), b(env)))
        if op == "icmp":
            m = re.fullmatch(r"icmp (\w+) (i\d+) (.*)", rhs)
            require(m, "Unparsed " + rhs)
            t = _type_of(m[2])
            a, b = [_getter(x, t) for x in split_top(m[3])]
            return self._icmp(m[1], t[1], a, b)
        if op == "fcmp":
            m = re.fullmatch(r"fcmp(?: [a-z]+)*? (\w+) (float|half) (.*)", rhs)
            require(m, "Unparsed " + rhs)
            t = ("float", m[2])
            a, b = [_getter(x, t) for x in split_top(m[3])]
            return self._fcmp(m[1], a, b)
        if op == "select":
            parts = split_top(rhs[len("select ") :])
            require(len(parts) == 3 and parts[0].startswith("i1 "), "Unparsed " + rhs)
            c = _getter(parts[0][3:], ("int", 1))
            ty, x = _typed(parts[1])
            _, y = _typed(parts[2])
            t = _type_of(ty)
            x, y = _getter(x, t), _getter(y, t)
            return lambda env: x(env) if c(env) else y(env)
        if op in CASTS or op == "bitcast":
            m = re.fullmatch(op + r" (<[^>]+>|\S+) (\S+) to (<[^>]+>|\S+)", rhs)
            require(m, "Unparsed " + rhs)
            return self._cast(op, _type_of(m[1]), _getter(m[2], _type_of(m[1])), _type_of(m[3]))
        if op == "extractvalue":
            m = re.fullmatch(r"extractvalue (\S+) (" + NAME + r"), (\d+)", rhs)
            require(m, "Unparsed " + rhs)
            source, index = m[2], int(m[3])
            return lambda env: env[source][index]
        if op == "extractelement":
            m = re.fullmatch(r"extractelement (<[^>]+>) (\S+), i32 (\d+)", rhs)
            require(m, "Unparsed " + rhs)
            vec, index = _getter(m[2], _type_of(m[1])), int(m[3])
            return lambda env: vec(env)[index]
        if op == "insertelement":
            m = re.fullmatch(r"insertelement (<[^>]+>) (\S+), (\S+) (\S+), i32 (\d+)", rhs)
            require(m, "Unparsed " + rhs)
            t = _type_of(m[1])
            vec, item, index = _getter(m[2], t), _getter(m[4], t[2]), int(m[5])

            def insert(env):
                v = list(vec(env))
                v[index] = item(env)
                return tuple(v)

            return insert
        if op == "call":
            return self._call(rhs)
        raise LoweringError("Unsupported instruction " + rhs)

    @staticmethod
    def _int_binary(op, t, a, b):
        if t[0] == "vec":
            width = t[2][1]
            f = Program._int_binary(op, ("int", width), lambda e: e[0], lambda e: e[1])
            return lambda env: tuple(f((x, y)) for x, y in zip(a(env), b(env), strict=True))
        w = t[1]
        mask = (1 << w) - 1
        if op == "add":
            return lambda env: wrap(a(env) + b(env), w)
        if op == "sub":
            return lambda env: wrap(a(env) - b(env), w)
        if op == "mul":
            return lambda env: wrap(a(env) * b(env), w)
        if op == "and":
            return lambda env: wrap(a(env) & b(env), w)
        if op == "or":
            return lambda env: wrap(a(env) | b(env), w)
        if op == "xor":
            return lambda env: wrap(a(env) ^ b(env), w)

        def shift(env):
            x, k = a(env), b(env) & mask
            if k >= w:
                raise Undefined(f"{op} by {k}")
            if op == "shl":
                return wrap(x << k, w)
            if op == "lshr":
                return wrap((x & mask) >> k, w)
            return wrap(x >> k, w)

        if op in ("shl", "lshr", "ashr"):
            return shift

        def divide(env):
            x, y = a(env), b(env)
            if op in ("udiv", "urem"):
                x, y = x & mask, y & mask
                if y == 0:
                    raise Undefined("division by zero")
                return wrap(x // y if op == "udiv" else x % y, w)
            if y == 0 or (x == -(1 << (w - 1)) and y == -1):
                raise Undefined("sdiv/srem undefined")
            q = abs(x) // abs(y)
            q = q if (x >= 0) == (y > 0) else -q
            return wrap(q if op == "sdiv" else x - q * y, w)

        return divide

    @staticmethod
    def _icmp(pred, w, a, b):
        mask = (1 << w) - 1
        unsigned = pred[0] == "u"
        test = {
            "eq": lambda x, y: x == y,
            "ne": lambda x, y: x != y,
            "gt": lambda x, y: x > y,
            "ge": lambda x, y: x >= y,
            "lt": lambda x, y: x < y,
            "le": lambda x, y: x <= y,
        }[pred if pred in ("eq", "ne") else pred[1:]]
        if unsigned:
            return lambda env: int(test(a(env) & mask, b(env) & mask))
        return lambda env: int(test(a(env), b(env)))

    @staticmethod
    def _fcmp(pred, a, b):
        ordered = {
            "eq": lambda x, y: x == y,
            "ne": lambda x, y: x != y,
            "gt": lambda x, y: x > y,
            "ge": lambda x, y: x >= y,
            "lt": lambda x, y: x < y,
            "le": lambda x, y: x <= y,
        }
        if pred in ("true", "false"):
            return lambda env: int(pred == "true")
        if pred in ("ord", "uno"):
            return lambda env: int((math.isnan(a(env)) or math.isnan(b(env))) == (pred == "uno"))
        test = ordered[pred[1:]]
        if pred[0] == "o":
            return lambda env: int(
                not math.isnan(x := a(env)) and not math.isnan(y := b(env)) and test(x, y)
            )
        return lambda env: int(math.isnan(x := a(env)) or math.isnan(y := b(env)) or test(x, y))

    @staticmethod
    def _cast(op, src, get, dst):
        if src[0] == "vec" and dst[0] == "vec" and op != "bitcast":
            f = Program._cast(op, src[2], lambda e: e, dst[2])
            return lambda env: tuple(f(x) for x in get(env))
        if op == "bitcast":
            return Program._bitcast(src, get, dst)
        if op == "zext":
            sw, dw = src[1], dst[1]
            return lambda env: wrap(get(env) & ((1 << sw) - 1), dw)
        if op == "sext":
            sw, dw = src[1], dst[1]
            if sw == 1:
                return lambda env: wrap(-get(env), dw)
            return lambda env: wrap(get(env), dw)
        if op == "trunc":
            dw = dst[1]
            return lambda env: wrap(get(env), dw)
        if op in ("sitofp", "uitofp"):
            rnd, sw = ROUND[dst[1]], src[1]
            if op == "uitofp":
                return lambda env: rnd(float(get(env) & ((1 << sw) - 1)))
            return lambda env: rnd(float(get(env)))
        if op in ("fptosi", "fptoui"):
            dw = dst[1]
            return lambda env: _to_int(get(env), dw, op == "fptosi")
        if op == "fptrunc":
            return lambda env: ROUND[dst[1]](get(env))
        if op == "fpext":
            return get
        raise LoweringError("Unsupported cast " + op)

    @staticmethod
    def _bitcast(src, get, dst):
        def to_word(value, t):
            if t[0] == "int":
                return value & ((1 << t[1]) - 1), t[1]
            if t[0] == "float":
                return (f32_bits(value), 32) if t[1] == "float" else (f16_bits(value), 16)
            width = t[2][1] if t[2][0] == "int" else (32 if t[2][1] == "float" else 16)
            word = 0
            for k, item in enumerate(value):
                word |= to_word(item, t[2])[0] << (width * k)
            return word, width * t[1]

        def from_word(word, t):
            if t[0] == "int":
                return wrap(word, t[1])
            if t[0] == "float":
                return bits_f32(word) if t[1] == "float" else bits_f16(word)
            width = t[2][1] if t[2][0] == "int" else (32 if t[2][1] == "float" else 16)
            return tuple(from_word(word >> (width * k), t[2]) for k in range(t[1]))

        def cast(env):
            word, width = to_word(get(env), src)
            return from_word(word & ((1 << width) - 1), dst)

        return cast

    def _call(self, rhs):
        m = re.fullmatch(r"call (\S+) @dx\.op\.([A-Za-z0-9]+)\.?([A-Za-z0-9.]*)\((.*)\)", rhs)
        require(m, "Unparsed call " + rhs)
        rtype, overload, args = m[1], m[3], split_top(m[4])
        opcode = int(_typed(args[0])[1])
        typed = [_typed(a) for a in args[1:]]
        ops = [_getter(token, _type_of(ty)) for ty, token in typed]
        return self._dxop(opcode, rtype, overload, typed, ops, rhs)

    def _dxop(self, opcode, rtype, overload, typed, ops, rhs):  # noqa: C901 - opcode table
        if opcode in (93, 94, 95):  # threadId, groupId, threadIdInGroup (component)
            kind, component = {93: "tid", 94: "gid", 95: "gtid"}[opcode], ops[0]
            return lambda env: env["$thread"][kind][component(env)]
        if opcode == 96:  # flattenedThreadIdInGroup
            return lambda env: env["$thread"]["flat"]
        if opcode == 217:  # createHandleFromBinding(ResBind {lo, hi, space, class}, index, nu)
            m = re.fullmatch(r"\{ i32 (\d+), i32 (\d+), i32 (\d+), i8 (\d+) \}", typed[0][1])
            require(m, "Unparsed binding " + rhs)
            cls, space, index = int(m[4]), int(m[3]), ops[1]
            return lambda env: ("handle", cls, space, index(env))
        if opcode == 216:  # annotateHandle(handle, properties)
            return ops[0]
        if opcode == 59:  # cbufferLoadLegacy(handle, row)
            view = overload
            handle, row = ops
            return lambda env: tuple(
                env["$world"].cbuffer(handle(env)[3], row(env), c, view) for c in range(4)
            )
        if opcode == 66:  # textureLoad(handle, mip, c0, c1, c2, o0, o1, o2)
            kind = overload
            handle, mip, c0, c1, c2 = ops[:5]

            def load(env):
                h = handle(env)
                coords = (c0(env), c1(env), c2(env))
                return env["$world"].texel((h[1], h[3]), mip(env), coords, kind) + (0,)

            return load
        if opcode == 139:  # rawBufferLoad(handle, index, offset, mask, alignment)
            handle, index = ops[0], ops[1]
            return lambda env: (
                tuple(
                    env["$world"].word(
                        (handle(env)[1], handle(env)[3]), wrap(index(env) + 4 * k, 32)
                    )
                    for k in range(4)
                )
                + (0,)
            )
        if opcode == 67:  # textureStore(handle, c0, c1, c2, v0..v3, mask)
            handle = ops[0]
            coords, values, mask = ops[1:4], ops[4:8], ops[8]
            to_bits = (
                f32_bits
                if overload == "f32"
                else (f16_bits if overload == "f16" else (lambda v: v & 0xFFFFFFFF))
            )

            def store(env):
                h = handle(env)
                env["$effects"].append(
                    (
                        "textureStore",
                        h[1],
                        h[3],
                        tuple(c(env) for c in coords),
                        tuple(to_bits(v(env)) for v in values),
                        mask(env),
                    )
                )

            return store
        if opcode == 140:  # rawBufferStore(handle, index, offset, v0..v3, mask, alignment)
            handle, index, values, mask = ops[0], ops[1], ops[3:7], ops[7]

            def raw_store(env):
                h = handle(env)
                env["$effects"].append(
                    (
                        "rawBufferStore",
                        h[1],
                        h[3],
                        index(env),
                        tuple(v(env) & 0xFFFFFFFF for v in values),
                        mask(env),
                    )
                )

            return raw_store
        if opcode == 163:  # dot4AddPacked(acc, a, b)
            acc, a, b = ops

            def dot(env):
                x, y = a(env), b(env)
                total = acc(env)
                for k in range(4):
                    p = (((x >> (8 * k)) & 255) ^ 128) - 128
                    q = (((y >> (8 * k)) & 255) ^ 128) - 128
                    total += p * q
                return wrap(total, 32)

            return dot
        if opcode in (37, 38, 39, 40):
            a, b = ops
            if opcode == 37:
                return lambda env: max(a(env), b(env))
            if opcode == 38:
                return lambda env: min(a(env), b(env))
            if opcode == 39:
                return lambda env: wrap(max(a(env) & 0xFFFFFFFF, b(env) & 0xFFFFFFFF), 32)
            return lambda env: wrap(min(a(env) & 0xFFFFFFFF, b(env) & 0xFFFFFFFF), 32)
        if opcode in (35, 36):
            a, b = ops
            f = _fmax if opcode == 35 else _fmin
            return lambda env: f(a(env), b(env))
        if opcode == 46:  # FMad
            a, b, c = ops
            return lambda env: _fmad(a(env), b(env), c(env), env["$fmad"])
        if opcode in (48, 49):  # IMad / UMad
            a, b, c = ops
            return lambda env: wrap(a(env) * b(env) + c(env), 32)
        if opcode in UNARY_FLOAT:
            require(overload in ("f32", "f16"), "Unsupported unary overload " + rhs)
            f, rnd, x = UNARY_FLOAT[opcode], (f32 if overload == "f32" else f16), ops[0]
            return lambda env: rnd(f(x(env)))
        if opcode == 220:  # pack4x8(mode, x, y, z, w)
            mode, items = ops[0], ops[1:]

            def pack(env):
                m, word = mode(env), 0
                for k, item in enumerate(items):
                    v = item(env)
                    if m == 1:
                        v = max(0, min(255, v))
                    elif m == 2:
                        v = max(-128, min(127, v))
                    word |= (v & 255) << (8 * k)
                return wrap(word, 32)

            return pack
        if opcode == 219:  # unpack4x8(mode, packed)
            mode, packed = ops

            def unpack(env):
                word, signed = packed(env), mode(env) == 1
                return tuple(
                    ((((word >> (8 * k)) & 255) ^ 128) - 128) if signed else (word >> (8 * k)) & 255
                    for k in range(4)
                )

            return unpack
        raise LoweringError(f"Unsupported dx.op {opcode}: {rhs}")

    # -- execution ------------------------------------------------------------------------

    def run(self, world, thread, fmad="fused", limit=2_000_000, values=False):
        """Side effects of one thread, in program order, and the blocks it entered.

        With `values`, also every value the thread defined ({name: value}).
        """
        env = {"$world": world, "$thread": thread, "$effects": [], "$fmad": fmad}
        block, previous, visited, steps = self.entry, None, [], 0
        while block is not None:
            visited.append(block)
            b = self.blocks[block]
            if b["phis"]:
                require(previous is not None, f"phi in the entry block {block}")
                values = [(name, incoming[previous](env)) for name, incoming in b["phis"]]
                env.update(values)
            for name, f in b["body"]:
                value = f(env)
                if name is not None:
                    env[name] = value
            steps += len(b["body"])
            if steps > limit:
                raise LoweringError("Step limit reached")
            previous, block = block, b["term"](env)
        if values:
            return env["$effects"], visited, {k: v for k, v in env.items() if k[0] == "%"}
        return env["$effects"], visited


def key(value):
    """Bit-exact comparison key: floats by their bits (NaN payloads included)."""
    if isinstance(value, float):
        return ("f", f32_bits(value))
    if isinstance(value, tuple):
        return tuple(key(v) for v in value)
    return value


def common_differences(a, b):
    """Names defined by both runs whose values differ (bit for bit)."""
    return sorted(n for n in a.keys() & b.keys() if key(a[n]) != key(b[n]))


def cbuffer_kinds(text):
    """'b<register>[row].<c>' -> 'i32'/'f32' for every constant-buffer field the program reads."""
    handles, loads, kinds = {}, {}, {}
    for line in text.splitlines():
        code = line.split(";", 1)[0]
        if m := re.match(
            r"  (" + NAME + r") = call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217,"
            r" %dx.types.ResBind \{ i32 (\d+), i32 \d+, i32 \d+, i8 2 \}",
            code,
        ):
            handles[m[1]] = int(m[2])
        elif m := re.match(
            r"  (" + NAME + r") = call %dx.types.Handle @dx.op.annotateHandle\(i32 216,"
            r" %dx.types.Handle (" + NAME + r"),",
            code,
        ):
            if m[2] in handles:
                handles[m[1]] = handles[m[2]]
        elif m := re.match(
            r"  ("
            + NAME
            + r") = call %dx.types.CBufRet.(\w+) @dx.op.cbufferLoadLegacy.\w+\(i32 59,"
            r" %dx.types.Handle (" + NAME + r"), i32 (\d+)\)",
            code,
        ):
            loads[m[1]] = (handles.get(m[3]), int(m[4]), m[2])
        elif m := re.match(
            r"  " + NAME + r" = extractvalue %dx.types.CBufRet.\w+ (" + NAME + r"), (\d+)", code
        ):
            register, row, view = loads[m[1]]
            kinds.setdefault(f"b{register}[{row}].{'xyzw'[int(m[2])]}", view)
    return kinds
