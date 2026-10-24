# SPDX-License-Identifier: MIT
"""Exact integer Winograd F(2,3) for a literal 3x3, 16-in/16-out INT8 model convolution.

`plan` reads the convolution from the canonical (unrolled) SDK program: the 16 accumulator chains
of 36 constant dot4s, their weights, and the scratch-buffer address of every input word, whose
affine form in the thread IDs gives the pixel stride, row pitch and kernel offsets. It transforms
the weights with twice-G (so all coefficients are integers) and proves every numerator fits i32.

`lower` rewrites the program: two Wave32 waves of each 64-thread group compute horizontally
adjacent output pixels (the wave holding group thread 0 owns even x; no thread-to-lane order is
assumed). Each wave transforms its inputs with B^T, computes two of the four transformed domains
as exact i16-packed dot products, shares one domain through group shared memory, and applies the
inverse transform A^T; the original i32 accumulator (bias) is added last. The result equals the
original dot4 chains bit for bit (numerator even, exact shift by one).

Domain dot products ("accumulate", bins.py): `sequential` (RC8 `winograd-model`), `matched`
(RC9 `winograd-pairing`) or `binned` (RC9 `winograd-bins`). With `postscale`, a domain whose twice-G row is all even computes with halved coefficients and
doubles the i32 result (RC9 `winograd-postscale`; exact, bounds checked).
"""

import hashlib
import re

if __package__:
    from .bins import ACCUMULATORS, I32_MAX, I32_MIN, INT8, OUTPUTS, Emitter
    from .ir import Program, entry_properties, numthreads, require
else:
    from bins import ACCUMULATORS, I32_MAX, I32_MIN, INT8, OUTPUTS, Emitter
    from ir import Program, entry_properties, numthreads, require

BT = ((1, 0, -1, 0), (0, 1, 1, 0), (0, -1, 1, 0), (0, 1, 0, -1))
G2 = ((2, 0, 0), (1, 1, 1), (1, -1, 1), (0, 0, 2))
AT = ((1, 1, 1, 0), (0, 1, -1, -1))
CHANNELS, TAPS = 16, 3
TERMS = TAPS * CHANNELS  # one transformed domain: 3 kernel rows x 16 channels
CHAIN = TAPS * TAPS * CHANNELS // 4  # dot4s per output chain
GROUP, LANES = 64, 32
PIXEL_BYTES = CHANNELS  # 16 INT8 channels = four packed words
WAVE_OPS_FLAG = 0x80000
# Role of each (wave, domain): left (even x) computes d1 = d1+d2 (shared) and d0 = d0-d2;
# right computes d2 = d2-d1 (shared) and d3 = d1-d3, whose coefficients enter negated.
ROLES = (
    ("left", False, 1, 1),
    ("left", True, 0, 1),
    ("right", False, 2, 1),
    ("right", True, 3, -1),
)


def domains():
    """Range of each B^T row over INT8 inputs."""
    return [
        (
            sum(min(b * INT8[0], b * INT8[1]) for b in row),
            sum(max(b * INT8[0], b * INT8[1]) for b in row),
        )
        for row in BT
    ]


def postscale_factor(domain):
    """2 when the twice-G row of this domain is all even (its coefficients halve exactly)."""
    return 2 if all(g % 2 == 0 for g in G2[domain]) else 1


def check_identity():
    """A^T ((2G g) * (B^T d)) = 2 (d[0:3].g, d[1:4].g) for every basis g and d."""
    for gi in range(3):
        for di in range(4):
            g = [int(i == gi) for i in range(3)]
            d = [int(i == di) for i in range(4)]
            m = [
                sum(G2[t][k] * g[k] for k in range(3)) * sum(BT[t][j] * d[j] for j in range(4))
                for t in range(4)
            ]
            result = [sum(AT[r][t] * m[t] for t in range(4)) for r in range(2)]
            expected = [2 * sum(d[i + k] * g[k] for k in range(3)) for i in range(2)]
            require(result == expected, "F(2,3) identity failed")


def transform(weights):
    """U[o][ky][t][c] = sum_k 2G[t][k] W[o][ky][k][c]: twice-G along the kernel's x axis."""
    return [
        [
            [
                [sum(G2[t][k] * weights[o][ky][k][c] for k in range(TAPS)) for c in range(CHANNELS)]
                for t in range(4)
            ]
            for ky in range(TAPS)
        ]
        for o in range(OUTPUTS)
    ]


