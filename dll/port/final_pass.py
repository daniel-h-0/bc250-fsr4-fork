# SPDX-License-Identifier: MIT
"""Final-pass (postpass) stages: exact FP32 integer graphs, native spatial blocks, colour reuse.

The INT8 final pass (`fsr4_model_v07_fp8_no_scale_postpass`, cs_6_6, 256 threads, one pixel per
thread) runs 960 constant-weight dot4s in 13 blocks (nine 3x3-neighbourhood blocks, one pointwise
block, three colour blocks) and normalises 36 colour samples (exposure, +1, log2, scale, max 0).
gfx1013 has no usable dot4. Every stage computes the SDK program's integers bit for bit:

- `float_graph` (RC7, Sep 9 `lower_float_graph`): each interval-bounded dot chain becomes FP32
  MADs on integers, carried through phis in FP32. Every operand and partial lies in
  [-2**24, 2**24], so fused and unfused MADs are exact; values convert back to i32 only where an
  integer use needs them, and an SDK `sitofp` of a converted value becomes `fadd 0.0`.
- `native_blocks` (RC8 `native-spatial-all-colors`): the spatial and colour chains plus the
  shortest pointwise group as packed-i16 streams (packing.stream_outputs, width 4); the remaining
  chains as FP32 graphs rooted at the streamed tails (`mixed_float`, with the tails' intervals
  from `float_graph`).
- `color_reuse` (RC9 `final-color-reuse-all`): in the second and fourth sample blocks, a sample
  whose coordinates equal one of two samples of the dominating first/third block takes that
  sample's normalised RGB; otherwise it runs the SDK's own instructions, renamed only. The
  inverse transform must restore the input byte for byte.

The stages edit the SDK's own value names (rename_ssa output, no canonical_ssa): the emitted
names (%stream, %exact, %float, %nativefp, %bcfr) are reserved and refused in the input.
"""

import collections
import re
import sys

if __package__:
    from . import packing
    from .ir import DOT, LoweringError, require
else:
    import packing
    from ir import DOT, LoweringError, require

EXACT = 1 << 24
DEFINITION = re.compile(r"^  (%v\d+) = ")
PHI = re.compile(r"^  (%v\d+) = phi i32 (.*)")
PHI_PAIR = re.compile(r"\[ ([^,]+), (%v\d+) \]")
INTEGER = re.compile(r"-?\d+")
RESERVED = ("%stream", "%exact", "%float", "%nativefp", "%bcfr", "\nbcfr")


def _weights(token):
    if not INTEGER.fullmatch(token):
        return None
    return [(((int(token) >> (8 * k)) & 255) ^ 128) - 128 for k in range(4)]


def _graph(lines):
    definitions, dots, phis = {}, {}, {}
    for n, line in enumerate(lines):
        if m := DEFINITION.match(line):
            definitions[m[1]] = (n, line)
        if m := DOT.match(line):
            name, acc, a, b = m.groups()
            if INTEGER.fullmatch(a):
                a, b = b, a
            dots[name] = (acc, a, b, _weights(b))
        if m := PHI.match(line):
            phis[m[1]] = PHI_PAIR.findall(m[2])
    return definitions, dots, phis


class _Depth:
    """Chains are walked recursively (as the Sep 9 lowering did); allow their depth."""

    def __enter__(self):
        self.limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(self.limit, 20000))

    def __exit__(self, *exc):
        sys.setrecursionlimit(self.limit)


