# SPDX-License-Identifier: MIT
"""Carry one slot's reviewed lowering to a sibling slot of the same pass (C10 family coverage).

FSR4 2.3.0 builds three copies of every model pass, one per context-size family (output up to
1080p, 1440p-4K, 8K). The SDK programs of a pass differ only in integer literals that follow
the grid: thread-ID bounds, row pitches and scratch-buffer base addresses. Weights, guards and
code shape are identical. RC8/RC9 lowered only the 1440p-4K ("donor") slots; every decision
those stages made (bins, pairs, flushes, guards) depends on weights and code shape, never on
grid literals. So the donor's release program with the grid literals of the target family is
the program the same stages would build for the target.

`literal_map` derives the map from the two SDK programs: they must be identical line for line
except integer literals, every differing literal must be an i32 operand, and the pairs must form
one injective function. Literals the RC7 lowering folded from a base address (an addition of
base + k, 0 < k < 64) extend the map by the same offset. `transport` rewrites the donor's release
program: every i32 literal operand of an instruction whose value is in the map's domain is
replaced; vector, array-size, alignment and dx.op opcode literals, globals (guard tables) and
metadata are never touched. It refuses when

- the two SDK programs differ in anything but i32 literals, or the map does not reproduce the
  target SDK program,
- a domain value sits in an instruction shape that carries no grid literal in the SDK program
  (other than the same shape with value operands constant-folded, e.g. RC7's z = 0 fast path,
  or a folded address addition), or a domain value is left after the rewrite, or
- the rewritten program and the target's own release program disagree on how often any value
  of the map's image occurs (both carry the same address code).

The result is then checked as a whole against the target's SDK program by differential.py.
"""

import collections
import re

if __package__:
    from .ir import LoweringError, require
else:
    from ir import LoweringError, require

# An integer literal token: not part of a name, type, float or metadata reference.
LITERAL = re.compile(r"(?<![\w.%@$!#-])-?\d+(?![\w.])")
TYPE = re.compile(r"(?<![\w.%@$])(i\d+|float|half|double)(?![\w.])")
HEADER = re.compile(r"^; shader hash: [0-9a-f]+$")
OFFSET_WINDOW = 64  # largest folded byte offset accepted beside a base address
BASE_MINIMUM = 1 << 16  # folded offsets only beside literals at least this large


def _code(line):
    return line.split(";")[0].rstrip()


def literals(code):
    """[(start, end, value, is_i32_operand)] of the integer literal tokens of one code line."""
    out = []
    opcode = re.search(r"@dx\.op\.[\w.]+\(i32 (\d+)", code)
    types = [(m.end(), m[1]) for m in TYPE.finditer(code)]
    for m in LITERAL.finditer(code):
        start, end = m.span()
        before, after = code[:start], code[end:]
        depth = before.count("<") - before.count(">")
        prior = [t for at, t in types if at <= start]
        i32 = (
            depth == 0
            and not after.startswith(" x ")
            and not before.endswith("align ")
            and not (opcode and opcode.start(1) == start)
            and bool(prior)
            and prior[-1] == "i32"
        )
        out.append((start, end, int(m[0]), i32))
    return out


def _shape(code):
    """Instruction shape with names and literals abstracted (for context comparisons)."""
    code = re.sub(r"\s+#\d+$", "", code)  # attribute group references ("#0")
    code = re.sub(r"%[\w.$-]+", "%v", code)
    return LITERAL.sub("N", code).strip()


def literal_map(donor_sdk, target_sdk):
    """Injective {donor literal: target literal} from two aligned SDK disassemblies."""
    a, b = donor_sdk.splitlines(), target_sdk.splitlines()
    require(len(a) == len(b), "SDK programs differ in length")
    sigma, contexts, headers = {}, collections.defaultdict(set), 0
    for x, y in zip(a, b):
        if x == y:
            continue
        if HEADER.match(x) and HEADER.match(y):
            headers += 1
            continue
        require(
            x.startswith("  ") and y.startswith("  "), "SDK programs differ outside code: " + x[:80]
        )
        cx, cy = _code(x), _code(y)
        require(_shape(cx) == _shape(cy), "SDK programs differ in more than literals: " + cx[:80])
        lx, ly = literals(cx), literals(cy)
        require(len(lx) == len(ly), "SDK programs differ in literal count: " + cx[:80])
        for (_, _, p, i32), (_, _, q, _) in zip(lx, ly):
            if p == q:
                continue
            require(i32, "A grid literal is not an i32 operand: " + cx[:80])
            require(sigma.setdefault(p, q) == q, f"Literal {p} maps to two values")
            contexts[p].add(_shape(cx))
        require(_comments_equal(x, y), "SDK programs differ in a comment: " + x[:80])
    require(sigma, "SDK programs are identical")
    require(len(set(sigma.values())) == len(sigma), "Literal map is not injective")
    require(headers <= 1, "More than the shader hash comment differs")
    return sigma, {k: sorted(v) for k, v in contexts.items()}


