#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Classify an SDK drop's shader programs and host layout against an earlier drop.

Both drops must be `sdk_intake.py ingest` outputs; each is re-verified against the allowlist,
including its license line, before any byte is read. Every DXBC container is disassembled with
the pinned DXC (`dxc -dumpbin`) and reduced to a skeleton: the program text with literal
constants replaced by placeholders and the entry name, shader hash and compiler string
normalized. Classes, for each container of the new drop:

  A  identical container (same SHA256)
  B  same skeleton as an old program: only literal constants differ
  C  same entry and shader model as an old program, different skeleton
  D  no old program with this entry and shader model (new)
  E  (old side) an old program that no A/B/C row of the new drop is based on (removed)

The host layout part compares the drop-profile components (PE, shader pointer table, host
patch sites, provider label) and the sized data tables the host code references (model weight
initializers are among them), so the report also says what the RC repacker must re-derive.
"""

import argparse
import bisect
import collections
import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from . import drop_profile, sdk_intake
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drop_profile
    import sdk_intake

ROOT = Path(__file__).resolve().parent
DLL = ROOT.parent
SCHEMA = 1
KIND = "fidelityfx-sdk-drop-classes"
ANALYZER = 1  # bump when the skeleton or constant extraction changes (cache key)
# Members of the extracted DXC 1.9.2607 archive (lower_pass.TOOLS; libdxcompiler: manifest pin).
DXC_SHA256 = "b1bfa493d5c780b94c20b8b5f5aed50d1c4d03339cd55f496bd223eedeec1734"
SEP = "\x1f"

# Literal roles: what a differing constant means for a port.
ROLES = {
    "s": "stored literal (packed weight tables)",
    "w": "dot4 literal operand",
    "f": "floating-point literal",
    "g": "global initializer element",
    "i": "integer operand (offsets, bounds, masks)",
    "x": "intrinsic immediate",
}
DATA_ROLES = set("swfg")
# Instructions whose literal operands select structure (types, lanes, fields, targets).
STRUCTURAL = re.compile(
    r"^  (?:%[\w.$-]+ = )?(?:alloca|getelementptr|extractvalue|insertvalue|extractelement"
    r"|insertelement|shufflevector|switch|br|ret)\b"
)
TOKEN = re.compile(
    r"(%[\w.$-]+|@[\w.$\\-]+|@\"[^\"]*\"|![\w.]+|\"[^\"]*\"|[\[<]\d+ x |align \d+"
    r"|addrspace\(\d+\)|\bi\d+\b|#\d+)"
    r"|(-?\d+\.\d+e[+-]\d+|0x[HKLMR][0-9A-Fa-f]+|0x[0-9A-Fa-f]+|-?\b\d+\b|\btrue\b|\bfalse\b)"
)
FLOAT_LITERAL = re.compile(r"-?\d\.\d+e[+-]\d+|0x")
OPCODE = re.compile(r"@dx\.op\.[\w.]+\(i32 \d+")
DX_OP = re.compile(r"@dx\.op\.([\w]+)")
DEFINE = re.compile(r"^define void @([\w.$-]+)\(", re.M)
IDENT = re.compile(r'^(!\d+ = !\{!)"((?:dxcoob|dxc|clang)[^"]*)"\}$')
BINDING = re.compile(
    r"^; (\S+)\s+(cbuffer|texture|UAV|sampler)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s+(\d+)$"
)


class ClassifyError(ValueError):
    """An input this classifier was not reviewed for."""


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Gated inputs


class Drop:
    """An ingested drop: DLL and license re-verified against the allowlist record."""

    def __init__(self, record, data, directory):
        self.record, self.data, self.directory = record, data, directory
        self.tag = record["tag"]

    @classmethod
    def load(cls, directory, allowlist=None):
        directory = Path(directory)
        paths = [directory / name for name in (sdk_intake.DLL_NAME, sdk_intake.LICENSE_NAME)]
        paths.append(directory / "intake.json")
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise sdk_intake.Refused(f"{path} is not a regular file of an ingested drop")
        intake = json.loads(paths[2].read_text())
        if allowlist is None:
            allowlist = sdk_intake.read_allowlist()[0]
        record = sdk_intake.lookup(allowlist, intake["record"]["tag"])
        if intake["record"] != record:
            raise sdk_intake.Refused(f"{directory}: intake.json differs from the allowlist record")
        data, license = paths[0].read_bytes(), paths[1].read_bytes()
        sdk_intake.verify_drop(record, data, license)  # hashes, license line, PE identity
        return cls(record, data, directory)


class Disassembler:
    """`dxc -dumpbin` from the pinned DXC 1.9.2607 archive (dxc and libdxcompiler pinned)."""

    def __init__(self, root, work):
        root = Path(root).resolve()
        self.dxc = root / "bin/dxc"
        library = root / "lib/libdxcompiler.so"
        manifest = json.loads((DLL / "manifest.json").read_text())
        for path, digest in ((self.dxc, DXC_SHA256), (library, manifest["dxcompiler_sha256"])):
            if sha256(path.read_bytes()) != digest:
                raise ClassifyError(f"{path} is not the pinned DXC 1.9.2607 file")
        self.work = Path(work)
        self.identity = DXC_SHA256[:16]

    def __call__(self, container):
        with tempfile.TemporaryDirectory(dir=self.work) as tmp:
            path = Path(tmp) / "shader.dxil"
            path.write_bytes(container)
            run = subprocess.run(
                [str(self.dxc), "-dumpbin", path.name],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=600,
            )
        if run.returncode:
            raise ClassifyError("dxc -dumpbin failed: " + run.stderr[:500])
        return run.stdout


# ---------------------------------------------------------------------------------------------
# Program analysis


def shader_model(data, offset):
    """(kind, 'major.minor') from the DXIL part's program version."""
    size, count = struct.unpack_from("<II", data, offset + 24)
    for index in range(count):
        child = struct.unpack_from("<I", data, offset + 32 + 4 * index)[0]
        if data[offset + child : offset + child + 4] == b"DXIL":
            version = struct.unpack_from("<I", data, offset + child + 8)[0]
            return version >> 16, f"{(version >> 4) & 15}.{version & 15}"
    raise ClassifyError(f"Container at 0x{offset:x} has no DXIL part")


