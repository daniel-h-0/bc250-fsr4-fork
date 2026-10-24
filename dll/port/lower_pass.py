#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Regenerate one model pass's replacement programs from the SDK DLL's own DXIL.

A recipe (recipes/*.json) names, for every INT8 slot of one model entry point, the sequence of
lowering stages that turns the SDK program into the RC release program. Weights, kernel layout,
bins, lane pairs and flush points are derived from the SDK program by the stages; the recipe holds
only the stage order and options. `generate` gates the SDK DLL on the allowlist and its reviewed
drop profile, disassembles each slot with the pinned DXC, runs the stages, assembles and
validates the result, and compares it with dll/manifest.json: the validated container must equal
the manifest's replacement hash, and the source must equal the manifest's source except its first
line (the `; ModuleID` comment `opt` writes from its input path). A slot the recipe pins under
`expected` gets a program that is not the release's; it must equal the pin instead (container and
whole source), and the group's checker (equivalence.py, simt_equivalence.py, execute.py,
differential.py) proves it against the SDK program.

Stages run on text IR with the DXC 1.9.2607 tools (`dxc -dumpbin`, `opt`, libdxcompiler through
dll/assemble.cpp); see dll/port/README.md for the recipe format.
"""

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from . import (
        bins,
        drop_profile,
        final_pass,
        guarded,
        guards,
        packing,
        sdk_intake,
        transport,
        unroll,
        winograd,
    )
    from .ir import (
        LoweringError,
        Program,
        canonical_ssa,
        count_dots,
        drop_dot_declaration,
        masked_stores,
        rename_ssa,
        require,
        shader_model,
        wave_size,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bins
    import drop_profile
    import final_pass
    import guarded
    import guards
    import packing
    import sdk_intake
    import transport
    import unroll
    import winograd
    from ir import (
        LoweringError,
        Program,
        canonical_ssa,
        count_dots,
        drop_dot_declaration,
        masked_stores,
        rename_ssa,
        require,
        shader_model,
        wave_size,
    )

ROOT = Path(__file__).resolve().parent
DLL = ROOT.parent
RECIPES = ROOT / "recipes"
KIND = "fsr4-lowering-recipe"
# Members of the extracted DXC 1.9.2607 Linux archive pinned by dll/manifest.json
# (`dxcompiler_archive`); libdxcompiler.so is pinned there as `dxcompiler_sha256`.
TOOLS = {
    "bin/dxc": "b1bfa493d5c780b94c20b8b5f5aed50d1c4d03339cd55f496bd223eedeec1734",
    "bin/opt": "e0d605396860b49678cd7f39678c64769e4545b1cf81623160bfc06a41349ab2",
}
MODULE_ID = re.compile(r"; ModuleID = '[^'\n]*'\n")
# A recipe slot whose program is not the manifest's names its pinned result and why:
# `rederived` = dll/rederived.json's program for the slot, `new` = a program RC11 does not have
# (proven equal to the SDK program by its recipe's stages and checks, see dll/port/README.md).
BASES = ("rederived", "new")
PIN_FIELDS = ("source_sha256", "replacement_sha256")


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def _casts():
    spec = importlib.util.spec_from_file_location(
        "bc250_scalarize_casts", DLL / "scalarize_casts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Toolchain:
    """The pinned DXC tools: disassembler, LLVM optimizer, assembler + validator."""

    def __init__(self, root, work, dxcompiler_sha256):
        root = Path(root).resolve()
        self.dxc, self.opt_path = root / "bin/dxc", root / "bin/opt"
        self.library = root / "lib/libdxcompiler.so"
        for path, digest in [
            (self.dxc, TOOLS["bin/dxc"]),
            (self.opt_path, TOOLS["bin/opt"]),
            (self.library, dxcompiler_sha256),
        ]:
            if sha(path.read_bytes()) != digest:
                raise ValueError(f"{path} is not the pinned DXC 1.9.2607 file")
        self.work = Path(work)
        self.assembler = self.work / "assemble"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(DLL / "assemble.cpp"),
                "-ldl",
                "-o",
                str(self.assembler),
            ],
            check=True,
        )

    def _run(self, command, cwd, timeout=600):
        q = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if q.returncode:
            raise RuntimeError(f"{Path(command[0]).name} failed: {q.stderr[:2000]}")
        return q.stdout

    def disassemble(self, container, name):
        with tempfile.TemporaryDirectory(dir=self.work) as tmp:
            path = Path(tmp) / f"{name}.dxil"
            path.write_bytes(container)
            return self._run([str(self.dxc), "-dumpbin", path.name], tmp)

    def opt(self, text, passes, name="input"):
        """`opt -S` over text; the output's ModuleID line names only `<name>.ll`."""
        with tempfile.TemporaryDirectory(dir=self.work) as tmp:
            (Path(tmp) / f"{name}.ll").write_text(text)
            self._run([str(self.opt_path), "-S", *passes, f"{name}.ll", "-o", "out.ll"], tmp)
            return (Path(tmp) / "out.ll").read_text()

    def assemble(self, text):
        """Assembled and validated DXIL container bytes."""
        with tempfile.TemporaryDirectory(dir=self.work) as tmp:
            tmp = Path(tmp)
            (tmp / "shader.ll").write_text(text)
            for action, first, last in [
                ("assemble", "shader.ll", "raw.dxil"),
                ("validate", "raw.dxil", "shader.dxil"),
            ]:
                self._run([str(self.assembler), str(self.library), action, first, last], tmp)
            return (tmp / "shader.dxil").read_bytes()


class NamedToolchain:
    """A toolchain whose `opt` calls name the slot, so ModuleID lines are reproducible."""

    def __init__(self, toolchain, name):
        self.toolchain, self.name = toolchain, name

    def opt(self, text, passes):
        return self.toolchain.opt(text, passes, self.name)


def _rewrite(function):
    def step(text, context, **options):
        text, proof = function(text, **options)
        return canonical_ssa(text), proof

    return step


def step_unroll(text, context):
    text, loops = unroll.unroll(text, context["toolchain"])
    program = Program(text)
    require(program.dynamic_dots == 0, "A dot4 weight is still dynamic after unrolling")
    require(
        len(program.dots) == 4 * context["sdk_dots"],
        "Unrolling did not expose four dots per SDK dot",
    )
    require(unroll.fold_mads(text)[1] == 0, "Foldable integer MADs remain after unrolling")
    return text, dict(
        loops=len(loops),
        iterations=sorted({loop["iterations"] for loop in loops}),
        dots=len(program.dots),
    )


def step_winograd(text, context, accumulate="binned", postscale=True):
    plan = winograd.plan(text)
    text, proof = winograd.lower(text, plan, accumulate=accumulate, postscale=postscale)
    return canonical_ssa(text), proof


def _named(function):
    """A stage that keeps the SDK's value names (no canonical_ssa afterwards)."""

    def step(text, context, **options):
        return function(text, **options)

    return step


def step_drop_dot_declaration(text, context):
    return drop_dot_declaration(text), {}


def step_shader_model(text, context, minor):
    return shader_model(text, minor), dict(minor=minor)


def step_wave_size(text, context, lanes):
    return wave_size(text, lanes)


def step_masked_stores(text, context):
    text, changed = masked_stores(text)
    return text, dict(repaired=len(changed))


def step_casts(text, context):
    text, count = context["casts"].lower(text)
    return text, dict(lowered=count)


def release_source(row):
    """A manifest row's committed release source, checked against its pinned hash."""
    source = (DLL / row["source"]).read_text()
    require(sha(source) == row["source_sha256"], f"{row['source']} differs from the manifest")
    return source


def step_release_source(text, context):
    """Start from the slot's committed release source instead of the SDK program.

    For passes whose RC7 lowering is not ported to these stages yet (prep, the guarded passes);
    the release source is the reviewed RC7 output and is pinned by the manifest.
    """
    require(context["first"], "release_source must be the first stage")
    row = context["row"]
    return release_source(row), dict(source=row["source"], source_sha256=row["source_sha256"])


def step_transport(text, context, donor):
    """The donor slot's release program with this slot's grid literals (transport.py)."""
    require(context["first"], "transport must be the first stage (it reads the SDK program)")
    offset = int(donor, 16)
    require(offset != context["offset"], "A slot cannot be its own donor")
    donor_row = context["rows"].get(offset)
    require(donor_row is not None, f"Donor {donor} is not a manifest row")
    require(context["slots"][offset]["entry"] == context["entry"], "Donor is another entry's slot")
    out, proof = transport.transport(
        context["sdk_program"](offset),
        text,
        release_source(donor_row),
        release_source(context["row"]),
    )
    proof.update(donor=donor, donor_source_sha256=donor_row["source_sha256"])
    return out, proof


def step_vector_guards(text, context):
    return guards.vector_guards(text)


def step_opt(text, context, passes):
    require(all(re.fullmatch(r"[a-z][a-z0-9-]*", p) for p in passes), "Invalid opt pass name")
    return context["toolchain"].opt(text, ["-" + p for p in passes]), dict(passes=passes)


def step_canonical(text, context):
    return canonical_ssa(text), {}


def step_specialize(text, context, model):
    """RC7 guarded specialisation with the initializer table the recipe's accessor returns."""
    require(re.fullmatch(r"0x[0-9a-f]+", model["accessor_rva"]), "Invalid accessor RVA")
    table, identity = guarded.model_table(
        context["sdk"], int(model["accessor_rva"], 16), model["bytes"], model["sha256"]
    )
    text, record = guarded.specialize(text, context["toolchain"], table)
    context.update(basis=text, native_tails=[])
    return text, dict(record, model_offset=identity["offset"])


def _native(function):
    def step(text, context, **options):
        require("basis" in context, "Native stages need a specialised basis")
        text, proof = function(text, **options)
        context["native_tails"].extend(proof.pop("tails"))
        return text, proof

    return step


def step_mixed_float(text, context, roots=None, graph_audit=False):
    """RC8 exact FP32 graph; `roots: native` passes the native tails' basis intervals."""
    require(roots in (None, "native"), "Invalid roots option")
    external = {}
    if roots == "native":
        intervals = guarded.graph_intervals(context["basis"])
        require(all(t in intervals for t in context["native_tails"]), "A native tail is unbounded")
        external = {t: intervals[t] for t in context["native_tails"]}
    remaining = {m[1] for line in text.splitlines() if (m := guarded.literal_dot(line))}
    code, proof = guarded.mixed_float(text, external)
    if graph_audit:
        control, _ = guarded.mixed_float(context["basis"])
        proof.update(guarded.float_graph_audit(control, code, remaining))
    return code, proof


STEPS = dict(
    unroll=step_unroll,
    winograd=step_winograd,
    pointwise=_rewrite(bins.pointwise),
    stream=_rewrite(packing.stream_outputs),
    prefix_pairs=_rewrite(packing.prefix_pairs),
    half_groups=_rewrite(packing.half_groups),
    pack=_rewrite(packing.packed_reductions),
    drop_dot_declaration=step_drop_dot_declaration,
    shader_model=step_shader_model,
    wave_size=step_wave_size,
    masked_stores=step_masked_stores,
    casts=step_casts,
    opt=step_opt,
    float_graph=_named(final_pass.float_graph),
    native_blocks=_named(final_pass.native_blocks),
    color_reuse=_named(final_pass.color_reuse),
    canonical=step_canonical,
    specialize=step_specialize,
    point_small=_native(guarded.point_small),
    native_stream=_native(guarded.native_stream),
    mixed_float=step_mixed_float,
    vector_guard=lambda text, context: guarded.vector_guard(text),
    group_guard=lambda text, context: guarded.group_guard(text),
    release_source=step_release_source,
    transport=step_transport,
    vector_guards=step_vector_guards,
)


def _summary(proof):
    """Scalar facts of a stage proof (counts, options), not its full tables."""
    out = {}
    for key, value in proof.items():
        if isinstance(value, (int, str, bool)) or value is None:
            out[key] = value
        elif (
            isinstance(value, list)
            and all(isinstance(v, (int, str)) for v in value)
            and len(value) <= 8
        ):
            out[key] = value
    return out


def load_recipe(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("A recipe must be a regular file")
    recipe = json.loads(path.read_text())
    require(recipe.get("schema") == 1 and recipe.get("kind") == KIND, "Not a lowering recipe")
    require(re.fullmatch(r"fsr4_[a-z0-9_]+", recipe["entry"]), "Invalid entry")
    for name, steps in recipe["sequences"].items():
        require(isinstance(steps, list) and steps, f"Empty sequence {name}")
        for step in steps:
            require(
                isinstance(step, list)
                and 1 <= len(step) <= 2
                and step[0] in STEPS
                and (len(step) == 1 or isinstance(step[1], dict)),
                f"Invalid step {step!r} in {name}",
            )
    require(recipe["slots"], "A recipe needs slots")
    for offset, sequences in recipe["slots"].items():
        require(re.fullmatch(r"0x[0-9a-f]+", offset), f"Invalid slot {offset}")
        require(
            sequences and all(s in recipe["sequences"] for s in sequences),
            f"Unknown sequence in {offset}",
        )
    # Slots whose program is new (not the release's): the generated program is pinned here,
    # with its basis (default `new`; `rederived` = dll/rederived.json's program for the slot).
    for offset, pin in recipe.get("expected", {}).items():
        require(offset in recipe["slots"], f"Expected program for {offset}, not a recipe slot")
        require(
            isinstance(pin, dict)
            and set(pin) - {"basis"} == set(PIN_FIELDS)
            and pin.get("basis", "new") in BASES
            and all(
                isinstance(pin[k], str) and re.fullmatch(r"[0-9a-f]{64}", pin[k])
                for k in PIN_FIELDS
            ),
            f"Invalid expected program for {offset}",
        )
    require(recipe.get("cover", "int8") in ("int8", "listed"), "Invalid cover")
    return recipe


def check_recipe(recipe, profile, manifest):
    """Offline: recipe slots are profile slots of the entry and manifest rows."""
    require(recipe["sdk_tag"] == profile["drop"]["tag"], "Recipe is for another drop")
    slots = {s["offset"]: s for s in profile["shaders"]["slots"]}
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    for key in recipe["slots"]:
        offset = int(key, 16)
        require(
            offset in slots and slots[offset]["entry"] == recipe["entry"],
            f"{key} is not a slot of the entry",
        )
        require(offset in rows, f"{key} is not a manifest row")
    expected = recipe.get("expected", {})
    for key, pin in expected.items():
        row = rows[int(key, 16)]
        require(
            pin["replacement_sha256"] != row["replacement_sha256"],
            f"{key}: a pin equal to the release program is redundant",
        )
        if pin.get("basis", "new") == "rederived":
            rederived = json.loads((DLL / "rederived.json").read_text())
            restored = {}
            for element in rederived["elements"].values():
                restored.update(element.get("slots", {}))
            require(
                key in restored and all(restored[key][k] == pin[k] for k in PIN_FIELDS),
                f"{key}: pin differs from dll/rederived.json",
            )
    bases = collections.Counter(pin.get("basis", "new") for pin in expected.values())
    pinned = ", ".join(f"{bases[b]} {b}" for b in BASES if bases[b])
    return f"recipe {recipe['name']}: {len(recipe['slots'])} slots of {recipe['entry']}" + (
        f" ({pinned})" if pinned else ""
    )


def sdk_program(data, slot, toolchain):
    """The renamed `dxc -dumpbin` text of one profile slot of the gated SDK DLL."""
    name = f"{slot['offset']:08x}"
    container = data[slot["offset"] : slot["offset"] + slot["bytes"]]
    require(sha(container) == slot["sha256"], f"{name}: SDK program differs from the profile")
    return rename_ssa(toolchain.disassemble(container, name))


def run_slot(slot, recipe, data, toolchain, casts, shared):
    name = f"{slot['offset']:08x}"
    text = sdk_program(data, slot, toolchain)
    record = dict(
        offset=hex(slot["offset"]),
        entry=slot["entry"],
        sdk_sha256=slot["sha256"],
        disassembly_sha256=sha(text),
        sdk_dots=count_dots(text),
        stages=[],
    )
    require(
        re.search(r"^define void @" + re.escape(slot["entry"]) + r"\(", text, re.M),
        f"{name}: entry name",
    )
    context = dict(
        toolchain=NamedToolchain(toolchain, name),
        casts=casts,
        sdk_dots=record["sdk_dots"],
        sdk=data,
        offset=slot["offset"],
        entry=slot["entry"],
        row=shared["rows"][slot["offset"]],
        rows=shared["rows"],
        slots=shared["slots"],
        sdk_program=lambda offset: sdk_program(data, shared["slots"][offset], toolchain),
        first=True,
    )
    for sequence in recipe["slots"][hex(slot["offset"])]:
        for step in recipe["sequences"][sequence]:
            text, proof = STEPS[step[0]](text, context, **(step[1] if len(step) > 1 else {}))
            context["first"] = False
            record["stages"].append(
                dict(sequence=sequence, step=step[0], sha256=sha(text), **_summary(proof))
            )
    record["source_sha256"] = sha(text)
    shader = toolchain.assemble(text)
    record["replacement_sha256"] = sha(shader)
    return name, text, shader, record


def compare(record, text, row):
    """Container identity, and source identity apart from the ModuleID comment."""
    expected = (DLL / row["source"]).read_text()
    module = MODULE_ID.match(expected)
    ours = MODULE_ID.match(text)
    if module is None and ours is None:  # never through `opt`: the whole text must match
        body_equal = text == expected
    else:
        body_equal = bool(module and ours) and text[ours.end() :] == expected[module.end() :]
    stamped = module[0] + text[ours.end() :] if module and ours else text
    return dict(
        container_equal=record["replacement_sha256"] == row["replacement_sha256"],
        source_equal_except_module_id=body_equal,
        source_equal_with_release_module_id=sha(stamped) == row["source_sha256"],
        release_module_id=module[0].strip() if module else None,
        generated_module_id=ours[0].strip() if ours else None,
    )


def compare_pinned(record, text, pin, row=None):
    """A pinned program: container and whole source equal the recipe's pin (ModuleID included)."""
    module = MODULE_ID.match(text)
    return dict(
        baseline="recipe pin",
        expected_basis=pin.get("basis", "new"),
        container_equal=record["replacement_sha256"] == pin["replacement_sha256"],
        source_equal_except_module_id=sha(text) == pin["source_sha256"],
        source_equal_with_release_module_id=None,
        release_replacement_sha256=row["replacement_sha256"] if row else None,
        release_module_id=None,
        generated_module_id=module[0].strip() if module else None,
    )


def generate(recipe_path, sdk, dxc, output, jobs=2):
    recipe = load_recipe(recipe_path)
    manifest = json.loads((DLL / "manifest.json").read_text())
    data = Path(sdk).read_bytes()
    _, profile = drop_profile.select(sha(data))
    drop_profile.verify(data, profile)  # the license gate (allowlist) and the reviewed layout
    check_recipe(recipe, profile, manifest)
    output = sdk_intake.outside_source(Path(output))
    output.mkdir(parents=True)
    (output / "shaders").mkdir()
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    slots = [s for s in profile["shaders"]["slots"] if s["entry"] == recipe["entry"]]
    with tempfile.TemporaryDirectory(dir=output) as work:
        toolchain = Toolchain(dxc, work, manifest["dxcompiler_sha256"])
        casts = _casts()
        if recipe.get("cover", "int8") == "int8":
            # Every INT8 (dot4) program of the entry must be covered by the recipe.
            int8 = []
            for slot in slots:
                text = toolchain.disassemble(
                    data[slot["offset"] : slot["offset"] + slot["bytes"]], "probe"
                )
                if "@dx.op.dot4AddPacked.i32(" in text:
                    int8.append(hex(slot["offset"]))
            require(
                sorted(int8) == sorted(recipe["slots"]),
                f"INT8 slots {int8} differ from the recipe's",
            )
        selected = [s for s in slots if hex(s["offset"]) in recipe["slots"]]
        require(len(selected) == len(recipe["slots"]), "A recipe slot is not a slot of the entry")
        shared = dict(rows=rows, slots={s["offset"]: s for s in profile["shaders"]["slots"]})
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(
                pool.map(lambda s: run_slot(s, recipe, data, toolchain, casts, shared), selected)
            )
    records = []
    pins = recipe.get("expected", {})
    for name, text, shader, record in results:
        (output / "shaders" / f"{name}.ll").write_text(text)
        (output / "shaders" / f"{name}.dxil").write_bytes(shader)
        row = rows[int(record["offset"], 16)]
        if record["offset"] in pins:
            record.update(compare_pinned(record, text, pins[record["offset"]], row))
        else:
            record.update(compare(record, text, row), baseline="release")
        records.append(record)
    kept = [r for r in records if r["baseline"] == "release"]
    summary = dict(
        recipe=recipe["name"],
        recipe_sha256=sha(Path(recipe_path).read_bytes()),
        sdk_sha256=sha(data),
        drop=profile["drop"]["tag"],
        slots=len(records),
        new_programs=len(pins),
        containers_equal=sum(r["container_equal"] for r in records),
        sources_equal_except_module_id=sum(r["source_equal_except_module_id"] for r in records),
        release_equal=sum(r["container_equal"] for r in kept) == len(kept),
        pinned_equal=all(r["container_equal"] for r in records if r["baseline"] == "recipe pin"),
        records=records,
    )
    (output / "record.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("generate", help="regenerate a recipe's slots and compare")
    command.add_argument("--recipe", type=Path, required=True)
    command.add_argument("--sdk", type=Path, required=True)
    command.add_argument("--dxc", type=Path, required=True, help="extracted DXC 1.9.2607 archive")
    command.add_argument(
        "--output", type=Path, required=True, help="new directory outside the tree"
    )
    command.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    command = commands.add_parser("check", help="offline: recipes against the profile and manifest")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check_tree())
            return 0
        summary = generate(args.recipe, args.sdk, args.dxc, args.output, args.jobs)
    except (sdk_intake.Refused, drop_profile.ProfileError, LoweringError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))
    for record in summary["records"]:
        print(
            record["offset"],
            "container",
            "equal" if record["container_equal"] else "DIFFERS",
            "source",
            "equal" if record["source_equal_except_module_id"] else "DIFFERS",
            f"(pinned, {record['expected_basis']})" if record["baseline"] == "recipe pin" else "",
        )
    # Every slot equals the release program, or the recipe's pin for slots it changes on purpose.
    ok = summary["containers_equal"] == summary["slots"]
    return 0 if ok else 1


def check_tree():
    manifest = json.loads((DLL / "manifest.json").read_text())
    _, profile = drop_profile.select(manifest["sdk_sha256"])
    paths = sorted(RECIPES.glob("*.json"))
    require(paths, "No lowering recipes")
    return "; ".join(check_recipe(load_recipe(p), profile, manifest) for p in paths)


if __name__ == "__main__":
    os.umask(0o022)
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit("ERROR: " + str(error))
