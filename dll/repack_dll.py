# SPDX-License-Identifier: MIT
"""Embed validated replacements in a single SDK DLL; never install it.

Every SDK-specific offset and byte (shader slots and their pointers, host patch sites, the
provider label and its reference, certificate and section-table placement) comes from the drop
profile in port/profiles/. The profile is re-derived from the SDK bytes on every build and must
equal the reviewed copy.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

if __package__:
    from .port import drop_profile
else:
    from port import drop_profile

ROOT = Path(__file__).resolve().parent
SECTION_NAME = b".bc250\0\0"
SECTION_FLAGS = 0x40000040  # initialized data, read-only
CERTIFICATE_DIRECTORY = 112 + 8 * drop_profile.CERTIFICATE_DIRECTORY


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n, alignment):
    return (n + alignment - 1) // alignment * alignment


def build(sdk, manifest, output, profile=None, allowlist=None, omit_host_patches=()):
    """Write the DLL and its record. `omit_host_patches` names profile host patches to leave out
    (their SDK bytes are checked and kept); the RC builds apply every patch."""
    if sys.byteorder != "little":
        raise ValueError("The reproducible DLL builder requires a little-endian host")
    for path in (output, output.with_suffix(".json")):
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    if not manifest["replacements"]:
        raise ValueError("Empty replacement set")
    original = sdk.read_bytes()
    if profile is None:
        profile = drop_profile.select(sha(original))[1]
    if sha(original) != profile["drop"]["dll_sha256"] or manifest["sdk_sha256"] != sha(original):
        raise ValueError("Unrecognized SDK")
    drop_profile.verify(original, profile, allowlist)
    layout = profile["pe"]
    data = bytearray(original)
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    optional = pe + 24
    count = len(layout["sections"])
    image_base = layout["image_base"]
    section_alignment, file_alignment = layout["section_alignment"], layout["file_alignment"]
    sections = layout["sections"]

    def to_rva(offset):
        for section in sections:
            if section["offset"] <= offset < section["offset"] + section["bytes"]:
                return section["rva"] + offset - section["offset"]
        raise ValueError("Offset outside section")

    omitted = sorted(set(omit_host_patches))
    if not set(omitted) <= {patch["name"] for patch in profile["host_patches"]}:
        raise ValueError("Unknown host patch to omit")
    host_changes = []
    for patch in profile["host_patches"]:
        for change in patch["edits"]:
            at = change["offset"]
            before, after = bytes.fromhex(change["before"]), bytes.fromhex(change["after"])
            if len(before) != len(after) or data[at : at + len(before)] != before:
                raise ValueError(f"Unexpected SDK bytes at the {patch['name']} site")
            if patch["name"] in omitted:
                continue
            data[at : at + len(after)] = after
            host_changes.append(dict(offset=at, before=change["before"], after=change["after"]))
    # The new provider name does not fit the original string slot. Put the terminated name in
    # the appended section and redirect the label's only code reference, a RIP-relative LEA;
    # the adjacent variant string and the numeric provider/API versions stay intact.
    label = profile["provider_label"]
    original_label = bytes.fromhex(label["original"])
    if data[label["offset"] : label["offset"] + len(original_label)] != original_label:
        raise ValueError("The SDK provider label differs from the profile")
    reference = label["reference"]
    lea = bytes.fromhex(reference["original"])
    label_instruction = reference["offset"]
    if data[label_instruction : label_instruction + len(lea)] != lea:
        raise ValueError("The SDK provider label reference differs from the profile")
    provider_name = manifest["provider_name"]
    if not provider_name or not provider_name.isascii() or not provider_name.isprintable():
        raise ValueError("The provider name must be printable ASCII")
    cert, size = struct.unpack_from("<II", data, optional + CERTIFICATE_DIRECTORY)
    if dict(offset=cert, bytes=size) != layout["certificate"]:
        raise ValueError("Unexpected SDK certificate placement")
    data = data[:cert]
    struct.pack_into("<II", data, optional + CERTIFICATE_DIRECTORY, 0, 0)
    raw = align(len(data), file_alignment)
    va = align(max(s["rva"] + s["virtual_size"] for s in sections), section_alignment)
    header = layout["spare_section_header"]
    if any(data[header : header + 40]):
        raise ValueError("No empty PE section-header slot")
    slots = {slot["offset"]: slot for slot in profile["shaders"]["slots"]}
    appended, records, offsets = bytearray(), [], set()
    for row in manifest["replacements"]:
        offset, size = row["original_offset"], row["original_size"]
        slot = slots.get(offset)
        if (
            slot is None
            or offset in offsets
            or slot["bytes"] != size
            or slot["sha256"] != row["original_sha256"]
            or sha(original[offset : offset + size]) != row["original_sha256"]
        ):
            raise ValueError("Duplicate/mismatched shader")
        offsets.add(offset)
        shader = Path(row["replacement"]).read_bytes()
        if (
            sha(shader) != row["replacement_sha256"]
            or shader[:4] != b"DXBC"
            or struct.unpack_from("<I", shader, 24)[0] != len(shader)
        ):
            raise ValueError("Replacement hash/container mismatch")
        pointers = slot["pointers"]
        for ptr in pointers:
            if struct.unpack_from("<QQ", original, ptr - 8) != (size, image_base + to_rva(offset)):
                raise ValueError("Shader pointer differs from the profile")
        start = align(len(appended), 16)
        appended.extend(bytes(start - len(appended)))
        appended.extend(shader)
        for ptr in pointers:
            struct.pack_into("<QQ", data, ptr - 8, len(shader), image_base + va + start)
        records.append(
            dict(
                original_offset=offset,
                original_sha256=row["original_sha256"],
                replacement_sha256=row["replacement_sha256"],
                entry=row["entry"],
                new_rva=va + start,
                new_size=len(shader),
                pointer_offsets=pointers,
            )
        )
    label_rva = va + len(appended)
    appended.extend(provider_name.encode("ascii") + b"\0")
    end = label_instruction + len(lea)
    displacement = label_rva - to_rva(end)
    struct.pack_into("<i", data, end - 4, displacement)
    host_changes.append(
        dict(offset=end - 4, before=lea[-4:].hex(), after=struct.pack("<i", displacement).hex())
    )
    size = align(len(appended), file_alignment)
    data.extend(bytes(raw - len(data)))
    data.extend(appended)
    data.extend(bytes(size - len(appended)))
    struct.pack_into(
        "<8sIIIIIIHHI",
        data,
        header,
        SECTION_NAME,
        len(appended),
        va,
        size,
        raw,
        0,
        0,
        0,
        0,
        SECTION_FLAGS,
    )
    struct.pack_into("<H", data, pe + 6, count + 1)
    struct.pack_into("<I", data, optional + 56, align(va + len(appended), section_alignment))
    struct.pack_into(
        "<I", data, optional + 8, struct.unpack_from("<I", data, optional + 8)[0] + size
    )
    struct.pack_into("<I", data, optional + 64, 0)
    checksum = sum(memoryview(data).cast("H"))
    while checksum >> 16:
        checksum = (checksum & 65535) + (checksum >> 16)
    struct.pack_into("<I", data, optional + 64, checksum + len(data))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as f:
        f.write(data)
    record = dict(
        sdk_sha256=profile["drop"]["dll_sha256"],
        drop_profile=dict(
            tag=profile["drop"]["tag"], sha256=sha(drop_profile.dumps(profile).encode())
        ),
        output=str(output),
        sha256=sha(data),
        bytes=len(data),
        host_changes=host_changes,
        provider_name=provider_name,
        provider_name_rva=label_rva,
        replacements=records,
    )
    if omitted:
        record["omitted_host_patches"] = omitted
    with output.with_suffix(".json").open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--sdk", type=Path, required=True)
    args = p.parse_args()
    record = build(args.sdk, json.loads(args.manifest.read_text()), args.output.resolve())
    print({k: v for k, v in record.items() if k not in ["replacements", "host_changes"]})
