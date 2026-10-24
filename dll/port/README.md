# SDK drop intake

Porting the DLL to a new FidelityFX SDK release starts here. [sdk_intake.py](sdk_intake.py)
accepts exactly two files from a public
[GPUOpen FidelityFX SDK](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK) tag:
the signed `Kits/FidelityFX/signedbin/amd_fidelityfx_upscaler_dx12.dll` and `docs/license.md`.

## License gate

From SDK 2.1.0, files not on the license's MIT list fall under terms that forbid reverse
engineering, decompilation and disassembly. Only a DLL that the license puts under MIT may
be inspected or disassembled by later port stages.

The gate is an allowlist, not a name check. [allowlist.json](allowlist.json) records each
accepted drop, and a file is accepted only if its bytes match a record:

| Field | Content |
| --- | --- |
| `tag`, `commit` | GPUOpen tag and the commit it resolved to when the record was made |
| `dll.path`, `license.path` | Fixed: the upscaler DLL and `docs/license.md` |
| `sha256`, `git_blob`, `bytes` | Identity of each file; `git_blob` equals `git ls-tree <commit> <path>` |
| `license.grant` | `mit-list`: the license names the DLL in its MIT file list. `whole-file-mit`: the whole license is MIT with no exceptions (v2.0.0) |
| `license.line_number`, `license.line` | The exact license line: the DLL's list entry, or the MIT permission line |

Every check re-derives the license line from the license bytes. A `mit-list` license must
apply its default terms "except as noted below", contain one `These source files` …
`are subject to the following license:` list made only of path entries, name the DLL once
(and nowhere else in the text), and be followed by MIT terms with no further restriction. A
`whole-file-mit` license must not name the DLL at all. Restriction words and the DLL name are
matched on letters and digits only, so hyphens, spacing and invisible characters do not hide
them. Anything else is refused, including a driver DLL renamed to the upscaler's name.

The PE check (x86-64 PE32+, export name, Authenticode table present) is a sanity check only.
It does not verify the signature; the SHA256 pin is the identity.

## Commands

From the repository root. Caches and outputs must be outside the source tree.

```sh
python3 dll/port/sdk_intake.py check [--cache DIR]       # allowlist format, RC11 pin and notice
python3 dll/port/sdk_intake.py seed --cache DIR FILE...  # only allowlisted hashes are copied
python3 dll/port/sdk_intake.py ingest --tag v2.3.0 --cache DIR --output DIR [--offline]
python3 dll/port/sdk_intake.py gate FILE                 # exit 0 only for an allowlisted DLL
python3 dll/port/sdk_intake.py propose --tag vX.Y.Z --cache DIR
```

- `ingest` checks with `git ls-remote` that the tag still resolves to the recorded commit,
  then asks GitHub's contents API for the listing of each file's directory at that commit and
  requires GitHub's blob id and size to equal the record (both skipped with `--offline`). Only
  then does it read the content-addressed cache (`DIR/sha256/<digest>`) or fetch from
  `raw.githubusercontent.com` at that commit, verify both files, and write `OUTPUT/<tag>/` with
  the DLL, `license.md` and `intake.json` (`tag_verified_online`, `blobs_verified_online`).
- `gate` is the check later stages run before reading a DLL's shaders.
- `propose` fetches the license first and the DLL only if the license puts it under MIT, then
  prints a draft record. It never edits the allowlist: a person reads the whole license and
  adds the record in a reviewed commit.

The allowlist is the root of trust: the code checks its format, not where its hashes came
from. A record is sound when `propose` made it (it always downloads both files at the commit
the tag resolves to and derives every field from those bytes). `gate`, `seed` and an offline
`ingest` compare bytes with the allowlist only; an online `ingest` also ties the record to
GitHub's own tree through the blob check above, so a cache hit is checked against GitHub
without downloading the file.

Exit status 2 means refused, 1 an error. Tests: [test_sdk_intake.py](../../tests/test_sdk_intake.py);
set `BC250_FSR4_SDK_CACHE` to a seeded cache to also verify the real v2.0.0–v2.3.0 files.

## Recorded drops