def _intervals(dots, phis, external):
    """Exact integer interval of every dot (and the phis/dots it accumulates), or None.

    A dot's interval adds each byte product's range in order and fails once a partial leaves
    [-2**24, 2**24]; a phi's interval is the hull of its incoming values; a cycle fails.
    """
    ranges, active = {}, set()

    def interval(v):
        if INTEGER.fullmatch(v):
            x = int(v)
            return (x, x) if abs(x) <= EXACT else None
        if v in external:
            return tuple(external[v])
        if v in ranges:
            return ranges[v]
        if v in active:
            return None
        active.add(v)
        result = None
        if v in dots:
            acc, _, _, weights = dots[v]
            if bounds := interval(acc):
                lo, hi = bounds
                terms = (
                    [(min(-128 * w, 127 * w), max(-128 * w, 127 * w)) for w in weights]
                    if weights is not None
                    else [(-16256, 16384)] * 4
                )
                good = True
                for low, high in terms:
                    lo += low
                    hi += high
                    good &= lo >= -EXACT and hi <= EXACT
                if good:
                    result = lo, hi
        elif v in phis:
            values = [interval(x) for x, _ in phis[v]]
            if values and all(x is not None for x in values):
                result = min(x[0] for x in values), max(x[1] for x in values)
        active.remove(v)
        ranges[v] = result
        return result

    with _Depth():
        for v in dots:
            interval(v)
    return ranges


def _emit_graph(lines, definitions, dots, phis, converted, external):
    """Rewrite the converted dot/phi graph in FP32 (shared by float_graph and mixed_float)."""
    uses = {v: [] for v in converted}
    for n, line in enumerate(lines):
        values = re.findall(r"%v\d+", line.split(";")[0])
        if line.startswith("  %v") and " = " in line:
            values = values[1:]
        for v in set(values) & converted:
            uses[v].append(n)
    serial, out, replacements, before = 0, [], {}, {}
    native_roots = {}

    def emit(code):
        nonlocal serial
        serial += 1
        name = "%exact" + str(serial)
        out.append("  " + name + " = " + code)
        return name

    def fv(v):
        if v in converted:
            return "%float" + v[1:]
        if INTEGER.fullmatch(v):
            return f"{float(int(v)):.9e}"
        if v in external:
            native_roots[v] = "%nativefp" + v[1:]
            return native_roots[v]
        raise LoweringError("Unproven floating root " + v)

    def external_use(v, n):
        line = lines[n]
        m = DEFINITION.match(line)
        if m and m[1] in converted:
            if m[1] in phis or (m[1] in dots and dots[m[1]][0] == v):
                return False
        return not re.match(r"^  %v\d+ = sitofp i32 " + re.escape(v) + r" to float", line)

    for v in sorted(converted, key=lambda value: definitions[value][0]):
        n, line = definitions[v]
        out = []
        if v in phis:
            incoming = ", ".join(f"[ {fv(x)}, {block} ]" for x, block in phis[v])
            out.append(f"  {fv(v)} = phi float " + incoming)
        else:
            acc, a, b, weights = dots[v]
            total = fv(acc)
            for k in range(4):
                if weights is not None and weights[k] == 0:
                    continue
                operands = []
                for packed in [a] if weights is not None else [a, b]:
                    x = emit(f"shl i32 {packed}, {24 - 8 * k}")
                    x = emit(f"ashr i32 {x}, 24")
                    operands.append(emit(f"sitofp i32 {x} to float"))
                w = f"{float(weights[k]):.9e}" if weights is not None else operands[1]
                total = emit(
                    f"call float @dx.op.tertiary.f32(i32 46, float {operands[0]}, float {w},"
                    f" float {total})"
                )
            out.append(f"  {fv(v)} = fadd float {total}, 0.000000e+00")
        if any(external_use(v, use) for use in uses[v]):
            if v in phis:  # a conversion after a phi must follow all phis of its block
                after = n + 1
                while after < len(lines) and re.match(r"^  %v\d+ = phi ", lines[after]):
                    after += 1
                before.setdefault(after, []).append(f"  {v} = fptosi float {fv(v)} to i32")
            else:
                out.append(f"  {v} = fptosi float {fv(v)} to i32")
        replacements[n] = out
    for v, name in native_roots.items():
        at = definitions[v][0] + 1
        while at < len(lines) and re.match(r"^  %v\d+ = phi ", lines[at]):
            at += 1
        before.setdefault(at, []).append(f"  {name} = sitofp i32 {v} to float")
    source = "\n".join(lines)
    output = []
    for n, line in enumerate(lines):
        output.extend(before.get(n, []))
        if line.startswith("declare i32 @dx.op.dot4AddPacked.i32("):
            if not all(v in converted for v in dots):
                output.append(line)
            if "declare float @dx.op.tertiary.f32(" not in source:
                output.append("declare float @dx.op.tertiary.f32(i32, float, float, float) #0")
            continue
        if m := re.match(r"^  (%v\d+) = sitofp i32 (%v\d+) to float", line):
            if m[2] in converted:
                line = f"  {m[1]} = fadd float {fv(m[2])}, 0.000000e+00"
        output.extend(replacements.get(n, [line]))
    return "\n".join(output) + "\n", native_roots


