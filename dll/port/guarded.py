# SPDX-License-Identifier: MIT
"""Guarded weight specialisation and the RC8/RC9 arithmetic of the guarded model passes 7, 8, 9.

The guarded passes read their weights from the `InitializerBuffer` (t18) at run time. RC7's
`specialize` copies the 128 KiB model table the SDK binds there into the program as constants,
behind a guard: dispatch z == 0 and every specialised word of the bound buffer equal to the copy
(`waveAllTrue` over a strided compare loop). The fast path is the SDK body with those constants,
unrolled and folded; the fallback is the SDK body unchanged. Later stages edit only the fast path:

| Stage | Origin | Does |
| --- | --- | --- |
| `specialize` | RC7 `specialize_weights.py` + `build_specialized.py` | guard + constant copy, inline, expand the fast path's fixed-trip loops, fold |
| `point_small` | RC9 `p78_point_small.py`, `p9_point_small.py` | 16-word pointwise chains in batches of `width` outputs: exact matched lane pairs, bin-packed signed-byte i16 chunks |
| `native_stream` | RC8 `stream_selected.py` | `packing.stream_outputs` over every remaining literal chain (lengths 4/8/16 and `extra_lengths`) |
| `mixed_float` | RC8 `lower_mixed_float.py` | the remaining interval-proven literal chains as exact FP32 MADs (|values| <= 2**24) |
| `vector_guard` | RC8 `build_vector_guards.py` | the guard's four scalar table loads as one `<4 x i32>` load (SM 6.9) |
| `group_guard` | RC8 `build_group_guard.py` | Wave32: the 64-thread group checks the table once, flags shared through group memory |

Every stage checks that the guard prefix and the complete dynamic fallback stay byte-identical,
and that only dot4 instructions of the fast path change (`point_small`, `native_stream`) or only
the interval-proven literal graph (`mixed_float`). Weights and plans are derived from the program
on every run; nothing is pinned to an SDK program hash.
"""

import collections
import hashlib
import re
import struct
from functools import lru_cache

if __package__:
    from . import drop_profile, packing
    from .ir import DOT, LoweringError, canonical_ssa, require, signed_bytes
    from .unroll import UNROLL_PASSES, expand_loops, fold_mads
else:
    import drop_profile
    import packing
    from ir import DOT, LoweringError, canonical_ssa, require, signed_bytes
    from unroll import UNROLL_PASSES, expand_loops, fold_mads

I16_MIN, I16_MAX = -32768, 32767
FP32_EXACT = 2**24
INITIALIZER = "%dx.types.ResBind { i32 18, i32 18, i32 0, i8 0 }"
# mov eax, imm32 ; lea rcx, [rip + disp32]: the SDK's initializer accessor returns the table
# pointer in rcx and its size in eax.
ACCESSOR = re.compile(rb"\xb8(....)\x48\x8d\x0d(....)", re.S)
GROWTH_LIMIT = 16 * 1024 * 1024


def sha(text):
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


# Model table


def model_table(data, rva, size, digest):
    """The initializer table an SDK accessor function returns (`mov eax, size; lea rcx, [rip+d]`).

    `rva` is the accessor's first instruction (a host-code site, like the profile's patch sites);
    the table's size and SHA256 must equal the recipe's.
    """
    image = drop_profile.Image(data)
    at = image.to_offset(rva)
    m = ACCESSOR.match(data, at)
    require(m is not None, "No initializer accessor at the given RVA")
    require(struct.unpack("<I", m[1])[0] == size, "The accessor returns another table size")
    target = image.to_offset(rva + 12 + struct.unpack("<i", m[2])[0])
    require(target + size <= len(data), "Initializer table outside the image")
    table = bytes(data[target : target + size])
    require(sha(table) == digest, "The initializer table differs from the recipe's")
    return table, dict(accessor_rva=rva, offset=target, bytes=size, sha256=digest)


# RC7: guarded weight specialisation


def _constant(v, values):
    if re.fullmatch(r"-?\d+", v):
        return int(v) & 0xFFFFFFFF
    return values.get(v)


def _arith(kind, av, bv):
    signed = av if av < 0x80000000 else av - 0x100000000
    return {
        "add": lambda: av + bv,
        "sub": lambda: av - bv,
        "mul": lambda: av * bv,
        "shl": lambda: av << bv,
        "lshr": lambda: av >> bv,
        "ashr": lambda: signed >> bv,
        "and": lambda: av & bv,
        "or": lambda: av | bv,
        "xor": lambda: av ^ bv,
    }[kind]()


def _index_domains(lines, values):
    """Finite domains of initializer indices, from bounded unit-step induction (RC7)."""
    definitions, block_of, branches, block = {}, {}, {}, "v0"
    for line in lines:
        if m := re.match(r"^(v\d+):", line):
            block = m[1]
        if m := re.match(r"  (%v\d+) = (.*)", line):
            definitions[m[1]] = m[2]
            block_of[m[1]] = block
        if line.startswith("  br "):
            branches[block] = line
    loops = {}
    for v, rhs in definitions.items():
        if not rhs.startswith("phi i32 "):
            continue
        pairs = re.findall(r"\[ ([^,]+), %(v\d+) \]", rhs)
        if len(pairs) != 2:
            continue
        literal = [(x, b) for x, b in pairs if re.fullmatch(r"-?\d+", x)]
        carried = [(x, b) for x, b in pairs if not re.fullmatch(r"-?\d+", x)]
        if len(literal) != 1 or len(carried) != 1:
            continue
        initial = int(literal[0][0])
        increment, latch = carried[0]
        if not re.fullmatch(
            r"add(?: nuw| nsw)* i32 " + re.escape(v) + r", 1", definitions.get(increment, "")
        ):
            continue
        header = block_of[v]
        # The phi has one backedge; its tested unit-step increment limits every trip even when
        # the body contains nested loops or earlier exits.
        if header not in re.findall(r"label %(v\d+)", branches.get(latch, "")):
            continue
        test = branches.get(block_of[increment], "")
        bm = re.match(r"  br i1 (%v\d+), label %(v\d+), label %(v\d+)", test)
        if not bm:
            continue
        cm = re.fullmatch(r"icmp (eq|ne) i32 (%v\d+), ([^ ;]+)", definitions.get(bm[1], ""))
        if not cm or cm[2] not in {increment, v}:
            continue
        continuation = bm[3] if cm[1] == "eq" else bm[2]
        if continuation not in {header, latch}:
            continue
        bound = _constant(cm[3], values)
        if bound is None:
            continue
        trip = bound - initial + (cm[2] == v)
        if 0 <= initial <= bound and 1 <= trip <= 32:
            loops[header] = (trip, latch)
    domains = {v: {value} for v, value in values.items()}
    for v, rhs in definitions.items():
        header = block_of[v]
        if header not in loops or not rhs.startswith("phi i32 "):
            continue
        trip, latch = loops[header]
        pairs = re.findall(r"\[ ([^,]+), %(v\d+) \]", rhs)
        initial = [x for x, b in pairs if b != latch]
        carried = [x for x, b in pairs if b == latch]
        if len(initial) != 1 or len(carried) != 1:
            continue
        start = _constant(initial[0], values)
        inc = re.fullmatch(
            r"add(?: nuw| nsw)* i32 " + re.escape(v) + r", (-?\d+)", definitions.get(carried[0], "")
        )
        if start is not None and inc:
            stride = int(inc[1])
            vals = {start + i * stride for i in range(trip)}
            if all(0 <= x <= 0xFFFFFFFF for x in vals):
                domains[v] = vals
    active = set()

    def domain(v):
        if re.fullmatch(r"-?\d+", v):
            return {int(v) & 0xFFFFFFFF}
        if v in domains:
            return domains[v]
        if v in active:
            return None
        active.add(v)
        rhs = definitions.get(v, "")
        result = None
        if op := re.match(
            r"(add|sub|mul|shl|lshr|ashr|and|or|xor)(?: nuw| nsw| exact)* i32 ([^,]+), ([^ ;]+)",
            rhs,
        ):
            kind, a, b = op.groups()
            aa, bb = domain(a), domain(b)
            if aa is not None and bb is not None and len(aa) * len(bb) <= 65536:
                result = set()
                for av in aa:
                    for bv in bb:
                        if kind in ("shl", "lshr", "ashr") and bv >= 32:
                            result = None
                            break
                        result.add(_arith(kind, av, bv) & 0xFFFFFFFF)
                    if result is None:
                        break
        elif mad := re.match(
            r"call i32 @dx.op.tertiary.i32\(i32 (?:48|49), i32 ([^,]+), i32 ([^,]+), i32 ([^)]+)\)",
            rhs,
        ):
            aa, bb, cc = [domain(x) for x in mad.groups()]
            if all(x is not None for x in [aa, bb, cc]) and len(aa) * len(bb) * len(cc) <= 65536:
                result = {(a * b + c) & 0xFFFFFFFF for a in aa for b in bb for c in cc}
        active.remove(v)
        if result is not None and len(result) > 32768:
            result = None
        domains[v] = result
        return result

    return domain, loops


