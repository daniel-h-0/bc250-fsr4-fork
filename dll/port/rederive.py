#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The re-derived DLL configuration: RC11's sources with each third-party-derived element replaced.

RC11 carries two elements whose design followed a third-party modified DLL. Each is re-derived
from the SDK 2.3.0 code alone and recorded in dll/rederived.json (`elements`):

  final-pass-divisions  Every `sdiv i32 x, 2**k` that the SDK compiled into a replaced program is
                        decided by port/sdiv_shift.py on the SDK's own program: a logical shift
                        only where the stage proves the dividend non-negative, the SDK's sdiv
                        otherwise. `audit` disassembles every manifest slot of the gated SDK DLL,
                        aligns each SDK division with the RC11 source (same value name, identical
                        dividend derivation) and lists the RC11 lines that differ from the stage's
                        decision. The configuration pins those restorations and their hashes.
  int8-eligibility      The host-code edit that makes the INT8 provider selectable. While its
                        status is "pending", the re-derived build applies no edit at that site
                        (the SDK's bytes stay); an independently derived edit replaces it later.

`check` (offline, run by scripts/check-repo.py) applies the pinned restorations to the frozen RC11
sources and requires the pinned hashes. `dll/build.py --config rederived` builds the DLL;
`compare` explains every byte in which two built DLLs differ, from their build records.
"""

import argparse
import hashlib
import json
import re
import struct
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from . import drop_profile, sdiv_shift
    from .ir import LoweringError, rename_ssa, require
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drop_profile
    import sdiv_shift
    from ir import LoweringError, rename_ssa, require

ROOT = Path(__file__).resolve().parent
DLL = ROOT.parent
CONFIG = DLL / "rederived.json"
KIND = "fsr4-rederived-configuration"
ELEMENTS = ("final-pass-divisions", "int8-eligibility")
STATUSES = ("derived", "pending")
DEFINITION = re.compile(r"^  (%[A-Za-z0-9_.$-]+) = (.*)$")
VALUE = re.compile(r"%[A-Za-z0-9_.$-]+")
SHA256 = re.compile(r"[0-9a-f]{64}")
CHAIN_LIMIT = 64


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def code(line):
    return line.split(";")[0].rstrip()


def definitions(text):
    lines = text.splitlines()
    defs = {}
    for n, line in enumerate(lines):
        if m := DEFINITION.match(code(line)):
            defs[m[1]] = n
    return lines, defs


def chain(lines, defs, name):
    """The defining instruction (without comments) of `name` and of every value it uses."""
    out, todo = {}, [name]
    while todo:
        value = todo.pop()
        if value in out or value not in defs:
            continue
        require(len(out) < CHAIN_LIMIT, f"The derivation of {name} is too long to align")
        out[value] = code(lines[defs[value]])
        todo.extend(VALUE.findall(out[value].split(" = ", 1)[1]))
    return out


# ------------------------------------------------------------------- final-pass divisions


def audit_slot(sdk_text, rc_text, assume=None):
    """Align each power-of-two sdiv of the SDK program with the RC source; decide each one.

    Verdicts: `kept` (the RC source has the SDK's instruction), `shift-proven` (the RC source has
    the logical shift and the stage proves it exact), `restore` (the RC source has the shift but
    the stage does not prove it: the SDK's instruction is restored). Any other form, or a
    division whose dividend is computed differently in the RC source, is refused.
    """
    sdk = rename_ssa(sdk_text)
    _, _, records = sdiv_shift.analyze(sdk, assume)
    sdk_lines, sdk_defs = definitions(sdk)
    rc_lines, rc_defs = definitions(rc_text)
    out = []
    for record in records:
        if record["form"] != "sdiv i32 x, 2**k":
            continue
        value, dividend, k = record["value"], record["dividend"], record["shift"]
        require(value in rc_defs, f"{value}: the SDK division has no counterpart in the source")
        require(
            chain(sdk_lines, sdk_defs, dividend) == chain(rc_lines, rc_defs, dividend),
            f"{value}: the dividend {dividend} is computed differently in the source",
        )
        sdk_code = code(sdk_lines[record["line"] - 1])
        rc_line = rc_lines[rc_defs[value]]
        exact = " exact" if record["exact"] else ""
        shifted = f"  {value} = lshr{exact} i32 {dividend}, {k}"
        entry = dict(
            value=value,
            dividend=dividend,
            divisor=record["divisor"],
            interval=record["interval"],
            facts=record["facts"],
            proven=record["proven"],
            line=rc_defs[value] + 1,
        )
        if code(rc_line) == sdk_code:
            entry["verdict"] = "kept"
        elif code(rc_line) == shifted:
            entry["verdict"] = "shift-proven" if record["proven"] else "restore"
            if not record["proven"]:
                entry.update(
                    before=rc_line,
                    after=sdk_code + rc_line[len(code(rc_line)) :],
                    witness=record.get("witness"),
                )
        else:
            raise LoweringError(f"{value}: unrecognised form in the source: {code(rc_line)}")
        out.append(entry)
    return out


def restore(text, edits):
    """Apply pinned line restorations; each `before` must be the source's line exactly."""
    lines = text.split("\n")
    for edit in edits:
        n = edit["line"] - 1
        require(0 <= n < len(lines) and lines[n] == edit["before"], f"Line {edit['line']} moved")
        lines[n] = edit["after"]
    return "\n".join(lines)


def restorations(config):
    """{slot offset: (row source, edits, pinned hashes)} of the final-pass element."""
    element = config["elements"]["final-pass-divisions"]
    return {int(key, 16): slot for key, slot in element["slots"].items()}


def restored_source(row, slot):
    """The re-derived source text of one manifest row, checked against both pins."""
    text = (DLL / row["source"]).read_text()
    require(sha(text) == row["source_sha256"] == slot["rc11_source_sha256"], "RC11 source moved")
    out = restore(text, slot["edits"])
    require(sha(out) == slot["source_sha256"], "Re-derived source differs from its pin")
    return out


def pending_host_patches(config):
    """Profile host patches the re-derived build leaves out (elements still pending)."""
    element = config["elements"]["int8-eligibility"]
    return [element["host_patch"]] if element["status"] == "pending" else []


def load_config(path=CONFIG):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "The configuration must be a regular file")
    return json.loads(path.read_text())


def check_config(config, manifest, manifest_sha256, profile):
    """Offline: format, the base manifest pin and every pinned restoration."""
    require(config.get("schema") == 1 and config.get("kind") == KIND, "Not a re-derived config")
    require(config["base_manifest_sha256"] == manifest_sha256, "Configuration for another RC")
    require(tuple(config["elements"]) == ELEMENTS, "Unexpected configuration elements")
    for name, element in config["elements"].items():
        require(element["status"] in STATUSES, f"{name}: unknown status")
    eligibility = config["elements"]["int8-eligibility"]
    names = [p["name"] for p in profile["host_patches"]]
    require(eligibility["host_patch"] in names, "The eligibility element names no profile patch")
    require(eligibility["status"] == "pending", "Only the pending eligibility form is supported")
    element = config["elements"]["final-pass-divisions"]
    require(element["status"] == "derived" and element["assume"] == {}, "Unexpected divisions")
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    restored = 0
    for offset, slot in restorations(config).items():
        require(offset in rows and rows[offset]["source"] == slot["source"], "Unknown slot")
        require(slot["edits"], "A pinned slot needs edits")
        for edit in slot["edits"]:
            require(
                re.fullmatch(r"  %[\w.$-]+ = lshr i32 %[\w.$-]+, \d+", code(edit["before"]))
                and re.fullmatch(r"  %[\w.$-]+ = sdiv i32 %[\w.$-]+, \d+", code(edit["after"])),
                "A restoration must turn a logical shift back into the SDK's division",
            )
        for key in ("source_sha256", "replacement_sha256"):
            require(SHA256.fullmatch(slot[key]), "Invalid pinned hash")
        restored_source(rows[offset], slot)
        restored += len(slot["edits"])
    totals = element["totals"]
    require(totals["restore"] == restored, "Restoration count differs from the slots")
    require(
        totals["kept"] + totals["restore"] + totals["shift-proven"] == totals["divisions"],
        "Division totals do not add up",
    )
    require(
        SHA256.fullmatch(config["expected_dll_sha256"])
        and type(config["expected_dll_bytes"]) is int
        and config["expected_dll_bytes"] == manifest["expected_dll_bytes"],
        "Invalid DLL pin (the re-derived elements keep the RC11 layout)",
    )
    return (
        f"re-derived configuration: {totals['divisions']} SDK divisions in "
        f"{totals['slots']} slots, {totals['kept']} kept, {restored} restored, "
        f"{totals['shift-proven']} proven shifts; eligibility {eligibility['status']}"
    )


def check_tree():
    manifest_path = DLL / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    _, profile = drop_profile.select(manifest["sdk_sha256"])
    return check_config(load_config(), manifest, sha(manifest_path.read_bytes()), profile)


def audit(sdk, dxc, output, jobs=2):
    """Gate the SDK, disassemble every manifest slot and decide every division (see audit_slot)."""
    if __package__:
        from .lower_pass import Toolchain
    else:
        from lower_pass import Toolchain
    manifest = json.loads((DLL / "manifest.json").read_text())
    data = Path(sdk).read_bytes()
    _, profile = drop_profile.select(sha(data))
    drop_profile.verify(data, profile)
    output = Path(output)
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(dir=output) as work:
        toolchain = Toolchain(dxc, work, manifest["dxcompiler_sha256"])

        def one(row):
            offset, size = row["original_offset"], row["original_size"]
            container = data[offset : offset + size]
            require(sha(container) == row["original_sha256"], "SDK slot differs from the manifest")
            sdk_text = toolchain.disassemble(container, f"{offset:08x}")
            rc_text = (DLL / row["source"]).read_text()
            require(sha(rc_text) == row["source_sha256"], "RC11 source differs from the manifest")
            return row, audit_slot(sdk_text, rc_text)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one, manifest["replacements"]))
        slots, totals = {}, dict(slots=len(results), divisions=0, kept=0, restore=0)
        totals["shift-proven"] = 0
        for row, entries in results:
            totals["divisions"] += len(entries)
            for entry in entries:
                totals[entry["verdict"]] += 1
            edits = [
                dict(line=e["line"], before=e["before"], after=e["after"])
                for e in entries
                if e["verdict"] == "restore"
            ]
            if not edits:
                continue
            text = restore((DLL / row["source"]).read_text(), edits)
            name = f"{row['original_offset']:08x}"
            (output / f"{name}.ll").write_text(text)
            shader = toolchain.assemble(text)
            (output / f"{name}.dxil").write_bytes(shader)
            slots[hex(row["original_offset"])] = dict(
                entry=row["entry"],
                source=row["source"],
                rc11_source_sha256=row["source_sha256"],
                rc11_replacement_sha256=row["replacement_sha256"],
                edits=edits,
                source_sha256=sha(text),
                replacement_sha256=sha(shader),
                replacement_bytes=len(shader),
            )
    element = dict(
        status="derived",
        stage="dll/port/sdiv_shift.py",
        assume={},
        totals=totals,
        slots=slots,
    )
    detail = [
        dict(offset=hex(row["original_offset"]), entry=row["entry"], divisions=entries)
        for row, entries in results
        if entries
    ]
    record = dict(sdk_sha256=sha(data), element=element, divisions=detail)
    (output / "audit.json").write_text(json.dumps(record, indent=1) + "\n")
    return record


