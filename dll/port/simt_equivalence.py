#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Differential execution of generated programs against the SDK program of their slot.

For each slot of a recipe, the gated SDK program (disassembled with the pinned DXC), the
generated program (`lower_pass.py generate` output) and optionally RC11's release program run on
the same modelled resources in the SIMT interpreter (simt.py): whole 64-thread groups, scratch
words from random and extreme fills, the SDK's own initializer table (the fast path) or a copy
with one specialised word changed (the guard must fall back), dispatch z = 0 and z = 1, grid
corners and an out-of-range row. Every scratch store must be identical.

One SDK difference is expected and checked separately: the SDK's masked DWORD stores go to the
discard address 128 MiB, which RC7 moved past the buffer (`masked_stores`, already in RC11), so
those stores are excluded from the SDK side and must equal the candidate's dropped stores.

A mutation control changes one coefficient in the candidate's fast path; the comparison must
then fail, which shows the scenarios exercise the lowered arithmetic.
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

if __package__:
    from . import drop_profile, guarded, lower_pass, simt
    from .ir import LoweringError, rename_ssa, require
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drop_profile
    import guarded
    import lower_pass
    import simt
    from ir import LoweringError, rename_ssa, require

DISCARD = 134217728
DLL = Path(__file__).resolve().parents[1]


