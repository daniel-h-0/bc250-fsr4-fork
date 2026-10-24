# SPDX-License-Identifier: MIT
"""Exact packed-i16 lowering of constant-weight dot4 chains (gfx1013 has no usable dot4).

Each signed-byte product with a literal weight is computed in a two-lane i16 vector
(`v_pk_mad_u16` class), accumulated in i16 only while the interval bound of every lane stays
inside [-32768, 32767], then widened ("flushed") to i32. The original i32 accumulator (bias) is
added only after widening, so the result equals the dot4 chain bit for bit. The stages below
are ports of the v4 lowering as used by RC7-RC11, applied in this order:

1. `stream_outputs`: columns sharing all inputs, two outputs per vector lane pair.
2. `prefix_pairs`: greedy bounded prefixes of two columns with equal leading inputs.
3. `half_groups`: the rest, two byte lanes per vector (with v4's biased fallback).
4. `packed_reductions`: anything left (none in the literal passes after 1-3).
"""

import collections

if __package__:
    from .ir import I16_MAX, I16_MIN, Program, byte_range, require
else:
    from ir import I16_MAX, I16_MIN, Program, byte_range, require


class Emitter:
    def __init__(self, prefix):
        self.prefix, self.serial = prefix, 0

    def __call__(self, out, rhs):
        self.serial += 1
        value = f"%{self.prefix}{self.serial}"
        out.append("  " + value + " = " + rhs)
        return value


def _bytes_of(emit, out, word):
    """Signed bytes of an i32 as (even, odd) <2 x i16>: bytes 0/2 and 1/3."""
    packed = emit(out, f"bitcast i32 {word} to <2 x i16>")
    even = emit(out, f"shl <2 x i16> {packed}, <i16 8, i16 8>")
    even = emit(out, f"ashr <2 x i16> {even}, <i16 8, i16 8>")
    odd = emit(out, f"ashr <2 x i16> {packed}, <i16 8, i16 8>")
    return even, odd


def _splat(emit, out, value):
    dup = emit(out, f"insertelement <2 x i16> undef, i16 {value}, i32 0")
    return emit(out, f"insertelement <2 x i16> {dup}, i16 {value}, i32 1")


def _apply(lines, replacements):
    output = []
    for n, line in enumerate(lines):
        output.extend(replacements.get(n, [line]))
    return "\n".join(output) + "\n"


def stream_outputs(text, width=4, primary=False, extra_lengths=()):
    """Stream common byte inputs through several outputs (chains of 4/8/16, or 36 if primary).

    Columns in one block with identical input words are grouped; each group of `width` (16 for
    primary 36-input groups) computes output pairs in i16 lanes with per-lane bounded flushes.
    """
    require(width in (4, 8, 16), "Unsupported stream width")
    p = Program(text)
    groups = collections.defaultdict(list)
    for col in sorted(p.columns(), key=lambda c: c["tail"]["line"]):
        n = len(col["chain"])
        if n in (4, 8, 16) or (primary and n == 36) or n in extra_lengths:
            groups[(col["tail"]["block"], col["inputs"])].append(col)
    emit, replace, records = Emitter("stream"), {}, []
    for (block, inputs), group in groups.items():
        capacity = 16 if primary and len(inputs) == 36 else width
        for start in range(0, len(group), capacity):
            cols = group[start : start + capacity]
            if len(cols) % 2:
                cols = cols[:-1]
            if len(cols) < 2:
                continue
            at = min(c["tail"]["line"] for c in cols)
            tails = {c["tail"]["name"] for c in cols}
            if any(p.blocks[v] == block and p.defs[v] >= at for v in inputs):
                continue
            if any(c["base"] in tails for c in cols):
                continue
            if any(
                c["base"].startswith("%")
                and p.blocks[c["base"]] == block
                and p.defs[c["base"]] >= c["tail"]["line"]
                for c in cols
            ):
                continue
            out, pairs = [], len(cols) // 2
            partial, sums = [None] * pairs, [None] * pairs
            lo, hi = [[0, 0] for _ in range(pairs)], [[0, 0] for _ in range(pairs)]
            chunks = []

            def flush(pair):
                if partial[pair] is None:
                    return
                wide = emit(out, f"sext <2 x i16> {partial[pair]} to <2 x i32>")
                sums[pair] = (
                    wide if sums[pair] is None else emit(out, f"add <2 x i32> {sums[pair]}, {wide}")
                )
                chunks.append(dict(pair=pair, minimum=lo[pair], maximum=hi[pair]))
                partial[pair] = None
                lo[pair], hi[pair] = [0, 0], [0, 0]

            for k, inp in enumerate(inputs):
                halves = _bytes_of(emit, out, inp)
                for byte in range(4):
                    x = emit(out, f"extractelement <2 x i16> {halves[byte % 2]}, i32 {byte // 2}")
                    dup = _splat(emit, out, x)
                    for pair in range(pairs):
                        weights = [
                            cols[pair * 2 + lane]["chain"][k]["w"][byte] for lane in range(2)
                        ]
                        low = [byte_range(w)[0] for w in weights]
                        high = [byte_range(w)[1] for w in weights]
                        if any(
                            lo[pair][lane] + low[lane] < I16_MIN
                            or hi[pair][lane] + high[lane] > I16_MAX
                            for lane in range(2)
                        ):
                            flush(pair)
                        lo[pair] = [lo[pair][lane] + low[lane] for lane in range(2)]
                        hi[pair] = [hi[pair][lane] + high[lane] for lane in range(2)]
                        if weights == [0, 0]:
                            continue
                        product = emit(
                            out, f"mul <2 x i16> {dup}, <i16 {weights[0]}, i16 {weights[1]}>"
                        )
                        partial[pair] = (
                            product
                            if partial[pair] is None
                            else emit(out, f"add <2 x i16> {partial[pair]}, {product}")
                        )
            for pair in range(pairs):
                flush(pair)
            tails_code = {}
            for o, col in enumerate(cols):
                tail = []
                value = emit(
                    tail,
                    f"extractelement <2 x i32> {sums[o // 2] or 'zeroinitializer'}, i32 {o % 2}",
                )
                tail.append(f"  {col['tail']['name']} = add i32 {col['base']}, {value}")
                tails_code[col["tail"]["line"]] = tail
                for dot in col["chain"]:
                    replace[dot["line"]] = []
            replace.update(tails_code)
            replace[at] = out + replace[at]
            records.append(
                dict(outputs=len(cols), dots=len(cols) * len(inputs), width=capacity, chunks=chunks)
            )
    proof = dict(streamed_dots=sum(r["dots"] for r in records), groups=records)
    return _apply(p.lines, replace), proof