def analyze(text):
    """Skeleton hash, ordered literal constants with roles, interface and statistics."""
    lines = text.split("\n")
    entries = DEFINE.findall(text)
    if len(entries) != 1:
        raise ClassifyError(f"Expected one entry function, found {len(entries)}")
    entry = entries[0]
    names = re.compile(r"(?<=@)" + re.escape(entry) + r"\b|(?<=!\")" + re.escape(entry) + r'(?=")')
    skeleton, constants, roles = [], [], []
    header, compiler, in_header = [], None, True
    bindings, numthreads, waves = [], None, [None, None]
    ops = collections.Counter()
    instructions = blocks = 0
    for line in lines:
        if line.startswith("; shader hash:"):
            continue
        if line.startswith("; EntryFunctionName:"):
            line = "; EntryFunctionName: @ENTRY"
        if in_header and not line.startswith(";"):
            in_header = False
        if in_header:
            if m := re.match(r"^; NumThreads=\((\d+),(\d+),(\d+)\)", line):
                numthreads = [int(v) for v in m.groups()]
            if m := re.match(r"^; (Minimum|Maximum)ExpectedWaveLaneCount: (\d+)", line):
                waves[m[1] == "Maximum"] = int(m[2])
            if m := BINDING.match(line):
                bindings.append(" ".join(m.groups()))
            header.append(line)
            skeleton.append(line)
            continue
        if line.startswith("!"):
            if m := IDENT.match(line):
                compiler = m[2]
                line = m[1] + '"COMPILER"}'
            skeleton.append(names.sub("ENTRY", line))
            continue
        if line.startswith("define "):
            skeleton.append(names.sub("ENTRY", line))
            continue
        if line.startswith("; <label>:"):
            blocks += 1
        if not line.startswith("  ") and not line.startswith("@"):
            skeleton.append(line)  # declarations, attributes, comments, labels, braces
            continue
        code = line.split(";", 1)[0].rstrip() if line.startswith("  ") else line
        if line.startswith("  ") and not line.startswith("    "):
            instructions += 1
            for op in DX_OP.findall(code):
                ops[op] += 1
        if STRUCTURAL.match(code) or line.startswith("    "):
            skeleton.append(code)
            continue
        keep = {m.end() for m in OPCODE.finditer(code)}  # the dx.op opcode literal
        if "@dx.op.dot4AddPacked" in code:
            role = "w"
        elif "@dx.op." in code:
            role = "x"
        elif code.startswith("@"):
            role = "g"
        elif re.match(r"^  store ", code):
            role = "s"
        else:
            role = "i"
        out, last = [], 0
        for m in TOKEN.finditer(code):
            if m[2] is None or m.end() in keep:
                continue
            out.append(code[last : m.start()])
            out.append("#")
            last = m.end()
            constants.append(m[2])
            roles.append("f" if role in "is" and FLOAT_LITERAL.match(m[2]) else role)
        out.append(code[last:])
        skeleton.append("".join(out))
    return dict(
        entry=entry,
        skeleton=sha256("\n".join(skeleton)),
        interface=sha256("\n".join(header)),
        constants=SEP.join(constants),
        roles="".join(roles),
        compiler=compiler,
        numthreads=numthreads,
        wave=waves,
        bindings=bindings,
        instructions=instructions,
        blocks=blocks + 1,
        ops=dict(sorted(ops.items())),
    )


