# SPDX-License-Identifier: MIT
"""Embed validated replacements in a single SDK DLL; never install it."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SDK_SHA = "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n, alignment):
    return (n + alignment - 1) // alignment * alignment


def build(sdk, manifest, output):
    if sys.byteorder != "little":
        raise ValueError("The reproducible DLL builder requires a little-endian host")
    for path in (output, output.with_suffix(".json")):
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
    if len(manifest["replacements"]) != 348:
        raise ValueError("The portable DLL requires all 348 shader replacements")
    original = sdk.read_bytes()
    if sha(original) != SDK_SHA or manifest["sdk_sha256"] != SDK_SHA:
        raise ValueError("Unrecognized SDK")
    data = bytearray(original)
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 4)[0] != 0x8664:
        raise ValueError("The pinned SDK is not an x86-64 PE image")
    optional = pe + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("The pinned SDK is not PE32+")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    table = optional + struct.unpack_from("<H", data, pe + 20)[0]
    image_base = struct.unpack_from("<Q", data, optional + 24)[0]
    section_alignment, file_alignment = struct.unpack_from("<II", data, optional + 32)
    sections = []
    for i in range(count):
        start = table + i * 40
        vsize, rva, size, offset = struct.unpack_from("<IIII", data, start + 8)
        sections.append(
            dict(
                name=bytes(data[start : start + 8]).rstrip(b"\0").decode(),
                vsize=vsize,
                rva=rva,
                size=size,
                offset=offset,
            )
        )

    def to_rva(offset):
        for section in sections:
            if section["offset"] <= offset < section["offset"] + section["size"]:
                return section["rva"] + offset - section["offset"]
        raise ValueError("Offset outside section")

    def to_offset(rva):
        for section in sections:
            if section["rva"] <= rva < section["rva"] + section["size"]:
                return section["offset"] + rva - section["rva"]
        raise ValueError("RVA outside section")

    # Preserve the previously audited INT8 eligibility repair exactly.
    host_changes = []
    for at, old, new in [
        (0x8189, "817c", "c744"),
        (0x8191, "440fb6c075238b5424308d4aff83f90e", "c74424300100000041b801000000eb15"),
    ]:
        before, after = bytes.fromhex(old), bytes.fromhex(new)
        if len(before) != len(after) or data[at : at + len(before)] != before:
            raise ValueError("Unexpected INT8 eligibility instruction bytes")
        data[at : at + len(after)] = after
        host_changes.append(dict(offset=at, before=old, after=new))
    # SDK 2.3.0 permits padding-clear compute jobs to skip UAV barriers.
    # Those clears can overlap the preceding model dispatch on this path.
    # Keep the existing buffer-UAV addBarrier call active for every non-null
    # buffer binding. The texture/SRV policy and all shader math stay intact.
    if data[0x4782:0x478A] != bytes.fromhex("41f686d80b000001"):
        raise ValueError("Unexpected SDK buffer-UAV skip-barrier instruction")
    data[0x4789] = 0
    host_changes.append(dict(offset=0x4789, before="01", after="00"))
    # Match the reference's bounded display-label convention. Numeric FFX
    # provider/API versions and the adjacent FSR4-i8 watermark stay unchanged.
    label_offset = 0xC8300
    if data[label_offset : label_offset + 8] != b"4.1.1\0\0\0":
        raise ValueError("The SDK provider label differs from the pinned layout")
    data[label_offset : label_offset + 8] = b"4.1.1r8\0"
    cert, size = struct.unpack_from("<II", data, optional + 112 + 4 * 8)
    if cert < max(s["offset"] + s["size"] for s in sections) or cert + size != len(data):
        raise ValueError("Unexpected SDK certificate placement")
    data = data[:cert]
    struct.pack_into("<II", data, optional + 112 + 4 * 8, 0, 0)
    reloc_rva, reloc_size = struct.unpack_from("<II", data, optional + 112 + 5 * 8)
    pos = to_offset(reloc_rva)
    end = pos + reloc_size
    relocations = set()
    while pos < end:
        page, size = struct.unpack_from("<II", data, pos)
        if not 8 <= size <= end - pos or size % 2:
            raise ValueError("Invalid SDK relocation block")
        for word in struct.unpack_from("<" + "H" * ((size - 8) // 2), data, pos + 8):
            if word >> 12 == 10:
                relocations.add(page + (word & 4095))
        pos += size
    raw = align(len(data), file_alignment)
    va = align(max(s["rva"] + s["vsize"] for s in sections), section_alignment)
    header = table + count * 40
    if header + 40 > min(s["offset"] for s in sections) or any(data[header : header + 40]):
        raise ValueError("No empty PE section-header slot")
    appended, records, offsets = bytearray(), [], set()
    for row in manifest["replacements"]:
        offset, size = row["original_offset"], row["original_size"]
        if offset in offsets or sha(original[offset : offset + size]) != row["original_sha256"]:
            raise ValueError("Duplicate/mismatched shader")
        offsets.add(offset)
        shader = Path(row["replacement"]).read_bytes()
        if (
            sha(shader) != row["replacement_sha256"]
            or shader[:4] != b"DXBC"
            or struct.unpack_from("<I", shader, 24)[0] != len(shader)
        ):
            raise ValueError("Replacement hash/container mismatch")
        pointers = []
        for section in sections:
            if section["name"] != ".data":
                continue
            for ptr in range(section["offset"] + 8, section["offset"] + section["size"] - 7, 8):
                length, pointer = struct.unpack_from("<QQ", original, ptr - 8)
                if length == size and pointer == image_base + to_rva(offset):
                    if to_rva(ptr) not in relocations:
                        raise ValueError("Shader pointer lacks DIR64 relocation")
                    pointers.append(ptr)
        if not pointers:
            raise ValueError("Shader pointer missing")
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
    if not records:
        raise ValueError("Empty replacement set")
    size = align(len(appended), file_alignment)
    data.extend(bytes(raw - len(data)))
    data.extend(appended)
    data.extend(bytes(size - len(appended)))
    struct.pack_into(
        "<8sIIIIIIHHI",
        data,
        header,
        b".bc250\0\0",
        len(appended),
        va,
        size,
        raw,
        0,
        0,
        0,
        0,
        0x40000040,
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
        sdk_sha256=SDK_SHA,
        output=str(output),
        sha256=sha(data),
        bytes=len(data),
        host_changes=host_changes,
        provider_name="4.1.1r8",
        replacements=records,
    )
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
