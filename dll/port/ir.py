# SPDX-License-Identifier: MIT
"""Textual LLVM/DXIL helpers shared by the lowering stages.

Every stage edits the textual IR printed by DXC 1.9.2607 (`dxc -dumpbin`, then its LLVM 3.7
`opt`). Values and block labels are renamed to %vN / vN in definition order (`canonical_ssa`)
between stages, so each stage can rely on that naming. The patterns here are the exact printer
forms; a stage that needs something else refuses instead of guessing.
"""

import collections
import re

VALUE = r"%[A-Za-z0-9_.$-]+"
INTEGER = re.compile(r"-?\d+")
DOT = re.compile(
    r"^  (%v\d+) = call i32 @dx\.op\.dot4AddPacked\.i32\(i32 163, i32 ([^,]+), i32 ([^,]+),"
    r" i32 ([^)]+)\)"
)
DOT_DECLARATION = re.compile(r"^declare i32 @dx.op.dot4AddPacked.i32[^\n]*\n", re.M)
I16_MIN, I16_MAX = -32768, 32767


class LoweringError(ValueError):
    """The program is not of a form the stage was reviewed for."""


def require(condition, message):
    if not condition:
        raise LoweringError(message)


def rename_ssa(text):
    """`dxc -dumpbin` output to editable IR: %N -> %vN, unnamed labels -> vN, entry label v0."""
    text = re.sub(r"%(\d+)", r"%v\1", text)
    text = re.sub(r"^; <label>:(\d+).*$", r"v\1:", text, flags=re.M)
    return re.sub(r"^(define void @[^\n]+\{)\n", r"\1\nv0:\n", text, flags=re.M)


def canonical_ssa(text):
    """Rename every defined value and block label to vN in order of definition."""
    names = []
    for line in text.splitlines():
        if m := re.match(r"^  (%[A-Za-z0-9_.$-]+) = ", line):
            names.append(m[1][1:])
        elif m := re.match(r"^([A-Za-z0-9_.$-]+):", line):
            names.append(m[1])
    mapping = {name: "v" + str(i) for i, name in enumerate(names)}
    text = re.sub(r"%([A-Za-z0-9_.$-]+)", lambda m: "%" + mapping.get(m[1], m[1]), text)
    return re.sub(
        r"^([A-Za-z0-9_.$-]+):[^\n]*", lambda m: mapping.get(m[1], m[1]) + ":", text, flags=re.M
    )


def signed_bytes(word):
    """The four signed INT8 lanes of a packed dot4 operand, low byte first."""
    return [(((word >> (8 * k)) & 255) ^ 128) - 128 for k in range(4)]


def byte_range(weight):
    """Exact range of weight * x over signed bytes x in [-128, 127]."""
    return min(-128 * weight, 127 * weight), max(-128 * weight, 127 * weight)


class Program:
    """Line table of canonical IR: definitions, their blocks, use counts and constant dot4s.

    A constant dot4 has one literal packed-weight operand; dynamic ones are not recorded.
    """

    def __init__(self, text):
        self.text = text
        self.lines = text.splitlines()
        self.defs, self.blocks, self.dots = {}, {}, {}
        self.uses = collections.Counter()
        self.dynamic_dots = 0
        block = None
        for n, line in enumerate(self.lines):
            if m := re.match(r"^(v\d+):", line):
                block = m[1]
            values = re.findall(r"%v\d+", line.split(";")[0])
            if line.startswith("  %v") and " = " in line:
                self.defs[values[0]] = n
                self.blocks[values[0]] = block
                values = values[1:]
            self.uses.update(values)
            if m := DOT.match(line):
                name, acc, a, b = m.groups()
                if INTEGER.fullmatch(a):
                    a, b = b, a
                if not INTEGER.fullmatch(b) or not a.startswith("%"):
                    self.dynamic_dots += 1
                    continue
                self.dots[name] = dict(
                    name=name, line=n, acc=acc, inp=a, w=signed_bytes(int(b)), block=block
                )

    def columns(self):
        """Accumulator chains of constant dot4s, newest tail first.

        A chain follows each dot's accumulator back while it is a constant dot in the same block
        with exactly one use. `base` is the first dot's accumulator (any i32 value).
        """
        columns, seen = [], set()
        for tail in reversed(list(self.dots.values())):
            if tail["name"] in seen:
                continue
            chain, current = [], tail
            while current["block"] == tail["block"] and current["name"] not in seen:
                chain.append(current)
                seen.add(current["name"])
                if current["acc"] not in self.dots or self.uses[current["acc"]] != 1:
                    break
                current = self.dots[current["acc"]]
            chain.reverse()
            columns.append(
                dict(
                    tail=tail,
                    chain=chain,
                    base=chain[0]["acc"],
                    inputs=tuple(d["inp"] for d in chain),
                )
            )
        return columns


def count_dots(text):
    return sum(bool(DOT.match(line)) for line in text.splitlines())