def prefix_pairs(text):
    """v4's greedy bounded prefix pairing, without extending any input's lifetime.

    Repeatedly pairs two chains whose leading dots read the same input words, over the longest
    common prefix (<= 32 dots) whose i16 bound holds, until no pair is found.
    """
    p = Program(text)
    # Tensor input words are never an active accumulator in this family, so the original use
    # counts stay valid while prefixes are replaced.
    require(all(d["inp"] not in p.dots for d in p.dots.values()), "An input word is an accumulator")
    active, replace, records = dict(p.dots), {}, []
    emit = Emitter("prefix")

    def columns():
        accs = {d["acc"] for d in active.values()}
        result = []
        for tail in active.values():
            if tail["name"] in accs:
                continue
            chain, current = [], tail
            while current["block"] == tail["block"]:
                chain.append(current)
                if len(chain) >= 128 or current["acc"] not in active or p.uses[current["acc"]] != 1:
                    break
                current = active[current["acc"]]
            chain.reverse()
            lo = hi = 0
            prefix = []
            for d in chain[:32]:
                low = sum(byte_range(w)[0] for w in d["w"])
                high = sum(byte_range(w)[1] for w in d["w"])
                if lo + low < I16_MIN or hi + high > I16_MAX:
                    break
                lo += low
                hi += high
                prefix.append(d)
            if prefix:
                result.append(dict(chain=prefix, base=prefix[0]["acc"], block=tail["block"]))
        return result

    def pair(a, b):
        count = min(len(a["chain"]), len(b["chain"]))
        ac, bc = a["chain"][:count], b["chain"][:count]
        if a["block"] != b["block"] or any(x["inp"] != y["inp"] for x, y in zip(ac, bc)):
            return False
        at = min(ac[-1]["line"], bc[-1]["line"])
        needed = [a["base"], b["base"], *(d["inp"] for d in ac)]
        if any(v.startswith("%") and p.blocks[v] == a["block"] and p.defs[v] >= at for v in needed):
            return False
        out, total, bounds = [], None, []
        for x, y in zip(ac, bc):
            halves = _bytes_of(emit, out, x["inp"])
            for byte in range(4):
                weights = [x["w"][byte], y["w"][byte]]
                if weights == [0, 0]:
                    continue
                component = emit(
                    out, f"extractelement <2 x i16> {halves[byte % 2]}, i32 {byte // 2}"
                )
                dup = _splat(emit, out, component)
                product = emit(out, f"mul <2 x i16> {dup}, <i16 {weights[0]}, i16 {weights[1]}>")
                total = product if total is None else emit(out, f"add <2 x i16> {total}, {product}")
        wide = emit(out, f"sext <2 x i16> {total or 'zeroinitializer'} to <2 x i32>")
        for lane, (col, chain) in enumerate([(a, ac), (b, bc)]):
            part = emit(out, f"extractelement <2 x i32> {wide}, i32 {lane}")
            out.append(f"  {chain[-1]['name']} = add i32 {col['base']}, {part}")
            bounds.append(
                [
                    sum(byte_range(w)[0] for d in chain for w in d["w"]),
                    sum(byte_range(w)[1] for d in chain for w in d["w"]),
                ]
            )
            for d in chain:
                del active[d["name"]]
                replace[d["line"]] = []
        replace[at] = out
        records.append(dict(dots=2 * count, bounds=bounds))
        return True

    for _ in range(4096):
        candidates = columns()
        if not any(pair(a, b) for i, a in enumerate(candidates) for b in candidates[i + 1 :]):
            break
    else:
        raise ValueError("Prefix pairing growth limit")
    proof = dict(
        paired_dots=sum(r["dots"] for r in records),
        pairs=records,
        remaining_constant_dots=len(active),
    )
    return _apply(p.lines, replace), proof