def _refuse_reserved(text):
    for name in RESERVED:
        require(name not in text, f"Reserved name {name.strip()} already in the program")


def float_graph(text):
    """RC7: every interval-proven dot/phi graph as exact FP32 MADs (Sep 9 lower_float_graph)."""
    _refuse_reserved(text)
    lines = text.splitlines()
    definitions, dots, phis = _graph(lines)
    require(dots, "No dot4 in the program")
    ranges = _intervals(dots, phis, {})
    converted = {v for v, bounds in ranges.items() if bounds is not None and v in definitions}
    code, _ = _emit_graph(lines, definitions, dots, phis, converted, {})
    proof = dict(
        dots=len(dots),
        converted_dots=sum(v in converted for v in dots),
        converted_phis=sum(v in converted for v in phis),
        intervals={v: ranges[v] for v in sorted(converted)},
    )
    return code, proof


def mixed_float(text, external_roots):
    """FP32 graphs of the remaining dots, rooted at already computed i32 values of known range.

    Only the closure of proven dots (their accumulators and phis) converts; a phi joins once a
    parent is converted. External roots enter through one `sitofp` after their definition.
    """
    require("%nativefp" not in text, "Reserved name %nativefp already in the program")
    require(
        all(-EXACT <= lo <= hi <= EXACT for lo, hi in external_roots.values()),
        "External root outside the exact FP32 integer range",
    )
    lines = text.splitlines()
    definitions, dots, phis = _graph(lines)
    ranges = _intervals(dots, phis, external_roots)
    valid = {v for v, bounds in ranges.items() if bounds is not None and v in definitions}
    converted = set()

    def require_float(v):
        if INTEGER.fullmatch(v) or v in external_roots or v in converted:
            return
        require(v in valid, "Unproven value in a converted graph: " + v)
        converted.add(v)
        if v in dots:
            require_float(dots[v][0])
        elif v in phis:
            for parent, _ in phis[v]:
                require_float(parent)
        else:
            raise LoweringError("Unrecognized proven node: " + v)

    with _Depth():
        for v in dots:
            if v in valid:
                require_float(v)
        changed = True
        while changed:
            changed = False
            for v, parents in phis.items():
                if (
                    v in valid
                    and v not in converted
                    and any(parent in converted for parent, _ in parents)
                ):
                    require_float(v)
                    changed = True
    code, native_roots = _emit_graph(lines, definitions, dots, phis, converted, external_roots)
    proof = dict(
        dots=len(dots),
        converted_dots=sum(v in converted for v in dots),
        converted_phis=sum(v in converted for v in phis),
        external_roots=len(external_roots),
        external_float_roots=len(native_roots),
        intervals={v: ranges[v] for v in sorted(converted)},
    )
    return code, proof


