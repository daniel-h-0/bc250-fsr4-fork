# SPDX-License-Identifier: MIT
"""Expose literal model weights: constant-fold integer MADs and expand fixed-trip loops.

The SDK's literal-weight passes keep their coefficients in local constant arrays read inside
four-trip loops. LLVM's generic passes (through DXC's `opt`) promote the arrays once the loops
are expanded; DXIL's integer MAD intrinsics are folded here because LLVM does not know them.
Bias/scale reads, clipping, resource bindings and z stay as they are.
"""

import re

if __package__:
    from .ir import VALUE, LoweringError, canonical_ssa, require
else:
    from ir import VALUE, LoweringError, canonical_ssa, require

UNROLL_PASSES = ("-sroa", "-mem2reg", "-instcombine", "-simplifycfg", "-gvn", "-dce", "-globaldce")
MAD = re.compile(
    r"call i32 @dx.op.tertiary.i32\(i32 (48|49), i32 (-?\d+), i32 (-?\d+), i32 (-?\d+)\)(?: #\d+)?"
)


def fold_mads(text):
    """Fold IMad/UMad (opcodes 48/49) with three literal operands; both wrap modulo 2**32."""
    count = 0

    def replace(m):
        nonlocal count
        count += 1
        value = (int(m[2]) * int(m[3]) + int(m[4])) & 0xFFFFFFFF
        if value >= 0x80000000:
            value -= 0x100000000
        return f"add i32 {value}, 0"

    return MAD.sub(replace, text), count


def _blocks(text):
    out, current = {}, None
    for line in text.splitlines():
        if m := re.match(r"^([A-Za-z0-9_.$-]+):", line):
            current = m[1]
            out[current] = []
        elif current is not None:
            if line == "}":
                current = None
            else:
                out[current].append(line)
    return out


def _loop(header, body, bodies, successors, predecessors):
    """The expansion of one two-block do/while loop with literal unit-step bounds, or None."""
    latches = (
        [header]
        if header in successors[header]
        else [
            name
            for name in successors[header]
            if successors[name] == {header} and predecessors[name] == {header}
        ]
    )
    if len(latches) != 1:
        return None
    latch = latches[0]
    outside = predecessors[header] - {latch}
    if len(outside) != 1:
        return None
    preheader = next(iter(outside))
    exit_block = next(iter(successors[header] - {latch}))
    phis = {}
    for line in body:
        if m := re.match(r"  (" + VALUE + r") = phi i32 (.*)", line):
            pairs = re.findall(r"\[ ([^,]+), %([A-Za-z0-9_.$-]+) \]", m[2])
            if len(pairs) != 2 or {block for _, block in pairs} != {preheader, latch}:
                return None
            phis[m[1]] = {block: value for value, block in pairs}
        elif "= phi " in line:
            return None
    if not phis:
        return None
    definitions = {
        m[1]: m[2]
        for line in body + bodies[latch]
        if (m := re.match(r"  (" + VALUE + r") = (.*)", line))
    }
    branch = next((line for line in reversed(body) if line.startswith("  br ")), None)
    bm = branch and re.match(
        r"  br i1 (" + VALUE + r"), label %([A-Za-z0-9_.$-]+), label %([A-Za-z0-9_.$-]+)", branch
    )
    if not bm:
        return None
    cm = re.match(r"icmp (eq|ne) i32 (" + VALUE + r"), (-?\d+)", definitions.get(bm[1], ""))
    if not cm:
        return None
    op, compared, bound = cm[1], cm[2], int(cm[3])
    if (op == "eq" and bm[2] != exit_block) or (op == "ne" and bm[3] != exit_block):
        return None
    old_value = compared in phis
    increment = phis[compared][latch] if old_value else compared
    inc = re.match(
        r"add(?: nuw| nsw)* i32 (" + VALUE + r"), 1(?: |$)", definitions.get(increment, "")
    )
    if not inc or inc[1] not in phis:
        return None
    induction = inc[1]
    if old_value and induction != compared:
        return None
    initial = phis[induction][preheader]
    if not re.fullmatch(r"-?\d+", initial) or phis[induction][latch] != increment:
        return None
    initial = int(initial)
    trip = bound - initial + old_value
    if not 0 <= initial <= bound <= 0x7FFFFFFF or not 1 <= trip <= 32:
        return None
    header_code = [
        line
        for line in body
        if not re.match(r"  " + VALUE + r" = phi ", line) and not line.startswith("  br ")
    ]
    latch_code = (
        [] if latch == header else [line for line in bodies[latch] if not line.startswith("  br ")]
    )
    if any(
        re.match(r"  (?:switch|ret|indirectbr|invoke)\b", line) for line in header_code + latch_code
    ):
        return None
    return dict(
        header=header,
        latch=latch,
        preheader=preheader,
        exit=exit_block,
        iterations=trip,
        induction=induction,
        phis=phis,
        header_code=header_code,
        latch_code=latch_code,
    )