def half_groups(text):
    """Chains of <= 16 dots in two i16 lanes (bytes 0+1 and 2+3), newest tail first.

    A dot whose own two-byte bound does not fit gets v4's exact biased form: two products of
    signed bytes sum to [-32512, 32768], so +32512 mod 2**16 is exact as unsigned; widening and
    subtracting 65024 recovers the sum.
    """
    p = Program(text)
    active, replace, records = dict(p.dots), {}, []
    emit = Emitter("halfgroup")
    while active:
        tail = next(reversed(active.values()))
        current, chain, lo, hi = tail, [], [0, 0], [0, 0]
        while current["block"] == tail["block"] and len(chain) < 16:
            lows = [
                sum(byte_range(w)[0] for w in current["w"][k * 2 : k * 2 + 2]) for k in range(2)
            ]
            highs = [
                sum(byte_range(w)[1] for w in current["w"][k * 2 : k * 2 + 2]) for k in range(2)
            ]
            if any(lo[k] + lows[k] < I16_MIN or hi[k] + highs[k] > I16_MAX for k in range(2)):
                break
            lo = [lo[k] + lows[k] for k in range(2)]
            hi = [hi[k] + highs[k] for k in range(2)]
            chain.append(current)
            if current["acc"] not in active or p.uses[current["acc"]] != 1:
                break
            current = active[current["acc"]]
        biased = not chain
        if biased:
            chain = [tail]
        out, total = [], None
        for d in chain:
            even, odd = _bytes_of(emit, out, d["inp"])
            even = emit(out, f"mul <2 x i16> {even}, <i16 {d['w'][0]}, i16 {d['w'][2]}>")
            odd = emit(out, f"mul <2 x i16> {odd}, <i16 {d['w'][1]}, i16 {d['w'][3]}>")
            partial = emit(out, f"add <2 x i16> {even}, {odd}")
            total = partial if total is None else emit(out, f"add <2 x i16> {total}, {partial}")
        if biased:
            total = emit(out, f"add <2 x i16> {total}, <i16 32512, i16 32512>")
        wide = emit(out, f"{'zext' if biased else 'sext'} <2 x i16> {total} to <2 x i32>")
        a = emit(out, f"extractelement <2 x i32> {wide}, i32 0")
        b = emit(out, f"extractelement <2 x i32> {wide}, i32 1")
        summed = emit(out, f"add i32 {a}, {b}")
        if biased:
            summed = emit(out, f"add i32 {summed}, -65024")
        out.append(f"  {tail['name']} = add i32 {chain[-1]['acc']}, {summed}")
        for d in chain:
            del active[d["name"]]
            replace[d["line"]] = []
        replace[tail["line"]] = out
        records.append(dict(dots=len(chain), biased=biased, minimum=lo, maximum=hi))
    return _apply(p.lines, replace), dict(converted_dots=len(p.dots), groups=records)


