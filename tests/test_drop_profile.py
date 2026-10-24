# SPDX-License-Identifier: MIT
"""SDK drop profile: derivation from DLL bytes, refusals, canonical form and the RC11 repack."""

import hashlib
import json
import os
import runpy
import struct
import tempfile
import unittest
from pathlib import Path

from dll import repack_dll
from dll.port import drop_profile as dp
from dll.port import sdk_intake as intake

ROOT = Path(__file__).resolve().parents[1]
PINNED = ROOT / "dll/port/profiles/sdk-2.3.0.json"
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
RELEASE_DLL = os.environ.get("BC250_FSR4_RC11_DLL")
BASE = 0x180000000
TEXT, RDATA, DATA, RELOC = (0x1000, 0x400), (0x2000, 0x600), (0x3000, 0xC00), (0x4000, 0xE00)
ELIGIBILITY = bytes.fromhex(
    "817c243491000000440fb6c075238b5424308d4aff83f90e7613"
    "8d4ae081f9de00000076088d4af083f90f7704b001eb0232c0"
)
BARRIER = bytes.fromhex(
    "486310 85d2 0f8411223344 41f686d80b000001 0f8555667788 448b8368100000 4c8bd2 49c1e206"
    " 4c039378100000 49c1e005 4c03c3 498b12 418b4a2c 488955b0 f6c102".replace(" ", "")
)
OTHER_FLAG_TEST = bytes.fromhex("41f686d80b000001756f")
LABEL = b"4.1.1\0\0\0FSR4-i8\0"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def container(entry):
    names = b"\0" + entry.encode() + b"\0"
    names += bytes(-len(names) % 4)
    info = bytearray(52)
    struct.pack_into("<I", info, 48, 1)
    psv = struct.pack("<I", 52) + info + struct.pack("<II", 0, len(names)) + names
    parts = [(b"PSV0", psv), (b"DXIL", b"\0" * 8)]
    header = 32 + 4 * len(parts)
    offsets, body = [], b""
    for tag, payload in parts:
        offsets.append(header + len(body))
        body += tag + struct.pack("<I", len(payload)) + payload
    size = header + len(body)
    return (
        b"DXBC"
        + bytes(16)
        + struct.pack("<III", 1, size, len(parts))
        + b"".join(struct.pack("<I", o) for o in offsets)
        + body
    )


