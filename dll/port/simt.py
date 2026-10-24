# SPDX-License-Identifier: MIT
"""A small SIMT interpreter for the DXIL compute programs the lowering stages read and write.

It runs whole thread groups of one program (text IR as `dxc -dumpbin` or the stages print it) on
a modelled set of resources and records every buffer store, so an SDK program and its
replacement can be compared on the same inputs. Each basic block is compiled once to a Python
function; threads are generators that yield at the two cross-thread operations the model passes
use (`waveAllTrue` and the group barrier). Lanes are threads in order (lane = flattened thread
index mod wave size).

Semantics (only what passes 1-12 use; anything else is refused): wrapping iN integer arithmetic on
unsigned canonical values, FP32 rounding after every float operation (FMad unfused; exact for the
integer-valued graphs the stages emit), Round_ne, Pack4x8/Unpack4x8, Dot4AddI8Packed, raw buffer
loads/stores of 4-byte words (out of range: read 0, write dropped), CBufferLoadLegacy, local and
group-shared arrays, constant global tables.
"""

import re
import struct

M = {1: 1, 8: 0xFF, 16: 0xFFFF, 32: 0xFFFFFFFF, 64: 0xFFFFFFFFFFFFFFFF}
_F32 = struct.Struct("<f")
_F64 = struct.Struct("<d")


class Unsupported(ValueError):
    """The program uses an instruction form the interpreter does not model."""


def f32(x):
    return _F32.unpack(_F32.pack(x))[0]


def sx(value, bits):
    half = 1 << (bits - 1)
    return ((value & M[bits]) ^ half) - half


def dot4(acc, a, b):
    total = sx(acc, 32)
    for k in range(4):
        total += sx(a >> (8 * k), 8) * sx(b >> (8 * k), 8)
    return total & M[32]


def pack4x8(mode, values):
    out = 0
    for k, v in enumerate(values):
        if mode == 0:
            byte = v & 0xFF
        elif mode == 1:
            byte = min(255, max(0, sx(v, 32)))
        elif mode == 2:
            byte = min(127, max(-128, sx(v, 32))) & 0xFF
        else:
            raise Unsupported("pack4x8 mode")
        out |= byte << (8 * k)
    return out


def unpack4x8(mode, word):
    if mode == 0:
        return tuple((word >> (8 * k)) & 0xFF for k in range(4))
    if mode == 1:
        return tuple(sx(word >> (8 * k), 8) & M[32] for k in range(4))
    raise Unsupported("unpack4x8 mode")


def round_ne(x):
    return f32(float(round(x)))


def binary_i32(op, a, b):
    if op == 37:
        return a if sx(a, 32) >= sx(b, 32) else b
    if op == 38:
        return a if sx(a, 32) <= sx(b, 32) else b
    if op == 39:
        return max(a, b)
    if op == 40:
        return min(a, b)
    raise Unsupported(f"binary.i32 {op}")