def packed_reductions(text, mode="halves"):
    """Remaining constant chains: output pairs with equal inputs ("paired"), else byte halves.

    Native <2 x i16> vectors only (the forms RC7-RC11 use); a weight vector whose bound cannot be
    narrowed is computed in i32.
    """
    require(mode in ("paired", "halves"), "Unsupported packing mode")
    p = Program(text)
    columns = p.columns()
    replacements, paired, records = {}, set(), []
    emit = Emitter("packed")

    def extract(out, word):
        even, odd = _bytes_of(emit, out, word)
        return [
            emit(out, f"extractelement <2 x i16> {v}, i32 {component}")
            for component in range(2)
            for v in [even, odd]
        ]

    def reduction(cols, pair_outputs):
        at = min(c["tail"]["line"] for c in cols)
        needed = [v for c in cols for v in [c["base"], *c["inputs"]]]
        if any(
            v.startswith("%") and p.blocks[v] == cols[0]["tail"]["block"] and p.defs[v] >= at
            for v in needed
        ):
            return False
        out = []
        state = dict(bases=[c["base"] for c in cols], lo=[0, 0], hi=[0, 0], count=0, vector=None)
        groups = []

        def vector_term(xs, ws):
            if ws == [0, 0]:
                return
            pair = emit(out, f"insertelement <2 x i16> undef, i16 {xs[0]}, i32 0")
            pair = emit(out, f"insertelement <2 x i16> {pair}, i16 {xs[1]}, i32 1")
            product = emit(out, f"mul <2 x i16> {pair}, <i16 {ws[0]}, i16 {ws[1]}>")
            state["vector"] = (
                product
                if state["vector"] is None
                else emit(out, f"add <2 x i16> {state['vector']}, {product}")
            )

        def flush():
            if not state["count"]:
                return
            wide_vec = emit(
                out, f"sext <2 x i16> {state['vector'] or 'zeroinitializer'} to <2 x i32>"
            )
            wide = [emit(out, f"extractelement <2 x i32> {wide_vec}, i32 {i}") for i in range(2)]
            bases = state["bases"]
            if pair_outputs:
                state["bases"] = [emit(out, f"add i32 {bases[k]}, {wide[k]}") for k in range(2)]
            else:
                s = emit(out, f"add i32 {wide[0]}, {wide[1]}")
                state["bases"] = [emit(out, f"add i32 {bases[0]}, {s}")]
            groups.append(dict(terms=state["count"], minimum=state["lo"], maximum=state["hi"]))
            state.update(lo=[0, 0], hi=[0, 0], count=0, vector=None)

        for index, da in enumerate(cols[0]["chain"]):
            ds = [c["chain"][index] for c in cols]
            terms = [d["w"] for d in ds] if pair_outputs else [da["w"][:2], da["w"][2:]]
            low = [sum(byte_range(w)[0] for w in ws) for ws in terms]
            high = [sum(byte_range(w)[1] for w in ws) for ws in terms]
            if any(
                state["lo"][k] + low[k] < I16_MIN or state["hi"][k] + high[k] > I16_MAX
                for k in range(2)
            ):
                flush()
            narrow = all(low[k] >= I16_MIN and high[k] <= I16_MAX for k in range(2))
            inputs = extract(out, da["inp"])
            if narrow:
                if pair_outputs:
                    for byte, x in enumerate(inputs):
                        vector_term([x, x], [ds[0]["w"][byte], ds[1]["w"][byte]])
                else:
                    for byte in range(2):
                        vector_term(
                            [inputs[byte], inputs[byte + 2]], [da["w"][byte], da["w"][byte + 2]]
                        )
                state["lo"] = [state["lo"][k] + low[k] for k in range(2)]
                state["hi"] = [state["hi"][k] + high[k] for k in range(2)]
                state["count"] += 1
            else:
                # A single unusually large weight vector cannot be narrowed.
                for byte, x in enumerate(inputs):
                    x = emit(out, f"sext i16 {x} to i32")
                    for k, d in enumerate(ds):
                        product = emit(out, f"mul i32 {x}, {d['w'][byte]}")
                        state["bases"][k] = emit(out, f"add i32 {state['bases'][k]}, {product}")
        flush()
        for k, c in enumerate(cols):
            out.append(f"  {c['tail']['name']} = add i32 {state['bases'][k]}, 0")
        for c in cols:
            for d in c["chain"]:
                replacements[d["line"]] = []
        replacements[at] = out
        records.append(
            dict(paired=pair_outputs, dots=sum(len(c["chain"]) for c in cols), groups=groups)
        )
        return True

    if mode == "paired":
        for i, a in enumerate(columns):
            if i in paired:
                continue
            for j in range(i + 1, len(columns)):
                b = columns[j]
                if (
                    j in paired
                    or a["inputs"] != b["inputs"]
                    or a["tail"]["block"] != b["tail"]["block"]
                ):
                    continue
                if reduction([a, b], True):
                    paired.update([i, j])
                    break
    for i, c in enumerate(columns):
        if i not in paired:
            require(reduction([c], False), "A remaining chain's operands do not dominate its tail")
    proof = dict(
        mode=mode,
        converted_dots=len(p.dots),
        paired_dots=sum(r["dots"] for r in records if r["paired"]),
        reductions=records,
    )
    return _apply(p.lines, replacements), proof