class Fixture:
    """A small signed-looking PE32+ with the three host sites, shader pairs and relocations."""

    def __init__(self, **changes):
        self.data = data = bytearray(0x1010)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", data, 0x84, 0x8664, 4)
        struct.pack_into("<H", data, 0x94, 240)
        opt = 0x98
        struct.pack_into("<H", data, opt, 0x20B)
        struct.pack_into("<Q", data, opt + 24, BASE)
        struct.pack_into("<II", data, opt + 32, 0x1000, 0x200)
        struct.pack_into("<I", data, opt + 56, 0x5000)
        struct.pack_into("<I", data, opt + 108, 16)
        sections = [
            (b".text", TEXT, 0x60000020),
            (b".rdata", RDATA, 0x40000040),
            (b".data", DATA, 0xC0000040),
            (b".reloc", RELOC, 0x42000040),
        ]
        sizes = {b".text": 0x200, b".rdata": 0x600, b".data": 0x200, b".reloc": 0x200}
        for index, (name, (rva, raw), flags) in enumerate(sections):
            start = opt + 240 + 40 * index
            data[start : start + 8] = name.ljust(8, b"\0")
            struct.pack_into("<IIII", data, start + 8, sizes[name], rva, sizes[name], raw)
            struct.pack_into("<I", data, start + 36, flags)
        # Export directory naming the upscaler DLL, Authenticode table at the end of the file.
        struct.pack_into("<II", data, opt + 112, RDATA[0], 40)
        struct.pack_into("<I", data, RDATA[1] + 12, RDATA[0] + 0x40)
        name = intake.DLL_NAME.encode() + b"\0"
        data[RDATA[1] + 0x40 : RDATA[1] + 0x40 + len(name)] = name
        struct.pack_into("<II", data, opt + 112 + 8 * 4, 0x1000, 16)
        struct.pack_into("<IHH", data, 0x1000, 16, 0x200, 2)
        # Host code: label LEA, eligibility test, buffer-UAV test, one other flag test.
        self.lea = TEXT[1] + 0x10
        self.label = RDATA[1] + 0x100
        self.put_lea(self.lea)
        self.eligibility = TEXT[1] + 0x40
        data[self.eligibility : self.eligibility + len(ELIGIBILITY)] = ELIGIBILITY
        self.barrier = TEXT[1] + 0x100 + 11
        data[TEXT[1] + 0x100 : TEXT[1] + 0x100 + len(BARRIER)] = BARRIER
        data[TEXT[1] + 0x180 : TEXT[1] + 0x180 + len(OTHER_FLAG_TEST)] = OTHER_FLAG_TEST
        data[self.label : self.label + len(LABEL)] = LABEL
        # Shaders A (two pointers), B (one) and C (unreferenced) in .rdata; pairs in .data.
        self.shaders = {}
        for at, entry in ((0x200, "entry_a"), (0x300, "entry_b"), (0x400, "entry_c")):
            blob = container(entry)
            data[RDATA[1] + at : RDATA[1] + at + len(blob)] = blob
            self.shaders[entry] = (RDATA[1] + at, blob)
        self.pointers = {"entry_a": [0x18, 0x48], "entry_b": [0x88]}
        relocated = []
        for entry, places in self.pointers.items():
            offset, blob = self.shaders[entry]
            for place in places:
                struct.pack_into("<QQ", data, DATA[1] + place - 8, len(blob), self.va(offset))
                relocated.append(DATA[0] + place)
        self.relocated = changes.get("relocated", relocated)
        for key, value in changes.items():
            if key != "relocated":
                value(self)
        self.write_relocations()

    def va(self, offset):
        for (rva, raw), size in zip((TEXT, RDATA, DATA, RELOC), (0x200, 0x600, 0x200, 0x200)):
            if raw <= offset < raw + size:
                return BASE + rva + offset - raw
        raise ValueError(offset)

    def put_lea(self, at):
        displacement = (RDATA[0] + 0x100) - (TEXT[0] + at - TEXT[1] + 7)
        self.data[at : at + 7] = b"\x48\x8d\x2d" + struct.pack("<i", displacement)

    def write_relocations(self, extra=()):
        words = [(10 << 12) | (rva - DATA[0]) for rva in self.relocated] + list(extra)
        if len(words) % 2:
            words.append(0)
        block = struct.pack("<II", DATA[0], 8 + 2 * len(words))
        block += struct.pack("<" + "H" * len(words), *words)
        self.data[RELOC[1] : RELOC[1] + 0x200] = block.ljust(0x200, b"\0")
        struct.pack_into("<II", self.data, 0x98 + 112 + 8 * 5, RELOC[0], len(block))

    def bytes(self):
        return bytes(self.data)

    def record(self, tag="v9.9.9"):
        data = self.bytes()
        return dict(tag=tag, commit="1" * 40, dll=dict(sha256=sha(data), bytes=len(data)))


class DerivationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.data = self.fixture.bytes()
        self.profile = dp.derive(self.data, self.fixture.record())

    def test_slots_patches_and_label(self):
        f, profile = self.fixture, self.profile
        shaders = profile["shaders"]
        self.assertEqual(shaders["containers"], 3)
        self.assertEqual(shaders["pointer_sections"], [".data"])
        slots = {row["entry"]: row for row in shaders["slots"]}
        self.assertEqual(set(slots), {"entry_a", "entry_b"})
        for entry, places in f.pointers.items():
            offset, blob = f.shaders[entry]
            self.assertEqual(slots[entry]["offset"], offset)
            self.assertEqual(slots[entry]["sha256"], sha(blob))
            self.assertEqual(slots[entry]["pointers"], [DATA[1] + p for p in places])
        self.assertEqual([row["entry"] for row in shaders["unreferenced"]], ["entry_c"])
        eligibility, barrier = profile["host_patches"]
        self.assertEqual(
            eligibility["edits"],
            [
                dict(offset=f.eligibility, before="817c", after="c744"),
                dict(
                    offset=f.eligibility + 8,
                    before="440fb6c075238b5424308d4aff83f90e",
                    after="c74424300100000041b801000000eb15",
                ),
            ],
        )
        self.assertEqual(barrier["edits"], [dict(offset=f.barrier + 7, before="01", after="00")])
        self.assertEqual(barrier["captures"]["flag_tests"], [f.barrier, TEXT[1] + 0x180])
        label = profile["provider_label"]
        self.assertEqual(
            (label["offset"], label["version"], label["slot_bytes"]), (f.label, "4.1.1", 8)
        )
        self.assertEqual(
            label["reference"], dict(offset=f.lea, original=f.data[f.lea : f.lea + 7].hex())
        )
        self.assertEqual(profile["pe"]["spare_section_header"], 0x98 + 240 + 4 * 40)
        self.assertEqual(dp.differences(dp.derive(self.data, f.record()), profile), [])

    def test_eligibility_replacement_follows_the_captured_layout(self):
        def move(fixture):
            block = bytearray(ELIGIBILITY)
            block[17] = 0x38  # revision slot [rsp+0x38]
            fixture.data[fixture.eligibility : fixture.eligibility + len(block)] = block

        fixture = Fixture(move=move)
        edits = dp.derive(fixture.bytes(), fixture.record())["host_patches"][0]["edits"]
        self.assertEqual(edits[1]["after"], "c74424380100000041b801000000eb15")

    def test_refuses_unknown_layouts(self):
        def twice(f):
            at = TEXT[1] + 0x1A0
            f.data[at : at + len(ELIGIBILITY)] = ELIGIBILITY

        def no_barrier(f):
            f.data[f.barrier - 11 : f.barrier - 11 + len(BARRIER)] = bytes(len(BARRIER))

        def wrong_branch(f):
            f.data[f.eligibility + 25] = 0x12

        def wrong_reject(f):
            f.data[f.eligibility + 44] = 0x05

        def short_length(f):
            struct.pack_into("<Q", f.data, DATA[1] + 0x18 - 8, 8)

        def second_reference(f):
            f.put_lea(TEXT[1] + 0x1E0)

        def absolute_reference(f):
            struct.pack_into("<Q", f.data, DATA[1] + 0x100, BASE + RDATA[0] + 0x100)
            f.relocated = [*f.relocated, DATA[0] + 0x100]

        def late_certificate(f):
            f.data.extend(bytes(8))

        def odd_relocation(f):
            f.write_relocations = lambda: Fixture.write_relocations(f, extra=[3 << 12])

        cases = {
            "found 2 times": dict(twice=twice),
            "found 0 times": dict(no_barrier=no_barrier),
            "revision ranges": dict(wrong_branch=wrong_branch),
            "reject branches differ": dict(wrong_reject=wrong_reject),
            "lacks a DIR64 relocation": dict(relocated=[DATA[0] + 0x18, DATA[0] + 0x88]),
            "lacks its shader's length": dict(short_length=short_length),
            "2 code references": dict(second_reference=second_reference),
            "absolute pointer": dict(absolute_reference=absolute_reference),
            "certificate placement": dict(late_certificate=late_certificate),
            "relocation type 3": dict(odd_relocation=odd_relocation),
        }
        for message, changes in cases.items():
            with self.subTest(message):
                fixture = Fixture(**changes)
                with self.assertRaisesRegex(dp.ProfileError, message):
                    dp.derive(fixture.bytes(), fixture.record())

    def test_refuses_a_record_for_other_bytes(self):
        record = self.fixture.record()
        record["dll"]["sha256"] = "0" * 64
        with self.assertRaises(intake.Refused):
            dp.derive(self.data, record)

    def test_survey_reports_each_component(self):
        fixture = Fixture(no_barrier=lambda f: f.data.__setitem__(f.barrier, 0x90))
        report = dp.survey(fixture.bytes(), fixture.record())
        self.assertTrue(report["shaders"].startswith("ok: 3 containers, 2 slots"))
        self.assertTrue(report["buffer-uav-barrier"].startswith("FAIL"))
        self.assertTrue(report["provider_label"].startswith("ok"))

    def test_canonical_files_and_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / "sdk-9.9.9.json"
            path.write_text(dp.dumps(self.profile))
            self.assertEqual(dp.select(sha(self.data), folder), (path, self.profile))
            path.write_text(json.dumps(self.profile, indent=1))
            with self.assertRaisesRegex(dp.ProfileError, "canonical"):
                dp.select(sha(self.data), folder)
            path.unlink()
            (folder / "sdk-1.0.0.json").write_text(dp.dumps(self.profile))
            with self.assertRaisesRegex(dp.ProfileError, "tag"):
                dp.select(sha(self.data), folder)

    def test_verify_requires_the_allowlist_and_the_reviewed_profile(self):
        allowlist = dict(drops=[self.fixture.record()])
        dp.verify(self.data, self.profile, allowlist)
        tampered = json.loads(json.dumps(self.profile))
        tampered["host_patches"][1]["edits"][0]["after"] = "03"
        with self.assertRaisesRegex(dp.ProfileError, "edits"):
            dp.verify(self.data, tampered, allowlist)
        with self.assertRaises(intake.Refused):
            dp.verify(self.data, self.profile, dict(drops=[]))
        other = json.loads(json.dumps(self.profile))
        other["drop"]["tag"] = "v9.9.8"
        with self.assertRaises(intake.Refused):
            dp.verify(self.data, other, allowlist)

    def test_verify_applies_the_intake_pe_checks(self):
        def rename(f):
            at = RDATA[1] + 0x40
            f.data[at : at + 9] = b"other.dll"

        fixture = Fixture(rename=rename)
        data = fixture.bytes()
        profile = dp.derive(data, fixture.record())
        with self.assertRaisesRegex(intake.Refused, "export name"):
            dp.verify(data, profile, dict(drops=[fixture.record()]))


