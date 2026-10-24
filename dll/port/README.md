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
| `stream`, `prefix_pairs`, `half_groups`, `pack` | [packing.py](packing.py) | v4's exact i16 packing of the remaining literal dot4 chains; `stream` option `pairing: "spatial"` (RC9 P5): each group of four 3x3 (36-word) outputs gets the lane pairing with the fewest flushes (exact minimum matching, source order on ties) |
| `drop_dot_declaration`, `shader_model`, `wave_size`, `masked_stores`, `canonical` | [ir.py](ir.py) | No dot4 left; SM 6.9; required wave size; SDK discard address 128 MiB -> `0xfffffffc`; renaming to `%vN`/`vN` |
| `casts` | [scalarize_casts.py](../scalarize_casts.py) | RC7 compatibility lowering of `<2 x i16>` extensions |
| `opt` | DXC `opt` | RC10 cleanup (`early-cse`, `dce`, `strip-dead-prototypes`) |
| `float_graph` | [final_pass.py](final_pass.py) | RC7 final pass: every interval-proven dot/phi graph as FP32 MADs on integers (all partials in [-2^24, 2^24], so fused and unfused MADs are exact) |
| `native_blocks` | [final_pass.py](final_pass.py) | RC8 final pass: spatial and colour chains plus the 8-output pointwise group as width-4 i16 streams; the other two pointwise groups as FP32 graphs rooted at the streamed tails |
| `color_reuse` | [final_pass.py](final_pass.py) | RC9 final pass: a colour sample whose coordinates equal one of two samples of the dominating block takes their normalised RGB, else runs the SDK's instructions; the inverse transform must restore the input |
| `specialize` | [guarded.py](guarded.py) | RC7 guarded weight specialisation (passes 7-9): the `InitializerBuffer` table that the recipe's host accessor returns (`mov eax, size; lea rcx, [rip+d]`, size and SHA256 checked) is copied into the program; a z == 0 and whole-table compare (`waveAllTrue`) selects the copy's fast path, otherwise the SDK body runs; the fast path's fixed-trip loops are expanded and folded |
| `point_small` | [guarded.py](guarded.py) | RC9: 16-word pointwise chains in batches of `width` outputs (`blocks`: all, or the first block), exact minimum-cost lane pairing per batch, signed-byte bin-packed i16 chunks |
| `native_stream` | [guarded.py](guarded.py) | RC8: `packing.stream_outputs` over every remaining literal chain (`extra_lengths` 32, 36) |
| `mixed_float` | [guarded.py](guarded.py) | RC7/RC8: the interval-proven literal dot4/phi graph as exact FP32 MADs (\|values\| <= 2**24); `roots: native` gives it the native tails' bounds; `graph_audit` compares each remaining FP32 graph with the all-FP32 lowering |
| `vector_guard`, `group_guard` | [guarded.py](guarded.py) | RC8 guard forms: one `<4 x i32>` table load per step; Wave32 groups check the table once and share the flags through group memory |
| `release_source` | [lower_pass.py](lower_pass.py) | First stage only: the slot's committed release source (manifest hash), for passes whose RC7 lowering is not ported (prep, guarded passes) |
| `vector_guards` | [guards.py](guards.py) | RC8: the guard's four scalar table loads become one `<4 x i32>` load of the same words; SM 6.9 |
| `transport` | [transport.py](transport.py) | First stage only: a donor slot's release program with this slot's grid literals (below) |

Every i16 product and partial is interval-bounded and widened to i32 before it could overflow;
biases are added in i32 afterwards, so each program computes the SDK program's integers bit for
bit. Stages refuse programs outside the form they were reviewed for. The final-pass stages keep
the SDK's value names (no `canonical_ssa`), as the RC7-RC9 builders did.

```sh
python3 dll/port/lower_pass.py check     # recipes against the profile and manifest (offline)
python3 dll/port/lower_pass.py generate --recipe dll/port/recipes/rc11-pass12.json \
  --sdk DLL --dxc DXC --output DIR       # DXC: extracted DXC 1.9.2607 archive (manifest pin)
```