def constant_differences(old, new):
    """Per-role count of differing literals between two programs with the same skeleton."""
    a, b = old["constants"].split(SEP), new["constants"].split(SEP)
    if len(a) != len(b) or old["roles"] != new["roles"]:
        raise ClassifyError("Equal skeletons must have aligned constants")
    counts = collections.Counter(role for x, y, role in zip(a, b, new["roles"]) if x != y)
    return dict(sorted(counts.items()))


class AnalysisCache:
    """Optional on-disk cache of analyses, keyed by container SHA256, analyzer and DXC."""

    def __init__(self, root, identity):
        self.root = None
        if root is not None:
            self.root = sdk_intake.outside_source(root) / f"classify-{ANALYZER}-{identity}"
            self.root.mkdir(parents=True, exist_ok=True)

    def get(self, digest):
        if self.root is None or not (path := self.root / f"{digest}.json.gz").is_file():
            return None
        found = json.loads(gzip.decompress(path.read_bytes()))
        return found if found.get("sha256") == digest else None

    def put(self, digest, analysis):
        if self.root is None:
            return
        fd, name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "wb") as stream:
            stream.write(gzip.compress(json.dumps(dict(analysis, sha256=digest)).encode(), 6))
        os.replace(name, self.root / f"{digest}.json.gz")


def inventory(drop):
    """Every DXBC container: offset, bytes, sha256, entry, pointer offsets, shader model."""
    data = drop.data
    found = drop_profile.containers(data)
    image = drop_profile.Image(data)
    try:
        dir64, _ = drop_profile.relocations(image)
        table = drop_profile.shader_table(image, found, dir64)
        pointers = {row["offset"]: row["pointers"] for row in table["slots"]}
        error = None
    except drop_profile.ProfileError as failure:
        pointers, error = {}, str(failure)
    rows = []
    for offset, info in sorted(found.items()):
        kind, model = shader_model(data, offset)
        rows.append(
            dict(
                offset=offset,
                bytes=info["bytes"],
                sha256=info["sha256"],
                entry=info["entry"],
                sm=model,
                kind=kind,
                pointers=pointers.get(offset, []),
            )
        )
    return rows, error


def analyze_drop(drop, disassemble, cache=None, jobs=2, entries=None):
    """Containers of a gated drop with their analyses (disassembled once per unique SHA256)."""
    if not isinstance(drop, Drop):
        raise ClassifyError("Only gated drops are disassembled")
    rows, error = inventory(drop)
    if entries is not None:
        rows = [r for r in rows if r["entry"] and re.fullmatch(entries, r["entry"])]
    unique = {r["sha256"]: r for r in rows}
    cache = cache or AnalysisCache(None, "")

    def work(row):
        found = cache.get(row["sha256"])
        if found is None:
            found = analyze(disassemble(drop.data[row["offset"] : row["offset"] + row["bytes"]]))
            cache.put(row["sha256"], found)
        found.pop("sha256", None)
        if row["entry"] is not None and found["entry"] != row["entry"]:
            raise ClassifyError(f"0x{row['offset']:x}: PSV0 entry differs from the program's")
        return row["sha256"], found

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        analyses = dict(pool.map(work, unique.values()))
    for row in rows:
        row["analysis"] = analyses[row["sha256"]]
        row["entry"] = row["entry"] or row["analysis"]["entry"]
    return rows, error


# ---------------------------------------------------------------------------------------------
# Classification


def _similarity(old, new):
    a, b = old["analysis"], new["analysis"]
    ops = set(a["ops"]) | set(b["ops"])
    distance = sum(abs(a["ops"].get(op, 0) - b["ops"].get(op, 0)) for op in ops)
    return distance + abs(a["instructions"] - b["instructions"])


