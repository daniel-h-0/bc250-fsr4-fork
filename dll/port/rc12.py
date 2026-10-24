#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The RC12 release configuration: the re-derived configuration, RC11's host edits and every
exact C10 program.

dll/rc12.json composes these on RC11's frozen sources:

  base        dll/rederived.json, pinned by hash: its final-pass divisions (the SDK's `sdiv`
              kept, the two RC11 shifts in 0x248220 restored) and its eligibility element
              (while that is "pending", the base leaves the SDK's bytes at the site).
  apply_host_patches
              the drop-profile host patches the base leaves pending that the release applies
              unchanged: `int8-eligibility`, RC11's edit (kept by decision, see
              dll/notices/PROVENANCE.md). It must equal the base's pending list, so the release
              keeps every RC11 host edit.
  slots       every program a lowering recipe pins under `expected` with basis `new` (the C10
              family coverage: the hot slot's RC8/RC9 lowering on the 1080p and 8K families).
              Each slot is stored as line edits of a donor slot's RC11 source (the hot program
              the recipe carried over); applying them must give the recipe's pinned source,
              which the pinned DXC assembles to the recipe's pinned container. A slot's
              `family` (1080p or 8k: the context-size family FSR4 selects it for) is descriptive,
              from the C10 reports.
  label       the provider name the DLL reports ("4.1.1r12"), so logs tell it from RC11.
  release     `release_version` and the DLL pin: scripts/package-dll.py packages this DLL.

`check` (offline, run by scripts/check-repo.py) verifies the configuration against the
manifest, dll/rederived.json, the drop profile and the recipes. `derive` writes the slots from
`lower_pass.py generate` output. `dll/build.py --config rc12` builds the DLL; `compare` explains
every difference between two built DLLs whose appended sections differ in layout.
"""

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

if __package__:
    from . import drop_profile, lower_pass, rederive
    from .ir import LoweringError, require
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import drop_profile
    import lower_pass
    import rederive
    from ir import LoweringError, require

ROOT = Path(__file__).resolve().parent
DLL = ROOT.parent
CONFIG = DLL / "rc12.json"
KIND = "fsr4-rc12-configuration"
SHA256 = re.compile(r"[0-9a-f]{64}")
SECTION_NAME = b".bc250\0\0"


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def load_config(path=CONFIG):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "The configuration must be a regular file")
    return json.loads(path.read_text())


def recipe_pins(recipes=None):
    """{slot: (recipe name, entry, pin)} of every `expected` pin in the committed recipes."""
    out = {}
    for path in sorted(recipes or lower_pass.RECIPES.glob("*.json")):
        recipe = lower_pass.load_recipe(path)
        for key, pin in recipe.get("expected", {}).items():
            require(key not in out, f"{key} is pinned by two recipes")
            out[key] = (recipe["name"], recipe["entry"], pin)
    return out


# ------------------------------------------------------------------------------- slots


MODULE_ID = re.compile(r"; ModuleID = '[^']*'")


def edit(line, before, after):
    """One line edit. A `; ModuleID` comment names the path a source file was written from, so
    the edit records that line's SHA256 instead of its text."""
    if MODULE_ID.fullmatch(before):
        return dict(line=line, before_sha256=sha(before), after=after)
    return dict(line=line, before=before, after=after)


def line_edits(donor, target):
    """Whole-line replacements that turn `donor` into `target` (same line count only)."""
    a, b = donor.split("\n"), target.split("\n")
    require(len(a) == len(b), "The new program does not keep the donor's line count")
    return [edit(n + 1, x, y) for n, (x, y) in enumerate(zip(a, b)) if x != y]