def linear_rows(text):
    """Dot chains per block (Sep 10 final_linear_forms.extract): tail, block, chain, terms."""
    definitions, uses, dots, blocks, block = {}, collections.Counter(), {}, {}, "entry"
    for index, line in enumerate(text.splitlines()):
        if re.fullmatch(r"v\d+:", line):
            block = line[:-1]
        values = re.findall(r"%v\d+", line.split(";")[0])
        if line.startswith("  %v") and " = " in line:
            definitions[values[0]] = index
            blocks[values[0]] = block
            values = values[1:]
        uses.update(values)
        if m := DOT.match(line):
            name, base, packed, weights = m.groups()
            if INTEGER.fullmatch(packed):
                packed, weights = weights, packed
            require(INTEGER.fullmatch(weights), "A final-pass dot4 has dynamic weights")
            dots[name] = dict(base=base, packed=packed, weights=_weights(weights))
    rows, seen = [], set()
    tails = set(dots) - {x["base"] for x in dots.values()}
    for tail in sorted(tails, key=definitions.get):
        reverse, current = [], tail
        while current in dots and blocks[current] == blocks[tail]:
            reverse.append(current)
            previous = dots[current]["base"]
            if previous not in dots or uses[previous] != 1:
                break
            current = previous
        chain = list(reversed(reverse))
        require(not seen.intersection(chain), "Dot chains overlap")
        seen.update(chain)
        coeffs = collections.Counter()
        for name in chain:
            for component, weight in enumerate(dots[name]["weights"]):
                if weight:
                    coeffs[(dots[name]["packed"], component)] += weight
        rows.append(
            dict(
                tail=tail,
                block=blocks[tail],
                chain=chain,
                terms={key: value for key, value in coeffs.items() if value},
            )
        )
    require(seen == set(dots), "Not every dot4 is on a chain")
    return rows


# Block layout of the INT8 final pass: nine spatial blocks, the pointwise block, three colour
# blocks; the pointwise block's chains group by input set into 32/16/8 outputs.
BLOCKS, POINTWISE = 13, 9
POINT_GROUPS = ([32, 16, 8], [128, 128, 32])


def native_blocks(text, width=4):
    """RC8 `spatial-all-colors`: stream every block except the two long pointwise groups."""
    _refuse_reserved(text)
    total = sum(bool(DOT.match(line)) for line in text.splitlines())
    _, full = float_graph(text)
    rows = linear_rows(text)
    blocks = list(dict.fromkeys(r["block"] for r in rows))
    require(len(blocks) == BLOCKS, f"Expected {BLOCKS} dot blocks, found {len(blocks)}")
    point = blocks[POINTWISE]
    grouped = {}
    for item in rows:
        if item["block"] == point:
            key = tuple(dict.fromkeys(term[0] for term in item["terms"]))
            grouped.setdefault(key, []).append(item)
    groups = list(grouped.values())
    require(
        [len(g) for g in groups] == POINT_GROUPS[0]
        and [sum(len(i["chain"]) for i in g) for g in groups] == POINT_GROUPS[1],
        "Pointwise block is not the reviewed 32/16/8 grouping",
    )
    allowed = {r["tail"] for r in rows if r["block"] != point} | {r["tail"] for r in groups[2]}
    code, native = packing.stream_outputs(
        text, width=width, allowed_blocks=set(blocks), allowed_tails=allowed
    )
    remaining = sum(bool(DOT.match(line)) for line in code.splitlines())
    require(remaining + native["streamed_dots"] == total, "Streamed and remaining dots differ")
    tails = {tail for group in native["groups"] for tail in group["tails"]}
    external = {tail: full["intervals"][tail] for tail in tails}
    code, mixed = mixed_float(code, external)
    require(mixed["converted_dots"] == remaining, "A remaining dot4 is not FP32-exact")
    require(not any(DOT.match(line) for line in code.splitlines()), "dot4 calls remain")
    if code.count("@dx.op.tertiary.f32(") == 1:
        code = re.sub(r"^declare float @dx.op.tertiary.f32[^\n]*\n", "", code, flags=re.M)
    proof = dict(
        dots=total,
        streamed_dots=native["streamed_dots"],
        fp32_dots=remaining,
        stream_groups=len(native["groups"]),
        flushes=sum(len(g["chunks"]) for g in native["groups"]),
        width=width,
        pointwise_block=point,
        external_roots=mixed["external_roots"],
        converted_phis=mixed["converted_phis"],
        streamed=[
            dict(block=g["block"], tails=g["tails"], chunks=g["chunks"]) for g in native["groups"]
        ],
    )
    return code, proof