# --------------------------------------------------------------------------- DLL compare


def regions(data, record, profile):
    """Named byte ranges of a built DLL: [(start, end, category, detail)]."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    optional = pe + 24
    out = [
        (pe + 6, pe + 8, "pe-header", "NumberOfSections"),
        (optional + 8, optional + 12, "pe-header", "SizeOfInitializedData"),
        (optional + 56, optional + 60, "pe-header", "SizeOfImage"),
        (optional + 64, optional + 68, "pe-header", "CheckSum (covers every byte)"),
        (
            optional + drop_profile_certificate(),
            optional + drop_profile_certificate() + 8,
            "pe-header",
            "certificate directory",
        ),
    ]
    header = profile["pe"]["spare_section_header"]
    out.append((header, header + 40, "pe-header", "appended section header"))
    for patch in profile["host_patches"]:
        for edit in patch["edits"]:
            start = edit["offset"]
            out.append((start, start + len(edit["before"]) // 2, "host-patch", patch["name"]))
    reference = profile["provider_label"]["reference"]
    end = reference["offset"] + len(reference["original"]) // 2
    out.append((end - 4, end, "provider-label", "label reference displacement"))
    va = raw = None
    count = struct.unpack_from("<H", data, pe + 6)[0]
    table = optional + struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(count):
        name, vsize, rva, size, offset = struct.unpack_from("<8sIIII", data, table + 40 * i)
        if name == b".bc250\0\0":
            va, raw, appended = rva, offset, vsize
    require(va is not None, "No appended section")
    for row in record["replacements"]:
        for ptr in row["pointer_offsets"]:
            out.append((ptr - 8, ptr + 8, "shader-pointer", row["original_offset"]))
        start = raw + row["new_rva"] - va
        out.append((start, start + row["new_size"], "replacement", row["original_offset"]))
    label = raw + record["provider_name_rva"] - va
    out.append((label, raw + appended, "provider-label", "label string"))
    return sorted(out)


def drop_profile_certificate():
    return 112 + 8 * drop_profile.CERTIFICATE_DIRECTORY


def container_parts(blob):
    """{part name: (start, end)} of one DXBC container (DxilContainer.h layout)."""
    count = struct.unpack_from("<I", blob, 28)[0]
    parts = {}
    for off in struct.unpack_from(f"<{count}I", blob, 32):
        name, size = struct.unpack_from("<4sI", blob, off)
        parts[name.decode("ascii")] = (off + 8, off + 8 + size)
    return parts


def compare(a_path, b_path):
    """Every differing byte of two DLLs built from the same SDK, by region; refuses the rest."""
    a, b = Path(a_path).read_bytes(), Path(b_path).read_bytes()
    a_record = json.loads(Path(a_path).with_suffix(".json").read_text())
    b_record = json.loads(Path(b_path).with_suffix(".json").read_text())
    require(a_record["sdk_sha256"] == b_record["sdk_sha256"], "DLLs from different SDKs")
    _, profile = drop_profile.select(a_record["sdk_sha256"])
    require(len(a) == len(b), "The DLLs differ in length (layout changed)")
    layout_a = regions(a, a_record, profile)
    require(layout_a == regions(b, b_record, profile), "The DLLs differ in layout")
    rows_a = {r["original_offset"]: r for r in a_record["replacements"]}
    rows_b = {r["original_offset"]: r for r in b_record["replacements"]}
    runs, start = [], None
    for i in range(len(a) + 1):
        differs = i < len(a) and a[i] != b[i]
        if differs and start is None:
            start = i
        elif not differs and start is not None:
            runs.append((start, i))
            start = None
    out = []
    for start, end in runs:
        hit = [r for r in layout_a if r[0] <= start and end <= r[1]]
        require(hit, f"Difference at {start:#x}..{end:#x} outside every known region")
        region = min(hit, key=lambda r: r[1] - r[0])
        row = dict(start=start, end=end, bytes=end - start, category=region[2], detail=region[3])
        if region[2] == "replacement":
            offset = region[3]
            blob = a[region[0] : region[1]]
            part = [
                name
                for name, (s, e) in container_parts(blob).items()
                if s <= start - region[0] and end - region[0] <= e
            ]
            if 24 <= start - region[0] and end - region[0] <= 28:
                part = ["(container size)"]
            elif 4 <= start - region[0] and end - region[0] <= 20:
                part = ["(container digest)"]
            row.update(
                detail=hex(offset),
                part=part[0] if part else "(spans parts)",
                a_replacement_sha256=rows_a[offset]["replacement_sha256"],
                b_replacement_sha256=rows_b[offset]["replacement_sha256"],
            )
        else:
            row.update(a_hex=a[start:end].hex(), b_hex=b[start:end].hex())
        out.append(row)
    return dict(
        a=dict(path=str(a_path), sha256=sha(a)),
        b=dict(path=str(b_path), sha256=sha(b)),
        differing_bytes=sum(r["bytes"] for r in out),
        runs=out,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="offline: the pinned configuration against RC11's sources")
    command = commands.add_parser("audit", help="decide every SDK division in the manifest slots")
    command.add_argument("--sdk", type=Path, required=True)
    command.add_argument("--dxc", type=Path, required=True, help="extracted DXC 1.9.2607 archive")
    command.add_argument("--output", type=Path, required=True, help="a new directory")
    command.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    command = commands.add_parser("compare", help="explain every differing byte of two DLLs")
    command.add_argument("a", type=Path)
    command.add_argument("b", type=Path)
    command.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check_tree())
        elif args.command == "audit":
            record = audit(args.sdk, args.dxc, args.output, args.jobs)
            pinned = load_config()["elements"]["final-pass-divisions"]
            same = pinned == record["element"]
            print(json.dumps(record["element"]["totals"]))
            print("pinned configuration", "matches" if same else "DIFFERS", "the audit")
            return 0 if same else 1
        else:
            report = compare(args.a, args.b)
            args.json.write_text(json.dumps(report, indent=1) + "\n")
            print(f"{report['differing_bytes']} differing bytes in {len(report['runs'])} runs")
            for run in report["runs"]:
                print(f"{run['start']:#010x} {run['bytes']:6d} {run['category']} {run['detail']}")
    except (drop_profile.ProfileError, LoweringError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit("ERROR: " + str(error))