def apply_edits(text, edits):
    """Apply line edits; each must match the source line exactly (by text, or for a ModuleID
    comment by SHA256)."""
    lines = text.split("\n")
    for e in edits:
        n = e["line"] - 1
        require(0 <= n < len(lines), f"Line {e['line']} moved")
        if "before_sha256" in e:
            require(
                "before" not in e
                and MODULE_ID.fullmatch(lines[n])
                and sha(lines[n]) == e["before_sha256"],
                f"Line {e['line']} moved",
            )
        else:
            require(lines[n] == e["before"], f"Line {e['line']} moved")
        lines[n] = e["after"]
    return "\n".join(lines)


def slot_source(rows, key, slot):
    """The RC12 source text of one C10 slot, checked against the donor and the slot pin."""
    donor = rows[int(slot["donor"], 16)]
    text = (DLL / donor["source"]).read_text()
    require(
        sha(text) == donor["source_sha256"] == slot["donor_source_sha256"],
        f"{key}: the donor's RC11 source moved",
    )
    out = apply_edits(text, slot["edits"])
    require(sha(out) == slot["source_sha256"], f"{key}: the RC12 source differs from its pin")
    return out


def check_config(config, manifest, manifest_sha256, profile, base=None, pins=None):
    """Offline: format, both base pins, every slot against its recipe and donor."""
    require(config.get("schema") == 1 and config.get("kind") == KIND, "Not an RC12 config")
    require(config["base_manifest_sha256"] == manifest_sha256, "Configuration for another RC")
    base_path = DLL / config["base"]["path"]
    if base is None:
        require(config["base"]["path"] == "rederived.json", "The base must be dll/rederived.json")
        require(sha(base_path.read_bytes()) == config["base"]["sha256"], "The base moved")
        base = rederive.load_config(base_path)
    summary = rederive.check_config(base, manifest, manifest_sha256, profile)
    applied = config["apply_host_patches"]
    require(
        isinstance(applied, list) and applied == rederive.pending_host_patches(base),
        "The release applies exactly the host patches its base leaves pending (RC11's edits)",
    )
    require(
        set(applied) <= {p["name"] for p in profile["host_patches"]},
        "A host patch the drop profile does not have",
    )
    version = config["release_version"]
    require(
        isinstance(version, str)
        and re.fullmatch(r"\d+\.\d+\.\d+-rc\d+(?:\.\d+)?", version)
        and version != manifest["release_version"],
        "The release version must be a new RC version",
    )
    name = config["provider_name"]
    require(
        isinstance(name, str)
        and name.isascii()
        and name.isprintable()
        and 0 < len(name) <= 32
        and name != manifest["provider_name"],
        "The provider name must be printable ASCII and differ from RC11's",
    )
    pins = recipe_pins() if pins is None else pins
    new = {k: v for k, v in pins.items() if v[2].get("basis", "new") == "new"}
    require(set(config["slots"]) == set(new), "The slots are not exactly the recipes' new programs")
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    restored = {hex(o) for o in rederive.restorations(base)}
    families = set()
    for key, slot in config["slots"].items():
        recipe, entry, pin = new[key]
        offset, donor = int(key, 16), int(slot["donor"], 16)
        require(offset in rows and donor in rows and donor != offset, f"{key}: unknown slot")
        require(key not in restored, f"{key}: already re-derived by the base")
        require(rows[offset]["entry"] == rows[donor]["entry"] == entry, f"{key}: other entry")
        require(slot["recipe"] == recipe and slot["entry"] == entry, f"{key}: other recipe")
        require(
            slot["donor_restored"] == (slot["donor"] in restored),
            f"{key}: the donor's re-derived state is recorded wrongly",
        )
        require(
            all(slot[k] == pin[k] for k in lower_pass.PIN_FIELDS)
            and slot["replacement_sha256"] != rows[offset]["replacement_sha256"],
            f"{key}: the slot pins differ from the recipe's",
        )
        require(
            SHA256.fullmatch(slot["donor_source_sha256"])
            and type(slot["replacement_bytes"]) is int
            and slot["replacement_bytes"] > 0,
            f"{key}: invalid slot record",
        )
        require(slot["edits"], f"{key}: no edits (the slot would be its donor)")
        slot_source(rows, key, slot)
        families.add(slot["family"])
    require(families <= {"1080p", "8k"}, "C10 slots are 1080p- or 8K-family slots")
    require(
        SHA256.fullmatch(config["expected_dll_sha256"])
        and type(config["expected_dll_bytes"]) is int,
        "Invalid DLL pin",
    )
    return (
        f"RC12 configuration {version}: {summary}; host patches applied unchanged from the "
        f"drop profile: {', '.join(applied) or 'none'}; {len(config['slots'])} C10 slots "
        f"({sum(len(s['edits']) for s in config['slots'].values())} line edits of their "
        f"hot donors); provider label {name}"
    )