| Tag | DLL SHA256 | License line |
| --- | --- | --- |
| v2.0.0 | `241e6e5e…` | whole-file MIT, line 7 |
| v2.1.0 | `3eab9a44…` | MIT list, line 593 |
| v2.1.1 | `3eab9a44…` (same DLL as v2.1.0) | MIT list, line 593 |
| v2.2.0 | `ec7ed3ca…` | MIT list, line 586 |
| v2.3.0 | `d0dcccc7…` (RC11's input) | MIT list, line 586 |

`check` also confirms that [manifest.json](../manifest.json)'s SDK input is the v2.3.0 record
and that [AMD-SDK-LICENSE.md](../notices/AMD-SDK-LICENSE.md) is its license.

## Drop profiles

[drop_profile.py](drop_profile.py) derives an SDK DLL's layout from its bytes, after the
license gate. [profiles/sdk-2.3.0.json](profiles/sdk-2.3.0.json) is the reviewed profile of
RC11's input. [repack_dll.py](../repack_dll.py) takes every SDK-specific offset and byte from
it, re-derives the profile from the SDK on each build and refuses any difference.

| Part | Derived from | v2.3.0 |
| --- | --- | --- |
| `pe` | Headers: sections, alignments, certificate (must end the file), relocations (DIR64 and padding only), the empty section-header slot | 6 sections; certificate at `0x1b6b200` |
| `shaders` | DXBC containers with a DXIL part, each slot referenced by a relocated (u64 length, u64 address) pair; entry names from `PSV0` | 1,028 containers; 998 slots in `.data`; 30 unreferenced |
| `host_patches` | Byte patterns (`??` = any byte) that must match exactly once, plus branch-target checks; the edits are built from the matched operands | INT8 eligibility `0x8189`, `0x8191`; buffer-UAV barrier `0x4789` |
| `provider_label` | The version string before `FSR4-i8`; its only code reference, a RIP-relative LEA; no absolute pointer to it | `4.1.1` at `0xc8300`; LEA at `0x4a5` |

```sh
python3 dll/port/drop_profile.py derive --sdk DLL [--tag TAG] [--output FILE]  # draft profile
python3 dll/port/drop_profile.py check [--sdk DLL]   # canonical, allowlisted, manifest slots; re-derive
python3 dll/port/drop_profile.py survey --sdk DLL    # which parts derive for a new drop
python3 scripts/check-drop-profile.py --sdk DLL --shaders DIR   # or --from-dll the release DLL
```

`check-drop-profile.py` repacks the release DLL from the profile, using the programs from
`dll/build.py`'s output or read back from the release DLL (each checked against the
manifest's replacement hash), and requires the manifest's expected DLL hash.

Earlier drops, by `survey`:

| Tag | Shader slots | INT8 eligibility | Buffer-UAV barrier | Provider label |
| --- | --- | --- | --- | --- |
| v2.0.0 | 554 in `.rdata` | not found | not found | not found |
| v2.1.0 | 554 in `.rdata`; 233 containers unreferenced | not found | not found | not found |
| v2.2.0 | 572 in `.rdata` | not found | found once | not found |
| v2.3.0 | 998 in `.data` | found once | found once | found once |

For a new drop: run `survey`; extend the pattern or checks in `drop_profile.py` for any part
that fails, after reading the new code; write the draft with `derive`, review it, and commit it
as `profiles/sdk-X.Y.Z.json`.

## Lowering

[lower_pass.py](lower_pass.py) regenerates a model pass's replacement programs from the SDK
DLL's own DXIL. A recipe ([recipes/](recipes)) lists, for every INT8 slot of one entry point, the
stage sequence from the SDK program to the release program. Weights, kernel layout, lane pairs,
bins and flush points are derived from the SDK program by the stages; a recipe holds only stage
order and options.

| Stage | Module | Does |
| --- | --- | --- |
| `unroll` | [unroll.py](unroll.py) | `opt` (sroa, mem2reg, instcombine, simplifycfg, gvn, dce, globaldce) and IMad/UMad folding until stable; expands fixed-trip loops (literal start, unit step, bound <= 32), exposing every weight as a literal |
| `winograd` | [winograd.py](winograd.py) | Exact integer F(2,3) for a 3x3, 16-to-16-channel convolution: weights and window read from the program's chains and affine load addresses; two Wave32 waves per 64-thread group, one domain shared through group memory; options `accumulate` (`sequential`, `matched`, `binned`) and `postscale` |
| `pointwise` | [bins.py](bins.py) | Groups of 16 pointwise chains of the given lengths: matched lane pairs, bin-packed i16 chunks |
| `stream`, `prefix_pairs`, `half_groups`, `pack` | [packing.py](packing.py) | v4's exact i16 packing of the remaining literal dot4 chains |
| `drop_dot_declaration`, `shader_model`, `wave_size`, `masked_stores` | [ir.py](ir.py) | No dot4 left; SM 6.9; required wave size; SDK discard address 128 MiB -> `0xfffffffc` |
| `casts` | [scalarize_casts.py](../scalarize_casts.py) | RC7 compatibility lowering of `<2 x i16>` extensions |
| `opt` | DXC `opt` | RC10 cleanup (`early-cse`, `dce`, `strip-dead-prototypes`) |

Every i16 product and partial is interval-bounded and widened to i32 before it could overflow;
biases are added in i32 afterwards, so each program computes the SDK program's integers bit for
bit. Stages refuse programs outside the form they were reviewed for.

```sh
python3 dll/port/lower_pass.py check     # recipes against the profile and manifest (offline)
python3 dll/port/lower_pass.py generate --recipe dll/port/recipes/rc11-pass12.json \
  --sdk DLL --dxc DXC --output DIR       # DXC: extracted DXC 1.9.2607 archive (manifest pin)
```

`generate` gates the SDK DLL (allowlist and reviewed drop profile), requires the recipe to cover
exactly the entry's INT8 (dot4) slots, disassembles each slot with `dxc -dumpbin`, runs the
stages, assembles and validates with libdxcompiler and compares with [manifest.json](../manifest.json):
the container must equal `replacement_sha256`, and the source must equal the manifest's source
except its first line (the `; ModuleID` comment `opt` writes from its input path). Output:
`DIR/shaders/<offset>.{ll,dxil}` and `DIR/record.json` (hash and summary after every stage).
`bin/dxc` and `bin/opt` are pinned in `lower_pass.TOOLS`; both are members of the manifest's DXC
archive, whose `lib/libdxcompiler.so` is the manifest's `dxcompiler_sha256`.

| Recipe | Slots | Result on SDK 2.3.0 |
| --- | --- | --- |
| [rc11-pass1](recipes/rc11-pass1.json) | 6 (0x7de1d0: Winograd + pointwise bins) | 6/6 containers and sources identical to RC11 |
| [rc11-pass12](recipes/rc11-pass12.json) | 6 (0xb14220: Winograd) | 6/6 containers and sources identical to RC11 |

Not covered yet: the hot slots of passes 2, 4, 5 and 10 (wave64, P2/mid-grid Winograd bins, P5
stream pairing), the guarded weight-specialised passes 3, 6, 7, 8, 9, 11, prep and final.
Tests: [test_lowering.py](../../tests/test_lowering.py) (an IR interpreter checks every packing stage
and accumulator against the original dot4 chains); with `BC250_FSR4_SDK_CACHE` and
`BC250_FSR4_DXC` set it also regenerates both recipes.


## Drop classes

[classify.py](classify.py) is the first tool to run on a new drop. It compares every shader
program of the new drop with an earlier one and reports what changed in the host layout, so the
port can be planned by class. Both inputs are `ingest` outputs and are re-verified against the
allowlist, license line included, before any byte is read.

| Class | Meaning | Port |
| --- | --- | --- |
| A | Identical container | Copy the RC replacement; only its offset moves |
| B | Same skeleton: only literal constants differ | Re-run the recipe; plans re-derive from the new constants |
| C | Same entry and shader model, different skeleton | New stage forms, then the harness gates |
| D | No program with that entry and shader model before | New work |
| E | An old program no new program is based on (old side) | Drop its replacement |

The skeleton is the `dxc -dumpbin` text with every literal operand replaced by a placeholder, and
the entry name, shader hash and compiler string normalized. Literals that select structure stay:
`dx.op` opcodes, types and array sizes, `alloca`, `getelementptr`, `extractvalue`/`insertvalue`,
vector lanes, branch and switch operands, metadata. B rows name the base program and count the
differing literals by role, which gives the B kind:

| B kind | Differing literals |
| --- | --- |
| `data` | Only stored literals (the packed weight tables), dot4 literal operands, floats, global initializers |
| `constants` | Also integer operands (offsets, bounds, masks) or intrinsic immediates |
| `rename` | None; only the entry name differs |
| `container` | None; container bytes outside the program text differ |

A B base is the old program with the same skeleton and the fewest differing literals, preferring the
same entry. A C base is the most similar old program of the same entry and shader model, preferring
one no other row uses. A program that is renamed and changed shows as D plus E.

The host part derives each [drop profile](#drop-profiles) component of both drops independently and
gives a verdict: unchanged, changed (re-derived from the bytes), moved (the pattern still matches
once), new, or lost (the pattern no longer matches: read the new code). It also lists the data
tables the host code loads with a RIP-relative LEA beside a size immediate, which finds the model
weight initializers that `InitializerBuffer` programs read, and checks whether each table's bytes
occur in the other drop. The RC view maps the [manifest](../manifest.json)'s replaced slots to their
classes when the manifest's SDK is one of the two drops.

```sh
python3 dll/port/classify.py diff --old DROPS/v2.3.0 --new DROPS/v2.4.0 --dxc DXC \
  --output DIR [--cache DIR] [--jobs N] [--entries REGEX]
python3 dll/port/classify.py layout --old DROPS/v2.3.0 --new DROPS/v2.4.0   # host part only, no DXC
```

`diff` writes `DIR/classes.json` (one row per container, the removed rows, summary, layout,
tables, RC view) and `DIR/classes.md`. `--cache` keeps one analysis per container hash.
Tests: [test_classify.py](../../tests/test_classify.py); with `BC250_FSR4_SDK_CACHE` and
`BC250_FSR4_DXC` set it also classifies passes 3 and 12 of SDK 2.2.0 -> 2.3.0.
