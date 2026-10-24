# SPDX-License-Identifier: MIT
"""Exact lane pairing and bounded bin packing of 16-output literal-weight dot products.

A block computes 16 outputs, each a dot product of the same inputs (i16 values in a known range)
with literal coefficients. Two outputs share a <2 x i16> vector: each coefficient is split into
pieces in [-127, 127], every product and i16 partial sum is interval-bounded, and a partial is
widened to i32 ("flush") before it could overflow. Bias and later arithmetic stay in i32, so every
output equals its integer dot product exactly.

- `sequential_dots`: outputs paired in order, terms in order (one chunk until it would overflow).
- `matched_dots`: outputs paired by an exact minimum-cost matching over the sequential cost.
- `binned_dots`: pieces of a pair bin-packed (three deterministic strategies, fewest bins), outputs
  paired by an exact minimum-cost matching over that cost.
- `pointwise`: rewrites groups of 16 pointwise chains (shared input words) with `binned_dots`.
"""

import collections
from functools import lru_cache

if __package__:
    from .ir import I16_MAX, I16_MIN, Program, require
else:
    from ir import I16_MAX, I16_MIN, Program, require

OUTPUTS = 16
I32_MIN, I32_MAX = -(2**31), 2**31
INT8 = (-128, 127)


def pieces(value):
    """Split a coefficient into parts in [-127, 127] (each product then fits i16)."""
    result = []
    while value:
        part = max(-127, min(127, value))
        result.append(part)
        value -= part
    return result or [0]


def _terms(weights, domain, left, right):
    require(len(weights[left]) == len(weights[right]), "Rows differ in length")
    tasks = []
    for term in range(len(weights[left])):
        parts = [pieces(int(weights[row][term])) for row in (left, right)]
        for piece in range(max(map(len, parts))):
            coefficient = [values[piece] if piece < len(values) else 0 for values in parts]
            if not any(coefficient):
                continue
            low = [min(v * domain[0], v * domain[1]) for v in coefficient]
            high = [max(v * domain[0], v * domain[1]) for v in coefficient]
            require(
                min(low) >= I16_MIN and max(high) <= I16_MAX, "A coefficient piece overflows i16"
            )
            tasks.append(
                dict(term=term, piece=piece, weights=coefficient, minimum=low, maximum=high)
            )
    return tasks


def sequential_pair_cost(weights, domain, left, right):
    """(wide flushes, packed products) when two outputs accumulate their terms in order."""
    low, high, active, flushes = [0, 0], [0, 0], False, 0
    tasks = _terms(weights, domain, left, right)
    for task in tasks:
        if any(
            low[k] + task["minimum"][k] < I16_MIN or high[k] + task["maximum"][k] > I16_MAX
            for k in (0, 1)
        ):
            require(active, "Empty chunk")
            flushes += 1
            low, high, active = [0, 0], [0, 0], False
        low = [low[k] + task["minimum"][k] for k in (0, 1)]
        high = [high[k] + task["maximum"][k] for k in (0, 1)]
        active = True
    return flushes + int(active), len(tasks)