def _change(old, new):
    a, b = old["analysis"], new["analysis"]
    ops = {}
    for op in sorted(set(a["ops"]) | set(b["ops"])):
        if a["ops"].get(op, 0) != b["ops"].get(op, 0):
            ops[op] = [a["ops"].get(op, 0), b["ops"].get(op, 0)]
    return dict(
        interface_equal=a["interface"] == b["interface"],
        numthreads=[a["numthreads"], b["numthreads"]],
        wave=[a["wave"], b["wave"]],
        bindings_removed=sorted(set(a["bindings"]) - set(b["bindings"])),
        bindings_added=sorted(set(b["bindings"]) - set(a["bindings"])),
        instructions=[a["instructions"], b["instructions"]],
        blocks=[a["blocks"], b["blocks"]],
        literals=[len(a["roles"]), len(b["roles"])],
        dx_ops=ops,
    )


def b_kind(differing, old, new):
    """rename: only the entry name; compiler: only the compiler string; container: no text change
    (other container bytes); data: only stored/dot4/float/global literals; constants: integer or
    intrinsic literals too."""
    if not differing:
        if old["entry"] != new["entry"]:
            return "rename"
        if old["analysis"]["compiler"] != new["analysis"]["compiler"]:
            return "compiler"
        return "container"
    return "data" if set(differing) <= DATA_ROLES else "constants"


def classify(old_rows, new_rows):
    """Class rows for every new container, plus the removed (E) old containers."""
    by_sha, by_skeleton, by_family = (collections.defaultdict(list) for _ in range(3))
    for row in old_rows:
        by_sha[row["sha256"]].append(row)
        by_skeleton[row["analysis"]["skeleton"]].append(row)
        by_family[(row["entry"], row["sm"])].append(row)
    used, result = set(), {}

    def record(new, cls, base, **details):
        result[new["offset"]] = dict(
            offset=new["offset"],
            entry=new["entry"],
            sm=new["sm"],
            sha256=new["sha256"],
            referenced=bool(new["pointers"]),
            initializer=any(
                b.startswith("InitializerBuffer ") for b in new["analysis"]["bindings"]
            ),
            cls=cls,
            base=[b["offset"] for b in base],
            base_entry=base[0]["entry"] if base else None,
            renamed=bool(base) and base[0]["entry"] != new["entry"],
            compiler=new["analysis"]["compiler"],
            **details,
        )
        used.update(b["offset"] for b in base)

    for new in new_rows:
        if new["sha256"] in by_sha:
            record(new, "A", by_sha[new["sha256"]])
    for new in new_rows:
        if new["offset"] in result:
            continue
        candidates = by_skeleton.get(new["analysis"]["skeleton"], [])
        same = [c for c in candidates if c["entry"] == new["entry"]]
        if candidates:
            scored = sorted(
                (sum(constant_differences(c["analysis"], new["analysis"]).values()), c["offset"], c)
                for c in (same or candidates)
            )
            base = scored[0][2]
            differing = constant_differences(base["analysis"], new["analysis"])
            record(
                new,
                "B",
                [base],
                kind=b_kind(differing, base, new),
                constants=len(new["analysis"]["roles"]),
                differing=differing,
                candidates=len(same or candidates),
            )
    for new in new_rows:
        if new["offset"] in result:
            continue
        family = by_family.get((new["entry"], new["sm"]))
        if family:
            base = min(
                family, key=lambda r: (r["offset"] in used, _similarity(r, new), r["offset"])
            )
            record(new, "C", [base], change=_change(base, new))
        else:
            other = sorted({r["sm"] for r in old_rows if r["entry"] == new["entry"]})
            record(new, "D", [], old_shader_models=other)
    removed = [
        dict(
            offset=r["offset"],
            entry=r["entry"],
            sm=r["sm"],
            sha256=r["sha256"],
            referenced=bool(r["pointers"]),
            cls="E",
        )
        for r in old_rows
        if r["offset"] not in used
    ]
    return [result[r["offset"]] for r in new_rows], removed