`generate` gates the SDK DLL (allowlist and reviewed drop profile), requires the recipe to cover
exactly the entry's INT8 (dot4) slots, disassembles each slot with `dxc -dumpbin`, runs the
stages, assembles and validates with libdxcompiler and compares with [manifest.json](../manifest.json):
the container must equal `replacement_sha256`, and the source must equal the manifest's source
except its first line (the `; ModuleID` comment `opt` writes from its input path; a source that
never went through `opt` must match whole). A recipe's `expected` block replaces the manifest for
the slots it names with a pinned `source_sha256` and `replacement_sha256` and a `basis`:
`rederived` (must equal [rederived.json](../rederived.json)'s program for the slot) or `new` (no
RC11 program; proven against the SDK program, below). Output:
`DIR/shaders/<offset>.{ll,dxil}` and `DIR/record.json` (hash and summary after every stage).
`bin/dxc` and `bin/opt` are pinned in `lower_pass.TOOLS`; both are members of the manifest's DXC
archive, whose `lib/libdxcompiler.so` is the manifest's `dxcompiler_sha256`.

| Recipe | Slots | Result on SDK 2.3.0 |
| --- | --- | --- |
| [rc11-pass1](recipes/rc11-pass1.json) | 6 (0x7de1d0: Winograd + pointwise bins) | 6/6 containers and sources identical to RC11 |
| [rc11-pass2](recipes/rc11-pass2.json) | 6 (0x482860: Winograd) | 6/6 containers and sources identical to RC11 |
| [rc11-pass12](recipes/rc11-pass12.json) | 6 (0xb14220: Winograd) | 6/6 containers and sources identical to RC11 |
| [rc12-final](recipes/rc12-final.json) | 96 (final pass; 0x5e7990, 0x248220, 0x7abdd0: native blocks, wave64, colour reuse) | 93/93 identical to RC11; 0x248220 = `rederived` (RC9 without the half shift); 0x5e7990, 0x7abdd0 `new` |
| [c10-pass1](recipes/c10-pass1.json) | 6; Winograd + pointwise bins also on 0x2cce40 (1080p family), 0x4bee10 (8K family) | 4/6 identical to RC11, 2/6 equal their pins |
| [c10-pass2](recipes/c10-pass2.json) | 6; Winograd also on 0x278960, 0x9739d0 | 4/6 identical to RC11, 2/6 equal their pins |
| [c10-pass12](recipes/c10-pass12.json) | 6; Winograd also on 0x647fd0, 0xaed1d0 | 4/6 identical to RC11, 2/6 equal their pins |
| [rc11-pass7](recipes/rc11-pass7.json), [8](recipes/rc11-pass8.json), [9](recipes/rc11-pass9.json) | 6 each: the 1440p-4K family (2 slots) RC8/RC9, the 1080p and 8K families RC7 | 18/18 containers and sources identical to RC11 |
| [c10-pass7](recipes/c10-pass7.json), [8](recipes/c10-pass8.json), [9](recipes/c10-pass9.json) | 6 each, all on the 1440p-4K family's sequence; `expected` pins the 4 slots of the 1080p and 8K families | 1440p-4K slots identical to RC11; 12 new programs (6 distinct), equal to the SDK programs in the interpreter (below) |
| [c10-pass5](recipes/c10-pass5.json) | 6 (0x7b6f00, 0xbd2c80, 0x3d9f20: stream pairing) | 4 identical to RC11 (incl. 0x7b6f00); 0xbd2c80, 0x3d9f20 new, pinned |
| [c10-pass11](recipes/c10-pass11.json) | 6 (0x820540, 0x9b99a0: transport from 0x234860) | 4 identical to RC11 (release source); 2 new, pinned |
| [c10-pass3](recipes/c10-pass3.json) | 6 (0x66ed50, 0x166450: vector guards) | 4 identical to RC11 (release source); 2 new, pinned |
| [c10-pass6](recipes/c10-pass6.json) | 6 (0x98e620, 0xbb0080, 0x4f4de0, 0xa616b0: vector guards) | 2 identical to RC11 (release source); 4 new, pinned |
| [c10-prepass](recipes/c10-prepass.json) | 6 listed of 180 (`cover: listed`; 4 get wave size 32) | 2 identical to RC11; 4 new, pinned |