def specialize_text(source, model):
    """RC7's guarded specialisation of an SDK program (`specialize_weights.specialize`).

    Every initializer read with a constant or bounded-induction address becomes the model word;
    the fast body runs only for z == 0 when every specialised word of the bound buffer equals the
    copy; otherwise the original body runs.
    """
    m = re.search(r"^define void @([^\(]+)\(\) \{\n(.*?)^\}", source, re.M | re.S)
    require(m, "Expected one compute entry")
    entry, body = m[1], m[2]
    require(
        "@bc250_" not in source and "%bc250_" not in source and "%bcw_" not in source,
        "Reserved names already used",
    )
    fast = body.splitlines()
    roots = set(
        re.findall(
            r"(%v\d+) = call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, "
            + re.escape(INITIALIZER),
            body,
        )
    )
    require(len(roots) == 1, "Unexpected InitializerBuffer binding")
    handles = set(roots)
    values = {}
    for i, line in enumerate(fast):
        if z := re.match(
            r"  (%v\d+) = call i32 @dx.op.(?:threadId|groupId).i32\(i32 (?:93|94), i32 2\)", line
        ):
            fast[i] = f"  {z[1]} = add i32 0, 0"
            values[z[1]] = 0
    loads, words = {}, set()
    for _ in range(20):
        changed = False
        for n, line in enumerate(fast):
            if ann := re.match(
                r"  (%v\d+) = call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%v\d+),",
                line,
            ):
                if ann[2] in handles:
                    handles.add(ann[1])
            if load := re.match(
                r"  (%v\d+) = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle (%v\d+), i32 ([^,]+),",
                line,
            ):
                if load[2] in handles:
                    loads[load[1]] = load[3]
            if extract := re.match(
                r"  (%v\d+) = extractvalue %dx.types.ResRet.i32 (%v\d+), ([0-4])", line
            ):
                v, load, index = extract[1], extract[2], int(extract[3])
                offset = _constant(loads[load], values) if load in loads else None
                if offset is not None and index < 4:
                    at = offset + 4 * index
                    require(
                        at % 4 == 0 and at + 4 <= len(model), "Initializer address out of bounds"
                    )
                    value = struct.unpack_from("<i", model, at)[0]
                    fast[n] = f"  {v} = add i32 {value}, 0"
                    words.add(at // 4)
                    values[v] = value & 0xFFFFFFFF
                    changed = True
            if op := re.match(
                r"  (%v\d+) = (add|sub|mul|shl|lshr|ashr|and|or|xor)(?: nuw| nsw| exact)* i32 ([^,]+), ([^ ;]+)",
                line,
            ):
                v, kind, a, b = op.groups()
                av, bv = _constant(a, values), _constant(b, values)
                if v not in values and (
                    (kind in ("mul", "and") and (av == 0 or bv == 0))
                    or (kind in ("xor", "sub") and a == b)
                ):
                    values[v] = 0
                    changed = True
                    continue
                if av is not None and bv is not None and v not in values:
                    require(not (kind in ("shl", "lshr", "ashr") and bv >= 32), "Invalid shift")
                    values[v] = _arith(kind, av, bv) & 0xFFFFFFFF
                    changed = True
            if mad := re.match(
                r"  (%v\d+) = call i32 @dx.op.tertiary.i32\(i32 (?:48|49), i32 ([^,]+), i32 ([^,]+), i32 ([^)]+)\)",
                line,
            ):
                v, a, b, c = mad.groups()
                args = [_constant(x, values) for x in (a, b, c)]
                if v not in values and (args[0] == 0 or args[1] == 0) and args[2] is not None:
                    values[v] = args[2]
                    changed = True
                    continue
                if all(x is not None for x in args) and v not in values:
                    values[v] = (args[0] * args[1] + args[2]) & 0xFFFFFFFF
                    changed = True
        if not changed:
            break
    domain, induction_loops = _index_domains(fast, values)
    dynamic_reads = []
    for n, line in enumerate(fast):
        extract = re.match(r"  (%v\d+) = extractvalue %dx.types.ResRet.i32 (%v\d+), ([0-3])", line)
        if not extract or extract[2] not in loads:
            continue
        v, load, component = extract[1], extract[2], int(extract[3])
        index = loads[load]
        offsets = domain(index)
        if offsets is None:
            continue
        addresses = {at + 4 * component for at in offsets}
        require(
            not any(at % 4 or at < 0 or at + 4 > len(model) for at in addresses),
            "Initializer domain out of bounds",
        )
        words.update(at // 4 for at in addresses)
        dynamic_reads.append(
            dict(
                line=n,
                value=v,
                index=index,
                component=component,
                minimum=min(addresses),
                maximum=max(addresses),
            )
        )
    require(words, "No initializer words specialized")
    start = min(words) & ~3
    end = (max(words) + 4) // 4 * 4
    count = end - start
    for read in dynamic_reads:
        suffix = read["value"][1:]
        fast[read["line"]] = f"""  %bcw_byte_{suffix} = sub i32 {read["index"]}, {start * 4}
  %bcw_word_{suffix} = lshr i32 %bcw_byte_{suffix}, 2
  %bcw_index_{suffix} = add i32 %bcw_word_{suffix}, {read["component"]}
  %bcw_ptr_{suffix} = getelementptr [{count} x i32], [{count} x i32]* @bc250_expected, i32 0, i32 %bcw_index_{suffix}
  {read["value"]} = load i32, i32* %bcw_ptr_{suffix}, align 4"""
    table = struct.unpack_from("<" + "i" * count, model, start * 4)
    global_def = (
        "@bc250_expected = internal unnamed_addr constant ["
        + str(count)
        + " x i32] ["
        + ", ".join("i32 " + str(v) for v in table)
        + "], align 16\n"
    )
    attr = 1 + max(int(x) for x in re.findall(r"attributes #(\d+) =", source))
    wrapper = f"""define void @{entry}() {{
bc250_entry:
  %bc250_z = call i32 @dx.op.threadId.i32(i32 93, i32 2)
  %bc250_zero = icmp eq i32 %bc250_z, 0
  br i1 %bc250_zero, label %bc250_guard_start, label %bc250_slow
bc250_guard_start:
  %bc250_raw = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind {{ i32 18, i32 18, i32 0, i8 0 }}, i32 18, i1 false)
  %bc250_handle = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %bc250_raw, %dx.types.ResourceProperties {{ i32 11, i32 0 }})
  %bc250_lane = call i32 @dx.op.waveGetLaneIndex(i32 111)
  %bc250_width = call i32 @dx.op.waveGetLaneCount(i32 112)
  %bc250_first = shl i32 %bc250_lane, 2
  %bc250_stride = shl i32 %bc250_width, 2
  br label %bc250_guard_loop
bc250_guard_loop:
  %bc250_index = phi i32 [ %bc250_first, %bc250_guard_start ], [ %bc250_next, %bc250_guard_body ]
  %bc250_ok = phi i1 [ true, %bc250_guard_start ], [ %bc250_matches3, %bc250_guard_body ]
  %bc250_more = icmp ult i32 %bc250_index, {count}
  br i1 %bc250_more, label %bc250_guard_body, label %bc250_guard_end
bc250_guard_body:
  %bc250_word = add i32 %bc250_index, {start}
  %bc250_byte = shl i32 %bc250_word, 2
  %bc250_actual = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bc250_handle, i32 %bc250_byte, i32 undef, i8 15, i32 4)
"""
    for i in range(4):
        previous = "%bc250_ok" if i == 0 else "%bc250_matches" + str(i - 1)
        wrapper += f"""  %bc250_at{i} = add i32 %bc250_index, {i}
  %bc250_ptr{i} = getelementptr [{count} x i32], [{count} x i32]* @bc250_expected, i32 0, i32 %bc250_at{i}
  %bc250_expected{i} = load i32, i32* %bc250_ptr{i}, align 4
"""
        wrapper += f"""
  %bc250_value{i} = extractvalue %dx.types.ResRet.i32 %bc250_actual, {i}
  %bc250_eq{i} = icmp eq i32 %bc250_value{i}, %bc250_expected{i}
  %bc250_matches{i} = and i1 {previous}, %bc250_eq{i}
"""
    wrapper += (
        f"""  %bc250_next = add i32 %bc250_index, %bc250_stride
  br label %bc250_guard_loop
bc250_guard_end:
  %bc250_all = call i1 @dx.op.waveAllTrue(i32 114, i1 %bc250_ok)
  br i1 %bc250_all, label %bc250_fast, label %bc250_slow
bc250_fast:
  call void @bc250_fast_body()
  br label %bc250_done
bc250_slow:
  call void @bc250_original_body()
  br label %bc250_done
bc250_done:
  ret void
}}
define internal void @bc250_fast_body() #{attr} {{
"""
        + "\n".join(fast)
        + f"\n}}\ndefine internal void @bc250_original_body() #{attr} {{\n"
        + body
        + "}\n"
    )
    declarations = f"""declare i32 @dx.op.waveGetLaneIndex(i32) #{attr + 1}
declare i32 @dx.op.waveGetLaneCount(i32) #{attr + 2}
declare i1 @dx.op.waveAllTrue(i32, i1) #{attr + 3}
attributes #{attr} = {{ alwaysinline nounwind }}
attributes #{attr + 1} = {{ nounwind readonly }}
attributes #{attr + 2} = {{ nounwind readnone }}
attributes #{attr + 3} = {{ nounwind }}
"""
    code = source[: m.start()] + global_def + "\n" + wrapper + source[m.end() :] + declarations
    code = re.sub(r"(!\d+ = !\{i32 0, i64 )(\d+)", lambda m: m[1] + str(int(m[2]) | 524288), code)
    record = dict(
        entry=entry,
        model_sha256=sha(model),
        specialized_words=len(words),
        guard_start_byte=start * 4,
        guard_bytes=count * 4,
        dynamic_initializer_reads=len(dynamic_reads),
        induction_loops=len(induction_loops),
        z_guard=True,
        full_specialized_word_guard=True,
        guard_table_sha256=sha(model[start * 4 : end * 4]),
    )
    return code, record


def _optimize(text, toolchain, inline=False):
    """`build_specialized.optimize`: opt, then fold MADs and re-run opt until no fold (<= 8)."""
    passes = (["-always-inline"] if inline else []) + list(UNROLL_PASSES)
    text = toolchain.opt(text, passes)
    for _ in range(8):
        text, count = fold_mads(text)
        if not count:
            break
        text = toolchain.opt(text, passes)
    return text


def _fast_region(text):
    """Blocks reachable from bc250_fast but not from bc250_slow (RC7's expansion region)."""
    bodies, current = {}, None
    for line in text.splitlines():
        if m := re.match(r"^([A-Za-z0-9_.$-]+):", line):
            current = m[1]
            bodies[current] = []
        elif current is not None:
            if line == "}":
                current = None
            else:
                bodies[current].append(line)
    successors = {
        name: set(re.findall(r"label %([A-Za-z0-9_.$-]+)", "\n".join(body)))
        for name, body in bodies.items()
    }
    require("bc250_fast" in bodies and "bc250_slow" in bodies, "Guard blocks missing")

    def reach(start):
        todo, seen = [start], set()
        while todo:
            item = todo.pop()
            if item not in seen:
                seen.add(item)
                todo.extend(successors[item])
        return seen

    return reach("bc250_fast") - reach("bc250_slow")


def specialize(text, toolchain, model):
    """SDK disassembly -> RC7 guarded basis: specialise, inline, expand fast-path loops, fold."""
    text, record = specialize_text(text, model)
    text = _optimize(text, toolchain, inline=True)
    loops = []
    for _ in range(4):
        text, found = expand_loops(text, headers=_fast_region(text))
        if not found:
            break
        loops.append(len(found))
        text = _optimize(text, toolchain)
        text = _optimize(text, toolchain)
        require(len(text) <= GROWTH_LIMIT, "Specialization growth limit")
    require(loops, "No fast-path loops expanded")
    text = canonical_ssa(text)
    require(not fold_mads(text)[1], "Foldable integer MADs remain")
    record.update(expansion_rounds=loops, dots=sum(bool(DOT.match(x)) for x in text.splitlines()))
    return text, record


# Guard structure


def literal_dot(line):
    """The DOT match of a dot4 with one literal operand and one value operand, else None."""
    m = DOT.match(line)
    if not m:
        return None
    _, _, a, b = m.groups()
    if re.fullmatch(r"-?\d+", a):
        a, b = b, a
    return m if a.startswith("%") and re.fullmatch(r"-?\d+", b) else None


def guard_audit(text):
    """The single model vote and its targets; literal dots only under the fast target.

    Every literal dot4 must be dominated by the vote's true target and not by its false target,
    every other dot4 the reverse (the fallback is the SDK body).
    """
    votes = re.findall(r"(%v\d+) = call i1 @dx.op.waveAllTrue\(i32 114, i1 %v\d+\)", text)
    require(len(votes) == 1, "Expected one model-guard vote")
    branch = re.search(r"br i1 " + re.escape(votes[0]) + r", label %(v\d+), label %(v\d+)", text)
    require(branch, "The vote does not branch")
    fast, fallback = branch.groups()
    functions = re.findall(r"^define void @\w+\([^\n]*\) [^{]*\{\n(.*?)\n\}", text, re.S | re.M)
    require(len(functions) == 1, "Expected one function")
    blocks, dots, block = {}, [], None
    for line in functions[0].splitlines():
        if m := re.fullmatch(r"(v\d+):", line):
            block = m[1]
            require(block not in blocks, "Duplicate block")
            blocks[block] = []
        if not block:
            require(not line.strip(), "Code before the first block")
            continue
        if line.strip().startswith("br "):
            blocks[block].extend(re.findall(r"label %(v\d+)", line))
        if DOT.match(line):
            dots.append(dict(block=block, literal=bool(literal_dot(line))))
    entry = next(iter(blocks))
    predecessors = {name: set() for name in blocks}
    for name, successors in blocks.items():
        for successor in successors:
            require(successor in blocks, "Branch to an unknown block")
            predecessors[successor].add(name)
    reached, pending = set(), [entry]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(blocks[name])
    require(reached == set(blocks), "Unreachable blocks")
    dominators = {name: ({entry} if name == entry else set(blocks)) for name in blocks}
    changed = True
    while changed:
        changed = False
        for name in blocks:
            if name == entry:
                continue
            value = {name} | set.intersection(*(dominators[p] for p in predecessors[name]))
            if value != dominators[name]:
                dominators[name], changed = value, True
    for dot in dots:
        root, excluded = (fast, fallback) if dot["literal"] else (fallback, fast)
        require(
            root in dominators[dot["block"]] and excluded not in dominators[dot["block"]],
            "A dot4 is outside its guard region",
        )
    return dict(
        fast_entry=fast,
        fallback_entry=fallback,
        guard_vote=votes[0],
        blocks=len(blocks),
        literal_dots=sum(d["literal"] for d in dots),
        dynamic_dots=sum(not d["literal"] for d in dots),
        guard_prefix_sha256=sha(prefix(text, fast)),
        fallback_tail_sha256=sha(fallback_tail(text, fallback)),
    )


def prefix(text, fast):
    return text[: text.index(fast + ":\n")]


def fallback_tail(text, fallback):
    marker = fallback + ":\n"
    return text[text.index(marker) : text.index("\n}\n", text.index(marker))]


def require_guard_kept(before, after, audit):
    require(
        prefix(after, audit["fast_entry"]) == prefix(before, audit["fast_entry"]),
        "The guard prefix changed",
    )
    require(
        fallback_tail(after, audit["fallback_entry"])
        == fallback_tail(before, audit["fallback_entry"]),
        "The dynamic fallback changed",
    )


# Literal chains (RC9 lower_partial_float.columns with literal dots only)


def literal_columns(text):
    lines = text.splitlines()
    definitions, uses, dots, block = {}, collections.Counter(), {}, None
    for n, line in enumerate(lines):
        if re.fullmatch(r"v\d+:", line):
            block = line[:-1]
        values = re.findall(r"%v\d+", line.split(";")[0])
        if line.startswith("  %v") and " = " in line:
            definitions[values[0]] = n
            values = values[1:]
        uses.update(values)
        if m := literal_dot(line):
            name, acc, inp, packed = m.groups()
            if re.fullmatch(r"-?\d+", inp):
                inp, packed = packed, inp
            dots[name] = dict(
                name=name,
                line=n,
                block=block,
                acc=acc,
                input=inp,
                weights=signed_bytes(int(packed)),
            )
    rows, seen = [], set()
    for tail in reversed(list(dots.values())):
        if tail["name"] in seen:
            continue
        chain, value = [], tail
        while value["block"] == tail["block"] and value["name"] not in seen:
            chain.append(value)
            seen.add(value["name"])
            if value["acc"] not in dots or uses[value["acc"]] != 1:
                break
            value = dots[value["acc"]]
        chain.reverse()
        rows.append(
            dict(
                tail=tail["name"],
                line=tail["line"],
                block=tail["block"],
                chain=chain,
                base=chain[0]["acc"],
            )
        )
    require(seen == set(dots), "A literal dot is in no chain")
    return sorted(rows, key=lambda row: row["line"]), definitions


def point_groups(text, length):
    """16-output groups of literal chains of `length` words sharing their inputs (RC9 plan)."""
    rows, definitions = literal_columns(text)
    grouped = collections.defaultdict(list)
    for row in rows:
        if len(row["chain"]) == length:
            inputs = tuple(dot["input"] for dot in row["chain"])
            grouped[row["block"], inputs].append(row)
    result = []
    for (block, inputs), group in grouped.items():
        require(len(group) % 16 == 0, "A pointwise stage is not a multiple of 16 outputs")
        require(len(set(inputs)) == len(inputs), "A pointwise chain reads a word twice")
        for start in range(0, len(group), 16):
            selected = group[start : start + 16]
            first = min(row["line"] for row in selected)
            require(all(definitions[v] < first for v in inputs), "Input defined after the group")
            tails = {row["tail"] for row in selected}
            require(not (tails & set(inputs)), "A tail is an input of its own group")
            require(all(row["base"] not in tails for row in selected), "A base is a group tail")
            require(
                all(
                    not row["base"].startswith("%") or definitions[row["base"]] < row["line"]
                    for row in selected
                ),
                "Late base",
            )
            result.append(
                dict(
                    block=block,
                    inputs=list(inputs),
                    rows=selected,
                    stage_output_start=start,
                    stage_outputs=len(group),
                )
            )
    return result


# RC9: small pointwise batches


def _byte_piece(value):
    require(-128 <= value <= 127, "Not a signed byte")
    return [value]


def pack_pair(weights, domain, left, right):
    """RC9 native_point_bins.pack_pair with signed-byte pieces (one product per weight)."""
    require(domain[0] <= 0 <= domain[1], "Domain must contain zero")
    tasks = []
    for term in range(len(weights[0])):
        parts = [_byte_piece(int(weights[row][term])) for row in [left, right]]
        for piece in range(max(map(len, parts))):
            coefficient = [values[piece] if piece < len(values) else 0 for values in parts]
            if not any(coefficient):
                continue
            low = [min(value * domain[0], value * domain[1]) for value in coefficient]
            high = [max(value * domain[0], value * domain[1]) for value in coefficient]
            require(min(low) >= I16_MIN and max(high) <= I16_MAX, "A product overflows i16")
            tasks.append(
                {
                    "term": term,
                    "piece": piece,
                    "weights": coefficient,
                    "minimum": low,
                    "maximum": high,
                }
            )

    def fits(chunk, task):
        return all(
            chunk["minimum"][lane] + task["minimum"][lane] >= I16_MIN
            and chunk["maximum"][lane] + task["maximum"][lane] <= I16_MAX
            for lane in [0, 1]
        )

    def generate(order, sequential):
        bins = []
        for task in order:
            choices = [index for index, chunk in enumerate(bins) if fits(chunk, task)]
            if sequential:
                choices = [len(bins) - 1] if bins and fits(bins[-1], task) else []
            if choices:
                index = min(
                    choices,
                    key=lambda index: (
                        sum(bins[index]["minimum"]) - sum(bins[index]["maximum"]),
                        index,
                    ),
                )
            else:
                index = len(bins)
                bins.append({"tasks": [], "minimum": [0, 0], "maximum": [0, 0]})
            chunk = bins[index]
            chunk["tasks"].append(task)
            chunk["minimum"] = [chunk["minimum"][lane] + task["minimum"][lane] for lane in [0, 1]]
            chunk["maximum"] = [chunk["maximum"][lane] + task["maximum"][lane] for lane in [0, 1]]
        return bins

    def needs(task):
        return [-value for value in task["minimum"]] + task["maximum"]

    maximum_first = sorted(
        tasks, key=lambda t: (-max(needs(t)), -sum(needs(t)), t["term"], t["piece"])
    )
    total_first = sorted(
        tasks, key=lambda t: (-sum(needs(t)), -max(needs(t)), t["term"], t["piece"])
    )
    choices = [generate(tasks, True), generate(maximum_first, False), generate(total_first, False)]
    selected = min(range(len(choices)), key=lambda index: (len(choices[index]), index))
    bins = choices[selected]
    require(
        sorted((t["term"], t["piece"]) for c in bins for t in c["tasks"])
        == sorted((t["term"], t["piece"]) for t in tasks),
        "Bin packing lost a task",
    )
    for lane, row in enumerate([left, right]):
        rebuilt = [
            sum(t["weights"][lane] for c in bins for t in c["tasks"] if t["term"] == term)
            for term in range(len(weights[0]))
        ]
        require(rebuilt == [int(v) for v in weights[row]], "Bin packing lost a coefficient")
    return {
        "bins": bins,
        "products": len(tasks),
        "sequential_flushes": len(choices[0]),
        "strategy": ["sequential", "maximum-first-best-fit", "total-first-best-fit"][selected],
    }


def matching(weights):
    """Exact minimum-cost pairing of a batch's outputs: (i16 chunks, products), then pair list."""
    width = len(weights)
    costs = {
        (left, right): pack_pair(weights, [-128, 127], left, right)
        for left in range(width)
        for right in range(left + 1, width)
    }

    @lru_cache(None)
    def solve(mask):
        if not mask:
            return (0, 0), ()
        bit = mask & -mask
        left, rest = bit.bit_length() - 1, mask ^ bit
        choices = []
        for right in range(left + 1, width):
            if rest & (1 << right):
                partial, pairs = solve(rest ^ (1 << right))
                edge = costs[left, right]
                choices.append(
                    (
                        (partial[0] + len(edge["bins"]), partial[1] + edge["products"]),
                        ((left, right),) + pairs,
                    )
                )
        return min(choices)

    optimum, pairs = solve((1 << width) - 1)
    adjacent = [
        sum(
            len(costs[left, left + 1]["bins"]) if i == 0 else costs[left, left + 1]["products"]
            for left in range(0, width, 2)
        )
        for i in [0, 1]
    ]
    require(tuple(optimum) <= tuple(adjacent), "Matching is worse than adjacent pairs")
    order = [output for pair in pairs for output in pair]
    require(sorted(order) == list(range(width)), "Matching is not a permutation")
    return dict(
        selected_cost=list(optimum),
        adjacent_cost=adjacent,
        pairs=[list(p) for p in pairs],
        output_order=order,
        layouts=[costs[pair] for pair in pairs],
    )


class PointEmitter:
    def __init__(self, group):
        self.group, self.serial, self.lines = group, 0, []

    def emit(self, rhs):
        self.serial += 1
        value = f"%point{self.group}_{self.serial}"
        self.lines.append("  " + value + " = " + rhs)
        return value

    def unpack(self, word):
        packed = self.emit(f"bitcast i32 {word} to <2 x i16>")
        even = self.emit(f"shl <2 x i16> {packed}, <i16 8, i16 8>")
        even = self.emit(f"ashr <2 x i16> {even}, <i16 8, i16 8>")
        odd = self.emit(f"ashr <2 x i16> {packed}, <i16 8, i16 8>")
        return even, odd


def _native_batch(emitter, inputs, weights, match):
    """RC9 p11_point_small.native: matched pairs in i16 lanes, one bin-packed chunk per flush."""
    width = len(weights)
    require(width in (2, 4, 8, 16) and len(inputs) in (32, 64), "Unsupported batch shape")
    require(all(len(row) == len(inputs) for row in weights), "Weights do not match the inputs")
    duplicated, totals, chunks, products = {}, [], [], 0

    def duplicate(term):
        if term not in duplicated:
            value = emitter.emit(f"insertelement <2 x i16> undef, i16 {inputs[term]}, i32 0")
            duplicated[term] = emitter.emit(
                f"insertelement <2 x i16> {value}, i16 {inputs[term]}, i32 1"
            )
        return duplicated[term]

    for pair in range(width // 2):
        plan = pack_pair(weights, [-128, 127], pair * 2, pair * 2 + 1)
        require(plan == match["layouts"][pair], "Bin layout differs from the matching")
        total = None
        for chunk in plan["bins"]:
            partial, low, high = None, [0, 0], [0, 0]
            for task in chunk["tasks"]:
                low = [low[lane] + task["minimum"][lane] for lane in [0, 1]]
                high = [high[lane] + task["maximum"][lane] for lane in [0, 1]]
                require(min(low) >= I16_MIN and max(high) <= I16_MAX, "An i16 partial overflows")
                c = task["weights"]
                value = emitter.emit(
                    f"mul <2 x i16> {duplicate(task['term'])}, <i16 {c[0]}, i16 {c[1]}>"
                )
                partial = (
                    value if partial is None else emitter.emit(f"add <2 x i16> {partial}, {value}")
                )
                products += 1
            require(
                partial is not None and [low, high] == [chunk["minimum"], chunk["maximum"]],
                "Bad chunk",
            )
            wide = emitter.emit(f"sext <2 x i16> {partial} to <2 x i32>")
            total = wide if total is None else emitter.emit(f"add <2 x i32> {total}, {wide}")
            chunks.append(dict(output_pair=pair, minimum=low, maximum=high))
        totals.append(total or "zeroinitializer")
    outputs = [
        emitter.emit(f"extractelement <2 x i32> {totals[o // 2]}, i32 {o % 2}")
        for o in range(width)
    ]
    for row in weights:
        low = sum(min(-128 * w, 127 * w) for w in row)
        high = sum(max(-128 * w, 127 * w) for w in row)
        require(low > -(2**31) and high < 2**31, "A pointwise sum overflows i32")
    require([len(chunks), products] == match["selected_cost"], "Emitted cost differs")
    return outputs, dict(products=products, chunks=len(chunks))


def point_small(text, length=16, width=4, blocks="all", dots=None):
    """RC9 small pointwise sums: `length`-word literal chains, `width` outputs per batch.

    `blocks`: `all` groups, or `first` (only the first block, in program order, holding such
    groups; RC9's P9 `first16`). Each 16-output group is cut into consecutive batches of `width`
    outputs, each with an exact minimum-cost lane pairing; every chain tail keeps its position and
    adds its original base (any i32) to the exact native partial.
    """
    require(blocks in ("all", "first") and width in (2, 4, 8, 16), "Invalid options")
    require("%point" not in text, "Reserved names already used")
    audit = guard_audit(text)
    groups = point_groups(text, length)
    require(groups, "No pointwise groups of that length")
    if blocks == "first":
        first_block = min(groups, key=lambda g: min(r["line"] for r in g["rows"]))["block"]
        groups = [g for g in groups if g["block"] == first_block]
    require(
        all(g["block"] == groups[0]["block"] or blocks == "all" for g in groups), "Mixed blocks"
    )
    batches = []
    for group in groups:
        for start in range(0, 16, width):
            batches.append(
                dict(
                    group,
                    rows=group["rows"][start : start + width],
                    stage_output_start=group["stage_output_start"] + start,
                )
            )
    replaced = sum(len(b["rows"]) * len(b["inputs"]) for b in batches)
    require(dots is None or replaced == dots, f"Expected {dots} pointwise dots, found {replaced}")
    replacements, records = {}, []
    for number, batch in enumerate(batches):
        weights = [[w for dot in row["chain"] for w in dot["weights"]] for row in batch["rows"]]
        match = matching(weights)
        order = match["output_order"]
        emitter, inputs = PointEmitter(number), []
        for word in batch["inputs"]:
            even, odd = emitter.unpack(word)
            for byte in range(4):
                half = even if byte % 2 == 0 else odd
                inputs.append(emitter.emit(f"extractelement <2 x i16> {half}, i32 {byte // 2}"))
        values, record = _native_batch(emitter, inputs, [weights[o] for o in order], match)
        outputs = [values[order.index(output)] for output in range(len(weights))]
        for row, value in zip(batch["rows"], outputs):
            for dot in row["chain"]:
                require(dot["line"] not in replacements, "Overlapping batches")
                replacements[dot["line"]] = []
            replacements[row["line"]] = [f"  {row['tail']} = add i32 {row['base']}, {value}"]
        first = min(row["line"] for row in batch["rows"])
        replacements[first] = emitter.lines + replacements[first]
        records.append(
            dict(block=batch["block"], outputs=len(weights), pairs=match["pairs"], **record)
        )
    result, edits = [], []
    for index, line in enumerate(text.splitlines()):
        if index in replacements:
            new = replacements[index]
            edits.append((len(result), line, new))
            result.extend(new)
        else:
            result.append(line)
    code, restored = "\n".join(result) + "\n", list(result)
    for start, old, new in reversed(edits):
        require(restored[start : start + len(new)] == new, "Edit does not invert")
        restored[start : start + len(new)] = [old]
    require("\n".join(restored) + "\n" == text, "Edits do not invert to the source")
    require(all(literal_dot(old) for _, old, _ in edits), "A non-dot line was replaced")
    require(len(edits) == replaced, "Unexpected edit count")
    require_guard_kept(text, code, audit)
    tails = [row["tail"] for b in batches for row in b["rows"]]
    return code, dict(
        pointwise_dots_replaced=replaced,
        outputs=len(tails),
        batches=len(records),
        width=width,
        block=groups[0]["block"] if blocks == "first" else "all",
        chunks=sum(r["chunks"] for r in records),
        products=sum(r["products"] for r in records),
        tails=tails,
        guard_prefix_and_fallback_unchanged=True,
        edits_invert_to_source=True,
    )


# RC8: native streams over the remaining literal chains


def native_stream(text, width=4, extra_lengths=(32, 36), all_literal=True):
    """`packing.stream_outputs` on the fast path; with `all_literal`, every literal chain."""
    audit = guard_audit(text)
    before = audit["literal_dots"]
    code, proof = packing.stream_outputs(text, width=width, extra_lengths=tuple(extra_lengths))
    require_guard_kept(text, code, audit)
    require(
        not all_literal or proof["streamed_dots"] == before, "Not every literal chain was streamed"
    )
    tails = sorted(set(re.findall(r"^  (%v\d+) = add i32 [^,]+, %stream\d+$", code, re.M)))
    return code, dict(
        streamed_dots=proof["streamed_dots"], groups=len(proof["groups"]), tails=tails
    )


# RC8: exact FP32 for the remaining interval-proven literal graph


def _graph(lines):
    definitions, dots, phis = {}, {}, {}
    for n, line in enumerate(lines):
        if m := re.match(r"^  (%v\d+) = ", line):
            definitions[m[1]] = (n, line)
        if m := DOT.match(line):
            name, acc, a, b = m.groups()
            if re.fullmatch(r"-?\d+", a):
                a, b = b, a
            weights = signed_bytes(int(b)) if re.fullmatch(r"-?\d+", b) else None
            dots[name] = (acc, a, b, weights)
        if m := re.match(r"^  (%v\d+) = phi i32 (.*)", line):
            phis[m[1]] = re.findall(r"\[ ([^,]+), (%v\d+) \]", m[2])
    return definitions, dots, phis


def _intervals(dots, phis, external_roots):
    ranges, active = {}, set()

    def interval(v):
        if re.fullmatch(r"-?\d+", v):
            x = int(v)
            return (x, x) if abs(x) <= FP32_EXACT else None
        if v in external_roots:
            return tuple(external_roots[v])
        if v in ranges:
            return ranges[v]
        if v in active:
            return None
        active.add(v)
        result = None
        if v in dots:
            acc, a, b, weights = dots[v]
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
                    good &= lo >= -FP32_EXACT and hi <= FP32_EXACT
                if good:
                    result = lo, hi
        elif v in phis:
            values = [interval(x) for x, _ in phis[v]]
            if values and all(x is not None for x in values):
                result = min(x[0] for x in values), max(x[1] for x in values)
        active.remove(v)
        ranges[v] = result
        return result

    for v in dots:
        interval(v)
    return ranges


def graph_intervals(text):
    """Interval of every dot4/phi value of the exact integer graph (RC7 lower_float_graph)."""
    definitions, dots, phis = _graph(text.splitlines())
    ranges = _intervals(dots, phis, {})
    return {v: list(b) for v, b in ranges.items() if b is not None and v in definitions}


def mixed_float(text, external_roots=None):
    """RC8 lower_mixed_float: every interval-proven literal dot4/phi graph node as exact FP32.

    External roots are i32 values computed by an earlier native stage; a remaining dot4 that
    accumulates onto one reads it through one `sitofp` (none occur in passes 7-9).
    """
    external_roots = dict(external_roots or {})
    require("%nativefp" not in text, "Reserved names already used")
    require(
        all(-FP32_EXACT <= lo <= hi <= FP32_EXACT for lo, hi in external_roots.values()),
        "External root outside exact FP32",
    )
    lines = text.splitlines()
    definitions, dots, phis = _graph(lines)
    ranges = _intervals(dots, phis, external_roots)
    valid = {v for v, bounds in ranges.items() if bounds is not None and v in definitions}
    converted = set()

    def require_float(v):
        if re.fullmatch(r"-?\d+", v) or v in external_roots or v in converted:
            return
        require(v in valid, "Unproven node " + v)
        converted.add(v)
        if v in dots:
            require_float(dots[v][0])
        elif v in phis:
            for parent, _ in phis[v]:
                require_float(parent)
        else:
            raise LoweringError("Unrecognized proven node: " + v)

    for v in dots:
        if v in valid:
            require_float(v)
    changed = True
    while changed:
        changed = False
        for v, parents in phis.items():
            if v in valid and v not in converted and any(p in converted for p, _ in parents):
                require_float(v)
                changed = True
    uses = {v: [] for v in converted}
    for n, line in enumerate(lines):
        vals = re.findall(r"%v\d+", line.split(";")[0])
        if line.startswith("  %v") and " = " in line:
            vals = vals[1:]
        for v in set(vals) & converted:
            uses[v].append(n)
    serial, out, replacements, before = 0, [], {}, {}
    native_float_roots = {}

    def emit(code):
        nonlocal serial
        serial += 1
        name = "%exact" + str(serial)
        out.append("  " + name + " = " + code)
        return name

    def fv(v):
        if v in converted:
            return "%float" + v[1:]
        if re.fullmatch(r"-?\d+", v):
            return f"{float(int(v)):.9e}"
        if v in external_roots:
            native_float_roots[v] = "%nativefp" + v[1:]
            return native_float_roots[v]
        raise LoweringError("Unproven floating root")

    def external_use(v, n):
        line = lines[n]
        m = re.match(r"^  (%v\d+) = ", line)
        if m and m[1] in converted:
            if m[1] in phis or (m[1] in dots and dots[m[1]][0] == v):
                return False
        return not re.match(r"^  %v\d+ = sitofp i32 " + re.escape(v) + r" to float", line)

    for v in sorted(converted, key=lambda value: definitions[value][0]):
        n, line = definitions[v]
        out = []
        if v in phis:
            out.append(
                f"  {fv(v)} = phi float "
                + ", ".join(f"[ {fv(x)}, {block} ]" for x, block in phis[v])
            )
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
                    f"call float @dx.op.tertiary.f32(i32 46, float {operands[0]}, float {w}, float {total})"
                )
            out.append(f"  {fv(v)} = fadd float {total}, 0.000000e+00")
        if any(external_use(v, use) for use in uses[v]):
            # A conversion after a phi follows every phi of its block.
            if v in phis:
                after = n + 1
                while after < len(lines) and re.match(r"^  %v\d+ = phi ", lines[after]):
                    after += 1
                before.setdefault(after, []).append(f"  {v} = fptosi float {fv(v)} to i32")
            else:
                out.append(f"  {v} = fptosi float {fv(v)} to i32")
        replacements[n] = out
    for v, name in native_float_roots.items():
        at = definitions[v][0] + 1
        while at < len(lines) and re.match(r"^  %v\d+ = phi ", lines[at]):
            at += 1
        before.setdefault(at, []).append(f"  {name} = sitofp i32 {v} to float")
    output = []
    for n, line in enumerate(lines):
        output.extend(before.get(n, []))
        if line.startswith("declare i32 @dx.op.dot4AddPacked.i32("):
            if not all(v in converted for v in dots):
                output.append(line)
            if "declare float @dx.op.tertiary.f32(" not in text:
                output.append("declare float @dx.op.tertiary.f32(i32, float, float, float) #0")
            continue
        if m := re.match(r"^  (%v\d+) = sitofp i32 (%v\d+) to float", line):
            if m[2] in converted:
                line = f"  {m[1]} = fadd float {fv(m[2])}, 0.000000e+00"
        output.extend(replacements.get(n, [line]))
    code = "\n".join(output) + "\n"
    # A declaration without a use (nothing converted) is dropped, as the RC8 builders did.
    if code.count("@dx.op.tertiary.f32(") == 1:
        code = re.sub(r"^declare float @dx.op.tertiary.f32[^\n]*\n", "", code, flags=re.M)
    if "@dx.op.waveAllTrue(" in text:
        # Only the fast path's literal graph may change; the SDK body (fallback) stays as it is.
        require_guard_kept(text, code, guard_audit(text))
    return code, dict(
        dots=len(dots),
        converted_dots=sum(v in converted for v in dots),
        converted_phis=sum(v in converted for v in phis),
        remaining_dots=sum(bool(DOT.match(x)) for x in code.splitlines()),
        external_roots=len(external_roots),
        external_float_roots=len(native_float_roots),
        maximum_magnitude=max(
            [max(abs(ranges[v][0]), abs(ranges[v][1])) for v in converted] or [0]
        ),
    )


INTERNAL = re.compile(r"%(?:exact\d+|floatv\d+)\b")


def float_graph_audit(original, candidate, remaining):
    """RC9 p9_float_replay: each remaining literal dot's FP32 graph equals the all-FP32 control's.

    `original` lowers every literal dot to FP32, `candidate` only `remaining` (the rest went
    native earlier). Graphs are compared by a digest in which only the stage's own temporaries
    are normalised; inputs, literals, opcodes and FMA operands must match.
    """
    require("%nativefp" not in original and "%nativefp" not in candidate, "Native-to-FP32 bridge")
    inventories = [
        dict(re.findall(r"^  (%[\w.$-]+) = (.*)$", t, re.M)) for t in (original, candidate)
    ]
    caches, active = [{}, {}], [set(), set()]

    def digest(index, value):
        if value in caches[index]:
            return caches[index][value]
        require(value not in active[index] and INTERNAL.fullmatch(value), "Unexpected graph node")
        active[index].add(value)
        rhs = inventories[index][value]
        require(
            re.match(r"(fadd|shl|ashr|sitofp) |call float @dx\.op\.tertiary\.f32\(", rhs),
            "Unexpected graph operation",
        )
        normalized = INTERNAL.sub(lambda match: "@value_" + digest(index, match[0]), rhs)
        result = sha(normalized)
        caches[index][value] = result
        active[index].remove(value)
        return result

    records = []
    for value in sorted(remaining):
        root = "%float" + value[1:]
        before, after = digest(0, root), digest(1, root)
        require(before == after, "The FP32 graph of " + value + " changed")
        records.append(value + " " + before)
    return dict(
        graph_audit_roots=len(records),
        graph_audit_sha256=sha("\n".join(records) + "\n"),
        unselected_fp32_graphs_identical=True,
    )


# RC8: model guard forms


def vector_guard(source):
    """The guard's four adjacent constant-table loads as one `<4 x i32>` load; SM 6.9."""
    require("%optguard" not in source and "@bc250_guard_vectors" not in source, "Reserved names")
    match = re.search(
        r"^@bc250_expected = internal unnamed_addr constant \[(\d+) x i32\] \[(.*)\], align 16$",
        source,
        re.M,
    )
    require(match, "No constant model table")
    count = int(match[1])
    values = [int(v) for v in re.findall(r"i32 (-?\d+)", match[2])]
    require(
        len(values) == count and count % 4 == 0 and source.count("@bc250_expected") == 5,
        "Unexpected model table uses",
    )
    lines, defs = source.splitlines(), {}
    for index, line in enumerate(lines):
        if m := re.match(r"^  (%\w+) = (.*)", line):
            defs[m[1]] = (index, m[2])
    geps = []
    pattern = re.compile(
        r"^  (%\w+) = getelementptr \["
        + str(count)
        + r" x i32\], \["
        + str(count)
        + r" x i32\]\* @bc250_expected, i32 0, i32 (%\w+)$"
    )
    for index, line in enumerate(lines):
        if m := pattern.fullmatch(line):
            geps.append((index, m[1], m[2]))
    require(len(geps) == 4, "Expected four table loads")
    base = geps[0][2]
    m = re.fullmatch(r"phi i32 \[ (%\w+), (%\w+) \], \[ (%\w+), (%\w+) \]", defs[base][1])
    require(m, "Guard index is not a two-way phi")
    initial, next_value = m[1], m[3]
    require(re.fullmatch(r"shl i32 %\w+, 2", defs[initial][1]), "Guard start is not lane*4")
    step = re.fullmatch(r"add i32 " + re.escape(base) + r", (%\w+)", defs[next_value][1])
    require(step and re.fullmatch(r"shl i32 %\w+, 2", defs[step[1]][1]), "Guard step")
    require(
        re.search(r"icmp ult i32 " + re.escape(base) + ", " + str(count) + r"\b", source),
        "Guard bound",
    )
    replacement = {}
    for lane, (index, name, at) in enumerate(geps):
        if lane:
            require(
                re.fullmatch(r"(?:or|add) i32 " + re.escape(base) + ", " + str(lane), defs[at][1]),
                "Guard lane index",
            )
        uses = [
            (n, v)
            for v, (n, rhs) in defs.items()
            if re.fullmatch(r"load i32, i32\* " + re.escape(name) + r", align (?:4|8|16)", rhs)
        ]
        require(len(uses) == 1, "Guard load use")
        require(len(re.findall(re.escape(name) + r"\b", source)) == 2, "Guard pointer use")
        at_load, value = uses[0]
        replacement[index] = []
        replacement[at_load] = [f"  {value} = extractelement <4 x i32> %optguard_value, i32 {lane}"]
    replacement[geps[0][0]] = [
        f"  %optguard_index = lshr i32 {base}, 2",
        f"  %optguard_ptr = getelementptr [{count // 4} x <4 x i32>], [{count // 4} x <4 x i32>]* @bc250_guard_vectors, i32 0, i32 %optguard_index",
        "  %optguard_value = load <4 x i32>, <4 x i32>* %optguard_ptr, align 16",
    ]
    vectors = [values[i : i + 4] for i in range(0, count, 4)]
    for width in [32, 64]:
        visited = []
        for lane in range(width):
            for first in range(lane * 4, count, width * 4):
                require(first % 4 == 0 and first + 3 < count, "Guard visit")
                visited.extend(range(first, first + 4))
        require(sorted(visited) == list(range(count)), "Guard does not visit every word once")
    new_global = (
        "@bc250_guard_vectors = internal unnamed_addr constant ["
        + str(count // 4)
        + " x <4 x i32>] ["
        + ", ".join("<4 x i32> <" + ", ".join("i32 " + str(v) for v in g) + ">" for g in vectors)
        + "], align 16"
    )
    output = []
    for index, line in enumerate(lines):
        if line == match[0]:
            output.append(new_global)
        else:
            output.extend(replacement.get(index, [line]))
    code = "\n".join(output) + "\n"
    require("@bc250_expected" not in code, "Table left")
    for key, body in [("version", "i32 1, i32 9"), ("shaderModel", '!"cs", i32 6, i32 9')]:
        node = re.search(r"!dx." + key + r" = !\{!(\d+)\}", code)[1]
        code = re.sub(
            r"^!" + node + r" = !\{[^\n]+\}",
            lambda m: "!" + node + " = !{" + body + "}",
            code,
            flags=re.M,
        )
    # Wave-size property 11 becomes 23 when a 6.6/6.7 shader is raised to 6.9.
    entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", code)[1]
    ep = re.search(r"^!" + entry + r" = !\{(.*)\}$", code, re.M)[1]
    props = re.findall(r"!(\d+)", ep)[-1]
    line = re.search(r"^!" + props + r" = !\{(.*)\}$", code, re.M)
    wave = re.search(r"i32 11, !(\d+)", line[1])
    if wave:
        node = wave[1]
        w = re.search(r"^!" + node + r" = !\{i32 (32|64)\}$", code, re.M)
        require(w, "Wave node")
        code = code.replace(line[0], line[0].replace("i32 11, !" + node, "i32 23, !" + node))
        code = code.replace(w[0], f"!{node} = !{{i32 {w[1]}, i32 0, i32 0}}")
    return code, dict(
        words=count,
        groups=count // 4,
        all_words_once_per_wave=[32, 64],
        table_sha256=sha(struct.pack("<" + "i" * count, *values)),
    )


def group_guard(source):
    """Wave32: 64 threads check the table once; each wave reduces all 64 flags (group memory)."""
    require(
        "%optcoop" not in source
        and "@coop_flags" not in source
        and "@dx.op.barrier" not in source
        and "@dx.op.threadIdInGroup.i32" not in source,
        "Reserved names or a barrier already present",
    )
    entry = re.search(r"!dx.entryPoints = !\{!(\d+)\}", source)[1]
    line = re.search(r"^!" + entry + r" = !\{(.*)\}$", source, re.M)[1]
    props = re.findall(r"!(\d+)", line)[-1]
    body = re.search(r"^!" + props + r" = !\{(.*)\}$", source, re.M)[1]
    threads = re.search(r"i32 4, !(\d+)", body)[1]
    require(
        re.search(r"^!" + threads + r" = !\{i32 64, i32 1, i32 1\}$", source, re.M), "numthreads"
    )
    wave = re.search(r"i32 23, !(\d+)", body)
    require(
        wave and re.search(r"^!" + wave[1] + r" = !\{i32 32, i32 0, i32 0\}$", source, re.M),
        "The group guard needs required wave size 32",
    )
    lines, defs = source.splitlines(), {}
    for x in lines:
        if m := re.match(r"^  (%\w+) = (.*)", x):
            defs[m[1]] = m[2]
    lane = [v for v, r in defs.items() if r == "call i32 @dx.op.waveGetLaneIndex(i32 111)"]
    widths = [v for v, r in defs.items() if r == "call i32 @dx.op.waveGetLaneCount(i32 112)"]
    require(len(lane) == len(widths) == 1, "Lane index/count")
    lane, width = lane[0], widths[0]
    initial = [v for v, r in defs.items() if r == f"shl i32 {lane}, 2"]
    steps = [v for v, r in defs.items() if r == f"shl i32 {width}, 2"]
    require(len(initial) == len(steps) == 1, "Guard start/step")
    initial, step = initial[0], steps[0]
    votes = [
        (v, m[1])
        for v, r in defs.items()
        if (m := re.fullmatch(r"call i1 @dx.op.waveAllTrue\(i32 114, i1 (%\w+)\)", r))
    ]
    require(len(votes) == 1, "One vote")
    vote, partial = votes[0]
    vote_line = f"  {vote} = {defs[vote]}"
    head = source[: source.index(vote_line)]
    # The only branch before the guard is the group-uniform dispatch-z check (numthreads.z = 1).
    start = head[head.index("define void @") :]
    z = re.search(
        r"  (%\w+) = call i32 @dx.op.threadId.i32\(i32 93, i32 2\)\n  (%\w+) = icmp eq i32 \1, 0\n  br i1 \2, label (%\w+), label (%\w+)",
        start,
    )
    require(z, "Dispatch-z gate")
    first_guard = start.index(z[0]) + len(z[0])
    require("  br " not in start[: start.index(z[0])], "Branch before the z gate")
    branches = re.findall(
        r"^  br i1 ([^,]+), label (%\w+), label (%\w+)", start[first_guard:], re.M
    )
    require(len(branches) == 1, "The guard loop has more than its bound branch")
    match = re.fullmatch(r"icmp ult i32 (%\w+), (\d+)", defs[branches[0][0]])
    require(match, "Guard bound")
    index, count = match[1], int(match[2])
    require(count % 4 == 0, "Table size")
    require(
        re.fullmatch(
            r"phi i32 \[ " + re.escape(initial) + r", %\w+ \], \[ (%\w+), %\w+ \]", defs[index]
        ),
        "Guard index",
    )
    next_index = re.findall(r"\[ (%\w+), %\w+ \]", defs[index])[1]
    require(defs[next_index] == f"add i32 {index}, {step}", "Guard step")
    init_line = f"  {initial} = {defs[initial]}"
    code = source.replace(
        init_line,
        "  %optcoop_tid = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)\n"
        + f"  {initial} = shl i32 %optcoop_tid, 2",
        1,
    )
    code = code.replace(f"  {step} = {defs[step]}", f"  {step} = add i32 256, 0", 1)
    join = f"""  %optcoop_value = zext i1 {partial} to i32
  %optcoop_destination = getelementptr [64 x i32], [64 x i32] addrspace(3)* @coop_flags, i32 0, i32 %optcoop_tid
  store i32 %optcoop_value, i32 addrspace(3)* %optcoop_destination, align 4
  call void @dx.op.barrier(i32 80, i32 9)
  %optcoop_other_lane = add i32 {lane}, 32
  %optcoop_low_ptr = getelementptr [64 x i32], [64 x i32] addrspace(3)* @coop_flags, i32 0, i32 {lane}
  %optcoop_high_ptr = getelementptr [64 x i32], [64 x i32] addrspace(3)* @coop_flags, i32 0, i32 %optcoop_other_lane
  %optcoop_low = load i32, i32 addrspace(3)* %optcoop_low_ptr, align 4
  %optcoop_high = load i32, i32 addrspace(3)* %optcoop_high_ptr, align 4
  %optcoop_both = and i32 %optcoop_low, %optcoop_high
  %optcoop_match = icmp ne i32 %optcoop_both, 0
  {vote} = call i1 @dx.op.waveAllTrue(i32 114, i1 %optcoop_match)"""
    code = code.replace(vote_line, join, 1)
    code = code.replace(
        "define void @",
        "@coop_flags = external addrspace(3) global [64 x i32], align 4\n\ndefine void @",
        1,
    )
    attr = 1 + max(map(int, re.findall(r"^attributes #(\d+) =", code, re.M)))
    declarations = f"""declare i32 @dx.op.threadIdInGroup.i32(i32, i32) #1
declare void @dx.op.barrier(i32, i32) #{attr}
attributes #{attr} = {{ noduplicate nounwind }}

"""
    require("attributes #1 = { nounwind readnone }" in code, "Attribute #1")
    code = code.replace("attributes #0 = ", declarations + "attributes #0 = ", 1)
    tail = source[source.index(vote_line) + len(vote_line) :]
    require(code[code.index(join) + len(join) :].replace(declarations, "", 1) == tail, "Guard tail")
    visits = []
    for tid in range(64):
        for first in range(tid * 4, count, 256):
            require(first + 3 < count, "Group visit")
            visits.extend(range(first, first + 4))
    require(sorted(visits) == list(range(count)), "The group does not check every word once")
    return code, dict(
        words=count, group_threads=[64, 1, 1], required_wave_size=32, shared_bytes=256
    )