def summarize(rows, removed, old_rows):
    """Counts per class (containers and unique programs) and per (entry, shader model)."""
    totals = collections.Counter(r["cls"] for r in rows)
    unique = {cls: len({r["sha256"] for r in rows if r["cls"] == cls}) for cls in "ABCD"}
    families = collections.defaultdict(lambda: collections.Counter())
    for row in rows:
        families[(row["entry"], row["sm"])][row["cls"]] += 1
    for row in removed:
        families[(row["entry"], row["sm"])]["E"] += 1
    old_count = collections.Counter((r["entry"], r["sm"]) for r in old_rows)
    table = []
    for (entry, model), counts in sorted(
        families.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
    ):
        new = [r for r in rows if (r["entry"], r["sm"]) == (entry, model)]
        b = [r for r in new if r["cls"] == "B"]
        table.append(
            dict(
                entry=entry,
                sm=model,
                old=old_count.get((entry, model), 0),
                new=len(new),
                new_unique=len({r["sha256"] for r in new}),
                **{cls: counts.get(cls, 0) for cls in "ABCDE"},
                b_kinds=dict(sorted(collections.Counter(r["kind"] for r in b).items())),
                renamed=sum(r["renamed"] for r in new),
            )
        )
    return dict(
        containers=len(rows),
        slots=sum(r["referenced"] for r in rows),
        classes={cls: totals.get(cls, 0) for cls in "ABCD"} | {"E": len(removed)},
        unique_programs=unique,
        a_initializer=sum(r["cls"] == "A" and r["initializer"] for r in rows),
        b_kinds=dict(
            sorted(collections.Counter(r["kind"] for r in rows if r["cls"] == "B").items())
        ),
        compilers=dict(collections.Counter(r["compiler"] for r in rows)),
        families=table,
    )


def rc_rows(manifest, old_sha, new_sha, rows, old_offsets):
    """Where the RC manifest's replaced slots go (manifest on the old side) or come from.

    Only manifest rows among the classified containers are counted (see `entries`)."""
    replaced = {r["original_offset"]: r for r in manifest["replacements"]}
    if manifest["sdk_sha256"] == old_sha:
        replaced = {o: r for o, r in replaced.items() if o in old_offsets}
        side, targets = "old", collections.defaultdict(list)
        for row in rows:
            for base in row["base"]:
                targets[base].append((row["offset"], row["cls"]))
        mapped = {}
        for offset in replaced:
            found = targets.get(offset, [])
            if not found:
                mapped[offset] = dict(cls="E", new=[])
            else:
                classes = sorted({cls for _, cls in found})
                mapped[offset] = dict(cls=classes[-1], new=sorted(o for o, _ in found))
    elif manifest["sdk_sha256"] == new_sha:
        side = "new"
        own = {r["offset"]: r for r in rows}
        replaced = {o: r for o, r in replaced.items() if o in own}
        mapped = {o: dict(cls=own[o]["cls"], base=own[o]["base"]) for o in replaced}
    else:
        return dict(side=None, note="The RC manifest's SDK is neither drop")
    counts = collections.Counter(v["cls"] for v in mapped.values())
    return dict(
        side=side,
        manifest=manifest["label"],
        rows=len(replaced),
        rows_total=len(manifest["replacements"]),
        classes=dict(sorted(counts.items())),
        slots={hex(k): v for k, v in sorted(mapped.items())},
    )


# ---------------------------------------------------------------------------------------------
# Host layout

LEA = re.compile(rb"(?=[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d])", re.DOTALL)
MOV_IMM32 = re.compile(rb"(?=[\xb8-\xbf](....))", re.DOTALL)  # mov r32, imm32 (also after 0x41)


def sized_tables(image, found, minimum=0x1000, maximum=0x1000000, window=32):
    """Data tables the code loads with a RIP-relative LEA next to a `mov r32, size` (heuristic).

    The model weight initializers of SDK 2.2.0 and 2.3.0 are found this way; other tables may be
    too. Containers and candidates that overlap another candidate are excluded."""
    data, tables = image.data, collections.defaultdict(collections.Counter)
    starts = sorted(found)
    ends = [o + found[o]["bytes"] for o in starts]
    for section in image.code_sections():
        body = image.body(section)
        for m in LEA.finditer(body):
            at = m.start()
            if at + 7 > len(body):
                continue
            displacement = struct.unpack_from("<i", body, at + 3)[0]
            target = image.to_offset(section["rva"] + at + 7 + displacement, required=False)
            if target is None:
                continue
            home = image.section_at(target)
            if home is None or home["characteristics"] & drop_profile.EXECUTE:
                continue
            index = bisect.bisect_right(starts, target) - 1
            if index >= 0 and target < ends[index]:
                continue  # inside a shader container
            end = home["offset"] + home["bytes"]
            sizes = []
            start = max(0, at - window)
            for mov in MOV_IMM32.finditer(body, start, min(len(body), at + 7 + window)):
                if at <= mov.start() < at + 7:
                    continue
                size = struct.unpack("<I", mov[1])[0]
                if minimum <= size <= maximum and target + size <= end:
                    sizes.append((abs(mov.start() - at), size))
            if sizes:
                tables[target][min(sizes)[1]] += 1
    ranges = [(target, sizes.most_common(1)[0][0]) for target, sizes in sorted(tables.items())]
    out = []
    for index, (target, size) in enumerate(ranges):
        before = ranges[index - 1] if index else None
        after = ranges[index + 1] if index + 1 < len(ranges) else None
        if (before and sum(before) > target) or (after and target + size > after[0]):
            continue
        out.append(dict(offset=target, bytes=size, sha256=sha256(data[target : target + size])))
    return out