class RepackTests(unittest.TestCase):
    def test_profile_driven_repack(self):
        fixture = Fixture()
        data = fixture.bytes()
        profile = dp.derive(data, fixture.record())
        allowlist = dict(drops=[fixture.record()])
        offset, blob = fixture.shaders["entry_a"]
        replacement = container("entry_a") + bytes(16)
        replacement = replacement[:24] + struct.pack("<I", len(replacement)) + replacement[28:]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sdk.dll").write_bytes(data)
            (folder / "a.dxil").write_bytes(replacement)
            manifest = dict(
                sdk_sha256=sha(data),
                provider_name="9.9.9r1",
                replacements=[
                    dict(
                        original_offset=offset,
                        original_size=len(blob),
                        original_sha256=sha(blob),
                        entry="entry_a",
                        replacement=str(folder / "a.dxil"),
                        replacement_sha256=sha(replacement),
                    )
                ],
            )
            output = folder / "out.dll"
            record = repack_dll.build(folder / "sdk.dll", manifest, output, profile, allowlist)
            result = output.read_bytes()
        self.assertEqual(record["sha256"], sha(result))
        image = dp.Image(result)
        self.assertEqual([s["name"] for s in image.sections][-1], ".bc250")
        new_va = BASE + record["replacements"][0]["new_rva"]
        for place in fixture.pointers["entry_a"]:
            self.assertEqual(
                struct.unpack_from("<QQ", result, DATA[1] + place - 8), (len(replacement), new_va)
            )
        b_offset, b_blob = fixture.shaders["entry_b"]
        self.assertEqual(
            struct.unpack_from("<QQ", result, DATA[1] + 0x88 - 8),
            (len(b_blob), fixture.va(b_offset)),
        )
        program = image.to_offset(new_va - BASE)
        self.assertEqual(result[program : program + len(replacement)], replacement)
        at = fixture.eligibility
        self.assertEqual(result[at : at + 2], b"\xc7\x44")
        self.assertEqual(result[fixture.barrier + 7], 0)
        lea = fixture.lea
        target = image.to_rva(lea + 7) + struct.unpack_from("<i", result, lea + 3)[0]
        name = image.to_offset(target)
        self.assertEqual(result[name : name + 8], b"9.9.9r1\0")
        self.assertEqual(result[fixture.label : fixture.label + len(LABEL)], LABEL)
        self.assertEqual(image.directory(4), (0, 0))
        self.assertEqual(len(result) % 0x200, 0)
        check = bytearray(result)
        struct.pack_into("<I", check, 0x98 + 64, 0)
        total = sum(memoryview(check).cast("H"))
        while total >> 16:
            total = (total & 65535) + (total >> 16)
        self.assertEqual(image.checksum, total + len(result))

    def test_repack_re_derives_the_profile(self):
        fixture = Fixture()
        data = fixture.bytes()
        profile = dp.derive(data, fixture.record())
        profile["provider_label"]["reference"]["offset"] += 1
        offset, blob = fixture.shaders["entry_a"]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sdk.dll").write_bytes(data)
            manifest = dict(
                sdk_sha256=sha(data),
                provider_name="x",
                replacements=[dict(original_offset=offset, replacement=str(folder / "a"))],
            )
            with self.assertRaisesRegex(dp.ProfileError, "reference.offset"):
                repack_dll.build(
                    folder / "sdk.dll",
                    manifest,
                    folder / "out.dll",
                    profile,
                    dict(drops=[fixture.record()]),
                )
            self.assertFalse((folder / "out.dll").exists())

    def test_repack_refuses_slots_outside_the_profile(self):
        fixture = Fixture()
        data = fixture.bytes()
        profile = dp.derive(data, fixture.record())
        offset, blob = fixture.shaders["entry_c"]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sdk.dll").write_bytes(data)
            manifest = dict(
                sdk_sha256=sha(data),
                provider_name="x",
                replacements=[
                    dict(
                        original_offset=offset,
                        original_size=len(blob),
                        original_sha256=sha(blob),
                        entry="entry_c",
                        replacement=str(folder / "missing.dxil"),
                        replacement_sha256="0" * 64,
                    )
                ],
            )
            with self.assertRaisesRegex(ValueError, "mismatched"):
                repack_dll.build(
                    folder / "sdk.dll",
                    manifest,
                    folder / "out.dll",
                    profile,
                    dict(drops=[fixture.record()]),
                )


class PinnedProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = dp.load(PINNED)
        self.manifest = json.loads((ROOT / "dll/manifest.json").read_text())

    def test_tree_check(self):
        self.assertIn("348 manifest slots match drop v2.3.0", dp.check_tree())

    def test_profile_holds_the_rc11_layout_constants(self):
        # The values dll/repack_dll.py hard-coded before the drop profile existed.
        eligibility, barrier = self.profile["host_patches"]
        self.assertEqual(
            eligibility["edits"],
            [
                dict(offset=0x8189, before="817c", after="c744"),
                dict(
                    offset=0x8191,
                    before="440fb6c075238b5424308d4aff83f90e",
                    after="c74424300100000041b801000000eb15",
                ),
            ],
        )
        self.assertEqual(barrier["site"], 0x4782)
        self.assertEqual(barrier["edits"], [dict(offset=0x4789, before="01", after="00")])
        label = self.profile["provider_label"]
        self.assertEqual(label["offset"], 0xC8300)
        self.assertEqual(bytes.fromhex(label["original"]), LABEL)
        self.assertEqual(label["reference"], dict(offset=0x4A5, original="488d2d54840c00"))
        shaders = self.profile["shaders"]
        self.assertEqual((shaders["containers"], len(shaders["slots"])), (1028, 998))
        self.assertEqual(shaders["pointer_sections"], [".data"])
        self.assertEqual(self.profile["pe"]["certificate"]["offset"], 28750336)

    def test_manifest_disagreements_are_refused(self):
        manifest = json.loads(json.dumps(self.manifest))
        manifest["replacements"][5]["original_sha256"] = "0" * 64
        with self.assertRaisesRegex(dp.ProfileError, "differs from the drop"):
            dp.check_manifest(self.profile, manifest)
        manifest = json.loads(json.dumps(self.manifest))
        manifest["sdk_synchronization"]["offset"] += 1
        with self.assertRaisesRegex(dp.ProfileError, "synchronization"):
            dp.check_manifest(self.profile, manifest)
        manifest = json.loads(json.dumps(self.manifest))
        manifest["replacements"][0]["original_offset"] = 0x15D76F0  # an unreferenced container
        with self.assertRaisesRegex(dp.ProfileError, "not a profile slot"):
            dp.check_manifest(self.profile, manifest)


@unittest.skipUnless(CACHE, "set BC250_FSR4_SDK_CACHE to a seeded SDK cache")
class PublicDropTests(unittest.TestCase):
    def setUp(self):
        self.cache = intake.Cache(CACHE)
        self.drops = {r["tag"]: r for r in intake.read_allowlist()[0]["drops"]}

    def dll(self, tag):
        return self.cache.get(self.drops[tag]["dll"]["sha256"])

    def test_v2_3_0_derives_the_pinned_profile_exactly(self):
        derived = dp.derive(self.dll("v2.3.0"), self.drops["v2.3.0"])
        self.assertEqual(dp.dumps(derived), PINNED.read_text())
        dp.verify(self.dll("v2.3.0"), dp.load(PINNED))

    def test_earlier_drops_do_not_derive(self):
        for tag in ("v2.0.0", "v2.1.0", "v2.2.0"):
            with self.subTest(tag):
                with self.assertRaises(dp.ProfileError):
                    dp.derive(self.dll(tag), self.drops[tag])
                report = dp.survey(self.dll(tag), self.drops[tag])
                self.assertTrue(report["shaders"].startswith("ok"), report)
                self.assertTrue(report["int8-eligibility"].startswith("FAIL"), report)

    @unittest.skipUnless(RELEASE_DLL, "set BC250_FSR4_RC11_DLL to the RC11 release DLL")
    def test_profile_driven_repack_reproduces_rc11(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory) / "sdk.dll"
            sdk.write_bytes(self.dll("v2.3.0"))
            check = runpy.run_path(str(ROOT / "scripts/check-drop-profile.py"))["check"]
            result = check(sdk, from_dll=Path(RELEASE_DLL))
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        self.assertTrue(result["reproduced"])
        self.assertEqual(result["sha256"], manifest["expected_dll_sha256"])


if __name__ == "__main__":
    unittest.main()