def _definitions(text):
    return {m[1]: m[2].split("  ;")[0] for m in re.finditer(r"^  (%\w+) = (.*)$", text, re.M)}


def plan(text):
    """The convolution in a canonical literal-weight program, transformed and bounded."""
    check_identity()
    program = Program(text)
    require(program.dynamic_dots == 0, "Winograd planning needs every dot4 weight literal")
    rows = sorted(program.columns(), key=lambda c: c["tail"]["line"])
    selected = [row for row in rows if len(row["chain"]) == CHAIN]
    require(len(selected) == OUTPUTS, f"Expected {OUTPUTS} spatial chains of {CHAIN} dot4s")
    definitions = _definitions(text)
    axes = []
    for axis in range(3):
        found = [
            v
            for v, rhs in definitions.items()
            if rhs == f"call i32 @dx.op.threadId.i32(i32 93, i32 {axis})"
        ]
        require(len(found) == 1, "Thread ID not unique")
        axes.extend(found)
    cache = {value: [int(i == k) for i in range(4)] for k, value in enumerate(axes)}

    def affine(value):
        """[x, y, z, constant] coefficients of an i32 address in the thread IDs."""
        if value in cache:
            return cache[value]
        if not value.startswith("%"):
            return [0, 0, 0, int(value)]
        rhs = definitions[value]
        if m := re.fullmatch(r"(add|sub)(?: nuw| nsw)* i32 ([^,]+), ([^,]+)", rhs):
            sign = 1 if m[1] == "add" else -1
            result = [a + sign * b for a, b in zip(affine(m[2]), affine(m[3]))]
        elif m := re.fullmatch(r"shl(?: nuw| nsw)* i32 ([^,]+), (\d+)", rhs):
            result = [a << int(m[2]) for a in affine(m[1])]
        elif m := re.fullmatch(
            r"call i32 @dx.op.tertiary.i32\(i32 49, i32 (-?\d+), i32 ([^,]+), i32 ([^)]+)\)", rhs
        ):
            result = [int(m[1]) * a + b for a, b in zip(affine(m[2]), affine(m[3]))]
        else:
            raise ValueError(f"Address {value} is not affine in the thread IDs: {rhs}")
        cache[value] = result
        return result

    weights = [[[[0] * CHANNELS for _ in range(TAPS)] for _ in range(TAPS)] for _ in range(OUTPUTS)]
    layout, resource, pitch, base = None, None, None, None
    for output, row in enumerate(selected):
        seen, items = set(), []
        for dot in row["chain"]:
            m = re.fullmatch(
                r"extractvalue %dx.types.ResRet.i32 (%\w+), ([0-3])",
                definitions.get(dot["inp"], ""),
            )
            require(m, "A spatial input is not a component of a raw buffer load")
            load, component = m[1], int(m[2])
            m = re.fullmatch(
                r"call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle (%\w+),"
                r" i32 (%\w+), i32 undef, i8 15, i32 4\)",
                definitions[load],
            )
            require(m, "A spatial input is not a four-word raw buffer load")
            handle, address = m[1], m[2]
            m = re.fullmatch(
                r"call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%\w+),"
                r" %dx.types.ResourceProperties (\{ i32 \d+, i32 \d+ \})\)",
                definitions[handle],
            )
            require(m, "A spatial load's handle is not an annotated binding")
            root, properties = m[1], m[2]
            binding = re.fullmatch(
                r"call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, %dx.types.ResBind"
                r" \{ i32 (\d+), i32 (\d+), i32 0, i8 1 \}, i32 (\d+), i1 false\)",
                definitions.get(root, ""),
            )
            require(
                binding and len(set(binding.groups())) == 1, "Spatial inputs must come from one UAV"
            )
            require(resource in (None, (root, properties)), "Spatial inputs use several resources")
            resource = (root, properties)
            x, y, z, constant = affine(address)
            require(x == PIXEL_BYTES and z == 0 and y > 0, "Unexpected spatial address stride")
            require(pitch in (None, y), "Spatial inputs use several row pitches")
            pitch = y
            # The first word read is the kernel's upper-left pixel; every other word must sit at
            # an exact (ky, kx) offset from it.
            if base is None:
                base = constant
            ky, rest = divmod(constant - base, pitch)
            kx, byte = divmod(rest, PIXEL_BYTES)
            require(
                0 <= ky < TAPS and 0 <= kx < TAPS and byte == 0, "Input word outside the 3x3 window"
            )
            for channel, weight in enumerate(dot["w"], component * 4):
                key = (ky, kx, channel)
                require(key not in seen, "Input word used twice")
                seen.add(key)
                weights[output][ky][kx][channel] = weight
            items.append(dict(input=dot["inp"], kernel_xy=[kx, ky], packed_component=component))
        require(len(seen) == TAPS * TAPS * CHANNELS, "Incomplete 3x3 window")
        shape = [(i["kernel_xy"], i["packed_component"]) for i in items]
        require(layout in (None, shape), "Output chains read their windows in different orders")
        layout = shape
    transformed = transform(weights)
    bounds = domains()
    totals = []
    for t, (lo, hi) in enumerate(bounds):
        per = []
        for o in range(OUTPUTS):
            values = [transformed[o][ky][t][c] for ky in range(TAPS) for c in range(CHANNELS)]
            per.append(
                (sum(min(w * lo, w * hi) for w in values), sum(max(w * lo, w * hi) for w in values))
            )
        totals.append(per)
    numerators = []
    for o in range(OUTPUTS):
        left = (sum(totals[t][o][0] for t in (0, 1, 2)), sum(totals[t][o][1] for t in (0, 1, 2)))
        right = (
            totals[1][o][0] - totals[2][o][1] - totals[3][o][1],
            totals[1][o][1] - totals[2][o][0] - totals[3][o][0],
        )
        numerators += [*left, *right]
    require(
        I32_MIN <= min(numerators) and max(numerators) < I32_MAX, "Winograd numerator exceeds i32"
    )
    return dict(
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        selected=[
            dict(tail=r["tail"]["name"], base=r["base"], line=r["tail"]["line"]) for r in selected
        ],
        chains=[[d["line"] for d in r["chain"]] for r in selected],
        axes=axes,
        resource=list(resource),
        row_pitch=pitch,
        input_buffer_base=base,
        input_layout=[dict(kernel_xy=k, packed_component=c) for k, c in layout],
        weights=weights,
        transformed=transformed,
        transformed_input_domains=[list(b) for b in bounds],
        numerator_bounds=[min(numerators), max(numerators)],
    )