def host_layout(drop):
    """Each drop-profile component derived independently, plus sized data tables."""
    data = drop.data
    image = drop_profile.Image(data)
    try:
        dir64, relocation_summary = drop_profile.relocations(image)
        relocation_error = None
    except drop_profile.ProfileError as error:
        dir64, relocation_summary, relocation_error = set(), {}, str(error)
    found = drop_profile.containers(data)
    layout = dict(
        tag=drop.tag,
        dll_sha256=drop.record["dll"]["sha256"],
        bytes=len(data),
        sections=[
            dict(name=s["name"], bytes=s["bytes"], virtual_size=s["virtual_size"])
            for s in image.sections
        ],
        relocations={k: v for k, v in relocation_summary.items() if k in ("absolute", "dir64")}
        or relocation_error,
    )

    def attempt(name, function):
        try:
            layout[name] = dict(status="ok", **function())
        except drop_profile.ProfileError as error:
            layout[name] = dict(status="absent", reason=str(error))

    def pe():
        if relocation_error:
            raise drop_profile.ProfileError(relocation_error)
        pe = drop_profile.pe_layout(image, relocation_summary)
        return dict(certificate=pe["certificate"], spare_section_header=pe["spare_section_header"])

    def shaders():
        table = drop_profile.shader_table(image, found, dir64)
        pointers = sorted(p for row in table["slots"] for p in row["pointers"])
        gaps = collections.Counter(b - a for a, b in zip(pointers, pointers[1:]))
        return dict(
            containers=table["containers"],
            slots=len(table["slots"]),
            unreferenced=len(table["unreferenced"]),
            pointer_sections=table["pointer_sections"],
            pointer_spacing=gaps.most_common(1)[0][0] if gaps else None,
        )

    attempt("pe", pe)
    attempt("shaders", shaders)
    attempt(drop_profile.ELIGIBILITY["name"], lambda: _site(drop_profile.eligibility_patch(image)))
    attempt(drop_profile.BARRIER["name"], lambda: _site(drop_profile.barrier_patch(image)))
    attempt(
        "provider_label",
        lambda: {
            k: v
            for k, v in drop_profile.provider_label(image, dir64).items()
            if k in ("version", "offset")
        },
    )
    try:
        drop_profile.derive(data, drop.record)
        layout["strict_derive"] = "ok"
    except drop_profile.ProfileError as error:
        layout["strict_derive"] = "refused: " + str(error)
    layout["sized_tables"] = sized_tables(image, found)
    return layout


def _site(patch):
    return dict(site=patch["site"], edits=len(patch["edits"]))


PROFILE_PARTS = ("pe", "shaders", "int8-eligibility", "buffer-uav-barrier", "provider_label")


def compare_layout(old, new):
    """What changed in each profile component, and what that means for the repacker."""
    changes = []
    for part in PROFILE_PARTS:
        a, b = old[part], new[part]
        if a["status"] == "absent" and b["status"] == "absent":
            verdict = "absent in both"
        elif b["status"] == "absent":
            verdict = (
                "lost: the pattern no longer matches; read the new code and extend drop_profile"
            )
        elif a["status"] == "absent":
            verdict = "new in this drop"
        elif a == b:
            verdict = "unchanged"
        elif part in ("pe", "shaders"):
            verdict = "changed: re-derived from the bytes"
        else:
            verdict = "moved: re-derive (the pattern still matches once)"
        changes.append(dict(part=part, old=a, new=b, verdict=verdict))
    sections = dict(
        old=[(s["name"], s["bytes"]) for s in old["sections"]],
        new=[(s["name"], s["bytes"]) for s in new["sections"]],
    )
    return dict(
        parts=changes,
        sections=sections,
        sections_renamed=[s[0] for s in sections["old"]] != [s[0] for s in sections["new"]],
        strict_derive=dict(old=old["strict_derive"], new=new["strict_derive"]),
    )