def expand_loops(text, serial_prefix="%bc250_unrolled"):
    """Expand every proven finite two-block loop (literal start, unit step, literal bound <= 32).

    The body and latch keep their instruction order, every phi is substituted per iteration and
    the last iteration omits the latch, exactly as the original branch does.
    """
    bodies = _blocks(text)
    successors = {
        name: set(re.findall(r"label %([A-Za-z0-9_.$-]+)", "\n".join(body)))
        for name, body in bodies.items()
    }
    predecessors = {name: set() for name in bodies}
    for name, dests in successors.items():
        for dest in dests:
            predecessors[dest].add(name)
    replacements, removed, renames, records = {}, set(), {}, []
    serial = max([0, *[int(x) for x in re.findall(re.escape(serial_prefix) + r"(\d+)", text)]])
    for header, body in bodies.items():
        if len(successors[header]) != 2:
            continue
        loop = _loop(header, body, bodies, successors, predecessors)
        if loop is None:
            continue
        mapping, code = {}, []

        def substitute(line):
            return re.sub(VALUE, lambda m: mapping.get(m[0], m[0]), line)

        latch, phis = loop["latch"], loop["phis"]
        for iteration in range(loop["iterations"]):
            incoming = {
                phi: (
                    values[loop["preheader"]]
                    if iteration == 0
                    else mapping.get(values[latch], values[latch])
                )
                for phi, values in phis.items()
            }
            mapping.update(incoming)
            last = iteration + 1 == loop["iterations"]
            for line in loop["header_code"] + ([] if last else loop["latch_code"]):
                if m := re.match(r"  (" + VALUE + r") = (.*)", line):
                    rhs = substitute(m[2])
                    serial += 1
                    name = serial_prefix + str(serial)
                    mapping[m[1]] = name
                    code.append("  " + name + " = " + rhs)
                else:
                    code.append(substitute(line))
        code.append("  br label %" + loop["exit"])
        replacements[header] = code
        if latch != header:
            removed.add(latch)
        renames.update(mapping)
        records.append(
            {
                k: loop[k]
                for k in ("header", "latch", "preheader", "exit", "iterations", "induction")
            }
        )
    output, current = [], None
    for line in text.splitlines():
        if m := re.match(r"^([A-Za-z0-9_.$-]+):", line):
            current = m[1]
            if current in removed:
                continue
            output.append(line)
            if current in replacements:
                output.extend(replacements[current])
            continue
        if line == "}":
            current = None
        if current in removed or current in replacements:
            continue
        output.append(re.sub(VALUE, lambda m: renames.get(m[0], m[0]), line))
    # A later loop's initial value may be an earlier loop's outgoing value: resolve after all
    # replacements exist.
    joined = "\n".join(output) + "\n"
    for _ in range(3):
        changed = re.sub(VALUE, lambda m: renames.get(m[0], m[0]), joined)
        if changed == joined:
            break
        joined = changed
    return joined, records


def optimize(text, toolchain, rounds=8):
    """`opt` with the unroll pipeline, then fold MADs; repeat while folds were found (<= 8x)."""
    for _ in range(rounds):
        text, count = fold_mads(toolchain.opt(text, UNROLL_PASSES))
        if not count:
            break
    return text


def unroll(text, toolchain):
    """SDK disassembly -> canonical IR with every literal-weight loop expanded and folded.

    Requires at least one expansion; afterwards every dot4 must carry a literal weight.
    """
    text = optimize(text, toolchain)
    loops = []
    for _ in range(4):
        text, found = expand_loops(text)
        if not found:
            break
        loops += found
        text = optimize(text, toolchain)
        text = optimize(text, toolchain)
    else:
        raise LoweringError("Loop expansion did not converge in four rounds")
    require(loops, "No literal-weight loops found")
    return canonical_ssa(text), loops