def domain_matrix(plan_, domain, sign):
    """16 x 48 coefficients of one transformed domain; terms are (kernel row, channel)."""
    return [
        [
            sign * plan_["transformed"][o][ky][domain][c]
            for ky in range(TAPS)
            for c in range(CHANNELS)
        ]
        for o in range(OUTPUTS)
    ]


def _domain_dots(e, inputs, matrix, bounds, domain, accumulate, postscale):
    require(len(inputs) == TERMS and len(matrix) == OUTPUTS, "A domain is 16 outputs of 48 terms")
    factor = postscale_factor(domain) if postscale else 1
    require(all(v % factor == 0 for row in matrix for v in row), "Postscale factor does not divide")
    weights = [[v // factor for v in row] for row in matrix]
    outputs, record = ACCUMULATORS[accumulate](e, inputs, weights, bounds)
    lo = [sum(min(w * bounds[0], w * bounds[1]) for w in row) for row in weights]
    hi = [sum(max(w * bounds[0], w * bounds[1]) for w in row) for row in weights]
    require(min(lo) > I32_MIN and max(hi) < I32_MAX, "Domain dot exceeds i32")
    if factor == 2:
        require(min(lo) >= -(2**30) and max(hi) < 2**30, "Postscaled dot exceeds i32")
        outputs = [e.emit(f"shl i32 {value}, 1") for value in outputs]
    record.update(
        domain=domain,
        postscale=factor,
        minimum=[factor * v for v in lo],
        maximum=[factor * v for v in hi],
    )
    return outputs, record


def _shape(text, plan_):
    """Check the program has the one form this rewrite was reviewed for; return its names."""
    require("%wino" not in text and "@wino_" not in text, "Reserved names already used")
    require("@dx.op.barrier" not in text, "The program already synchronizes")
    labels = re.findall(r"^(v\d+):", text, re.M)
    require(
        len(labels) == 3 and labels[0] == "v0" and " phi " not in text, "Expected entry, body, exit"
    )
    entry, body, exit_block = labels
    program = Program(text)
    require(
        all(program.blocks[r["tail"]] == body for r in plan_["selected"]),
        "Spatial chains must sit in the body block",
    )
    x, y, _ = plan_["axes"]
    x_line = f"  {x} = call i32 @dx.op.threadId.i32(i32 93, i32 0)"
    require(text.count(x_line + "\n") == 1, "Thread ID x not found")
    lines = text.splitlines()
    bounds = None
    for i in range(len(lines) - 1):
        a = re.fullmatch(r"  (%v\d+) = icmp ugt i32 " + re.escape(x) + r", (\d+)", lines[i])
        b = re.fullmatch(r"  (%v\d+) = icmp ugt i32 " + re.escape(y) + r", (\d+)", lines[i + 1])
        if a and b:
            require(bounds is None, "Several bound checks")
            bounds = (a[1], b[1], int(a[2]) + 1, int(b[2]) + 1)
    require(bounds, "Grid bound checks not found")
    group = numthreads(text)
    require(group == (GROUP, 1, 1), f"Expected numthreads ({GROUP}, 1, 1)")
    # Uniform branch: every 64-thread group is entirely inside or outside the grid.
    require(bounds[2] % GROUP == 0, "Grid width is not a multiple of the group width")
    require(text.count("  br ") == 2 and text.count("  ret void") == 1, "Unexpected control flow")
    start = re.search(r"^" + body + r":$", text, re.M).start()
    section = text[start : re.search(r"^" + exit_block + r":$", text, re.M).start()]
    require(section.count("  br ") == 1, "The body must be one block")
    branch = re.search(
        r"^  br i1 (%v\d+), label %" + exit_block + r", label %" + body + r"$", text, re.M
    )
    require(branch, "Entry branch not found")
    require(
        re.search(
            r"^  " + re.escape(branch[1]) + r" = or i1 " + bounds[0] + ", " + bounds[1] + "$",
            text,
            re.M,
        ),
        "The entry branch is not the grid bound check",
    )
    attributes = {}
    for kind in ("nounwind readnone", "nounwind readonly", "nounwind"):
        found = re.findall(r"^attributes #(\d+) = \{ " + kind + r" \}$", text, re.M)
        require(len(found) == 1, f"Attribute set '{kind}' not found once")
        attributes[kind] = found[0]
    props, body_props = entry_properties(text)
    flags = re.match(r"i32 0, i64 (\d+),", body_props)
    require(flags, "Missing shader flags")
    return dict(
        body=body,
        x=x,
        y=y,
        x_line=x_line,
        grid=list(bounds[2:]),
        attributes=attributes,
        props=props,
        flags=int(flags[1]),
    )


def lower(text, plan_, accumulate="binned", postscale=True):
    """Rewrite the 16 spatial chains with the two-wave F(2,3) lowering; returns (text, proof)."""
    require(accumulate in ACCUMULATORS, "Unknown accumulator")
    require(
        hashlib.sha256(text.encode()).hexdigest() == plan_["source_sha256"],
        "Plan is for another program",
    )
    shape = _shape(text, plan_)
    pitch, base = plan_["row_pitch"], plan_["input_buffer_base"]
    root, properties = plan_["resource"]
    words = PIXEL_BYTES // 4
    role_words = TAPS * words * LANES
    common_words = OUTPUTS * LANES
    x, y = shape["x"], shape["y"]
    e = Emitter()
    # Remap x: within each 64-thread group, lane L of the wave holding thread 0 takes x = 2L,
    # the other wave x = 2L + 1. Emitted before the original bound check.
    e.line("  %wino_orig_x = call i32 @dx.op.threadId.i32(i32 93, i32 0)")
    e.line("  %wino_tid = call i32 @dx.op.threadIdInGroup.i32(i32 95, i32 0)")
    e.line("  %wino_lane = call i32 @dx.op.waveGetLaneIndex(i32 111)")
    e.line("  %wino_zero = icmp eq i32 %wino_tid, 0")
    e.line("  %wino_left = call i1 @dx.op.waveAnyTrue(i32 113, i1 %wino_zero)")
    e.line("  %wino_role = select i1 %wino_left, i32 0, i32 1")
    e.line(f"  %wino_group_x = and i32 %wino_orig_x, {-GROUP}")
    e.line("  %wino_pair_offset = shl i32 %wino_lane, 1")
    e.line("  %wino_pair_x = add i32 %wino_group_x, %wino_pair_offset")
    e.line(f"  {x} = add i32 %wino_pair_x, %wino_role")
    mapping, e.lines = e.lines, []
    e.line(
        f"  %wino_handle = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {root},"
        f" %dx.types.ResourceProperties {properties})"
    )
    if base:
        e.line(f"  %wino_row_offset = mul i32 {y}, {pitch}")
        e.line(f"  %wino_row_bytes = add i32 %wino_row_offset, {base}")
    else:
        e.line(f"  %wino_row_bytes = mul i32 {y}, {pitch}")
    shift = PIXEL_BYTES.bit_length() - 1
    e.line(f"  %wino_pair_bytes = shl i32 %wino_pair_x, {shift}")
    e.line("  %wino_pair_address = add i32 %wino_row_bytes, %wino_pair_bytes")
    e.line(f"  %wino_input_role = mul i32 %wino_role, {role_words}")
    e.line("  %wino_input_base = add i32 %wino_input_role, %wino_lane")
    e.line("  %wino_peer_role = xor i32 %wino_role, 1")
    e.line(f"  %wino_peer_offset = mul i32 %wino_peer_role, {role_words}")
    e.line("  %wino_peer_base = add i32 %wino_peer_offset, %wino_lane")
    e.line(f"  %wino_common_role = shl i32 %wino_role, {common_words.bit_length() - 1}")
    e.line("  %wino_common_base = add i32 %wino_common_role, %wino_lane")
    e.line(f"  %wino_common_peer_role = shl i32 %wino_peer_role, {common_words.bit_length() - 1}")
    e.line("  %wino_common_peer_base = add i32 %wino_common_peer_role, %wino_lane")
    # Each thread publishes the pixel at x + 1: d1 for the even wave, d2 for the odd one.
    e.line(f"  %wino_publish_x = add i32 {x}, 1")
    e.line(f"  %wino_publish_xbytes = shl i32 %wino_publish_x, {shift}")
    e.line("  %wino_publish_address = add i32 %wino_row_bytes, %wino_publish_xbytes")
    inputs_size, common_size = 2 * role_words, 2 * common_words

    def global_words(address, ky):
        address = e.emit(f"add i32 {address}, {ky * pitch}")
        raw = e.emit(
            f"call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %wino_handle,"
            f" i32 {address}, i32 undef, i8 15, i32 4)"
        )
        return [e.emit(f"extractvalue %dx.types.ResRet.i32 {raw}, {k}") for k in range(words)]

    for ky in range(TAPS):
        for word, value in enumerate(global_words("%wino_publish_address", ky)):
            index = e.emit(f"add i32 %wino_input_base, {(ky * words + word) * LANES}")
            e.shared("wino_inputs", inputs_size, index, value)
    e.line("  call void @dx.op.barrier(i32 80, i32 9)")
    e.line("  br i1 %wino_left, label %wino_left_math, label %wino_right_math")
    bounds = plan_["transformed_input_domains"]
    records, others = [], []
    for role, other, domain, sign in ROLES:
        left = role == "left"
        if not other:
            e.line("")
            e.line(f"wino_{role}_math:")
        else:
            other_address = e.emit(
                "add i32 %wino_pair_address, " + ("0" if left else str(3 * PIXEL_BYTES))
            )
        inputs = []
        for ky in range(TAPS):
            words_ = global_words(other_address, ky) if other else None
            for word in range(words):
                peer_index = e.emit(f"add i32 %wino_peer_base, {(ky * words + word) * LANES}")
                peer = e.shared("wino_inputs", inputs_size, peer_index)
                if other:
                    own = words_[word]
                else:
                    own_index = e.emit(f"add i32 %wino_input_base, {(ky * words + word) * LANES}")
                    own = e.shared("wino_inputs", inputs_size, own_index)
                transformed = []
                for a, c in zip(e.unpack(own), e.unpack(peer)):
                    op = "add" if left and not other else "sub"
                    if other and not left:  # d3 = d1 - d3: peer minus own
                        a, c = c, a
                    transformed.append(e.emit(f"{op} <2 x i16> {a}, {c}"))
                for byte in range(4):
                    inputs.append(
                        e.emit(f"extractelement <2 x i16> {transformed[byte % 2]}, i32 {byte // 2}")
                    )
        matrix = domain_matrix(plan_, domain, sign)
        values, record = _domain_dots(
            e, inputs, matrix, bounds[domain], domain, accumulate, postscale
        )
        record.update(name=f"{role}-{'other' if other else 'common'}")
        records.append(record)
        if other:
            others.append(values)
            e.line("  br label %wino_join")
        else:
            for output, value in enumerate(values):
                index = e.emit(f"add i32 %wino_common_base, {output * LANES}")
                e.shared("wino_common", common_size, index, value)
    e.line("")
    e.line("wino_join:")
    joined = [
        e.emit(f"phi i32 [ {others[0][i]}, %wino_left_math ], [ {others[1][i]}, %wino_right_math ]")
        for i in range(OUTPUTS)
    ]
    e.line("  call void @dx.op.barrier(i32 80, i32 9)")
    inverse = []
    for output, value in enumerate(joined):
        own_index = e.emit(f"add i32 %wino_common_base, {output * LANES}")
        peer_index = e.emit(f"add i32 %wino_common_peer_base, {output * LANES}")
        own = e.shared("wino_common", common_size, own_index)
        peer = e.shared("wino_common", common_size, peer_index)
        negative = e.emit(f"sub i32 0, {own}")
        signed = e.emit(f"select i1 %wino_left, i32 {own}, i32 {negative}")
        numerator = e.emit(f"add i32 {value}, {peer}")
        numerator = e.emit(f"add i32 {numerator}, {signed}")
        inverse.append(e.emit(f"ashr i32 {numerator}, 1"))
    replacements = {}
    for row, chain, value in zip(plan_["selected"], plan_["chains"], inverse):
        for line in chain:
            replacements[line] = []
        replacements[row["line"]] = [f"  {row['tail']} = add i32 {row['base']}, {value}"]
    result = []
    for n, line in enumerate(text.splitlines()):
        if line == shape["x_line"]:
            result.extend(mapping)
        elif line == shape["body"] + ":":
            result.extend([line, *e.lines])
        else:
            result.extend(replacements.get(n, [line]))
    code = "\n".join(result) + "\n"
    code = code.replace(
        "define void @",
        f"@wino_inputs = external addrspace(3) global [{inputs_size} x i32], align 4\n"
        f"@wino_common = external addrspace(3) global [{common_size} x i32], align 4\n\ndefine void @",
        1,
    )
    attribute = 1 + max(map(int, re.findall(r"^attributes #(\d+) =", code, re.M)))
    a = shape["attributes"]
    code += (
        f"\ndeclare i32 @dx.op.threadIdInGroup.i32(i32, i32) #{a['nounwind readnone']}\n"
        f"declare i32 @dx.op.waveGetLaneIndex(i32) #{a['nounwind readonly']}\n"
        f"declare i1 @dx.op.waveAnyTrue(i32, i1) #{a['nounwind']}\n"
        f"declare void @dx.op.barrier(i32, i32) #{attribute}\n"
        f"attributes #{attribute} = {{ noduplicate nounwind }}\n"
    )
    old = f"!{shape['props']} = !{{i32 0, i64 {shape['flags']},"
    require(code.count(old) == 1, "Shader flags not found once")
    code = code.replace(
        old, f"!{shape['props']} = !{{i32 0, i64 {shape['flags'] | WAVE_OPS_FLAG},", 1
    )
    proof = dict(
        accumulate=accumulate,
        postscale=postscale,
        grid=shape["grid"],
        row_pitch=pitch,
        input_buffer_base=base,
        converted_dots=OUTPUTS * CHAIN,
        shared_bytes=4 * (inputs_size + common_size),
        group_barriers=2,
        wave_size_required=LANES,
        products=sum(r["products"] for r in records),
        chunks=sum(r["chunks"] for r in records),
        domains=records,
        numerator_bounds=plan_["numerator_bounds"],
    )
    return code, proof
