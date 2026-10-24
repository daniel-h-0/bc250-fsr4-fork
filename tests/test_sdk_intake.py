# SPDX-License-Identifier: MIT
"""SDK drop intake: allowlist format, license gate, PE checks, cache, fetch and ingest."""

import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dll.port import sdk_intake as intake

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "dll/notices/AMD-SDK-LICENSE.md"
FIXTURES = ROOT / "tests/fixtures"
CACHE = os.environ.get("BC250_FSR4_SDK_CACHE")
COMMIT = "1" * 40
OTHER_COMMIT = "2" * 40
DLL_LINE = "- `Kits\\FidelityFX\\signedbin\\amd_fidelityfx_upscaler_dx12.dll`"
MIT = (
    "Copyright (C) Advanced Micro Devices, Inc.\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this "
    "software and associated documentation files (the “Software”), to deal in the "
    "Software without restriction, including without limitation the rights to use, copy, "
    "modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to "
    "permit persons to whom the Software is furnished to do so, subject to the following "
    "conditions:\n\n"
    "The above copyright notice and this permission notice shall be included in all copies or "
    "substantial portions of the Software.\n\n"
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.\n'
)


def list_license(entries=(DLL_LINE,), preamble=intake.LIST_PREAMBLE, terms=MIT, before=""):
    body = "\n".join(entries)
    return (
        f"{preamble}\n\nCopyright (C) Advanced Micro Devices, Inc.\n\n"
        "No reverse engineering, decompilation, or disassembly of this Software is permitted.\n"
        f"{before}\nThese source files \n\n- `docs\\license.md`\n{body}\n\n"
        f"are subject to the following license:   \n\n{terms}"
    ).encode()