def pack_pair(weights, domain, left, right):
    """Deterministic bounded bin packing of two outputs' term pieces into i16 chunks.

    Three candidates: in order (sequential), best fit by largest single need, best fit by
    largest total need; the fewest bins wins, earlier candidate on ties.
    """
    require(domain[0] <= 0 <= domain[1], "Domain must contain zero")
    tasks = _terms(weights, domain, left, right)

    def fits(chunk, task):
        return all(
            chunk["minimum"][k] + task["minimum"][k] >= I16_MIN
            and chunk["maximum"][k] + task["maximum"][k] <= I16_MAX
            for k in (0, 1)
        )

    def generate(order, sequential):
        bins = []
        for task in order:
            choices = [i for i, chunk in enumerate(bins) if fits(chunk, task)]
            if sequential:
                choices = [len(bins) - 1] if bins and fits(bins[-1], task) else []
            if choices:
                index = min(
                    choices, key=lambda i: (sum(bins[i]["minimum"]) - sum(bins[i]["maximum"]), i)
                )
            else:
                index = len(bins)
                bins.append(dict(tasks=[], minimum=[0, 0], maximum=[0, 0]))
            chunk = bins[index]
            chunk["tasks"].append(task)
            chunk["minimum"] = [chunk["minimum"][k] + task["minimum"][k] for k in (0, 1)]
            chunk["maximum"] = [chunk["maximum"][k] + task["maximum"][k] for k in (0, 1)]
        return bins

    def needs(task):
        return [-v for v in task["minimum"]] + task["maximum"]

    largest = sorted(tasks, key=lambda t: (-max(needs(t)), -sum(needs(t)), t["term"], t["piece"]))
    total = sorted(tasks, key=lambda t: (-sum(needs(t)), -max(needs(t)), t["term"], t["piece"]))
    choices = [generate(tasks, True), generate(largest, False), generate(total, False)]
    selected = min(range(3), key=lambda i: (len(choices[i]), i))
    bins = choices[selected]
    # Every coefficient is conserved, independently of chunk order.
    for lane, row in enumerate((left, right)):
        rebuilt = [
            sum(t["weights"][lane] for c in bins for t in c["tasks"] if t["term"] == term)
            for term in range(len(weights[row]))
        ]
        require(rebuilt == [int(v) for v in weights[row]], "Bin packing lost a coefficient")
    return dict(
        bins=bins,
        products=len(tasks),
        sequential_flushes=len(choices[0]),
        strategy=("sequential", "maximum-first-best-fit", "total-first-best-fit")[selected],
    )


def binned_pair_cost(weights, domain, left, right):
    packed = pack_pair(weights, domain, left, right)
    return len(packed["bins"]), packed["products"]


def matching(weights, domain, cost):
    """Exact minimum-cost perfect matching of the 16 outputs into lane pairs.

    Objective: (wide flushes, packed products), then the lexicographically smallest pair list.
    """
    costs = {
        (a, b): cost(weights, domain, a, b) for a in range(OUTPUTS) for b in range(a + 1, OUTPUTS)
    }

    @lru_cache(None)
    def solve(mask):
        if not mask:
            return (0, 0), ()
        bit = mask & -mask
        left = bit.bit_length() - 1
        remaining = mask ^ bit
        options = []
        for right in range(left + 1, OUTPUTS):
            if remaining & (1 << right):
                rest, pairs = solve(remaining ^ (1 << right))
                edge = costs[left, right]
                options.append(((edge[0] + rest[0], edge[1] + rest[1]), ((left, right),) + pairs))
        return min(options)

    optimum, pairs = solve((1 << OUTPUTS) - 1)
    adjacent = tuple(sum(costs[a, a + 1][k] for a in range(0, OUTPUTS, 2)) for k in (0, 1))
    require(optimum <= adjacent, "Matching is worse than adjacent pairs")
    return dict(
        adjacent_cost=list(adjacent),
        selected_cost=list(optimum),
        pairs=[list(p) for p in pairs],
        output_order=[v for p in pairs for v in p],
    )


class Emitter:
    """Collects new instructions; values are numbered %<prefix>N (canonical_ssa renames them)."""

    def __init__(self, prefix="wino"):
        self.lines, self.serial, self.prefix = [], 0, prefix

    def emit(self, rhs):
        self.serial += 1
        value = f"%{self.prefix}{self.serial}"
        self.lines.append("  " + value + " = " + rhs)
        return value

    def line(self, line):
        self.lines.append(line)

    def shared(self, array, size, index, value=None):
        ptr = self.emit(
            f"getelementptr [{size} x i32], [{size} x i32] addrspace(3)* @{array}, i32 0, i32 {index}"
        )
        if value is None:
            return self.emit(f"load i32, i32 addrspace(3)* {ptr}, align 4")
        self.line(f"  store i32 {value}, i32 addrspace(3)* {ptr}, align 4")

    def unpack(self, word):
        packed = self.emit(f"bitcast i32 {word} to <2 x i16>")
        even = self.emit(f"shl <2 x i16> {packed}, <i16 8, i16 8>")
        even = self.emit(f"ashr <2 x i16> {even}, <i16 8, i16 8>")
        odd = self.emit(f"ashr <2 x i16> {packed}, <i16 8, i16 8>")
        return even, odd

    def splat(self, value):
        dup = self.emit(f"insertelement <2 x i16> undef, i16 {value}, i32 0")
        return self.emit(f"insertelement <2 x i16> {dup}, i16 {value}, i32 1")