# ------------------------------------------------------------------------- colour-sample reuse

NAME = r"%[A-Za-z0-9_.]+"
CODE_DEFINITION = re.compile(r"^  (" + NAME + r") = (.*)")
LOAD = re.compile(
    r"^  (%v\d+) = call %dx.types.ResRet.f32 @dx.op.textureLoad.f32\(i32 66, %dx.types.Handle"
    r" (%v\d+), i32 0, i32 (%v\d+), i32 (%v\d+), i32 undef, i32 undef, i32 undef, i32 undef\)"
)
COLOR_PROPERTIES = "%dx.types.ResourceProperties { i32 2, i32 777 }"
SAMPLES, SAMPLE_BLOCKS = 36, 4
LOG_SCALE = "0x3FB4D50600000000"


def _definitions(lines):
    definitions, uses, blocks, current = {}, collections.defaultdict(list), {}, None
    for index, line in enumerate(lines):
        if m := re.fullmatch(r"(v\d+):", line):
            current = m[1]
        blocks[index] = current
        code = line.split(";", 1)[0]
        names = re.findall(NAME, code)
        if match := CODE_DEFINITION.match(code):
            definitions[match[1]] = (index, match[2].strip())
            names = [n for n in names[1:] if n not in {"%dx.types.ResRet.f32", "%dx.types.Handle"}]
        for name in names:
            uses[name].append(index)
    return definitions, uses, blocks


def _exposure(lines, definitions, uses, first):
    """The value every colour sample is multiplied by: the operand of channel 0's only use."""
    load = LOAD.match(lines[first])[1]
    channel = [
        name
        for name, (_, rhs) in definitions.items()
        if rhs == f"extractvalue %dx.types.ResRet.f32 {load}, 0"
    ]
    require(len(channel) == 1, "Colour sample without one channel-0 extract")
    users = uses[channel[0]]
    require(len(users) == 1, "Channel 0 has more than one use")
    m = re.fullmatch(
        r"  " + NAME + r" = fmul fast float " + re.escape(channel[0]) + r", (%v\d+)",
        lines[users[0]].split(";", 1)[0],
    )
    require(m, "Channel 0 is not multiplied by the exposure")
    exposure = m[1]
    require(exposure in definitions, "Exposure is not defined in the function")
    return exposure