def omitted_host_patches(config, base):
    """Profile host patches the RC12 build leaves out: those its base leaves pending and the
    configuration does not apply (none: the release applies every pending one)."""
    applied = config["apply_host_patches"]
    return [p for p in rederive.pending_host_patches(base) if p not in applied]


def check_tree():
    manifest_path = DLL / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    _, profile = drop_profile.select(manifest["sdk_sha256"])
    return check_config(load_config(), manifest, sha(manifest_path.read_bytes()), profile)


def derive(generated, families):
    """Slot records from `lower_pass.py generate` output (one directory per recipe name).

    The donor is the recipe's slot of the same entry whose program the new one was carried
    from: among the recipe's slots that are not new, the one with the fewest differing lines.
    """
    manifest = json.loads((DLL / "manifest.json").read_text())
    rows = {r["original_offset"]: r for r in manifest["replacements"]}
    base = rederive.load_config()
    restored = rederive.restorations(base)
    pins = recipe_pins()
    out = {}
    for key, (recipe, entry, pin) in sorted(pins.items(), key=lambda x: int(x[0], 16)):
        if pin.get("basis", "new") != "new":
            continue
        text = (Path(generated) / recipe / "shaders" / f"{int(key, 16):08x}.ll").read_text()
        require(sha(text) == pin["source_sha256"], f"{key}: generated source differs from pin")
        container = (Path(generated) / recipe / "shaders" / f"{int(key, 16):08x}.dxil").read_bytes()
        require(sha(container) == pin["replacement_sha256"], f"{key}: container differs from pin")
        recipe_slots = json.loads((lower_pass.RECIPES / f"{recipe}.json").read_text())["slots"]
        best = None
        for other in recipe_slots:
            if other == key or pins.get(other, (0, 0, {}))[2].get("basis", "release") == "new":
                continue
            row = rows[int(other, 16)]
            donor_text = (DLL / row["source"]).read_text()
            if donor_text.count("\n") != text.count("\n"):
                continue
            edits = line_edits(donor_text, text)
            if best is None or len(edits) < len(best[1]):
                best = (other, edits, row)
        require(best is not None, f"{key}: no donor slot in {recipe}")
        donor, edits, row = best
        out[key] = dict(
            recipe=recipe,
            entry=entry,
            family=families[key],
            donor=donor,
            donor_restored=int(donor, 16) in restored,
            donor_source_sha256=row["source_sha256"],
            source_sha256=pin["source_sha256"],
            replacement_sha256=pin["replacement_sha256"],
            replacement_bytes=len(container),
            edits=edits,
        )
    return out


def dumps(config):
    """The committed file format: two-space indent, one line per line edit."""
    edits = {}

    def mark(key, slot):
        edits[key] = slot["edits"]
        return dict(slot, edits=f"@@{key}@@")

    shell = dict(config, slots={k: mark(k, v) for k, v in config["slots"].items()})
    text = json.dumps(shell, indent=2)
    for key, rows in edits.items():
        lines = ",\n".join("        " + json.dumps(e, ensure_ascii=False) for e in rows)
        text = text.replace(json.dumps(f"@@{key}@@"), "[\n" + lines + "\n      ]")
    return text + "\n"