def compare_tables(old_drop, new_drop, old_tables, new_tables):
    """Sized tables of each drop, found again (bytes) in the other drop or not."""

    def present(rows, other):
        out = []
        for row in rows:
            blob = row["data"]
            at = other.find(blob)
            out.append(dict({k: v for k, v in row.items() if k != "data"}, in_other=at >= 0))
        return out

    def load(drop, rows):
        return [dict(r, data=drop.data[r["offset"] : r["offset"] + r["bytes"]]) for r in rows]

    old = present(load(old_drop, old_tables), new_drop.data)
    new = present(load(new_drop, new_tables), old_drop.data)
    sizes = collections.defaultdict(
        lambda: dict(old=0, old_distinct=set(), new=0, new_distinct=set(), old_kept=0, new_in_old=0)
    )
    for side, rows in (("old", old), ("new", new)):
        for r in rows:
            entry = sizes[r["bytes"]]
            entry[side] += 1
            entry[side + "_distinct"].add(r["sha256"])
            entry["old_kept" if side == "old" else "new_in_old"] += r["in_other"]
    by_size = [
        dict(v, bytes=k, old_distinct=len(v["old_distinct"]), new_distinct=len(v["new_distinct"]))
        for k, v in sorted(sizes.items())
    ]
    return dict(
        old=old,
        new=new,
        by_size=by_size,
        old_kept=sum(r["in_other"] for r in old),
        new_found_in_old=sum(r["in_other"] for r in new),
        new_unique=len({r["sha256"] for r in new}),
        new_unique_not_in_old=len({r["sha256"] for r in new if not r["in_other"]}),
    )


# ---------------------------------------------------------------------------------------------
# Report


def diff(old, new, disassemble, cache=None, jobs=2, entries=None, manifest=None):
    old_rows, old_error = analyze_drop(old, disassemble, cache, jobs, entries)
    new_rows, new_error = analyze_drop(new, disassemble, cache, jobs, entries)
    rows, removed = classify(old_rows, new_rows)
    old_layout, new_layout = host_layout(old), host_layout(new)
    if manifest is None:
        manifest = json.loads((DLL / "manifest.json").read_text())
    return dict(
        schema=SCHEMA,
        kind=KIND,
        analyzer=ANALYZER,
        old=dict(tag=old.tag, dll_sha256=old.record["dll"]["sha256"], table_error=old_error),
        new=dict(tag=new.tag, dll_sha256=new.record["dll"]["sha256"], table_error=new_error),
        entries=entries,
        summary=summarize(rows, removed, old_rows),
        layout=compare_layout(old_layout, new_layout),
        tables=compare_tables(old, new, old_layout["sized_tables"], new_layout["sized_tables"]),
        rc=rc_rows(
            manifest,
            old.record["dll"]["sha256"],
            new.record["dll"]["sha256"],
            rows,
            {r["offset"] for r in old_rows},
        ),
        rows=rows,
        removed=removed,
    )


