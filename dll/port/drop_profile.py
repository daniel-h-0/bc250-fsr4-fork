#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Derive and check the layout profile of an allowlisted FidelityFX SDK upscaler DLL.

A drop profile holds every SDK-specific fact the repacker uses: the PE layout, each shader slot
reachable through a relocated (length, address) pair, the host patch sites with their original
and replacement bytes, and the provider label with its only code reference. `derive` computes the
profile from the DLL bytes alone; the build refuses an SDK whose derived profile differs from the
reviewed copy in profiles/. Only allowlisted DLLs are read (sdk_intake's license gate).
"""

import argparse
import hashlib
import json
import re
import struct
import sys
from array import array
from pathlib import Path

if __package__:
    from . import sdk_intake
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sdk_intake

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"
SCHEMA = 1
KIND = "fidelityfx-sdk-drop-profile"
EXECUTE = 0x20000000
RELOCATION_TYPES = {0: "absolute", 10: "dir64"}
CERTIFICATE_DIRECTORY = 4
RELOCATION_DIRECTORY = 5
LABEL_VARIANT = b"FSR4-i8"
LABEL = re.compile(rb"(?<=\x00)([0-9]+\.[0-9]+\.[0-9]+)(\x00+)" + LABEL_VARIANT + rb"\x00")
LEA_RIP = re.compile(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", re.DOTALL)

# Host patch sites: byte patterns ("??" = any byte), the patched instruction's position in the
# pattern, and the semantic checks applied to the match before the edits are derived.
ELIGIBILITY = dict(
    name="int8-eligibility",
    section=".text",
    pattern=(
        "817c24?? 91000000 "  # cmp dword [rsp+family], 0x91
        "440fb6c0 75?? "  # movzx r8d, al (query succeeded); jne reject
        "8b5424?? "  # mov edx, [rsp+revision]
        "8d4aff 83f90e 76?? "  # revision 1..15: jbe accept
        "8d4ae0 81f9de000000 76?? "  # revision 0x20..0xfe: jbe accept
        "8d4af0 83f90f 77?? "  # revision 0x10..0x1f, else ja reject
        "b001"  # accept: mov al, 1
    ),
    anchor=0,
)
BARRIER = dict(
    name="buffer-uav-barrier",
    section=".text",
    pattern=(
        "486310 85d2 0f84???????? "  # movsxd rdx, [rax]; test edx, edx; je (null binding)
        "41f686????????01 "  # test byte [r14+flags], 1 (SKIP_BARRIERS)
        "0f85???????? "  # jne (skip the barrier)
        "448b83???????? 4c8bd2 49c1e206 4c0393???????? "  # r10 = &resources[index]
        "49c1e005 4c03c3 498b12 418b4a2c 488955?? "
        "f6c102"  # test cl, 2 (UAV)
    ),
    anchor=11,
)


class ProfileError(ValueError):
    """The DLL's layout is not one the repacker understands, or differs from its profile."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Image:
    """Read-only PE32+ x86-64 view with the offset/RVA mapping used by the repacker."""

    def __init__(self, data):
        self.data = data
        try:
            if data[:2] != b"MZ":
                raise ProfileError("Not a PE image")
            self.pe = struct.unpack_from("<I", data, 0x3C)[0]
            machine, count = struct.unpack_from("<HH", data, self.pe + 4)
            if data[self.pe : self.pe + 4] != b"PE\0\0" or machine != 0x8664:
                raise ProfileError("Not an x86-64 PE image")
            self.optional = self.pe + 24
            if struct.unpack_from("<H", data, self.optional)[0] != 0x20B:
                raise ProfileError("Not a PE32+ image")
            optional_size = struct.unpack_from("<H", data, self.pe + 20)[0]
            self.image_base = struct.unpack_from("<Q", data, self.optional + 24)[0]
            self.section_alignment, self.file_alignment = struct.unpack_from(
                "<II", data, self.optional + 32
            )
            self.size_of_image = struct.unpack_from("<I", data, self.optional + 56)[0]
            self.checksum = struct.unpack_from("<I", data, self.optional + 64)[0]
            self.directories = struct.unpack_from("<I", data, self.optional + 108)[0]
            self.table = self.optional + optional_size
            self.sections = []
            for index in range(count):
                start = self.table + 40 * index
                vsize, rva, size, offset = struct.unpack_from("<IIII", data, start + 8)
                flags = struct.unpack_from("<I", data, start + 36)[0]
                name = bytes(data[start : start + 8]).rstrip(b"\0").decode("ascii")
                self.sections.append(
                    dict(
                        name=name,
                        rva=rva,
                        virtual_size=vsize,
                        offset=offset,
                        bytes=size,
                        characteristics=flags,
                    )
                )
        except (struct.error, UnicodeDecodeError) as error:
            raise ProfileError("Malformed PE image") from error
        if len({s["name"] for s in self.sections}) != len(self.sections):
            raise ProfileError("Duplicate PE section names")

    def section(self, name):
        for section in self.sections:
            if section["name"] == name:
                return section
        raise ProfileError("The image has no section " + name)

    def section_at(self, offset):
        for section in self.sections:
            if section["offset"] <= offset < section["offset"] + section["bytes"]:
                return section
        return None

    def to_rva(self, offset):
        section = self.section_at(offset)
        if section is None:
            raise ProfileError("Offset outside section")
        return section["rva"] + offset - section["offset"]

    def to_offset(self, rva, required=True):
        for section in self.sections:
            if section["rva"] <= rva < section["rva"] + section["bytes"]:
                return section["offset"] + rva - section["rva"]
        if required:
            raise ProfileError("RVA outside section")
        return None

    def directory(self, index):
        if index >= self.directories:
            return 0, 0
        return struct.unpack_from("<II", self.data, self.optional + 112 + 8 * index)

    def body(self, section):
        return self.data[section["offset"] : section["offset"] + section["bytes"]]

    def code_sections(self):
        return [s for s in self.sections if s["characteristics"] & EXECUTE]


def relocations(image):
    """DIR64 relocation RVAs and per-type counts; any other relocation type is refused."""
    rva, size = image.directory(RELOCATION_DIRECTORY)
    pos = image.to_offset(rva)
    end = pos + size
    dir64, counts = set(), {name: 0 for name in RELOCATION_TYPES.values()}
    while pos < end:
        page, block = struct.unpack_from("<II", image.data, pos)
        if not 8 <= block <= end - pos or block % 2:
            raise ProfileError("Invalid SDK relocation block")
        for word in struct.unpack_from("<" + "H" * ((block - 8) // 2), image.data, pos + 8):
            kind = RELOCATION_TYPES.get(word >> 12)
            if kind is None:
                raise ProfileError(f"Unsupported relocation type {word >> 12}")
            counts[kind] += 1
            if kind == "dir64":
                dir64.add(page + (word & 4095))
        pos += block
    listing = b"".join(struct.pack("<I", value) for value in sorted(dir64))
    return dir64, dict(rva=rva, bytes=size, **counts, dir64_sha256=sha256(listing))


def pe_layout(image, relocation_summary):
    data = image.data
    certificate, certificate_size = image.directory(CERTIFICATE_DIRECTORY)
    raw_end = max(s["offset"] + s["bytes"] for s in image.sections)
    if certificate < raw_end or certificate + certificate_size != len(data):
        raise ProfileError("Unexpected SDK certificate placement")
    spare = image.table + 40 * len(image.sections)
    if spare + 40 > min(s["offset"] for s in image.sections) or any(data[spare : spare + 40]):
        raise ProfileError("No empty PE section-header slot")
    return dict(
        image_base=image.image_base,
        section_alignment=image.section_alignment,
        file_alignment=image.file_alignment,
        size_of_image=image.size_of_image,
        checksum=image.checksum,
        section_table=image.table,
        spare_section_header=spare,
        sections=image.sections,
        certificate=dict(offset=certificate, bytes=certificate_size),
        relocations=relocation_summary,
    )


def entry_name(data, part):
    """Entry function name from a PSV0 v3+ runtime-info part, or None."""
    if part is None:
        return None
    start, size = part
    body = data[start : start + size]
    try:
        info = struct.unpack_from("<I", body, 0)[0]
        if info < 52:
            return None
        index = struct.unpack_from("<I", body, 4 + 48)[0]
        pos = 4 + info
        resources = struct.unpack_from("<I", body, pos)[0]
        pos += 4
        if resources:
            pos += 4 + struct.unpack_from("<I", body, pos)[0] * resources
        length = struct.unpack_from("<I", body, pos)[0]
        strings = body[pos + 4 : pos + 4 + length]
        end = strings.index(b"\0", index)
        return strings[index:end].decode("ascii") or None
    except (struct.error, ValueError, UnicodeDecodeError):
        return None


def containers(data):
    """Every DXBC container with a DXIL part: offset -> (bytes, sha256, entry)."""
    found, scan = {}, 0
    while (scan := data.find(b"DXBC", scan)) >= 0:
        offset, scan = scan, scan + 4
        if offset + 32 > len(data):
            continue
        version, size, count = struct.unpack_from("<III", data, offset + 20)
        if version != 1 or not 0 < count <= 128 or not 32 + 4 * count <= size <= len(data) - offset:
            continue
        parts = {}
        for index in range(count):
            child = struct.unpack_from("<I", data, offset + 32 + 4 * index)[0]
            if child + 8 > size:
                break
            child_size = struct.unpack_from("<I", data, offset + child + 4)[0]
            if child + 8 + child_size > size:
                break
            tag = data[offset + child : offset + child + 4].decode("latin-1")
            parts.setdefault(tag, (offset + child + 8, child_size))
        else:
            if "DXIL" in parts:
                found[offset] = dict(
                    bytes=size,
                    sha256=sha256(data[offset : offset + size]),
                    entry=entry_name(data, parts.get("PSV0")),
                )
    return found


def shader_table(image, found, dir64):
    """Slots: containers referenced by relocated (u64 length, u64 VA) pairs."""
    data = image.data
    by_va = {}
    for offset in found:
        section = image.section_at(offset)
        if section is not None:
            by_va[image.image_base + image.to_rva(offset)] = offset
    pointers = {}
    for rva in sorted(dir64):
        at = image.to_offset(rva, required=False)
        if at is None or at < 8 or at + 8 > len(data):
            continue
        target = by_va.get(struct.unpack_from("<Q", data, at)[0])
        if target is None:
            continue
        section = image.section_at(at)
        if section["characteristics"] & EXECUTE:
            raise ProfileError(f"Shader pointer at 0x{at:x} lies in code")
        if struct.unpack_from("<Q", data, at - 8)[0] != found[target]["bytes"]:
            raise ProfileError(f"Relocated pointer at 0x{at:x} lacks its shader's length")
        pointers.setdefault(target, []).append(at)
    if not pointers:
        raise ProfileError("No relocated shader pointer pairs")
    sections = sorted({image.section_at(p)["name"] for ps in pointers.values() for p in ps})
    relocated = {p for ps in pointers.values() for p in ps}
    for name in sections:
        # Every aligned pair in a pointer section must be relocated (the RC11 repacker's scan).
        section = image.section(name)
        for at in range(section["offset"] + 8, section["offset"] + section["bytes"] - 7, 8):
            length, value = struct.unpack_from("<QQ", data, at - 8)
            target = by_va.get(value)
            if target is not None and length == found[target]["bytes"] and at not in relocated:
                raise ProfileError(f"Shader pointer at 0x{at:x} lacks a DIR64 relocation")
    slots, unreferenced = [], []
    for offset in sorted(found):
        row = dict(offset=offset, **found[offset])
        if offset in pointers:
            slots.append(dict(row, pointers=sorted(pointers[offset])))
        else:
            unreferenced.append(row)
    return dict(
        containers=len(found),
        pointer_pair="u64 length at pointer-8, u64 VA with a DIR64 relocation",
        pointer_sections=sections,
        slots=slots,
        unreferenced=unreferenced,
    )


def pattern_regex(pattern):
    parts = []
    for token in pattern.split():
        if len(token) % 2:
            raise ValueError("Odd pattern token: " + token)
        for index in range(0, len(token), 2):
            pair = token[index : index + 2]
            parts.append(b"." if pair == "??" else re.escape(bytes.fromhex(pair)))
    return re.compile(b"(?=(" + b"".join(parts) + b"))", re.DOTALL)


def locate(image, site):
    """File offset of the site's patched instruction; exactly one match is required."""
    section = image.section(site["section"])
    matches = [m.start() for m in pattern_regex(site["pattern"]).finditer(image.body(section))]
    if len(matches) != 1:
        raise ProfileError(f"{site['name']}: pattern found {len(matches)} times, expected once")
    return section["offset"] + matches[0] + site["anchor"]


def rel8(data, at):
    """Target of the 2-byte short jump at `at`."""
    return at + 2 + struct.unpack_from("<b", data, at + 1)[0]


def edit(data, offset, after):
    return dict(offset=offset, before=data[offset : offset + len(after)].hex(), after=after.hex())


def eligibility_patch(image):
    """Force the INT8 path's device test: store the accepted family and revision, then accept."""
    data = image.data
    site = locate(image, ELIGIBILITY)
    accept, reject = site + 45, rel8(data, site + 12)
    if rel8(data, site + 24) != accept or rel8(data, site + 35) != accept:
        raise ProfileError("int8-eligibility: the revision ranges do not branch to the accept path")
    if rel8(data, site + 43) != reject:
        raise ProfileError("int8-eligibility: the two reject branches differ")
    family, revision = data[site + 3], data[site + 17]
    jump = accept - (site + 24)
    if not -128 <= jump <= 127:
        raise ProfileError("int8-eligibility: the accept path is out of short-jump range")
    replacement = (
        bytes([0xC7, 0x44, 0x24, revision]) + struct.pack("<I", 1)  # mov [rsp+revision], 1
        + bytes([0x41, 0xB8]) + struct.pack("<I", 1)  # mov r8d, 1
        + bytes([0xEB]) + struct.pack("<b", jump)  # jmp accept
    )  # fmt: skip
    return dict(
        name=ELIGIBILITY["name"],
        section=ELIGIBILITY["section"],
        pattern=ELIGIBILITY["pattern"],
        site=site,
        captures=dict(family_slot=family, revision_slot=revision, accept=accept, reject=reject),
        edits=[
            edit(data, site, bytes([0xC7, 0x44])),  # cmp -> mov dword [rsp+family], 0x91
            edit(data, site + 8, replacement),
        ],
    )


def barrier_patch(image):
    """Keep the buffer-UAV addBarrier call when a job requests SKIP_BARRIERS."""
    data = image.data
    site = locate(image, BARRIER)
    field = data[site + 3 : site + 7]
    section = image.section(BARRIER["section"])
    tests = [
        section["offset"] + m.start()
        for m in re.finditer(re.escape(b"\x41\xf6\x86" + field + b"\x01"), image.body(section))
    ]
    return dict(
        name=BARRIER["name"],
        section=BARRIER["section"],
        pattern=BARRIER["pattern"],
        site=site,
        captures=dict(flags_field=struct.unpack("<I", field)[0], flag_tests=tests),
        edits=[edit(data, site + 7, b"\x00")],
    )


def rip_references(image, target):
    """(displacement offset, trailing immediate bytes) of every RIP-relative disp32 to `target`."""
    found = []
    for section in image.code_sections():
        body = image.body(section)
        for phase in range(4):
            count = (len(body) - phase) // 4
            words = array("i", body[phase : phase + 4 * count])
            if sys.byteorder != "little":
                words.byteswap()
            base = target - section["rva"] - phase - 4
            for index, displacement in enumerate(words):
                trailing = base - 4 * index - displacement
                if trailing in (0, 1, 2, 4):
                    found.append((section["offset"] + phase + 4 * index, trailing))
    return sorted(found)


def provider_label(image, dir64):
    """The INT8 provider's version string and its single code reference (a RIP-relative LEA)."""
    data = image.data
    matches = []
    for section in image.sections:
        if section["characteristics"] & EXECUTE:
            continue
        for m in LABEL.finditer(image.body(section)):
            offset = section["offset"] + m.start()
            variant = offset + len(m.group(1)) + len(m.group(2))
            if image.to_rva(offset) % 8 == 0 and image.to_rva(variant) % 8 == 0:
                matches.append((offset, m))
    if len(matches) != 1:
        raise ProfileError(f"provider label: {len(matches)} version strings before 'FSR4-i8'")
    offset, match = matches[0]
    rva = image.to_rva(offset)
    va = image.image_base + rva
    for at in dir64:
        location = image.to_offset(at, required=False)
        if location is not None and struct.unpack_from("<Q", data, location)[0] == va:
            raise ProfileError("provider label: an absolute pointer also references it")
    references = rip_references(image, rva)
    if len(references) != 1 or references[0][1] != 0:
        raise ProfileError(f"provider label: {len(references)} code references, expected one LEA")
    lea = references[0][0] - 3
    if not LEA_RIP.fullmatch(data[lea : lea + 3]):
        raise ProfileError("provider label: the reference is not a RIP-relative LEA")
    slot = len(match.group(1)) + len(match.group(2))
    return dict(
        variant=LABEL_VARIANT.decode(),
        version=match.group(1).decode(),
        offset=offset,
        rva=rva,
        slot_bytes=slot,
        original=data[offset : offset + slot + len(LABEL_VARIANT) + 1].hex(),
        reference=dict(offset=lea, original=data[lea : lea + 7].hex()),
    )


def drop_identity(record, data):
    if sha256(data) != record["dll"]["sha256"] or len(data) != record["dll"]["bytes"]:
        raise sdk_intake.Refused("The DLL bytes differ from the drop record")
    return dict(
        tag=record["tag"],
        commit=record["commit"],
        dll_sha256=record["dll"]["sha256"],
        dll_bytes=record["dll"]["bytes"],
    )


def derive(data, record):
    """The profile of an allowlisted SDK DLL; `record` is its allowlist entry."""
    drop = drop_identity(record, data)
    image = Image(data)
    dir64, relocation_summary = relocations(image)
    return dict(
        schema=SCHEMA,
        kind=KIND,
        drop=drop,
        pe=pe_layout(image, relocation_summary),
        shaders=shader_table(image, containers(data), dir64),
        host_patches=[eligibility_patch(image), barrier_patch(image)],
        provider_label=provider_label(image, dir64),
    )


def survey(data, record):
    """Derive each component independently; report what a new drop breaks."""
    drop_identity(record, data)
    try:
        image = Image(data)
        dir64, relocation_summary = relocations(image)
    except ProfileError as error:
        return {"pe": "FAIL: " + str(error)}

    def shaders():
        table = shader_table(image, containers(data), dir64)
        return (
            f"{table['containers']} containers, {len(table['slots'])} slots in "
            f"{', '.join(table['pointer_sections'])}, {len(table['unreferenced'])} unreferenced"
        )

    steps = {
        "pe": lambda: f"{len(pe_layout(image, relocation_summary)['sections'])} sections",
        "shaders": shaders,
        ELIGIBILITY["name"]: lambda: f"site 0x{eligibility_patch(image)['site']:x}",
        BARRIER["name"]: lambda: f"site 0x{barrier_patch(image)['site']:x}",
        "provider_label": lambda: "{version!r} at 0x{offset:x}".format(
            **provider_label(image, dir64)
        ),
    }
    report = {}
    for name, step in steps.items():
        try:
            report[name] = "ok: " + step()
        except ProfileError as error:
            report[name] = "FAIL: " + str(error)
    return report


def dumps(profile):
    """Canonical text: two-space JSON with one line per shader slot."""
    shaders = profile["shaders"]
    lists = {key: shaders[key] for key in ("slots", "unreferenced")}
    shell = dict(profile, shaders=dict(shaders, **{key: f"@@{key}@@" for key in lists}))
    text = json.dumps(shell, indent=2)
    for key, rows in lists.items():
        body = ",\n".join("      " + json.dumps(row) for row in rows)
        text = text.replace(f'"@@{key}@@"', "[\n" + body + "\n    ]" if rows else "[]")
    return text + "\n"


def differences(derived, pinned, path="profile"):
    if type(derived) is not type(pinned):
        return [path]
    if isinstance(derived, dict):
        found = [f"{path}.{key}" for key in sorted(set(derived) ^ set(pinned))]
        for key in sorted(set(derived) & set(pinned)):
            found += differences(derived[key], pinned[key], f"{path}.{key}")
        return found
    if isinstance(derived, list):
        if len(derived) != len(pinned):
            return [f"{path} (length {len(derived)} vs {len(pinned)})"]
        found = []
        for index, (a, b) in enumerate(zip(derived, pinned)):
            found += differences(a, b, f"{path}[{index}]")
        return found
    return [] if derived == pinned else [path]


def check_format(profile):
    keys = {"schema", "kind", "drop", "pe", "shaders", "host_patches", "provider_label"}
    if not isinstance(profile, dict) or set(profile) != keys:
        raise ProfileError("Malformed drop profile")
    if profile["schema"] != SCHEMA or profile["kind"] != KIND:
        raise ProfileError("Unsupported drop profile schema")
    slots = profile["shaders"]["slots"]
    offsets = [row["offset"] for row in slots]
    if offsets != sorted(set(offsets)) or not all(row["pointers"] for row in slots):
        raise ProfileError("Drop profile slots must be unique, sorted and referenced")
    names = [patch["name"] for patch in profile["host_patches"]]
    if names != [ELIGIBILITY["name"], BARRIER["name"]]:
        raise ProfileError("Drop profile host patches differ from the supported set")
    for patch in profile["host_patches"]:
        for change in patch["edits"]:
            if len(bytes.fromhex(change["before"])) != len(bytes.fromhex(change["after"])):
                raise ProfileError("Drop profile edit changes length")
    return profile


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ProfileError("Drop profiles must be regular files")
    text = path.read_text()
    profile = check_format(json.loads(text))
    if dumps(profile) != text:
        raise ProfileError(f"{path.name} is not in canonical form (rewrite it with 'derive')")
    if path.name != f"sdk-{profile['drop']['tag'].removeprefix('v')}.json":
        raise ProfileError(f"{path.name} does not match its drop tag")
    return profile


def select(dll_sha256, directory=PROFILES):
    """The one reviewed profile for an SDK DLL hash."""
    matches = []
    for path in sorted(Path(directory).glob("*.json")):
        profile = load(path)
        if profile["drop"]["dll_sha256"] == dll_sha256:
            matches.append((path, profile))
    if len(matches) != 1:
        raise ProfileError(f"{len(matches)} drop profiles for SDK {dll_sha256}, expected one")
    return matches[0]


def verify(data, profile, allowlist=None):
    """Gate the bytes, re-derive their profile and require it to equal the reviewed one."""
    records = sdk_intake.allowlisted(data, allowlist)
    record = next((r for r in records if r["tag"] == profile["drop"]["tag"]), None)
    if record is None:
        raise sdk_intake.Refused("The SDK DLL is not the profile's allowlisted drop")
    found = differences(derive(data, record), profile)
    if found:
        shown = ", ".join(found[:8]) + (f" and {len(found) - 8} more" if len(found) > 8 else "")
        raise ProfileError("The SDK's derived layout differs from its reviewed profile: " + shown)
    return profile


def check_manifest(profile, manifest):
    """The RC manifest's slots and synchronization record agree with the profile."""
    if manifest["sdk_sha256"] != profile["drop"]["dll_sha256"]:
        raise ProfileError("The manifest's SDK is not the profile's drop")
    slots = {row["offset"]: row for row in profile["shaders"]["slots"]}
    for row in manifest["replacements"]:
        slot = slots.get(row["original_offset"])
        if slot is None:
            raise ProfileError(f"Manifest slot 0x{row['original_offset']:x} is not a profile slot")
        if (slot["bytes"], slot["sha256"], slot["entry"]) != (
            row["original_size"],
            row["original_sha256"],
            row["entry"],
        ):
            raise ProfileError(f"Manifest slot 0x{row['original_offset']:x} differs from the drop")
    barrier = profile["host_patches"][1]["edits"]
    sync = manifest.get("sdk_synchronization")
    if (
        sync is not None
        and [
            dict(
                offset=sync["offset"],
                before=f"{sync['original_byte']:02x}",
                after=f"{sync['replacement_byte']:02x}",
            )
        ]
        != barrier
    ):
        raise ProfileError("The manifest's synchronization record differs from the profile")
    return f"{len(manifest['replacements'])} manifest slots match drop {profile['drop']['tag']}"


def check_tree():
    """Offline: every profile is canonical and allowlisted; the RC manifest matches its profile."""
    allowlist = sdk_intake.read_allowlist()[0]
    records = {r["tag"]: r for r in allowlist["drops"]}
    paths = sorted(PROFILES.glob("*.json"))
    if not paths:
        raise ProfileError("No drop profiles")
    for path in paths:
        profile = load(path)
        record = records.get(profile["drop"]["tag"])
        if record is None or profile["drop"] != dict(
            tag=record["tag"],
            commit=record["commit"],
            dll_sha256=record["dll"]["sha256"],
            dll_bytes=record["dll"]["bytes"],
        ):
            raise ProfileError(f"{path.name} is not an allowlisted drop")
    manifest = json.loads((ROOT.parent / "manifest.json").read_text())
    _, profile = select(manifest["sdk_sha256"])
    tags = ", ".join(load(path)["drop"]["tag"] for path in paths)
    return f"drop profiles {tags}; " + check_manifest(profile, manifest)


def read_gated(path, tag=None):
    data = Path(path).read_bytes()
    records = sdk_intake.allowlisted(data, name=Path(path).name)
    if tag is not None:
        records = [r for r in records if r["tag"] == tag]
    if len(records) != 1:
        raise sdk_intake.Refused("The DLL matches several drops; choose one with --tag")
    return data, records[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("derive", help="print or write a draft profile")
    command.add_argument("--sdk", type=Path, required=True)
    command.add_argument("--tag")
    command.add_argument("--output", type=Path, help="new file outside the source tree")
    command = commands.add_parser("check", help="derive and compare with the reviewed profile")
    command.add_argument("--sdk", type=Path)
    command = commands.add_parser("survey", help="report which profile components derive")
    command.add_argument("--sdk", type=Path, required=True)
    command.add_argument("--tag")
    args = parser.parse_args(argv)
    try:
        if args.command == "derive":
            data, record = read_gated(args.sdk, args.tag)
            text = dumps(derive(data, record))
            if args.output is None:
                sys.stdout.write(text)
            else:
                output = sdk_intake.outside_source(args.output)
                with output.open("x") as stream:
                    stream.write(text)
                print(f"{output} sha256 {sha256(text.encode())}")
        elif args.command == "check":
            print(check_tree())
            if args.sdk is not None:
                data = args.sdk.read_bytes()
                path, profile = select(sha256(data))
                verify(data, profile)
                print(f"{args.sdk.name} derives {path.name} exactly")
        else:
            data, record = read_gated(args.sdk, args.tag)
            print(json.dumps(dict(tag=record["tag"], **survey(data, record)), indent=2))
    except (sdk_intake.Refused, ProfileError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit("ERROR: " + str(error))