A slot listed under a recipe's `expected` gets a program that is not the release's: `generate`
requires its container and whole source (ModuleID line included) to equal the pin, and `check`
refuses a pin equal to the release program. A pin's optional `basis` says why it differs: `new`
(the default; a program RC11 does not have) or `rederived` (dll/rederived.json's program for the
slot, cross-checked by `check`). The `c10-*` recipes apply the hot slot's sequence to the slots of
the other two context-size families (FSR4 picks one of three 19-slot families by the context's
maximum output size: up to 1920x1080, 1440p to 4K, 7680x4320). `cover` is `int8` (default: the
recipe must cover exactly the entry's dot4 slots) or `listed` (prep has 180 slots and no dot4).

The final pass's slot is chosen by the context's maximum output size: 0x5e7990 up to 1920x1080,
0x248220 from 2560x1440 to 3840x2160, 0x7abdd0 for 7680x4320. The three SDK programs differ only in
the scratch row pitch (15,392 / 30,752 / 61,472 bytes in ten `UMad`s), and so do their rc12-final
programs. RC11 carried the RC8/RC9 work in 0x248220 only.

No recipe yet: passes 4 and 10 (mid-grid Winograd bins). Not ported as stages from the SDK
program: the RC7 guarded lowering of passes 3, 6 and 11, the P11 RC8/RC9 components and prep (their
recipes start from the committed release source, `release_source`, or carry the hot slot's program
to its siblings, `transport`).
Tests: [test_lowering.py](../../tests/test_lowering.py) (an IR interpreter checks every packing stage
and accumulator against the original dot4 chains) and [test_guarded.py](../../tests/test_guarded.py)
(the guarded stages on synthetic guarded programs, run before and after in the SIMT interpreter on
random and extreme inputs; the interpreter's semantics); with `BC250_FSR4_SDK_CACHE` and
`BC250_FSR4_DXC` set they also regenerate the recipes and compare one C10 program with its SDK
program.
[test_final_pass.py](../../tests/test_final_pass.py) checks the final-pass stages on synthetic
programs and, with the same variables, regenerates rc12-final and runs the three family slots
against the SDK's programs.

### Whole-program interpreter

[execute.py](execute.py) runs single-thread DXIL compute programs (no group-shared memory,
barriers, wave or quad operations) on a synthetic resource world, to prove a lowered program
equal to the SDK program: both run the same threads, and every store (in order, bit for bit) and
every value both programs define under the same name must agree.

- Resource contents are deterministic functions of (seed, resource, address): raw-buffer words
  `random`, `extreme` (bytes -128/127), `min`, `max` or `mixed`; floats with a share of zeros,
  infinities, NaN, denormals and f32/f16 extremes; constant-buffer fields as the program reads
  them (i32: small sizes with extremes; f32: scales and specials), each overridable.
- Integers wrap; floats round once to f32/f16 per operation (round to nearest even, computed in
  double); `FMad` is exact a*b+c rounded once (`fused`) or after the product too (`separate`),
  so a lowering that is exact only under one contraction fails the other mode. Shifts out of
  range and integer division by zero raise; `undef` is zero.

## Group equivalence

[equivalence.py](equivalence.py) checks a finished program end to end against the SDK's own
program for the same slot. It interprets the text IR (the SDK disassembly and the stages' output)
for whole 64-thread groups and compares every buffer store.

| Model element | Definition |
| --- | --- |
| Threads and waves | Per-thread control flow; Wave32; the thread-to-(wave, lane) assignment is a parameter: identity, other wave first, seeded permutation |
| `dx.op.barrier` | Group sync: every live thread must reach the same barrier |
| Wave operations | Need every live lane of the wave in the same step |
| Group shared memory | Race-checked per barrier interval (cross-thread write/read or write/write); unwritten reads refused |
| Buffers | Loads read pre-dispatch contents, a deterministic function of binding, byte address and profile; stores are logged; two different values at one address are refused |
| Integers, floats | Wrap at their width; binary32 round-to-nearest-even per operation; `fptosi` saturates to i32, then wraps |

`prove` gates the SDK DLL, disassembles each slot, applies RC7's discard-address repair
(`masked_stores`, 128 MiB -> `0xfffffffc`) to the SDK program, and checks RC11's program against
the SDK's (control), the new program against the SDK's, and the new program against RC11's. Cases
per slot: 8 groups (four grid corners, the centre, two seeded, one outside the grid; one at
z = 1) x 2 extents (the whole grid, and a valid region ending inside the group) x 7 input profiles
(random, small, extreme, sparse, all -128, all 127, alternating) x 3 lane assignments for programs
with wave operations.

```sh
python3 dll/port/equivalence.py prove --recipe dll/port/recipes/c10-pass12.json --sdk DLL \
  --dxc DXC --generated DIR --output RECORD.json [--slot 0x647fd0] [--profile small] [--jobs 6]
```

Tests: [test_equivalence.py](../../tests/test_equivalence.py) (the model on synthetic programs:
shared-memory exchange under every lane assignment, races, barrier and wave rules, loops with
local arrays, buffer loads and dot4, discards, scalar semantics; with `BC250_FSR4_SDK_CACHE` and
`BC250_FSR4_DXC`: the C10 recipes regenerate their pins, a new program equals the SDK program,
and a one-coefficient mutant is detected).

## SIMT differential execution

[simt.py](simt.py) runs whole thread groups of a DXIL compute program (text IR) and records its
buffer stores: each block compiles to a Python function, threads are generators that meet at
`waveAllTrue` and group barriers, values wrap as iN, every float operation rounds to FP32.
[simt_equivalence.py](simt_equivalence.py) runs a recipe's generated programs, RC11's release programs and the
gated SDK program of each slot on the same inputs and requires identical stores:

| Scenario | Inputs |
| --- | --- |
| random corners, interior | random scratch words; the four grid-corner groups (the last column group straddles the x bound) and one interior group |
| extreme bytes, all -128, all +127, alternating | every input byte -128 or 127 |
| changed model word | one word of the guard's table changed in the bound `InitializerBuffer`: the fast path must be refused and the SDK body run |
| row out of range | a row past the grid: no store |
| tensor bound | `CsTensorSizes` row 9 (pass 9's second bound) cut inside a group |

The SDK's masked stores to its 128 MiB discard address are excluded on the SDK side and must equal
the candidate's dropped stores (RC7's `masked_stores`). A mutation control changes one i16
coefficient of the candidate's fast path; the comparison must then fail. Dispatch z > 0 is not an
input: the SDK programs of passes 7-9 then index their own local arrays out of range.

```sh
python3 dll/port/simt_equivalence.py --recipe dll/port/recipes/c10-pass7.json --sdk DLL --dxc DXC \
  --generated DIR/c10-pass7 --json OUT.json [--slots 0x522900 ...] [--no-release]
```

### Context-size families (C10)

The SDK builds three copies of each model pass and picks one by the context's maximum output size:
up to 1920x1080, 2560x1440 to 3840x2160, and 7680x4320. A pass's three SDK programs are identical
except integer literals that follow the grid (thread bounds, row pitches, scratch base addresses);
RC8/RC9 lowered only the 1440p-4K slots. [transport.py](transport.py) carries such a slot's release
program to a sibling: it derives an injective literal map from the two SDK programs (every
difference an i32 operand), extends it by folded base + k additions, and rewrites only i32
operands in the map's domain. It refuses a domain value in an instruction shape that carries no
grid literal in the SDK program (or a constant-folded form of one), a leftover domain value, and
any disagreement with the target's own release program about how often each image value occurs.
Applied to the published RC7 sources it reproduces each target family's RC7 source exactly (only
the comment line differs), and for passes 3, 5, 6 and prep it builds byte for byte what the
stages build.

[differential.py](differential.py) checks a finished program as a whole: it interprets the SDK
program, the release program and the new program of one slot on the same deterministic inputs
(thread and group IDs, constant-buffer rows, raw-buffer words, the model initializer real,
perturbed or random, extreme activation bytes) and requires identical store logs and identical
requantization inputs (`Round_ne` and `Pack4x8` operands, bit for bit). `waveAllTrue` combines
every lane of the wave, so guards are checked as the GPU evaluates them; stores to the SDK's
discard address (128 MiB) and RC7's (`0xfffffffc`) compare equal against the SDK program only.
Stdlib only; binary32 arithmetic is binary64 rounded once (exact for +, -, *, /).

```sh
python3 dll/port/differential.py --sdk DLL --dxc DXC --slot 0xbd2c80 \
  --program dll/shaders/00bd2c80.ll --program NEW.ll --trials 128 [--json OUT]
```

Tests: [test_c10_misc.py](../../tests/test_c10_misc.py) (pairing exactness and optimality, vector
guards with both guard outcomes, transport maps and refusals, interpreter semantics; with the SDK
cache and DXC: all five c10 recipes, transport equal to the stage construction, short differential
checks of the new slots).

## Re-derived configuration

[rederive.py](rederive.py) and [../rederived.json](../rederived.json) define a second build of the
RC11 sources (`dll/build.py --config rederived`) in which each element whose design followed the
third-party "4.1.1b" DLL is replaced by an independent re-derivation from the SDK 2.3.0 code.

| Element | Status | Re-derived form |
| --- | --- | --- |
| `final-pass-divisions` | derived | [sdiv_shift.py](sdiv_shift.py) decides each `sdiv i32 x, 2**k` on the SDK's own program: a logical shift only where an interval analysis proves `x >= 0`. None of SDK 2.3.0's 192 qualifies (each divides a constant-buffer field). RC11 has a shift in two of them (slot `0x248220`); the configuration restores the SDK's division there. |
| `int8-eligibility` | pending | Not re-derived: two attempts at an independent derivation (2026-10-23) did not complete. The re-derived build leaves the SDK's bytes at the profile's `int8-eligibility` site, so that DLL does not offer the INT8 provider on non-RDNA4 GPUs: a comparison build. The [RC12 release](#rc12-configuration) applies RC11's edit unchanged ([provenance](../notices/PROVENANCE.md#int8-eligibility)). |

```sh
python3 dll/port/rederive.py check                         # pinned restorations (offline)
python3 dll/port/rederive.py audit --sdk DLL --dxc DXC --output DIR
python3 dll/port/rederive.py compare A.dll B.dll --json OUT  # explain every differing byte
```

`audit` disassembles every manifest slot of the gated SDK, aligns each SDK division with the RC11
source (same value, identical dividend derivation) and requires the result to equal the pinned
element. `compare` reads both DLLs' build records and assigns every differing byte to a PE field,
host-patch site, shader pointer, replacement container part or the provider label; anything else
is refused. Tests: [test_rederive.py](../../tests/test_rederive.py), with `BC250_FSR4_SDK_CACHE` and
`BC250_FSR4_DXC` also the audit and both builds; [test_sdiv_shift.py](../../tests/test_sdiv_shift.py).

## RC12 configuration

[rc12.py](rc12.py) and [../rc12.json](../rc12.json) define the RC12 release (`dll/build.py --config
rc12`): the [re-derived configuration](#re-derived-configuration), pinned by hash as its `base`,
RC11's eligibility edit, and every program a recipe pins under `expected` with basis `new`, under
the provider label `4.1.1r12`. `scripts/package-dll.py` and `scripts/source-release.py` take the
release identity from this file (RC11's `manifest.json` stays frozen as the source base).

| Part | Content |
| --- | --- |
| `release_version` | `4.0.0-rc12` |
| `base` | `rederived.json` (its SHA256): the SDK's divisions kept, the two RC11 shifts in `0x248220` restored; eligibility pending |
| `apply_host_patches` | `["int8-eligibility"]`: the host patches the base leaves pending, applied unchanged from the drop profile (RC11's edit). It must equal the base's pending list, so RC12 keeps every RC11 host edit |
| `slots` | 34 slots (24 distinct programs; 17 in the 1080p family, 17 in the 8K family) of passes 1, 2, 3, 5, 6, 7, 8, 9, 11, 12, prep and the final pass. Each is line edits of its donor's RC11 source (the hot slot the recipe carried over; for the final pass `0x248220`, whose two restored lines the edits include); the edited text must equal the recipe's pinned source |
| `provider_name` | `4.1.1r12`, so a log tells this DLL from RC11 (`4.1.1r11`) |
| `notes` | why the divisions stay and why RC11's eligibility edit is applied |
| `expected_dll_sha256`, `expected_dll_bytes` | the release DLL's pin |

`check` (offline, in `scripts/check-repo.py`) requires the slots to be exactly the recipes' `new`
pins, each donor to be a non-new slot of the same entry and recipe, the edits to reproduce the
pinned source, the host patches to be exactly the base's pending ones, a new RC version, and the
base and manifest hashes to match. `derive` writes the slot records from
`lower_pass.py generate` output of the recipes. `compare` explains two DLLs whose appended sections
differ in layout: each section must hold exactly its build record's containers and label (zero
padding only), every differing byte before it must lie in a PE field, host-patch site, shader
pointer or the label reference, and the slots are compared by container hash.

```sh
python3 dll/port/rc12.py check
python3 dll/port/rc12.py derive --generated DIR --families FAMILIES.json   # DIR/<recipe>/shaders
python3 dll/port/rc12.py compare A.dll B.dll --json OUT
```

Tests: [test_rc12.py](../../tests/test_rc12.py); with `BC250_FSR4_SDK_CACHE` and `BC250_FSR4_DXC`
also the RC11 and RC12 builds, their comparison and packaging. The release record
[portable-dll-rc12.json](../../docs/data/portable-dll-rc12.json) is checked by
`scripts/check-rc12-release.py`.

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