def drop_dot_declaration(text):
    """Remove the dot4 declaration once no dot4 call remains."""
    require(count_dots(text) == 0, "dot4 calls remain")
    return DOT_DECLARATION.sub("", text)


def shader_model(text, minor):
    """Set DXIL version 1.minor and shader model cs_6_minor (needed for native i16 vectors)."""
    for key, body in [
        ("version", f"i32 1, i32 {minor}"),
        ("shaderModel", f'!"cs", i32 6, i32 {minor}'),
    ]:
        found = re.search(r"!dx." + key + r" = !\{!(\d+)\}", text)
        require(found, f"Missing !dx.{key}")
        node = found[1]
        text = re.sub(
            r"^!" + node + r" = !\{[^\n]+\}",
            lambda m: "!" + node + " = !{" + body + "}",
            text,
            flags=re.M,
        )
    return text


def entry_properties(text):
    """(node, body) of the entry point's property list."""
    entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", text)
    require(entry, "Missing entry metadata")
    line = re.search(r"^!" + entry[1] + r" = !\{(.*)\}$", text, re.M)
    require(line, "Missing entry node")
    node = re.findall(r"!(\d+)", line[1])[-1]
    body = re.search(r"^!" + node + r" = !\{(.*)\}$", text, re.M)
    require(body, "Missing entry properties")
    return node, body[1]


def numthreads(text):
    node, body = entry_properties(text)
    found = re.search(r"i32 4, !(\d+)", body)
    require(found, "Missing numthreads")
    sizes = re.search(r"^!" + found[1] + r" = !\{i32 (\d+), i32 (\d+), i32 (\d+)\}$", text, re.M)
    require(sizes, "Unexpected numthreads node")
    return tuple(int(v) for v in sizes.groups())


def wave_size(text, lanes):
    """Required wave size in the entry properties (tag 23 from SM 6.8, else tag 11).

    An existing policy of the same tag is rewritten in place; otherwise a new node is appended.
    """
    props, body = entry_properties(text)
    version = re.search(r"!dx.shaderModel = !\{!(\d+)\}", text)[1]
    minor = int(re.search(r"^!" + version + r' = !\{!"cs", i32 6, i32 (\d+)\}', text, re.M)[1])
    tag = 23 if minor >= 8 else 11
    values = f"i32 {lanes}, i32 0, i32 0" if minor >= 8 else f"i32 {lanes}"
    pattern = r"^!" + props + r" = !\{(.*)\}$"
    old = re.search(r"i32 (11|23), !(\d+)", body)
    if old:
        require(int(old[1]) == tag, "Existing wave policy uses the other tag")
        node_pattern = r"^!" + old[2] + r" = !\{([^\n]*)\}$"
        before = re.search(node_pattern, text, re.M)[1]
        if before == values:
            return text, dict(old=before, new=values, changed=False)
        text = re.sub(
            node_pattern, lambda m: "!" + old[2] + " = !{" + values + "}", text, flags=re.M
        )
        return text, dict(old=before, new=values, changed=True)
    node = str(1 + max(int(n) for n in re.findall(r"^!(\d+) =", text, re.M)))
    text = re.sub(
        pattern,
        lambda m: "!" + props + " = !{" + m[1] + f", i32 {tag}, !{node}" + "}",
        text,
        flags=re.M,
    )
    return text + f"\n!{node} = !{{{values}}}\n", dict(old=None, new=values, changed=True)


DISCARD = "134217728"
STORE = (
    r"  call void @dx.op.rawBufferStore.i32\(i32 140, %dx.types.Handle (%\w+), i32 {}, i32 undef,"
    r" i32 [^,]+, i32 undef, i32 undef, i32 undef, i8 1, i32 4\)(?: #[0-9]+)?(?:  ;.*)?"
)


def masked_stores(text):
    """Move the SDK's 128 MiB discard address of masked DWORD stores to 0xfffffffc.

    The SDK's large scratch bucket (332,352,256 bytes) contains 128 MiB, so a masked store there
    can hit live data. Each repaired select must feed exactly one direct one-component store.
    """
    lines = text.splitlines()
    changed = []
    for i, line in enumerate(lines):
        m = re.match(r"  (%\w+) = select i1 [^,]+, i32 ([^,]+), i32 ([^ ;]+)", line)
        if not m or DISCARD not in (m[2], m[3]):
            continue
        name = m[1]
        pattern = re.compile(re.escape(name) + r"(?![A-Za-z0-9_])")
        uses = [o for n, o in enumerate(lines) if n != i and pattern.search(o.split(";")[0])]
        require(len(uses) == 1, "Discard address has more than one use")
        require(
            re.fullmatch(STORE.format(re.escape(name)), uses[0]),
            "Discard address is not a direct scalar DWORD store: " + uses[0],
        )
        lines[i] = line.replace("i32 " + DISCARD, "i32 -4")
        changed.append(dict(line=i + 1, address=name, old=134217728, new=4294967292))
    require(changed, "No masked DWORD stores found")
    return "\n".join(lines) + "\n", changed