# --------------------------------------------------------------------------- DLL compare


def _sections(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    optional = pe + 24
    count = struct.unpack_from("<H", data, pe + 6)[0]
    table = optional + struct.unpack_from("<H", data, pe + 20)[0]
    out = {}
    for i in range(count):
        name, vsize, rva, size, offset = struct.unpack_from("<8sIIII", data, table + 40 * i)
        out[name] = dict(vsize=vsize, rva=rva, size=size, offset=offset, header=table + 40 * i)
    return pe, optional, out


def _appended(data, record):
    """Check that the appended section holds exactly the record's containers and label."""
    _, _, sections = _sections(data)
    require(SECTION_NAME in sections, "No appended section")
    section = sections[SECTION_NAME]
    va, raw = section["rva"], section["offset"]
    require(raw + section["size"] == len(data), "The appended section is not the file's end")
    expected = bytearray(section["size"])
    containers = {}
    for row in record["replacements"]:
        start = raw + row["new_rva"] - va
        blob = data[start : start + row["new_size"]]
        require(sha(blob) == row["replacement_sha256"], "A container differs from its record")
        expected[row["new_rva"] - va : row["new_rva"] - va + row["new_size"]] = blob
        containers[row["original_offset"]] = row
    label = record["provider_name"].encode("ascii") + b"\0"
    at = record["provider_name_rva"] - va
    expected[at : at + len(label)] = label
    require(at + len(label) == section["vsize"], "The label is not the section's last item")
    require(data[raw:] == bytes(expected), "The appended section holds unexplained bytes")
    return raw, containers


def prefix_regions(data, record, profile):
    """Named byte ranges before the appended section that a build may set: [(start, end, ...)]."""
    pe, optional, sections = _sections(data)
    out = [
        (pe + 6, pe + 8, "pe-header", "NumberOfSections"),
        (optional + 8, optional + 12, "pe-header", "SizeOfInitializedData"),
        (optional + 56, optional + 60, "pe-header", "SizeOfImage"),
        (optional + 64, optional + 68, "pe-header", "CheckSum (covers every byte)"),
    ]
    certificate = optional + 112 + 8 * drop_profile.CERTIFICATE_DIRECTORY
    out.append((certificate, certificate + 8, "pe-header", "certificate directory"))
    header = sections[SECTION_NAME]["header"]
    out.append((header, header + 40, "pe-header", "appended section header"))
    for patch in profile["host_patches"]:
        for edit in patch["edits"]:
            start = edit["offset"]
            out.append((start, start + len(edit["before"]) // 2, "host-patch", patch["name"]))
    reference = profile["provider_label"]["reference"]
    end = reference["offset"] + len(reference["original"]) // 2
    out.append((end - 4, end, "provider-label", "label reference displacement"))
    for row in record["replacements"]:
        for ptr in row["pointer_offsets"]:
            out.append((ptr - 8, ptr + 8, "shader-pointer", row["original_offset"]))
    return sorted(out)


def compare(a_path, b_path):
    """Every difference of two DLLs built from the same SDK, by region; refuses the rest.

    Unlike rederive.compare, the appended sections may differ in layout: each one must hold
    exactly its build record's containers and label, and the slots are compared by content.
    """
    a, b = Path(a_path).read_bytes(), Path(b_path).read_bytes()
    records = [json.loads(Path(p).with_suffix(".json").read_text()) for p in (a_path, b_path)]
    require(records[0]["sdk_sha256"] == records[1]["sdk_sha256"], "DLLs from different SDKs")
    _, profile = drop_profile.select(records[0]["sdk_sha256"])
    (raw_a, rows_a), (raw_b, rows_b) = _appended(a, records[0]), _appended(b, records[1])
    require(raw_a == raw_b, "The appended sections start at different offsets")
    require(set(rows_a) == set(rows_b), "The DLLs replace different slots")
    layout = prefix_regions(a, records[0], profile)
    require(layout == prefix_regions(b, records[1], profile), "The DLLs differ in layout")
    runs, start = [], None
    for i in range(raw_a + 1):
        differs = i < raw_a and a[i] != b[i]
        if differs and start is None:
            start = i
        elif not differs and start is not None:
            runs.append((start, i))
            start = None
    regions = {}
    for start, end in runs:
        hit = [r for r in layout if r[0] <= start and end <= r[1]]
        require(hit, f"Difference at {start:#x}..{end:#x} outside every known region")
        region = min(hit, key=lambda r: r[1] - r[0])
        key = region[:4]
        regions.setdefault(key, []).append((start, end))
    prefix, moved = [], set()
    for (start, end, category, detail), parts in sorted(regions.items()):
        row = dict(
            start=start,
            end=end,
            category=category,
            detail=hex(detail) if category == "shader-pointer" else detail,
            differing_bytes=sum(e - s for s, e in parts),
        )
        if category == "shader-pointer":
            row.update(
                a=list(struct.unpack_from("<QQ", a, start)),
                b=list(struct.unpack_from("<QQ", b, start)),
            )
            moved.add(detail)
        elif category != "host-patch":  # host-patch bytes stay in the receipts only
            row.update(a_hex=a[start:end].hex(), b_hex=b[start:end].hex())
        prefix.append(row)
    changed = [
        dict(
            slot=hex(o),
            entry=rows_a[o]["entry"],
            a_replacement_sha256=rows_a[o]["replacement_sha256"],
            b_replacement_sha256=rows_b[o]["replacement_sha256"],
            a_bytes=rows_a[o]["new_size"],
            b_bytes=rows_b[o]["new_size"],
        )
        for o in sorted(rows_a)
        if rows_a[o]["replacement_sha256"] != rows_b[o]["replacement_sha256"]
    ]
    changed_slots = {int(c["slot"], 16) for c in changed}
    return dict(
        a=dict(path=str(a_path), sha256=sha(a), bytes=len(a), label=records[0]["provider_name"]),
        b=dict(path=str(b_path), sha256=sha(b), bytes=len(b), label=records[1]["provider_name"]),
        prefix_differences=prefix,
        changed_slots=changed,
        moved_only_slots=sorted(hex(o) for o in moved - changed_slots),
        host_patches_differing=sorted(
            {r["detail"] for r in prefix if r["category"] == "host-patch"}
        ),
        label_changed=records[0]["provider_name"] != records[1]["provider_name"],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="offline: the configuration against manifest and recipes")
    command = commands.add_parser("derive", help="print the slots from lower_pass generate output")
    command.add_argument("--generated", type=Path, required=True, help="DIR/<recipe>/shaders")
    command.add_argument("--families", type=Path, required=True, help="JSON {slot: family}")
    command = commands.add_parser("compare", help="explain every difference of two DLLs")
    command.add_argument("a", type=Path)
    command.add_argument("b", type=Path)
    command.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check_tree())
        elif args.command == "derive":
            families = json.loads(args.families.read_text())
            print(json.dumps(derive(args.generated, families), indent=1))
        else:
            report = compare(args.a, args.b)
            args.json.write_text(json.dumps(report, indent=1) + "\n")
            print(
                f"{len(report['changed_slots'])} changed slots, "
                f"{len(report['moved_only_slots'])} moved only, host patches differing "
                f"{report['host_patches_differing']}, label "
                f"{report['a']['label']} -> {report['b']['label']}"
            )
            for row in report["prefix_differences"]:
                if row["category"] != "shader-pointer":
                    print(
                        f"{row['start']:#010x} {row['differing_bytes']:4d} {row['category']} "
                        f"{row['detail']}"
                    )
            for row in report["changed_slots"]:
                print(f"{row['slot']:>9} {row['entry']} {row['a_bytes']} -> {row['b_bytes']}")
    except (drop_profile.ProfileError, LoweringError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit("ERROR: " + str(error))