def color_samples(source):
    """The 36 normalised colour samples, grouped by block (program order)."""
    lines = source.splitlines()
    definitions, uses, blocks = _definitions(lines)
    loads = [n for n, line in enumerate(lines) if LOAD.match(line)]
    samples, exposure, binding = [], None, None
    for start in loads:
        load, handle, x, y = LOAD.match(lines[start]).groups()
        annotation = definitions[handle][1]
        m = re.fullmatch(
            r"call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%v\d+), "
            + re.escape(COLOR_PROPERTIES)
            + r"\)",
            annotation,
        )
        if not m:
            continue
        require(binding in (None, m[1]), "Colour samples read two resources")
        binding = m[1]
        if exposure is None:
            exposure = _exposure(lines, definitions, uses, start)
        at = start + 1
        stages = [[] for _ in range(6)]

        def instruction(expected):
            nonlocal at
            if lines[at].startswith("  " + exposure + " = "):
                at += 1
            parsed = CODE_DEFINITION.match(lines[at].split(";", 1)[0])
            require(
                parsed and parsed[2].strip() == expected, f"Unexpected colour step: {lines[at]}"
            )
            at += 1
            return parsed[1]

        for channel in range(3):
            stages[0].append(instruction(f"extractvalue %dx.types.ResRet.f32 {load}, {channel}"))
        for value in stages[0]:
            stages[1].append(instruction(f"fmul fast float {value}, {exposure}"))
        for value in stages[1]:
            stages[2].append(instruction(f"fadd fast float {value}, 1.000000e+00"))
        for value in stages[2]:
            stages[3].append(instruction(f"call float @dx.op.unary.f32(i32 23, float {value})"))
        for value in stages[3]:
            stages[4].append(instruction(f"fmul fast float {value}, {LOG_SCALE}"))
        for value in stages[4]:
            stages[5].append(
                instruction(
                    f"call float @dx.op.binary.f32(i32 35, float {value}, float 0.000000e+00)"
                )
            )
        local = [load, *[v for stage in stages[:5] for v in stage]]
        require(
            all(all(start <= i < at for i in uses[value]) for value in local),
            "A colour intermediate is used outside its sample",
        )
        require(all(blocks[i] == blocks[start] for i in range(start, at)), "A sample spans blocks")
        samples.append(
            dict(
                block=blocks[start],
                start=start,
                end=at,
                load=load,
                handle=handle,
                x=x,
                y=y,
                normalized=stages[5],
                source="\n".join(lines[start:at]),
            )
        )
    require(binding is not None, "No colour samples")
    require(
        definitions[binding][1].startswith("call %dx.types.Handle @dx.op.createHandleFromBinding(")
        and ", i8 0 }" in definitions[binding][1],
        "The reused colour resource must be a read-only (SRV) binding",
    )
    require(len(samples) == SAMPLES, f"Expected {SAMPLES} colour samples, found {len(samples)}")
    grouped = collections.OrderedDict()
    for sample in samples:
        grouped.setdefault(sample["block"], []).append(sample)
    require(
        len(grouped) == SAMPLE_BLOCKS and all(len(g) == 9 for g in grouped.values()),
        "Colour samples are not four blocks of nine",
    )
    info = dict(binding=binding, exposure=exposure, exposure_definition=definitions[exposure][1])
    return lines, grouped, definitions, info


def dominators(source):
    """Dominator sets of the function's blocks (labels vN)."""
    successors, block = {}, None
    for line in source.splitlines():
        if match := re.fullmatch(r"(v\d+):", line):
            block = match[1]
            successors[block] = set()
        elif line == "}":
            block = None
        elif block is not None:
            successors[block].update(re.findall(r"label %(v\d+)", line))
    entry = next(iter(successors))
    reachable, todo = set(), [entry]
    while todo:
        block = todo.pop()
        if block in reachable:
            continue
        reachable.add(block)
        todo.extend(successors[block])
    require(reachable == set(successors), "Unreachable block")
    predecessors = {block: set() for block in successors}
    for block, targets in successors.items():
        for target in targets:
            predecessors[target].add(block)
    result = {block: ({entry} if block == entry else set(reachable)) for block in successors}
    changed = True
    while changed:
        changed = False
        for block in successors:
            if block == entry:
                continue
            updated = {block} | set.intersection(*(result[p] for p in predecessors[block]))
            if updated != result[block]:
                result[block] = updated
                changed = True
    return result