def fake_dll(name=intake.DLL_NAME, machine=0x8664, signed=True, padding=b""):
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 112, 0x1000, 40)
    table = optional + 240
    data[table : table + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, table + 8, 0x200, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", data, 0x200 + 12, 0x1040)
    encoded = name.encode() + b"\0"
    data[0x240 : 0x240 + len(encoded)] = encoded
    data += padding
    if signed:
        struct.pack_into("<II", data, optional + 144, len(data), 16)
        data += struct.pack("<IHH", 16, 0x200, 2) + b"\x30" * 8
    return bytes(data)


def write_allowlist(folder, records):
    path = Path(folder) / "allowlist.json"
    document = {"schema": 1, "repository": intake.REPOSITORY, "drops": records}
    path.write_text(json.dumps(document))
    return path


class Network:
    """Stand-in for GPUOpen: serves files by commit and path and records every request."""

    def __init__(self, files, commit=COMMIT):
        self.files = {intake.raw_url(commit, path): data for path, data in files.items()}
        self.requests = []

    def __call__(self, url, commit, limit):
        intake.check_url(url, commit)
        self.requests.append(url)
        data = self.files[url]
        if len(data) > limit:
            raise intake.Refused("Download exceeds its expected size")
        return data


class GitHub:
    """Stand-in for GitHub's contents API: blob ids and sizes by commit and path."""

    def __init__(self, files, commit=COMMIT):
        self.blobs = {
            (commit, path): {"git_blob": intake.identity(data)["git_blob"], "bytes": len(data)}
            for path, data in files.items()
        }
        self.requests = []

    def __call__(self, commit, path):
        self.requests.append((commit, path))
        if (commit, path) not in self.blobs:
            raise intake.Refused(f"GitHub lists no {path} at {commit}")
        return dict(self.blobs[(commit, path)])


class Response(io.BytesIO):
    def __init__(self, data, final):
        super().__init__(data)
        self.final = final

    def geturl(self):
        return self.final


class LicenseGrantTests(unittest.TestCase):
    def test_mit_list_names_the_dll_on_its_line(self):
        data = list_license(("- `Kits\\other.dll`", DLL_LINE))
        grant = intake.license_grant(data)
        self.assertEqual(grant["grant"], "mit-list")
        self.assertEqual(data.decode().split("\n")[grant["line_number"] - 1], DLL_LINE)
        self.assertEqual(grant["line"], DLL_LINE)

    def test_sibling_files_of_the_dll_do_not_count_as_mentions(self):
        siblings = [DLL_LINE.replace(".dll", suffix) for suffix in (".lib", ".pdb")]
        grant = intake.license_grant(list_license((siblings[0], DLL_LINE, siblings[1])))
        self.assertEqual((grant["grant"], grant["line"]), ("mit-list", DLL_LINE))

    def test_shipped_sdk_notice_lists_the_dll_under_mit(self):
        grant = intake.license_grant(NOTICE.read_bytes())
        self.assertEqual(grant, {"grant": "mit-list", "line_number": 586, "line": DLL_LINE})

    def test_whole_file_mit_with_irregular_spacing(self):
        text = MIT.replace("files (the", "files(the").replace("and/or", "and /or")
        text = text.replace(" in the Software", "\nin the Software")
        grant = intake.license_grant(("# License\n\n" + text).encode())
        self.assertEqual(grant["grant"], "whole-file-mit")
        self.assertEqual(grant["line_number"], 5)
        self.assertTrue(grant["line"].startswith(intake.PERMISSION))

    def test_refuses_licenses_that_do_not_put_the_dll_under_mit(self):
        notice = NOTICE.read_text()
        cases = {
            "not listed": list_license(("- `Kits\\other.dll`",)),
            "listed twice": list_license((DLL_LINE, DLL_LINE)),
            "named before the list": list_license(("- `Kits\\other.dll`",), before=DLL_LINE),
            "prose inside the list": list_license((DLL_LINE, "and the files below")),
            "other default scope": list_license(preamble="This license applies to all files."),
            "non-MIT terms": list_license(terms="All rights reserved.\n"),
            "restricted MIT": list_license(terms=MIT + "No disassembly is permitted.\n"),
            "second list": notice.replace(
                "are subject to the following license:",
                "are subject to the following license:\n\nThese source files\n",
                1,
            ).encode(),
            "shipped notice without the DLL": notice.replace(DLL_LINE + "\n", "").encode(),
            "restricted whole file": (MIT + "No reverse engineering is permitted.\n").encode(),
            "hyphenated restriction": (MIT + "You may not reverse-engineer it.\n").encode(),
            "soft hyphen": (MIT + "You may not dis\u00adassemble it.\n").encode(),
            "zero-width space": (MIT + "No reverse\u200bengineering.\n").encode(),
            "fullwidth letters": (MIT + "No \uff24ecompilation.\n").encode(),
            "hyphenated restriction after the list": list_license(
                terms=MIT + "Reverse-engineering is prohibited.\n"
            ),
            "DLL named in the default terms": list_license(
                before="Notwithstanding the list below, amd_fidelityfx_upscaler_dx12.dll stays "
                "under the terms above.\n"
            ),
            "DLL named after the list": list_license(
                terms=MIT + "This does not cover amd_fidelityfx_upscaler_dx12.dll.\n"
            ),
            "whole file naming the DLL": (
                MIT + "AMD_FIDELITYFX_UPSCALER_DX12.DLL is excluded from this license.\n"
            ).encode(),
            "other license": b"Apache License 2.0\n",
            "not UTF-8": b"\xff\xfe" + MIT.encode("utf-16-le"),
            "unsafe path": list_license(("- `..\\Kits\\FidelityFX\\x.dll`",)),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(intake.Refused):
                intake.license_grant(data)


class PeIdentityTests(unittest.TestCase):
    def test_signed_upscaler_image(self):
        self.assertEqual(
            intake.pe_identity(fake_dll()),
            {
                "machine": "x86-64",
                "format": "PE32+",
                "export_name": intake.DLL_NAME,
                "certificate_table_bytes": 16,
            },
        )

    def test_refuses_other_images(self):
        cases = {
            "driver export name": fake_dll(name="amdxcffx64.dll"),
            "unsigned": fake_dll(signed=False),
            "certificate past the end": fake_dll()[:-4],
            "x86": fake_dll(machine=0x14C),
            "truncated": fake_dll()[:0x150],
            "text": b"MZ but not a PE",
            "not PE": b"\x7fELF" + bytes(60),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(intake.Refused):
                intake.pe_identity(data)


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self.allowlist = intake.read_allowlist()[0]
        self.drops = {record["tag"]: record for record in self.allowlist["drops"]}

    def test_public_tags_and_their_license_lines(self):
        self.assertEqual(list(self.drops), ["v2.0.0", "v2.1.0", "v2.1.1", "v2.2.0", "v2.3.0"])
        lines = {
            tag: (r["license"]["grant"], r["license"]["line_number"])
            for tag, r in self.drops.items()
        }
        self.assertEqual(
            lines,
            {
                "v2.0.0": ("whole-file-mit", 7),
                "v2.1.0": ("mit-list", 593),
                "v2.1.1": ("mit-list", 593),
                "v2.2.0": ("mit-list", 586),
                "v2.3.0": ("mit-list", 586),
            },
        )
        self.assertEqual(self.drops["v2.1.0"]["dll"], self.drops["v2.1.1"]["dll"])

    def test_rc11_input_and_notice_are_the_allowlisted_v2_3_0_drop(self):
        manifest = json.loads((ROOT / "dll/manifest.json").read_text())
        record = self.drops["v2.3.0"]
        self.assertEqual(record["dll"]["sha256"], manifest["sdk_sha256"])
        self.assertEqual(intake.raw_url(record["commit"], intake.DLL_PATH), manifest["sdk_url"])
        self.assertEqual(
            intake.identity(NOTICE.read_bytes()),
            {key: record["license"][key] for key in ("sha256", "git_blob", "bytes")},
        )
        self.assertIn("RC11 input and notice match v2.3.0 line 586", intake.check_tree())

    def test_refuses_malformed_records(self):
        record = self.drops["v2.3.0"]

        def changed(edit):
            copy = json.loads(json.dumps(record))
            edit(copy)
            return copy

        cases = {
            "driver DLL path": changed(
                lambda r: r["dll"].update(path="Kits/FidelityFX/signedbin/amdxcffx64.dll")
            ),
            "other license file": changed(lambda r: r["license"].update(path="LICENSE.txt")),
            "short hash": changed(lambda r: r["dll"].update(sha256="d0dcccc7")),
            "tag": changed(lambda r: r.update(tag="latest")),
            "commit": changed(lambda r: r.update(commit="v2.3.0")),
            "line names other file": changed(
                lambda r: r["license"].update(line="- `Kits\\FidelityFX\\signedbin\\x.dll`")
            ),
            "unknown grant": changed(lambda r: r["license"].update(grant="permissive")),
            "bool line number": changed(lambda r: r["license"].update(line_number=True)),
            "extra field": changed(lambda r: r.update(url="https://example.com/x.dll")),
        }
        for name, bad in cases.items():
            with self.subTest(name), self.assertRaises(intake.Refused):
                intake.check_record(bad)
        for name, document in {
            "other repository": {**self.allowlist, "repository": "someone/FidelityFX-SDK"},
            "duplicate tag": {**self.allowlist, "drops": [record, record]},
            "empty": {**self.allowlist, "drops": []},
        }.items():
            with self.subTest(name), self.assertRaises(intake.Refused):
                intake.check_allowlist(document)


class UrlAndTagTests(unittest.TestCase):
    def test_urls_stay_on_gpuopen_at_the_pinned_commit(self):
        url = intake.raw_url(COMMIT, intake.DLL_PATH)
        intake.check_url(url, COMMIT)
        for bad in (
            url.replace("https:", "http:"),
            url.replace(intake.RAW_HOST, "example.com"),
            url.replace("GPUOpen-LibrariesAndSDKs", "someone"),
            url.replace(COMMIT, OTHER_COMMIT),
            url + "?token=1",
        ):
            with self.subTest(bad), self.assertRaises(intake.Refused):
                intake.check_url(bad, COMMIT)
        for path in ("Kits/FidelityFX/signedbin/amdxcffx64.dll", "README.md"):
            with self.subTest(path), self.assertRaises(intake.Refused):
                intake.raw_url(COMMIT, path)
        with self.assertRaises(intake.Refused):
            intake.raw_url("v2.3.0", intake.DLL_PATH)

    def test_fetch_checks_redirect_target_and_size(self):
        url = intake.raw_url(COMMIT, intake.LICENSE_PATH)
        with patch("urllib.request.urlopen", return_value=Response(b"ok", url)):
            self.assertEqual(intake.fetch(url, COMMIT, 2), b"ok")
        with patch("urllib.request.urlopen", return_value=Response(b"too long", url)):
            with self.assertRaises(intake.Refused):
                intake.fetch(url, COMMIT, 2)
        moved = Response(b"ok", "https://example.com/amd_fidelityfx_upscaler_dx12.dll")
        with patch("urllib.request.urlopen", return_value=moved):
            with self.assertRaises(intake.Refused):
                intake.fetch(url, COMMIT, 2)

    def test_tag_resolution_uses_peeled_commit(self):
        def remote(stdout):
            return subprocess.CompletedProcess([], 0, stdout, "")

        lightweight = f"{COMMIT}\trefs/tags/v2.3.0\n"
        annotated = lightweight + f"{OTHER_COMMIT}\trefs/tags/v2.3.0^{{}}\n"
        with patch("subprocess.run", return_value=remote(lightweight)) as run:
            self.assertEqual(intake.resolve_tag("v2.3.0"), COMMIT)
        self.assertIn(intake.REMOTE, run.call_args.args[0])
        with patch("subprocess.run", return_value=remote(annotated)):
            self.assertEqual(intake.resolve_tag("v2.3.0"), OTHER_COMMIT)
        with patch("subprocess.run", return_value=remote("")):
            with self.assertRaises(intake.Refused):
                intake.resolve_tag("v2.3.0")
        with patch("subprocess.run") as run:
            for tag in ("main", "v2.3.0;rm", "--upload-pack=x"):
                with self.subTest(tag), self.assertRaises(intake.Refused):
                    intake.resolve_tag(tag)
            run.assert_not_called()


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.dll = fake_dll(padding=b"model" * 10)
        self.license = list_license()
        self.record = intake.make_record("v9.9.0", COMMIT, self.dll, self.license)
        self.allowlist = write_allowlist(self.folder, [self.record])
        self.cache = self.folder / "cache"
        self.network = Network({intake.DLL_PATH: self.dll, intake.LICENSE_PATH: self.license})
        self.github = GitHub({intake.DLL_PATH: self.dll, intake.LICENSE_PATH: self.license})

    def tearDown(self):
        self.temp.cleanup()

    def run_ingest(self, output="out", tag="v9.9.0", resolved=COMMIT, **options):
        return intake.ingest(
            tag,
            self.cache,
            self.folder / output,
            fetcher=options.pop("fetcher", self.network),
            resolver=lambda _: resolved,
            blob_lookup=options.pop("blob_lookup", self.github),
            allowlist_path=self.allowlist,
            **options,
        )

    def test_fetches_once_then_reads_the_cache(self):
        record, sources = self.run_ingest()
        self.assertEqual(sources, {"dll": "network", "license": "network"})
        self.assertEqual(
            self.network.requests,
            [intake.raw_url(COMMIT, intake.DLL_PATH), intake.raw_url(COMMIT, intake.LICENSE_PATH)],
        )
        drop = self.folder / "out/v9.9.0"
        self.assertEqual((drop / intake.DLL_NAME).read_bytes(), self.dll)
        self.assertEqual((drop / intake.LICENSE_NAME).read_bytes(), self.license)
        written = json.loads((drop / "intake.json").read_text())
        self.assertEqual(written, record)
        self.assertEqual(written["record"], self.record)
        self.assertTrue(written["tag_verified_online"])
        self.assertEqual(written["license_grant"]["line"], DLL_LINE)
        self.assertEqual(written["allowlist_sha256"], intake.sha256(self.allowlist.read_bytes()))

        def offline_network(*_):
            raise AssertionError("network used with a complete cache")

        _, sources = self.run_ingest("again", fetcher=offline_network)
        self.assertEqual(sources, {"dll": "cache", "license": "cache"})
        _, sources = self.run_ingest("offline", fetcher=offline_network, offline=True)
        self.assertFalse(
            json.loads((self.folder / "offline/v9.9.0/intake.json").read_text())[
                "tag_verified_online"
            ]
        )

    def test_online_ingest_checks_github_blobs_first_and_offline_does_not(self):
        written, _ = self.run_ingest()
        self.assertTrue(written["blobs_verified_online"])
        self.assertEqual(
            self.github.requests, [(COMMIT, intake.DLL_PATH), (COMMIT, intake.LICENSE_PATH)]
        )

        def no_github(*_):
            raise AssertionError("GitHub queried offline")

        written, _ = self.run_ingest("offline", offline=True, blob_lookup=no_github)
        self.assertFalse(written["blobs_verified_online"])

    def test_refuses_a_record_whose_blob_or_size_github_does_not_list(self):
        for key, field, value in (
            ("dll", "git_blob", "f" * 40),
            ("license", "git_blob", "e" * 40),
            ("dll", "bytes", len(self.dll) + 1),
        ):
            with self.subTest(key=key, field=field):
                github = GitHub({intake.DLL_PATH: self.dll, intake.LICENSE_PATH: self.license})
                github.blobs[(COMMIT, self.record[key]["path"])][field] = value
                self.network.requests.clear()
                with self.assertRaisesRegex(intake.Refused, "GitHub's " + field):
                    self.run_ingest(blob_lookup=github)
                self.assertEqual(self.network.requests, [])
                self.assertFalse((self.folder / "out").exists())
                self.assertFalse((self.cache / "sha256").exists())

    def test_cache_hits_are_checked_against_github_too(self):
        self.run_ingest()

        def offline_network(*_):
            raise AssertionError("network used with a complete cache")

        moved = GitHub({intake.DLL_PATH: self.dll + b"x", intake.LICENSE_PATH: self.license})
        with self.assertRaisesRegex(intake.Refused, "git_blob"):
            self.run_ingest("again", fetcher=offline_network, blob_lookup=moved)
        self.assertFalse((self.folder / "again").exists())
        _, sources = self.run_ingest("again", fetcher=offline_network)
        self.assertEqual(sources, {"dll": "cache", "license": "cache"})

    def test_refuses_a_file_github_does_not_list(self):
        github = GitHub({intake.LICENSE_PATH: self.license})
        with self.assertRaisesRegex(intake.Refused, "lists no"):
            self.run_ingest(blob_lookup=github)
        self.assertEqual(self.network.requests, [])

    def assert_refused_without_output(self, **options):
        with self.assertRaises(intake.Refused):
            self.run_ingest(**options)
        self.assertFalse((self.folder / "out").exists() and any((self.folder / "out").iterdir()))

    def test_refuses_unlisted_tags_before_any_request(self):
        self.assert_refused_without_output(tag="v9.9.1")
        self.assertEqual(self.network.requests, [])

    def test_refuses_a_moved_tag(self):
        self.assert_refused_without_output(resolved=OTHER_COMMIT)
        self.assertEqual(self.network.requests, [])

    def test_refuses_other_bytes_under_the_upscaler_name(self):
        renamed = fake_dll(padding=b"driver" * 10)
        network = Network({intake.DLL_PATH: renamed, intake.LICENSE_PATH: self.license})
        self.assert_refused_without_output(fetcher=network)
        self.assertFalse((self.cache / "sha256" / intake.sha256(renamed)).exists())

    def test_refuses_a_changed_license(self):
        changed = list_license(("- `Kits\\other.dll`",))
        record = intake.make_record("v9.9.0", COMMIT, self.dll, self.license)
        record["license"].update(intake.identity(changed))
        write_allowlist(self.folder, [record])
        network = Network({intake.DLL_PATH: self.dll, intake.LICENSE_PATH: changed})
        self.assert_refused_without_output(fetcher=network)

    def test_refuses_missing_or_corrupt_cache_objects_offline(self):
        self.assert_refused_without_output(offline=True)
        self.run_ingest()
        cached = self.cache / "sha256" / self.record["dll"]["sha256"]
        cached.chmod(0o644)
        cached.write_bytes(self.dll[:-1] + b"\0")
        with self.assertRaises(intake.Refused):
            self.run_ingest("corrupt", offline=True)
        cache = intake.Cache(self.cache)
        for operation in (
            lambda: cache.get(self.record["dll"]["sha256"]),
            lambda: cache.put(self.dll),
        ):
            with self.assertRaisesRegex(intake.Refused, "Corrupt cache object"):
                operation()

    def test_refuses_existing_output_and_source_tree_locations(self):
        self.run_ingest()
        with self.assertRaises(FileExistsError):
            self.run_ingest()
        for cache, output in ((ROOT / ".work/sdk-cache", "x"), (self.cache, ROOT / ".work/drops")):
            with self.subTest(str(cache)):
                with self.assertRaisesRegex(ValueError, "outside the source tree"):
                    intake.ingest(
                        "v9.9.0",
                        cache,
                        self.folder / output,
                        resolver=lambda _: COMMIT,
                        fetcher=self.network,
                        allowlist_path=self.allowlist,
                    )
        self.assertFalse((ROOT / ".work/drops").exists())

    def test_gate_accepts_allowlisted_bytes_only(self):
        good = self.folder / "renamed.bin"
        good.write_bytes(self.dll)
        allowlist = intake.read_allowlist(self.allowlist)[0]
        self.assertEqual(intake.require_allowlisted(good, allowlist), [self.record])
        impostor = self.folder / intake.DLL_NAME
        impostor.write_bytes(fake_dll(padding=b"driver" * 10))
        link = self.folder / "link.dll"
        link.symlink_to(good)
        for path in (impostor, link):
            with self.subTest(path.name), self.assertRaises(intake.Refused):
                intake.require_allowlisted(path, allowlist)

    def test_seed_accepts_only_allowlisted_hashes(self):
        good = self.folder / "license.md"
        good.write_bytes(self.license)
        other = self.folder / intake.DLL_NAME
        other.write_bytes(fake_dll(padding=b"x"))
        with self.assertRaises(intake.Refused):
            intake.seed(self.cache, [good, other], self.allowlist)
        self.assertFalse((self.cache / "sha256").exists())
        seeded = intake.seed(self.cache, [good], self.allowlist)
        self.assertEqual(
            seeded, [(str(good), self.record["license"]["sha256"], ["v9.9.0 license"])]
        )

    def test_propose_drafts_the_record_and_checks_the_license_first(self):
        record = intake.propose(
            "v9.9.0", self.cache, fetcher=self.network, resolver=lambda _: COMMIT
        )
        self.assertEqual(record, self.record)
        self.assertEqual(self.network.requests[0], intake.raw_url(COMMIT, intake.LICENSE_PATH))
        network = Network(
            {intake.DLL_PATH: self.dll, intake.LICENSE_PATH: list_license(("- `x.dll`",))},
            OTHER_COMMIT,
        )
        with self.assertRaises(intake.Refused):
            intake.propose(
                "v9.9.1",
                self.folder / "cache2",
                fetcher=network,
                resolver=lambda _: OTHER_COMMIT,
            )
        self.assertEqual(network.requests, [intake.raw_url(OTHER_COMMIT, intake.LICENSE_PATH)])
        self.assertFalse((self.folder / "cache2/sha256").exists())

    def test_command_line_exit_codes(self):
        dll = self.folder / "x.dll"
        dll.write_bytes(self.dll)
        with patch("sys.stderr", io.StringIO()) as stderr, patch("sys.stdout", io.StringIO()):
            self.assertEqual(intake.main(["gate", str(dll)]), 2)
            self.assertIn("REFUSED", stderr.getvalue())
            self.assertEqual(intake.main(["check"]), 0)


class GitHubListingTests(unittest.TestCase):
    """The contents-API lookup: URL, redirect, size and listing checks, on GitHub's own answers."""

    V230 = "60f4ea81909200d8542eca14dccb2628b763a9a3"

    def listing(self, name):
        return (FIXTURES / f"github-contents-v2.3.0-{name}.json").read_bytes()

    def test_recorded_github_listings_give_the_allowlisted_blobs(self):
        record = intake.lookup(intake.read_allowlist()[0], "v2.3.0")
        self.assertEqual(record["commit"], self.V230)
        for name, key in (("signedbin", "dll"), ("docs", "license")):
            with self.subTest(name):
                found = intake.listed_blob(self.listing(name), record[key]["path"])
                self.assertEqual(
                    found, {"git_blob": record[key]["git_blob"], "bytes": record[key]["bytes"]}
                )
        intake.verify_remote_blobs(
            record,
            lambda commit, path: intake.listed_blob(
                self.listing("signedbin" if path == intake.DLL_PATH else "docs"), path
            ),
        )

    def test_lookup_requests_only_the_parent_listing_at_the_commit(self):
        url = intake.listing_url(self.V230, intake.DLL_PATH)
        self.assertEqual(
            url,
            f"https://api.github.com/repos/{intake.REPOSITORY}/contents/Kits/FidelityFX/"
            f"signedbin?ref={self.V230}",
        )
        with patch(
            "urllib.request.urlopen", return_value=Response(self.listing("signedbin"), url)
        ) as opened:
            blob = intake.remote_blob(self.V230, intake.DLL_PATH)
        self.assertEqual(opened.call_args.args[0].full_url, url)
        self.assertEqual(blob["bytes"], 28761864)
        for path in ("Kits/FidelityFX/signedbin/amdxcffx64.dll", "README.md"):
            with self.subTest(path), self.assertRaises(intake.Refused):
                intake.listing_url(self.V230, path)
        with self.assertRaises(intake.Refused):
            intake.listing_url("v2.3.0", intake.DLL_PATH)

    def test_refuses_redirects_oversize_and_malformed_listings(self):
        url = intake.listing_url(COMMIT, intake.LICENSE_PATH)
        for final in (
            url.replace("api.github.com", "example.com"),
            url.replace(COMMIT, OTHER_COMMIT),
            url + "&per_page=1",
            url.replace("/docs?", "/Kits?"),
        ):
            with (
                self.subTest(final),
                patch("urllib.request.urlopen", return_value=Response(b"[]", final)),
            ):
                with self.assertRaisesRegex(intake.Refused, "GitHub API URL"):
                    intake.remote_blob(COMMIT, intake.LICENSE_PATH)
        big = b" " * (intake.MAX_LISTING + 1)
        with patch("urllib.request.urlopen", return_value=Response(big, url)):
            with self.assertRaisesRegex(intake.Refused, "size"):
                intake.remote_blob(COMMIT, intake.LICENSE_PATH)
        entry = json.loads(self.listing("docs"))[1]
        self.assertEqual(entry["path"], intake.LICENSE_PATH)
        for data in (
            b"not json",
            json.dumps(entry).encode(),
            json.dumps([]).encode(),
            json.dumps([entry, entry]).encode(),
            json.dumps([dict(entry, type="dir")]).encode(),
            json.dumps([dict(entry, type="symlink")]).encode(),
            json.dumps([dict(entry, sha="v2.3.0")]).encode(),
            json.dumps([dict(entry, size="60891")]).encode(),
            json.dumps([dict(entry, size=0)]).encode(),
        ):
            with self.subTest(data[:40]), self.assertRaises(intake.Refused):
                intake.listed_blob(data, intake.LICENSE_PATH)


@unittest.skipUnless(CACHE, "set BC250_FSR4_SDK_CACHE to a seeded SDK drop cache")
class PublicDropTests(unittest.TestCase):
    """Real GPUOpen v2.0.0..v2.3.0 files from a local cache, without network access."""

    def test_every_public_drop_verifies_and_rederives_its_record(self):
        allowlist = intake.read_allowlist()[0]
        self.assertIn("every cached drop re-verified", intake.check_tree(CACHE))
        cache = intake.Cache(CACHE)
        for record in allowlist["drops"]:
            with self.subTest(record["tag"]):
                dll = cache.get(record["dll"]["sha256"])
                license = cache.get(record["license"]["sha256"])
                self.assertEqual(
                    intake.make_record(record["tag"], record["commit"], dll, license), record
                )

    def test_offline_ingest_of_every_public_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            for record in intake.read_allowlist()[0]["drops"]:
                written, sources = intake.ingest(record["tag"], CACHE, directory, offline=True)
                self.assertEqual(sources, {"dll": "cache", "license": "cache"})
                drop = Path(directory) / record["tag"]
                self.assertEqual(
                    intake.require_allowlisted(drop / intake.DLL_NAME)[0]["dll"], record["dll"]
                )
                self.assertEqual(written["pe"]["export_name"], intake.DLL_NAME)


if __name__ == "__main__":
    unittest.main()