def mix(value):
    value = (value + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return value ^ (value >> 31)


def fill(kind, seed):
    """Scratch contents: `random` words, `extreme` bytes (each -128 or 127), or one constant."""
    if kind == "random":
        return lambda address: mix((seed << 32) ^ address) & 0xFFFFFFFF
    if kind == "extreme":

        def extreme(address):
            bits = mix((seed << 32) ^ address)
            return sum((0x80 if bits >> k & 1 else 0x7F) << (8 * k) for k in range(4))

        return extreme
    if kind in ("min", "max", "alternate"):
        word = dict(min=0x80808080, max=0x7F7F7F7F, alternate=0x807F807F)[kind]
        return lambda address: word
    raise ValueError("Unknown fill " + kind)


def grid(text):
    """The SDK program's dispatch bound: the first `icmp ugt` of thread x and y with literals."""
    x = re.search(r"(%v\d+) = call i32 @dx.op.threadId.i32\(i32 93, i32 0\)", text)[1]
    y = re.search(r"(%v\d+) = call i32 @dx.op.threadId.i32\(i32 93, i32 1\)", text)[1]
    bx = re.search(r"icmp ugt i32 " + re.escape(x) + r", (\d+)", text)
    by = re.search(r"icmp ugt i32 " + re.escape(y) + r", (\d+)", text)
    require(bx and by, "No dispatch bound in the SDK program")
    return int(bx[1]) + 1, int(by[1]) + 1


def scenarios(width, height):
    last = (width - 1) // 64
    return [
        dict(
            name="random-corners",
            fill="random",
            seed=1,
            model="sdk",
            groups=[(0, 0, 0), (last, 0, 0), (0, height - 1, 0), (last, height - 1, 0)],
        ),
        dict(
            name="random-interior",
            fill="random",
            seed=2,
            model="sdk",
            groups=[(last // 2, height // 2, 0)],
        ),
        dict(
            name="extreme-bytes",
            fill="extreme",
            seed=3,
            model="sdk",
            groups=[(1 % (last + 1), 7, 0), (last, height - 2, 0)],
        ),
        dict(name="all-minus-128", fill="min", seed=0, model="sdk", groups=[(0, 5, 0)]),
        dict(name="all-plus-127", fill="max", seed=0, model="sdk", groups=[(last, 9, 0)]),
        dict(name="alternating", fill="alternate", seed=0, model="sdk", groups=[(0, 11, 0)]),
        dict(
            name="changed-model-word",
            fill="random",
            seed=4,
            model="changed",
            groups=[(0, 3, 0), (last, height - 1, 0)],
        ),
        dict(name="row-out-of-range", fill="random", seed=6, model="sdk", groups=[(0, height, 0)]),
        # Pass 9 also bounds threads by CsTensorSizes row 9; cut it inside a group (others ignore it).
        dict(
            name="tensor-bound",
            fill="random",
            seed=7,
            model="sdk",
            tensor=(width - 37, height - 2),
            groups=[(last, height // 3, 0), (0, height - 3, 0), (0, height - 2, 0)],
        ),
    ]
    # Dispatch z > 0 is not a valid input: the SDK programs of passes 7-9 then index their own
    # 32-entry local arrays out of range (the guard sends z > 0 to the unchanged SDK body).


def changed_model(model, text):
    """The model with word 7 of the candidate's guard table changed (the guard must fail)."""
    table = re.search(
        r"^@bc250_(?:expected|guard_vectors) = internal unnamed_addr constant \[\d+ x [^\]]*\] \[(.*)\], align",
        text,
        re.M,
    )
    require(table, "Candidate has no guard table")
    values = [int(v) & 0xFFFFFFFF for v in re.findall(r"i32 (-?\d+)", table[1])]
    packed = struct.pack(f"<{len(values)}I", *values)
    start = model.find(packed)
    require(
        start >= 0 and start % 4 == 0 and model.find(packed, start + 1) < 0,
        "Guard table not in model",
    )
    at = start // 4 + 7
    data = bytearray(model)
    struct.pack_into("<I", data, 4 * at, values[7] ^ 0x01000000)
    return bytes(data), at


def run(program, model, scenario, cbuffer):
    if "tensor" in scenario:
        cbuffer = bytearray(cbuffer.ljust(272, b"\0"))
        struct.pack_into("<2I", cbuffer, 16 * 9, *scenario["tensor"])
        cbuffer = bytes(cbuffer)
    resources = simt.Resources(fill(scenario["fill"], scenario["seed"]), model, cbuffer)
    discard = [0]
    original = resources.store

    def store(handle, index, values, mask):
        for k in range(4):
            if mask >> k & 1 and index + 4 * k == DISCARD:
                discard[0] += 1
        original(handle, index, values, mask)

    resources.store = store
    votes = []
    for group in scenario["groups"]:
        votes.extend((group, *v) for v in simt.run_group(program, resources, group))
    writes = dict(resources.writes)
    discarded = discard[0]
    writes.pop(DISCARD, None)
    return dict(
        writes=writes,
        stores=resources.stores - discarded,
        discard_stores=discarded,
        dropped=resources.dropped,
        votes=votes,
        digest=hashlib.sha256(repr(sorted(writes.items())).encode()).hexdigest(),
    )


def mutate(text):
    """One i16 coefficient of the first native product of the fast path, plus one."""
    m = re.search(r"mul <2 x i16> (%[\w.]+), <i16 (-?\d+), i16 (-?\d+)>", text)
    require(m, "No native product to mutate")
    value = int(m[2]) + 1 if int(m[2]) < 127 else int(m[2]) - 1
    return text[: m.start(2)] + str(value) + text[m.end(2) :], m[0]


def compare_slot(sdk_text, candidates, model, cbuffer=b"", mutation=True):
    """Run every scenario on the SDK program and each candidate; every store must match."""
    width, height = grid(sdk_text)
    reference = simt.Program(sdk_text)
    programs = {name: simt.Program(text) for name, text in candidates.items()}
    changed, changed_word = changed_model(model, next(iter(candidates.values())))
    report = dict(grid=[width, height], changed_model_word=changed_word, scenarios=[])
    for scenario in scenarios(width, height):
        table = changed if scenario["model"] == "changed" else model
        base = run(reference, table, scenario, cbuffer)
        row = dict(
            name=scenario["name"],
            groups=scenario["groups"],
            sdk_stores=base["stores"],
            sdk_discard_stores=base["discard_stores"],
            sdk_digest=base["digest"],
            candidates={},
        )
        for name, program in programs.items():
            result = run(program, table, scenario, cbuffer)
            expected_vote = 1 if scenario["model"] == "sdk" else 0
            row["candidates"][name] = dict(
                wave=program.wave,
                stores=result["stores"],
                dropped=result["dropped"],
                digest=result["digest"],
                equal=result["writes"] == base["writes"],
                dropped_equals_sdk_discards=result["dropped"] == base["discard_stores"],
                votes=sorted({v[3] for v in result["votes"]}),
                vote_count=len(result["votes"]),
                expected_path_taken=bool(result["votes"])
                and all(v[3] == expected_vote for v in result["votes"]),
            )
        report["scenarios"].append(row)
    report["all_equal"] = all(
        c["equal"] and c["dropped_equals_sdk_discards"] and c["expected_path_taken"]
        for row in report["scenarios"]
        for c in row["candidates"].values()
    )
    report["stores_compared"] = sum(row["sdk_stores"] for row in report["scenarios"])
    if mutation:
        name, text = next(iter(candidates.items()))
        mutated, site = mutate(text)
        program = simt.Program(mutated)
        detected = []
        for scenario in scenarios(width, height)[:3]:
            base = run(reference, model, scenario, cbuffer)
            result = run(program, model, scenario, cbuffer)
            detected.append(result["writes"] != base["writes"])
        report["mutation"] = dict(
            candidate=name, site=site, detected=any(detected), per_scenario=detected
        )
    return report


def check(recipe_path, sdk, dxc, generated, slots=None, release=True, jobs=1):
    recipe = lower_pass.load_recipe(recipe_path)
    data = Path(sdk).read_bytes()
    _, profile = drop_profile.select(lower_pass.sha(data))
    drop_profile.verify(data, profile)  # license gate and reviewed layout
    manifest = json.loads((DLL / "manifest.json").read_text())
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    specialize = next(
        step[1]
        for steps in recipe["sequences"].values()
        for step in steps
        if step[0] == "specialize"
    )["model"]
    model, identity = guarded.model_table(
        data, int(specialize["accessor_rva"], 16), specialize["bytes"], specialize["sha256"]
    )
    cbuffer = bytearray(272)
    selected = slots or list(recipe["slots"])
    results = {}
    with tempfile.TemporaryDirectory() as work:
        toolchain = lower_pass.Toolchain(dxc, work, manifest["dxcompiler_sha256"])
        for key in selected:
            offset = int(key, 16)
            slot = next(s for s in profile["shaders"]["slots"] if s["offset"] == offset)
            container = data[offset : offset + slot["bytes"]]
            require(lower_pass.sha(container) == slot["sha256"], "SDK program differs from profile")
            sdk_text = rename_ssa(toolchain.disassemble(container, f"{offset:08x}"))
            width, height = grid(sdk_text)
            # CsTensorSizes (pass 9 reads row 9 as its output bound): the dispatch grid.
            struct.pack_into("<4I", cbuffer, 16 * 9, width, height, 0, 0)
            candidates = {
                "generated": (Path(generated) / "shaders" / f"{offset:08x}.ll").read_text()
            }
            if release:
                candidates["rc11"] = (DLL / rows[offset]["source"]).read_text()
            report = compare_slot(sdk_text, candidates, model, bytes(cbuffer))
            report.update(sdk_sha256=slot["sha256"], model=identity)
            results[key] = report
            print(
                key,
                "equal" if report["all_equal"] else "DIFFERS",
                "stores",
                report["stores_compared"],
                "mutation detected",
                report["mutation"]["detected"],
                flush=True,
            )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--dxc", type=Path, required=True)
    parser.add_argument("--generated", type=Path, required=True, help="lower_pass generate output")
    parser.add_argument("--slots", nargs="*")
    parser.add_argument("--no-release", action="store_true", help="skip RC11's release program")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        results = check(
            args.recipe, args.sdk, args.dxc, args.generated, args.slots, not args.no_release
        )
    except (LoweringError, simt.Unsupported, drop_profile.ProfileError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    args.json.write_text(json.dumps(results, indent=1) + "\n")
    ok = all(r["all_equal"] and r["mutation"]["detected"] for r in results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