def markdown(result):
    s, layout, tables = result["summary"], result["layout"], result["tables"]
    out = [
        f"# SDK drop classes: {result['old']['tag']} -> {result['new']['tag']}",
        "",
        f"Old DLL `{result['old']['dll_sha256']}`, new DLL `{result['new']['dll_sha256']}`"
        + (f"; entries matching `{result['entries']}`" if result["entries"] else "")
        + ".",
        "",
        "| Class | Containers | Unique programs |",
        "| --- | ---: | ---: |",
    ]
    names = dict(
        A="A identical",
        B="B constants only",
        C="C structure changed",
        D="D new",
        E="E removed (old side)",
    )
    for cls in "ABCDE":
        unique = s["unique_programs"].get(cls, "")
        out.append(f"| {names[cls]} | {s['classes'][cls]} | {unique} |")
    kinds = ", ".join(f"{k} {v}" for k, v in s["b_kinds"].items()) or "none"
    out += [
        "",
        f"{s['containers']} new containers, {s['slots']} of them shader slots. B kinds: {kinds}. "
        f"{s['a_initializer']} A rows bind `InitializerBuffer` (weights from host tables, below). "
        "Compilers: " + ", ".join(f"`{k}` {v}" for k, v in s["compilers"].items()) + ".",
        "",
        "## Per entry and shader model",
        "",
        "| Entry | SM | Old | New | New unique | A | B | B kinds | C | D | E | Renamed |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for f in s["families"]:
        kinds = ", ".join(f"{k} {v}" for k, v in f["b_kinds"].items())
        out.append(
            f"| `{f['entry']}` | {f['sm']} | {f['old']} | {f['new']} | {f['new_unique']} | {f['A']} "
            f"| {f['B']} | {kinds} | {f['C']} | {f['D']} | {f['E']} | {f['renamed']} |"
        )
    out += ["", "## Host layout (drop profile components)", ""]
    out += ["| Part | Old | New | Verdict |", "| --- | --- | --- | --- |"]

    def brief(part):
        if part["status"] == "absent":
            return "absent"
        return ", ".join(
            f"{k} {hex(v) if isinstance(v, int) and k in ('site', 'offset') else v}"
            for k, v in part.items()
            if k != "status"
        )

    for part in layout["parts"]:
        out.append(
            f"| {part['part']} | {brief(part['old'])} | {brief(part['new'])} | {part['verdict']} |"
        )
    out += [
        "",
        f"Strict `drop_profile.derive`: old {layout['strict_derive']['old']}; "
        f"new {layout['strict_derive']['new']}.",
        "",
        "Sections: old "
        + ", ".join(f"{n} {b:,}" for n, b in layout["sections"]["old"])
        + "; new "
        + ", ".join(f"{n} {b:,}" for n, b in layout["sections"]["new"])
        + ".",
        "",
        "## Sized data tables (initializer candidates)",
        "",
        "Tables the host code loads with a RIP-relative LEA beside a size immediate; found again "
        "means the same bytes occur anywhere in the other DLL.",
        "",
        "| Bytes | Old (distinct) | Old found in new | New (distinct) | New found in old |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for size in tables["by_size"]:
        out.append(
            f"| {size['bytes']:#x} | {size['old']} ({size['old_distinct']}) | {size['old_kept']} "
            f"| {size['new']} ({size['new_distinct']}) | {size['new_in_old']} |"
        )
    out.append("")
    rc = result["rc"]
    out += ["## RC manifest slots", ""]
    if rc["side"] is None:
        out.append(rc["note"] + ".")
    else:
        out.append(
            f"`{rc['manifest']}` ({rc['rows']} of {rc['rows_total']} replaced slots in scope, "
            f"manifest on the {rc['side']} side): "
            + ", ".join(f"{k} {v}" for k, v in rc["classes"].items())
            + "."
        )
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("diff", help="classify every program of NEW against OLD")
    command.add_argument("--old", type=Path, required=True, help="ingested drop directory")
    command.add_argument("--new", type=Path, required=True, help="ingested drop directory")
    command.add_argument("--dxc", type=Path, required=True, help="extracted DXC 1.9.2607 archive")
    command.add_argument(
        "--output", type=Path, required=True, help="new directory outside the tree"
    )
    command.add_argument("--cache", type=Path, help="analysis cache directory outside the tree")
    command.add_argument("--jobs", type=int, default=2, choices=range(1, 17))
    command.add_argument("--entries", help="only entries fully matching this regex")
    command = commands.add_parser("layout", help="host layout comparison only (no DXC)")
    command.add_argument("--old", type=Path, required=True)
    command.add_argument("--new", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        old, new = Drop.load(args.old), Drop.load(args.new)
        if args.command == "layout":
            a, b = host_layout(old), host_layout(new)
            result = dict(
                layout=compare_layout(a, b),
                tables=compare_tables(old, new, a["sized_tables"], b["sized_tables"]),
            )
            print(json.dumps(result, indent=2))
            return 0
        output = sdk_intake.outside_source(args.output)
        output.mkdir(parents=True)
        with tempfile.TemporaryDirectory(dir=output) as work:
            disassemble = Disassembler(args.dxc, work)
            cache = AnalysisCache(args.cache, disassemble.identity)
            result = diff(old, new, disassemble, cache, args.jobs, args.entries)
    except (sdk_intake.Refused, drop_profile.ProfileError, ClassifyError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    (output / "classes.json").write_text(json.dumps(result, indent=1) + "\n")
    (output / "classes.md").write_text(markdown(result))
    print(markdown(result))
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit("ERROR: " + str(error))
