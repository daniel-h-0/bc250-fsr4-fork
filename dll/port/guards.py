# SPDX-License-Identifier: MIT
"""RC8 vector guard tables for the guarded (weight-specialised) passes 3, 6, 7, 8, 9 and 11.

RC7's guard (research specialize_weights.py) checks every model-initializer word the fast path
replaced by a constant: each lane walks the table `@bc250_expected` in steps of four words and
compares the four words of one raw-buffer load with four scalar table loads; `waveAllTrue`
combines the lanes. RC8 (`build_vector_guards.py`, research/fsr4-dll-opt-20260910) loads the
same four words as one `<4 x i32>` from `@bc250_guard_vectors`, a copy of the table in groups of
four. Only those four table loads change: the resource loads, comparisons, control flow and all
model arithmetic are the same instructions. The table holds exactly the original words, and a
walk with stride 4 x lanes covers each word once for 32- and 64-lane waves.
"""

import hashlib
import re
import struct

if __package__:
    from .ir import entry_properties, require
else:
    from ir import entry_properties, require

TABLE = re.compile(
    r"^@bc250_expected = internal unnamed_addr constant \[(\d+) x i32\] \[(.*)\], align 16$", re.M
)


def vector_guards(text):
    """Replace the guard's four scalar table loads by one vector load; return (text, proof)."""
    require(
        "%optguard" not in text and "@bc250_guard_vectors" not in text, "Guard already vectorised"
    )
    match = TABLE.search(text)
    require(match, "No guard table @bc250_expected")
    count = int(match[1])
    values = [int(v) for v in re.findall(r"i32 (-?\d+)", match[2])]
    require(len(values) == count and count % 4 == 0, "Guard table is not whole groups of four")
    require(text.count("@bc250_expected") == 5, "Guard table is not used by exactly four loads")
    lines = text.splitlines()
    defs = {}
    for index, line in enumerate(lines):
        if m := re.match(r"^  (%[\w.]+) = (.*)", line):
            defs[m[1]] = (index, m[2])
    gep = re.compile(
        r"^  (%[\w.]+) = getelementptr \["
        + str(count)
        + r" x i32\], \["
        + str(count)
        + r" x i32\]\* @bc250_expected, i32 0, i32 (%[\w.]+)$"
    )
    geps = [(i, m[1], m[2]) for i, line in enumerate(lines) if (m := gep.fullmatch(line))]
    require(len(geps) == 4, "Expected four guard table addresses")
    base = geps[0][2]
    phi = re.fullmatch(
        r"phi i32 \[ (%[\w.]+), (%[\w.]+) \], \[ (%[\w.]+), (%[\w.]+) \]", defs[base][1]
    )
    require(phi, "Guard index is not a two-way phi")
    initial, step_value = phi[1], phi[3]
    require(re.fullmatch(r"shl i32 %[\w.]+, 2", defs[initial][1]), "Guard start is not lane * 4")
    step = re.fullmatch(r"add i32 " + re.escape(base) + r", (%[\w.]+)", defs[step_value][1])
    require(
        step and re.fullmatch(r"shl i32 %[\w.]+, 2", defs[step[1]][1]),
        "Guard step is not lanes * 4",
    )
    require(
        re.search(r"icmp ult i32 " + re.escape(base) + ", " + str(count) + r"\b", text),
        "Guard bound",
    )
    replacement = {}
    for lane, (index, name, at) in enumerate(geps):
        if lane:
            require(
                re.fullmatch(r"(?:or|add) i32 " + re.escape(base) + ", " + str(lane), defs[at][1]),
                "Guard table loads are not four adjacent words",
            )
        uses = [
            (n, v)
            for v, (n, rhs) in defs.items()
            if re.fullmatch(r"load i32, i32\* " + re.escape(name) + r", align (?:4|8|16)", rhs)
        ]
        require(len(uses) == 1, "A guard table address has other uses")
        require(
            len(re.findall(re.escape(name) + r"\b", text)) == 2,
            "A guard table address has other uses",
        )
        at_load, value = uses[0]
        replacement[index] = []
        replacement[at_load] = [f"  {value} = extractelement <4 x i32> %optguard_value, i32 {lane}"]
    replacement[geps[0][0]] = [
        f"  %optguard_index = lshr i32 {base}, 2",
        f"  %optguard_ptr = getelementptr [{count // 4} x <4 x i32>], [{count // 4} x <4 x i32>]*"
        " @bc250_guard_vectors, i32 0, i32 %optguard_index",
        "  %optguard_value = load <4 x i32>, <4 x i32>* %optguard_ptr, align 16",
    ]
    vectors = [values[i : i + 4] for i in range(0, count, 4)]
    for width in (32, 64):
        visited = []
        for lane in range(width):
            for first in range(lane * 4, count, width * 4):
                require(vectors[first // 4] == values[first : first + 4], "Vector table differs")
                visited.extend(range(first, first + 4))
        require(
            sorted(visited) == list(range(count)), f"Guard walk does not cover the table ({width})"
        )
    table = (
        f"@bc250_guard_vectors = internal unnamed_addr constant [{count // 4} x <4 x i32>] ["
        + ", ".join("<4 x i32> <" + ", ".join(f"i32 {v}" for v in g) + ">" for g in vectors)
        + "], align 16"
    )
    out = []
    for index, line in enumerate(lines):
        if line == match[0]:
            out.append(table)
        else:
            out.extend(replacement.get(index, [line]))
    code = "\n".join(out) + "\n"
    require("@bc250_expected" not in code, "Scalar guard table still referenced")
    for key, body in [("version", "i32 1, i32 9"), ("shaderModel", '!"cs", i32 6, i32 9')]:
        node = re.search(r"!dx." + key + r" = !\{!(\d+)\}", code)[1]
        code = re.sub(
            r"^!" + node + r" = !\{[^\n]+\}",
            lambda m: "!" + node + " = !{" + body + "}",
            code,
            flags=re.M,
        )
    # A wave-size property of a 6.6/6.7 program changes from tag 11 to tag 23 at 6.9.
    props, body = entry_properties(code)
    if wave := re.search(r"i32 11, !(\d+)", body):
        node = wave[1]
        w = re.search(r"^!" + node + r" = !\{i32 (32|64)\}$", code, re.M)
        require(w, "Unexpected wave-size node")
        line = re.search(r"^!" + props + r" = !\{(.*)\}$", code, re.M)[0]
        code = code.replace(line, line.replace("i32 11, !" + node, "i32 23, !" + node))
        code = code.replace(w[0], f"!{node} = !{{i32 {w[1]}, i32 0, i32 0}}")
    proof = dict(
        words=count,
        groups=count // 4,
        all_words_once_per_wave=[32, 64],
        table_sha256=hashlib.sha256(struct.pack("<" + "i" * count, *values)).hexdigest(),
    )
    return code, proof