def sequential_dots(e, inputs, weights, domain):
    """Sixteen exact i32 dots: output pairs in lanes, terms in order, bounded i16 chunks."""
    lo, hi = domain
    partial, sums = [None] * 8, [None] * 8
    minimum, maximum = [[0, 0] for _ in range(8)], [[0, 0] for _ in range(8)]
    chunks, products = 0, 0

    def flush(pair):
        nonlocal chunks
        if partial[pair] is None:
            return
        wide = e.emit(f"sext <2 x i16> {partial[pair]} to <2 x i32>")
        sums[pair] = wide if sums[pair] is None else e.emit(f"add <2 x i32> {sums[pair]}, {wide}")
        chunks += 1
        partial[pair], minimum[pair], maximum[pair] = None, [0, 0], [0, 0]

    for term, value in enumerate(inputs):
        dup = e.splat(value)
        for pair in range(8):
            parts = [pieces(int(weights[pair * 2 + lane][term])) for lane in range(2)]
            for k in range(max(map(len, parts))):
                w = [values[k] if k < len(values) else 0 for values in parts]
                if not any(w):
                    continue
                low = [min(v * lo, v * hi) for v in w]
                high = [max(v * lo, v * hi) for v in w]
                if any(
                    minimum[pair][j] + low[j] < I16_MIN or maximum[pair][j] + high[j] > I16_MAX
                    for j in (0, 1)
                ):
                    flush(pair)
                minimum[pair] = [minimum[pair][j] + low[j] for j in (0, 1)]
                maximum[pair] = [maximum[pair][j] + high[j] for j in (0, 1)]
                product = e.emit(f"mul <2 x i16> {dup}, <i16 {w[0]}, i16 {w[1]}>")
                partial[pair] = (
                    product
                    if partial[pair] is None
                    else e.emit(f"add <2 x i16> {partial[pair]}, {product}")
                )
                products += 1
    for pair in range(8):
        flush(pair)
    outputs = [
        e.emit(f"extractelement <2 x i32> {sums[o // 2] or 'zeroinitializer'}, i32 {o % 2}")
        for o in range(OUTPUTS)
    ]
    return outputs, dict(products=products, chunks=chunks)


def binned_dots(e, inputs, weights, domain):
    """Sixteen exact i32 dots: matched output pairs, bin-packed bounded i16 chunks."""
    match = matching(weights, domain, binned_pair_cost)
    order = match["output_order"]
    ordered = [weights[o] for o in order]
    duplicated, totals, chunks, products, strategies = {}, [], 0, 0, []

    def duplicate(term):
        if term not in duplicated:
            duplicated[term] = e.splat(inputs[term])
        return duplicated[term]

    for pair in range(8):
        packed = pack_pair(ordered, domain, pair * 2, pair * 2 + 1)
        strategies.append(packed["strategy"])
        total = None
        for chunk in packed["bins"]:
            partial = None
            for task in chunk["tasks"]:
                w = task["weights"]
                value = e.emit(f"mul <2 x i16> {duplicate(task['term'])}, <i16 {w[0]}, i16 {w[1]}>")
                partial = value if partial is None else e.emit(f"add <2 x i16> {partial}, {value}")
                products += 1
            require(partial is not None, "Empty bin")
            wide = e.emit(f"sext <2 x i16> {partial} to <2 x i32>")
            total = wide if total is None else e.emit(f"add <2 x i32> {total}, {wide}")
            chunks += 1
        totals.append(total or "zeroinitializer")
    outputs = [
        e.emit(f"extractelement <2 x i32> {totals[o // 2]}, i32 {o % 2}") for o in range(OUTPUTS)
    ]
    require([chunks, products] == match["selected_cost"], "Emitted cost differs from the matching")
    unordered = [outputs[order.index(o)] for o in range(OUTPUTS)]
    return unordered, dict(
        products=products, chunks=chunks, pairs=match["pairs"], strategies=strategies
    )