def split_args(text):
    """Top-level comma split (not inside <>, {}, [], ())."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "<{[(":
            depth += 1
        elif ch in ">}])":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


TYPED = re.compile(
    r"^((?:<\d+ x \w+>|\[\d+ x [^\]]+\]|%[\w.]+|\w+)(?: addrspace\(\d+\))?\**)\s+(.*)$"
)
VECTOR = re.compile(r"^<(\d+) x (\w+)>$")


def bits_of(ty):
    m = re.fullmatch(r"i(\d+)", ty)
    return int(m[1]) if m else None


class Program:
    """One compute entry compiled to Python block functions."""

    def __init__(self, text, wave=None):
        self.text = text
        self.globals = {}
        self.shared = {}
        self._parse_globals(text)
        m = re.search(r"^define void @[\w.]+\(\)[^{\n]*\{\n(.*?)^\}", text, re.S | re.M)
        if not m:
            raise Unsupported("No compute entry")
        self.numthreads = self._numthreads(text)
        self.required_wave = self._wave(text)
        self.wave = wave or self.required_wave or 32
        if self.required_wave and self.wave != self.required_wave:
            raise Unsupported("Wave size differs from the program's requirement")
        self.registers = {}
        self.blocks, order = {}, []
        current = None
        for line in m[1].splitlines():
            code = re.sub(r",\s*!\w+ !\d+", "", line.split(";")[0]).rstrip()
            if not code.strip():
                continue
            if label := re.match(r"^([A-Za-z0-9_.$-]+):", code):
                current = label[1]
                self.blocks[current] = []
                order.append(current)
                continue
            if current is None:
                raise Unsupported("Code before the first label")
            self.blocks[current].append(code.strip())
        if not order:
            raise Unsupported("No blocks")
        self.entry = order[0]
        self.sites = 0
        namespace = dict(
            f32=f32,
            sx=sx,
            dot4=dot4,
            pack4x8=pack4x8,
            unpack4x8=unpack4x8,
            round_ne=round_ne,
            binary_i32=binary_i32,
            G=self.globals,
        )
        source = []
        self.generators = set()
        for name in order:
            source.append(self._compile_block(name, self.blocks[name]))
        exec(compile("\n".join(source), "<simt>", "exec"), namespace)
        self.functions = {name: namespace["b_" + _ident(name)] for name in order}
        self.phis = {name: namespace["p_" + _ident(name)] for name in order}
        self.size = len(self.registers)

    # Metadata

    def _numthreads(self, text):
        entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", text)
        line = re.search(r"^!" + entry[1] + r" = !\{(.*)\}$", text, re.M)[1]
        props = re.findall(r"!(\d+)", line)[-1]
        body = re.search(r"^!" + props + r" = !\{(.*)\}$", text, re.M)[1]
        node = re.search(r"i32 4, !(\d+)", body)[1]
        sizes = re.search(r"^!" + node + r" = !\{i32 (\d+), i32 (\d+), i32 (\d+)\}$", text, re.M)
        return tuple(int(v) for v in sizes.groups())

    def _wave(self, text):
        entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", text)
        line = re.search(r"^!" + entry[1] + r" = !\{(.*)\}$", text, re.M)[1]
        props = re.findall(r"!(\d+)", line)[-1]
        body = re.search(r"^!" + props + r" = !\{(.*)\}$", text, re.M)[1]
        found = re.search(r"i32 (?:11|23), !(\d+)", body)
        if not found:
            return None
        return int(re.search(r"^!" + found[1] + r" = !\{i32 (\d+)", text, re.M)[1])

    def _parse_globals(self, text):
        for m in re.finditer(r"^@(\w+) = (.*)$", text, re.M):
            name, rest = m[1], m[2]
            if g := re.match(r"external addrspace\(3\) global \[(\d+) x i32\]", rest):
                self.shared[name] = int(g[1])
                continue
            g = re.match(
                r"internal unnamed_addr constant \[(\d+) x ([^\]]+?)\] \[(.*)\], align \d+$", rest
            )
            if not g:
                raise Unsupported("Global " + name)
            count, ty, body = int(g[1]), g[2], g[3]
            if ty == "i32":
                values = [int(v) & M[32] for v in re.findall(r"i32 (-?\d+)", body)]
            elif ty == "<4 x i32>":
                values = [
                    tuple(int(x) & M[32] for x in re.findall(r"i32 (-?\d+)", v))
                    for v in re.findall(r"<4 x i32> <([^>]*)>", body)
                ]
            else:
                raise Unsupported("Global type " + ty)
            if len(values) != count:
                raise Unsupported("Global size " + name)
            self.globals[name] = values

    # Code generation

    def reg(self, name):
        if name not in self.registers:
            self.registers[name] = len(self.registers)
        return f"r[{self.registers[name]}]"

    def value(self, ty, token):
        token = token.strip()
        if token.startswith("%"):
            return self.reg(token)
        if token.startswith("@"):
            name = token[1:]
            if name in self.shared:
                return f"S[{name!r}]"
            if name in self.globals:
                return f"G[{name!r}]"
            raise Unsupported("Unknown global " + token)
        if vm := VECTOR.match(ty):
            count = int(vm[1])
            if token in ("undef", "zeroinitializer"):
                return repr((0,) * count)
            items = split_args(token[1:-1])
            return "(" + ", ".join(self.value(*item.split(" ", 1)) for item in items) + ",)"
        if ty == "i1":
            return {"true": "1", "false": "0", "undef": "0"}[token]
        if bits := bits_of(ty):
            if token in ("undef", "zeroinitializer"):
                return "0"
            return str(int(token) & M[bits])
        if ty in ("float", "double", "half"):
            if token in ("undef", "zeroinitializer"):
                return "0.0"
            if token.startswith("0x"):
                return repr(f32(_F64.unpack(int(token, 16).to_bytes(8, "little"))[0]))
            return repr(f32(float(token)))
        if token == "undef":
            return "None"
        raise Unsupported(f"Operand {ty} {token}")

    def typed(self, text):
        m = TYPED.match(text.strip())
        if not m:
            raise Unsupported("Typed operand " + text)
        return m[1], m[2]

    def _compile_block(self, name, lines):
        body, phis, is_generator = [], [], False
        for line in lines:
            m = re.match(r"^(%[\w.]+) = (.*)$", line)
            dest, rhs = (m[1], m[2]) if m else (None, line)
            if rhs.startswith("phi "):
                ty, rest = rhs[4:].split(" ", 1)
                pairs = re.findall(r"\[ ([^,\]]+), %([\w.$-]+) \]", rest)
                phis.append((self.reg(dest), [(self.value(ty, v), b) for v, b in pairs]))
                continue
            code, generator = self._instruction(dest, rhs)
            is_generator |= generator
            body.extend(code)
        ident = _ident(name)
        out = [f"def p_{ident}(r, pred):"]
        if phis:
            preds = sorted({b for _, pairs in phis for _, b in pairs})
            for i, pred in enumerate(preds):
                targets, values = [], []
                for dest, pairs in phis:
                    choice = [v for v, b in pairs if b == pred]
                    if len(choice) != 1:
                        raise Unsupported("Phi without a value for " + pred)
                    targets.append(dest)
                    values.append(choice[0])
                keyword = "if" if i == 0 else "elif"
                out.append(f"    {keyword} pred == {pred!r}:")
                # Phis read their inputs before any of them is written (parallel assignment).
                out.append(f"        {', '.join(targets)} = {', '.join(values)}")
            out.append("    else:")
            out.append("        raise ValueError('phi predecessor')")
        else:
            out.append("    pass")
        out.append(f"def b_{ident}(r, t, c, S):")
        if is_generator:
            self.generators.add(name)
        out.extend("    " + x for x in body)
        if not body or not body[-1].startswith("return"):
            raise Unsupported("Block without terminator: " + name)
        return "\n".join(out)

    def _instruction(self, dest, rhs):
        d = self.reg(dest) if dest else None
        if rhs.startswith("br "):
            args = split_args(rhs[3:])
            if len(args) == 1:
                return [f"return {args[0].split('%')[1]!r}"], False
            cond = self.value("i1", args[0].split(" ", 1)[1])
            a, b = args[1].split("%")[1], args[2].split("%")[1]
            return [f"return {a!r} if {cond} else {b!r}"], False
        if rhs == "ret void":
            return ["return None"], False
        if rhs.startswith("call "):
            return self._call(d, rhs)
        if rhs.startswith("store "):
            value, pointer = split_args(rhs[6:])[:2]
            vty, vtok = self.typed(value)
            _, ptok = self.typed(pointer)
            return [
                f"_p = {self.value('ptr', ptok)}; _p[0][_p[1]] = {self.value(vty, vtok)}"
            ], False
        op, rest = rhs.split(" ", 1)
        rest = re.sub(r"^(?:(?:nuw|nsw|exact|fast|nnan|ninf|nsz|arcp|inbounds) )+", "", rest)
        if op in ("add", "sub", "mul", "shl", "lshr", "ashr", "and", "or", "xor"):
            ty, operands = rest.split(" ", 1) if not rest.startswith("<") else _vector_type(rest)
            a, b = split_args(operands)
            if vm := VECTOR.match(ty):
                count, bits = int(vm[1]), bits_of(vm[2])
                expr = _int_op(op, "_a[{0}]", "_b[{0}]", bits)
                lanes = ", ".join(expr.format(i) for i in range(count))
                return [
                    f"_a = {self.value(ty, a)}; _b = {self.value(ty, b)}; {d} = ({lanes},)"
                ], False
            bits = bits_of(ty)
            if bits is None:
                raise Unsupported(rhs)
            return [f"{d} = " + _int_op(op, self.value(ty, a), self.value(ty, b), bits)], False
        if op in ("fadd", "fsub", "fmul", "fdiv"):
            ty, operands = rest.split(" ", 1)
            a, b = (self.value(ty, x) for x in split_args(operands))
            symbol = {"fadd": "+", "fsub": "-", "fmul": "*", "fdiv": "/"}[op]
            return [f"{d} = f32({a} {symbol} {b})"], False
        if op == "icmp":
            pred, rest2 = rest.split(" ", 1)
            ty, operands = rest2.split(" ", 1)
            bits = bits_of(ty)
            a, b = (self.value(ty, x) for x in split_args(operands))
            if pred in ("slt", "sle", "sgt", "sge"):
                a, b = f"sx({a}, {bits})", f"sx({b}, {bits})"
            symbol = dict(
                eq="==",
                ne="!=",
                ugt=">",
                uge=">=",
                ult="<",
                ule="<=",
                sgt=">",
                sge=">=",
                slt="<",
                sle="<=",
            )[pred]
            return [f"{d} = 1 if {a} {symbol} {b} else 0"], False
        if op == "select":
            c, a, b = split_args(rest)
            cty, ctok = self.typed(c)
            aty, atok = self.typed(a)
            bty, btok = self.typed(b)
            return [
                f"{d} = {self.value(aty, atok)} if {self.value(cty, ctok)} else {self.value(bty, btok)}"
            ], False
        if op in ("sext", "zext", "trunc", "bitcast", "sitofp", "fptosi", "uitofp", "fptoui"):
            m = re.fullmatch(r"(.+?) (\S+) to (.+)", rest)
            if not m:
                raise Unsupported(rhs)
            src, token, dst = m[1], m[2], m[3]
            a = self.value(src, token) if not src.endswith("*") else "None"
            return [f"{d} = " + _cast(op, src, dst, a)], False
        if op == "extractvalue":
            agg, index = split_args(rest)
            _, tok = self.typed(agg)
            return [f"{d} = {self.reg(tok)}[{int(index)}]"], False
        if op == "extractelement":
            vec, index = split_args(rest)
            vty, vtok = self.typed(vec)
            ity, itok = self.typed(index)
            return [f"{d} = {self.value(vty, vtok)}[{self.value(ity, itok)}]"], False
        if op == "insertelement":
            vec, elem, index = split_args(rest)
            vty, vtok = self.typed(vec)
            ety, etok = self.typed(elem)
            _, itok = self.typed(index)
            k = int(itok)
            count = int(VECTOR.match(vty)[1])
            lanes = ", ".join(self.value(ety, etok) if i == k else f"_v[{i}]" for i in range(count))
            return [f"_v = {self.value(vty, vtok)}; {d} = ({lanes},)"], False
        if op == "alloca":
            m = re.match(r"\[(\d+) x (i32|float)\]", rest)
            if not m:
                raise Unsupported(rhs)
            return [f"{d} = [0] * {int(m[1])}"], False
        if op == "getelementptr":
            args = split_args(rest)
            if len(args) != 4 or not re.fullmatch(r"\[\d+ x [^\]]+\]", args[0]):
                raise Unsupported(rhs)
            _, base = self.typed(args[1])
            if self.typed(args[2])[1] != "0":
                raise Unsupported(rhs)
            ity, itok = self.typed(args[3])
            return [f"{d} = ({self.value('ptr', base)}, {self.value(ity, itok)})"], False
        if op == "load":
            ty, pointer = split_args(rest)[:2]
            _, ptok = self.typed(pointer)
            return [f"_p = {self.value('ptr', ptok)}; {d} = _p[0][_p[1]]"], False
        raise Unsupported(rhs)

    def _call(self, d, rhs):
        m = re.match(r"call (\S+(?: \S+)?) @([\w.]+)\((.*)\)(?: #\d+)?$", rhs)
        if not m:
            raise Unsupported(rhs)
        function, args = m[2], split_args(m[3])
        if function.startswith("llvm.lifetime."):
            return ["pass"], False
        typed = [self.typed(a) for a in args]
        v = [
            self.value(ty, tok) if not ty.startswith("%dx.types.Res") else None for ty, tok in typed
        ]
        name = function.split(".")[2] if function.startswith("dx.op.") else function
        if name == "threadId":
            return [f"{d} = t.dtid[{v[1]}]"], False
        if name == "groupId":
            return [f"{d} = t.gid[{v[1]}]"], False
        if name == "threadIdInGroup":
            return [f"{d} = t.gtid[{v[1]}]"], False
        if name == "createHandleFromBinding":
            bind = re.findall(r"i\d+ (-?\d+)", typed[1][1])
            return [f"{d} = ({int(bind[3])}, {int(bind[0])})"], False
        if name == "annotateHandle":
            return [f"{d} = {v[1]}"], False
        if name == "rawBufferLoad":
            return [f"{d} = c.load({v[1]}, {v[2]})"], False
        if name == "rawBufferStore":
            return [f"c.store({v[1]}, {v[2]}, ({v[4]}, {v[5]}, {v[6]}, {v[7]}), {v[8]})"], False
        if name == "cbufferLoadLegacy":
            return [f"{d} = c.cbuffer({v[1]}, {v[2]})"], False
        if name == "binary":
            return [f"{d} = binary_i32({v[0]}, {v[1]}, {v[2]})"], False
        if name == "tertiary" and function.endswith(".i32"):
            if v[0] not in ("48", "49"):
                raise Unsupported(rhs)
            return [f"{d} = ({v[1]} * {v[2]} + {v[3]}) & 4294967295"], False
        if name == "tertiary" and function.endswith(".f32"):
            if v[0] != "46":
                raise Unsupported(rhs)
            return [f"{d} = f32(f32({v[1]} * {v[2]}) + {v[3]})"], False
        if name == "unary" and function.endswith(".f32"):
            if v[0] != "26":
                raise Unsupported(rhs)
            return [f"{d} = round_ne({v[1]})"], False
        if name == "pack4x8":
            return [f"{d} = pack4x8({v[1]}, ({v[2]}, {v[3]}, {v[4]}, {v[5]}))"], False
        if name == "unpack4x8":
            return [f"{d} = unpack4x8({v[1]}, {v[2]})"], False
        if name == "dot4AddPacked":
            return [f"{d} = dot4({v[1]}, {v[2]}, {v[3]})"], False
        if name == "waveGetLaneIndex":
            return [f"{d} = t.lane"], False
        if name == "waveGetLaneCount":
            return [f"{d} = t.width"], False
        if name == "waveAllTrue":
            self.sites += 1
            return [f"{d} = yield ('all', {self.sites}, {v[1]})"], True
        if name == "barrier":
            self.sites += 1
            return [f"yield ('barrier', {self.sites}, None)"], True
        raise Unsupported(rhs)

    # Execution

    def thread(self, t, c, shared):
        r = [None] * self.size
        block, pred = self.entry, None
        functions, phis, generators = self.functions, self.phis, self.generators
        while True:
            phis[block](r, pred)
            if block in generators:
                following = yield from functions[block](r, t, c, shared)
            else:
                following = functions[block](r, t, c, shared)
            if following is None:
                return
            pred, block = block, following


def _ident(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _vector_type(rest):
    end = rest.index(">") + 1
    return rest[:end], rest[end + 1 :]


def _int_op(op, a, b, bits):
    mask = M[bits]
    if op == "add":
        return f"({a} + {b}) & {mask}"
    if op == "sub":
        return f"({a} - {b}) & {mask}"
    if op == "mul":
        return f"({a} * {b}) & {mask}"
    if op == "shl":
        return f"({a} << {b}) & {mask}"
    if op == "lshr":
        return f"{a} >> {b}"
    if op == "ashr":
        return f"(sx({a}, {bits}) >> {b}) & {mask}"
    if op == "and":
        return f"{a} & {b}"
    if op == "or":
        return f"{a} | {b}"
    if op == "xor":
        return f"{a} ^ {b}"
    raise Unsupported(op)


def _cast(op, src, dst, a):
    if op == "bitcast":
        if src.endswith("*") or dst.endswith("*"):
            return "None"
        if src == "i32" and dst == "<2 x i16>":
            return f"({a} & 65535, {a} >> 16)"
        if src == "<2 x i16>" and dst == "i32":
            return f"{a}[0] | ({a}[1] << 16)"
        raise Unsupported(f"bitcast {src} to {dst}")
    if vm := VECTOR.match(src):
        count, sbits = int(vm[1]), bits_of(vm[2])
        dbits = bits_of(VECTOR.match(dst)[2])
        lane = "sx({a}[{i}], {s}) & {m}" if op == "sext" else "{a}[{i}] & {m}"
        if op not in ("sext", "zext", "trunc"):
            raise Unsupported(op + " " + src)
        return (
            "(" + ", ".join(lane.format(a=a, i=i, s=sbits, m=M[dbits]) for i in range(count)) + ",)"
        )
    if op == "sext":
        return f"sx({a}, {bits_of(src)}) & {M[bits_of(dst)]}"
    if op == "zext":
        return f"{a}"
    if op == "trunc":
        return f"{a} & {M[bits_of(dst)]}"
    if op == "sitofp":
        return f"f32(float(sx({a}, {bits_of(src)})))"
    if op == "uitofp":
        return f"f32(float({a}))"
    if op == "fptosi":
        return f"int({a}) & {M[bits_of(dst)]}"
    if op == "fptoui":
        return f"int({a}) & {M[bits_of(dst)]}"
    raise Unsupported(op)


class Thread:
    __slots__ = ("dtid", "gid", "gtid", "lane", "width")

    def __init__(self, group, index, numthreads, width):
        x = index % numthreads[0]
        y = (index // numthreads[0]) % numthreads[1]
        z = index // (numthreads[0] * numthreads[1])
        self.gtid = (x, y, z)
        self.gid = tuple(group)
        self.dtid = tuple(g * n + i for g, n, i in zip(group, numthreads, self.gtid))
        self.lane = index % width
        self.width = width


class Resources:
    """ScratchBuffer (u11), InitializerBuffer (t18) and one constant buffer (b0).

    Unwritten scratch words come from `fill(address)`; writes are recorded in `writes` (last
    value per address) and counted. Addresses at or past `size` read 0 and drop writes, as raw
    buffer accesses out of range do.
    """

    def __init__(self, fill, model, cbuffer=b"", size=332352256):
        self.fill, self.model, self.cbuffer_bytes, self.size = fill, model, cbuffer, size
        self.writes, self.stores, self.dropped = {}, 0, 0

    def word(self, address):
        if address + 4 > self.size:
            return 0
        if address in self.writes:
            return self.writes[address]
        return self.fill(address) & M[32]

    def load(self, handle, index):
        if handle == (1, 11):
            return tuple(self.word(index + 4 * k) for k in range(4)) + (0,)
        if handle == (0, 18):
            out = []
            for k in range(4):
                at = index + 4 * k
                out.append(
                    struct.unpack_from("<I", self.model, at)[0] if at + 4 <= len(self.model) else 0
                )
            return tuple(out) + (0,)
        raise Unsupported(f"load from {handle}")

    def store(self, handle, index, values, mask):
        if handle != (1, 11):
            raise Unsupported(f"store to {handle}")
        for k in range(4):
            if mask >> k & 1:
                address = index + 4 * k
                if address + 4 > self.size:
                    self.dropped += 1
                    continue
                self.writes[address] = values[k] & M[32]
                self.stores += 1

    def cbuffer(self, handle, row):
        if handle != (2, 0):
            raise Unsupported(f"cbuffer {handle}")
        at = 16 * row
        data = self.cbuffer_bytes[at : at + 16].ljust(16, b"\0")
        return struct.unpack("<4I", data)


def run_group(program, resources, group, record=None):
    """Run one thread group to completion; returns the vote results (one per wave and site)."""
    count = program.numthreads[0] * program.numthreads[1] * program.numthreads[2]
    width = program.wave
    if count % width:
        raise Unsupported("Group is not a whole number of waves")
    shared = {name: [0] * size for name, size in program.shared.items()}
    threads = [
        program.thread(Thread(group, i, program.numthreads, width), resources, shared)
        for i in range(count)
    ]
    pending, votes = {}, []

    def advance(i, value):
        try:
            pending[i] = threads[i].send(value)
        except StopIteration:
            pending.pop(i, None)

    for i in range(count):
        advance(i, None)
    while pending:
        kinds = {request[0] for request in pending.values()}
        if "barrier" in kinds:
            if len(pending) != count or any(r[0] != "barrier" for r in pending.values()):
                raise Unsupported("Barrier not reached by the whole group")
            if len({r[1] for r in pending.values()}) != 1:
                raise Unsupported("Threads at different barriers")
            for i in sorted(pending):
                advance(i, None)
            continue
        waves = {}
        for i, request in pending.items():
            waves.setdefault(i // width, []).append((i, request))
        for wave, lanes in sorted(waves.items()):
            if len(lanes) != width or len({r[1] for _, r in lanes}) != 1:
                raise Unsupported("Wave vote with inactive or diverged lanes")
            result = int(all(r[2] for _, r in lanes))
            votes.append((wave, lanes[0][1][1], result))
            for i, _ in lanes:
                advance(i, result)
    return votes