def _reuse(target, old_a, old_b, index):
    prefix = "bcfr" + str(index)
    same_x, same_y, same = [f"%{prefix}{suffix}" for suffix in ["ax", "ay", "a"]]
    shift_x, shift_y, shift = [f"%{prefix}{suffix}" for suffix in ["bx", "by", "b"]]
    hit = f"%{prefix}hit"
    cache_label, slow_label, join_label = [prefix + s for s in ["cache", "slow", "join"]]
    begin = [
        f"  {same_x} = icmp eq i32 {target['x']}, {old_a['x']}",
        f"  {same_y} = icmp eq i32 {target['y']}, {old_a['y']}",
        f"  {same} = and i1 {same_x}, {same_y}",
        f"  {shift_x} = icmp eq i32 {target['x']}, {old_b['x']}",
        f"  {shift_y} = icmp eq i32 {target['y']}, {old_b['y']}",
        f"  {shift} = and i1 {shift_x}, {shift_y}",
        f"  {hit} = or i1 {same}, {shift}",
        f"  br i1 {hit}, label %{cache_label}, label %{slow_label}",
        "",
        cache_label + ":",
    ]
    cached = []
    for channel, (a, b) in enumerate(zip(old_a["normalized"], old_b["normalized"], strict=True)):
        name = f"%{prefix}rgb{channel}"
        begin.append(f"  {name} = select i1 {same}, float {a}, float {b}")
        cached.append(name)
    begin.extend([f"  br label %{join_label}", "", slow_label + ":"])
    slowed = [f"%{prefix}slowrgb{c}" for c in range(3)]
    slow = target["source"]
    for old, new in zip(target["normalized"], slowed, strict=True):
        require(slow.count("  " + old + " = ") == 1, "Sample output defined twice")
        slow = slow.replace("  " + old + " = ", "  " + new + " = ")
    end = [f"  br label %{join_label}", "", join_label + ":"]
    for original, fast, fallback in zip(target["normalized"], cached, slowed, strict=True):
        end.append(
            f"  {original} = phi float [ {fast}, %{cache_label} ], [ {fallback}, %{slow_label} ]"
        )
    code = "\n".join([*begin, slow, *end])
    return code, dict(join_label=join_label, target=target, replacement=code)


def color_reuse(text, rows=(0, 1, 2)):
    """RC9: guarded within-thread reuse of identical normalised colour samples (exact)."""
    require(set(rows) <= {0, 1, 2} and len(set(rows)) == len(rows), "Invalid sample rows")
    require("%bcfr" not in text and "\nbcfr" not in text, "Reserved name bcfr already in use")
    lines, groups, definitions, info = color_samples(text)
    order = list(groups)
    dom = dominators(text)
    edits, records, final_labels = {}, [], {}
    for source_block, target_block in [(order[0], order[1]), (order[2], order[3])]:
        require(source_block in dom[target_block], "Cached samples do not dominate the target")
        for row in rows:
            for column in (0, 1):
                old_a = groups[source_block][row * 3 + column]
                old_b = groups[source_block][row * 3 + column + 1]
                target = groups[target_block][row * 3 + column]
                require(
                    all(
                        definitions[v][0] < target["start"]
                        for v in old_a["normalized"] + old_b["normalized"]
                    ),
                    "A cached value is defined after its target",
                )
                require(
                    not any(
                        line.startswith("  " + info["exposure"] + " = ")
                        for line in target["source"].splitlines()
                    ),
                    "A target defines the exposure",
                )
                new, record = _reuse(target, old_a, old_b, len(records))
                records.append(record)
                edits[target["start"]] = (target["end"], new)
                final_labels[target_block] = record["join_label"]
    result, index = [], 0
    while index < len(lines):
        if index in edits:
            index, new = edits[index]
            result.extend(new.splitlines())
        else:
            result.append(lines[index])
            index += 1
    phi_changes = []
    for i, line in enumerate(result):
        if " = phi " not in line:
            continue
        before = line
        for block, new in final_labels.items():
            line = re.sub(r", %" + block + r"(\s*\])", lambda m: ", %" + new + m[1], line)
        if line != before:
            phi_changes.append((before, line))
            result[i] = line
    code = "\n".join(result) + "\n"
    restored = code
    for before, after in phi_changes:
        require(restored.count(after) == 1, "Relabelled phi is not unique")
        restored = restored.replace(after, before)
    for record in reversed(records):
        require(restored.count(record["replacement"]) == 1, "Replacement is not unique")
        restored = restored.replace(record["replacement"], record["target"]["source"])
    require(restored == text, "The inverse transform does not restore the input")
    proof = dict(
        reused_samples=len(records),
        relabelled_phis=len(phi_changes),
        saved_texture_loads_per_thread=len(records),
        exposure=info["exposure"],
        exposure_definition=info["exposure_definition"],
        color_binding=info["binding"],
        sample_blocks=order,
        inverse_restores_input=True,
    )
    return code, proof