def matched_dots(e, inputs, weights, domain):
    """Sixteen exact i32 dots: matched output pairs, terms in order (RC9 `winograd-pairing`)."""
    match = matching(weights, domain, sequential_pair_cost)
    order = match["output_order"]
    outputs, record = sequential_dots(e, inputs, [weights[o] for o in order], domain)
    require(
        [record["chunks"], record["products"]] == match["selected_cost"],
        "Emitted cost differs from the matching",
    )
    return [outputs[order.index(o)] for o in range(OUTPUTS)], dict(record, pairs=match["pairs"])


# RC9 built `matched` and `binned` by replacing a `sequential` block in place. For weights with a
# leading all-zero term that replacement would have left two dead splats in front of the block;
# these are emitted directly instead (no pass-1/12 domain has such a term).
ACCUMULATORS = dict(sequential=sequential_dots, matched=matched_dots, binned=binned_dots)


def pointwise_groups(text, lengths):
    """Groups of 16 literal chains of a selected length reading the same distinct input words."""
    program = Program(text)
    require(program.dynamic_dots == 0, "Pointwise packing needs every dot4 weight literal")
    rows = sorted(program.columns(), key=lambda c: c["tail"]["line"])
    grouped = collections.defaultdict(list)
    for row in rows:
        if len(row["chain"]) in lengths:
            grouped[row["tail"]["block"], row["inputs"]].append(row)
    groups = []
    for (block, inputs), group in grouped.items():
        require(len(group) % OUTPUTS == 0, "A pointwise stage is not a multiple of 16 outputs")
        require(len(set(inputs)) == len(inputs), "A pointwise chain reads a word twice")
        for start in range(0, len(group), OUTPUTS):
            selected = group[start : start + OUTPUTS]
            first = min(row["tail"]["line"] for row in selected)
            tails = {row["tail"]["name"] for row in selected}
            require(
                all(program.defs[v] < first for v in inputs), "An input is defined after the group"
            )
            require(not tails & set(inputs), "A tail is an input of its own group")
            for row in selected:
                base = row["base"]
                require(base not in tails, "A base is a tail of its own group")
                require(
                    not base.startswith("%") or program.defs[base] < row["tail"]["line"],
                    "Late base",
                )
            groups.append(dict(block=block, inputs=list(inputs), rows=selected, first=first))
    return groups


def pointwise(text, lengths):
    """Replace each 16-output pointwise group with matched, bin-packed exact i16 sums.

    The new block goes before the group's first tail; each tail keeps its position and adds its
    original base to the new partial. Only dot4 lines change.
    """
    require("%point" not in text, "Reserved names already used")
    groups = pointwise_groups(text, lengths)
    require(groups, "No pointwise groups of the selected lengths")
    replacements, records = {}, []
    for number, group in enumerate(groups):
        weights = [[w for dot in row["chain"] for w in dot["w"]] for row in group["rows"]]
        e = Emitter(f"point{number}_")
        inputs = []
        for word in group["inputs"]:
            halves = e.unpack(word)
            for byte in range(4):
                inputs.append(
                    e.emit(f"extractelement <2 x i16> {halves[byte % 2]}, i32 {byte // 2}")
                )
        outputs, record = binned_dots(e, inputs, weights, INT8)
        for row, value in zip(group["rows"], outputs):
            for dot in row["chain"]:
                require(dot["line"] not in replacements, "Overlapping pointwise groups")
                replacements[dot["line"]] = []
            replacements[row["tail"]["line"]] = [
                f"  {row['tail']['name']} = add i32 {row['base']}, {value}"
            ]
        replacements[group["first"]] = e.lines + replacements[group["first"]]
        records.append(
            dict(inputs=len(group["inputs"]), products=record["products"], chunks=record["chunks"])
        )
    output = []
    for n, line in enumerate(text.splitlines()):
        output.extend(replacements.get(n, [line]))
    proof = dict(
        groups=len(records),
        replaced_dots=sum(len(row["chain"]) for g in groups for row in g["rows"]),
        products=sum(r["products"] for r in records),
        chunks=sum(r["chunks"] for r in records),
    )
    return "\n".join(output) + "\n", proof