def _specializes(shape, allowed):
    """`shape` is `allowed` with some value operands constant-folded (%v -> N), else equal."""
    a, b = shape.split(), allowed.split()
    return len(a) == len(b) and all(
        x == y or (y.rstrip(",)") == "%v" and x.rstrip(",)") == "N" and x[1:] == y[2:])
        for x, y in zip(a, b)
    )


def _comments_equal(x, y):
    """Trailing comments (DXC names the intrinsic) must match."""
    return x.partition(";")[2] == y.partition(";")[2]


def extend(sigma, donor_release):
    """Add folded base + k literals (add i32 %x, base + k) of the donor release to the map."""
    bases = sorted(k for k in sigma if k >= BASE_MINIMUM)
    extended, folded = dict(sigma), {}
    for line in donor_release.splitlines():
        m = re.fullmatch(r"  %[\w.$-]+ = add(?: nuw| nsw)* i32 %[\w.$-]+, (-?\d+)", _code(line))
        if not m:
            continue
        v = int(m[1])
        if v in extended:
            continue
        below = [k for k in bases if 0 < v - k < OFFSET_WINDOW]
        if below:
            k = max(below)
            folded[v] = sigma[k] + (v - k)
            extended[v] = folded[v]
    require(len(set(extended.values())) == len(extended), "Extended literal map is not injective")
    return extended, folded


def apply(text, sigma):
    """Replace every i32 literal operand in sigma's domain; return (text, replaced counts)."""
    out, replaced = [], collections.Counter()
    for line in text.splitlines():
        if not line.startswith("  "):
            out.append(line)
            continue
        code = _code(line)
        rest = line[len(code) :]
        pieces, at = [], 0
        for start, end, value, i32 in literals(code):
            if i32 and value in sigma:
                pieces.append(code[at:start] + str(sigma[value]))
                at = end
                replaced[value] += 1
        out.append("".join(pieces) + code[at:] + rest)
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), replaced


def i32_literals(text):
    """Counter of i32 literal operands in instruction lines."""
    counts = collections.Counter()
    for line in text.splitlines():
        if line.startswith("  "):
            counts.update(v for _, _, v, i32 in literals(_code(line)) if i32)
    return counts


def transport(donor_sdk, target_sdk, donor_release, target_release):
    """The donor's release program with the target family's grid literals."""
    sigma, contexts = literal_map(donor_sdk, target_sdk)
    mapped, _ = apply(donor_sdk, sigma)
    for x, y in zip(mapped.splitlines(), target_sdk.splitlines()):
        require(
            x == y or (HEADER.match(x) and HEADER.match(y)),
            "Literal map does not reproduce the target SDK program",
        )
    sigma, folded = extend(sigma, donor_release)
    allowed = {shape for shapes in contexts.values() for shape in shapes}
    for line in donor_release.splitlines():
        if not line.startswith("  "):
            continue  # globals (guard tables), metadata and comments are never rewritten
        code = _code(line)
        if not any(i32 and v in sigma for _, _, v, i32 in literals(code)):
            continue
        shape = _shape(code)
        require(
            any(_specializes(shape, a) for a in allowed)
            or re.fullmatch(r"%v = add(?: nuw| nsw)* i32 %v, N", shape),
            "A grid literal value occurs in an unexpected instruction: " + code.strip()[:100],
        )
    result, replaced = apply(donor_release, sigma)
    left = i32_literals(result)
    stale = {v: left[v] for v in sigma if v not in sigma.values() and left[v]}
    require(not stale, f"Grid literals left after the rewrite: {stale}")
    theirs = i32_literals(target_release)
    mismatch = {v: (left[v], theirs[v]) for v in set(sigma.values()) if left[v] != theirs[v]}
    require(
        not mismatch, f"Grid literal counts differ from the target's release program: {mismatch}"
    )
    hashes = [line for line in target_sdk.splitlines() if HEADER.match(line)]
    if hashes:
        lines = [hashes[0] if HEADER.match(line) else line for line in result.splitlines()]
        result = "\n".join(lines) + ("\n" if result.endswith("\n") else "")
    proof = dict(
        literal_map={str(k): v for k, v in sorted(sigma.items())},
        folded={str(k): v for k, v in sorted(folded.items())},
        replaced=sum(replaced.values()),
        replaced_values=len(replaced),
        sdk_lines=len(donor_sdk.splitlines()),
        grid_literal_shapes=len(allowed),
    )
    return result, proof


__all__ = [
    "LoweringError",
    "apply",
    "extend",
    "i32_literals",
    "literal_map",
    "literals",
    "transport",
]
