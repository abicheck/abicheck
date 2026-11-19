# E16: closed record-edit fragments (remove, swap, retype)

The insertion fragment E10 ([`FRAGMENT.md`](FRAGMENT.md)) closed one record
edit. This closes the other three single-field edits of the same kind of
struct, with stronger observations: typed report fields instead of
description text, the header and DWARF evidence lanes kept apart, an
old-consumer probe in both directions, unchanged-record controls, and two
other tools on a shared vocabulary.

**The claim, exactly.** For the space **R** below: the enumeration is the
space without duplicates and the abstract generator is a bijection onto its
image (proved); the C generator realizes it (tested on every case); every
case's layout and expected findings follow from the Lean layout model
(computed, re-derived independently in the tests); gcc 13.3 and clang 18.1
lay out every case as the model does, abicheck's typed findings and values
equal the oracle for every case in each of three evidence lanes, and an
old-header consumer observes exactly what a byte model predicts (tested
exhaustively). abidiff and ABICC agree with the oracle on every claim of a
common vocabulary they can express (tested exhaustively). Nothing is claimed
outside R.

Files: [`lean/AbiLean/RecordEditFragment.lean`](lean/AbiLean/RecordEditFragment.lean)
(space, generator, oracle, theorems),
[`lean/AbiLean/RecordEditFragmentExport.lean`](lean/AbiLean/RecordEditFragmentExport.lean)
→ [`lean/record_edit_fragment_cases.json`](lean/record_edit_fragment_cases.json)
(550 KB: one row per case, one per observable class, the `gen` checksum),
[`experiments/record_edit_fragment.py`](experiments/record_edit_fragment.py)
(generator, builds, lanes, consumer probe),
[`experiments/record_edit_competitors.py`](experiments/record_edit_competitors.py)
(abidiff, ABICC),
[`../tests/test_formal_record_edit_fragment.py`](../tests/test_formal_record_edit_fragment.py).

## The fragment R

| Axis | In R | Outside R |
|---|---|---|
| Language, ABI | C11, x86-64 LP64 SysV, little endian | AArch64 (signed `char` assumed by the probe values), ILP32, LLP64, C++ |
| Field types | old fields: `char`, `short`, `int`, `double` (size = alignment 1, 2, 4, 8); a retype may also introduce `unsigned int`, `float` | `long`, pointers, `long double`, 16-byte types, arrays, nested records, enums, bit-fields |
| Old struct | 1–4 fields `f0 … f(n-1)` | empty structs, more than 4 fields, unions, `packed`/`aligned`/`#pragma pack`, flexible array members |
| Edit | **remove** field k (n ≥ 2); **swap** fields i < j (identity kept: names move with the fields); **retype** field k to any of the six types other than its own | insertion (E10), renames, several edits at once, reordering beyond one transposition |
| Exposure | record passed by pointer to exported `use_sN`, `fill_sN`, `show_sN`; headers given where the lane takes them | by-value passing, opaque records, inline functions, variables of the type |
| Evidence lanes | `hdr`: built without `-g`, public header given · `dwarf`: `-g`, no header · `both`: `-g` and header | stripped binaries, build data, sources |
| Observable | per record: the multiset of `kind:field_name`, the typed `old_value`/`new_value`, the verdict | `description` text, other output formats |

Size: |R| = 1248 removals + 1744 swaps + 6260 retypes = **9252** cases
(`space_length`, `op_sizes`); with E10's 6368 insertions, 15620 single-field
edits of this record shape.

## 1. Space and generator (proved)

`Case = (base : List Ty, edit : Edit)`; `InSpace` bounds the length, the base
types and the edit's indices, with no reference to the generator.

| Theorem | Statement | How |
|---|---|---|
| `mem_space` | `c ∈ space ↔ InSpace c` | structural proof |
| `space_nodup` | no case listed twice | a mixed-radix code strictly increases along `space` (`decide +kernel`, linear), so the codes are pairwise distinct |
| `space_length`, `op_sizes` | 9252 = 1248 + 1744 + 6260 | computed by the kernel |
| `decode_gen` | `InSpace c → decode (gen c) = some c` | `decide +kernel` on all 9252 cases of the space, then `mem_space` |
| `gen_decode` | `decode p = some c → InSpace c ∧ gen c = p` | by construction |
| `generated_in_exactly_one`, `every_case_generated` | every generated program pair belongs to exactly one case, and every case has one | from the two above |

`decide +kernel` has the kernel evaluate the decision procedure; it adds no
axiom (unlike `native_decide`). `#print axioms`: at most `propext`,
`Classical.choice`, `Quot.sound`; the computed facts use none. The kernel
needs a larger thread stack for these checks, set in `lakefile.lean`
(`--tstack`). `lake build` of the module takes ≈ 4 min, the export ≈ 2 min.

The C generator enumerates in its own order (op, length, edit, base: not
Lean's). `test_generator_is_a_bijection_onto_the_space` decodes each of its
9252 programs from the C text onto the space predicate enumerated with
`itertools`; `test_generator_realizes_leans_gen` puts them in Lean's order
and reproduces the export's FNV-1a-64 checksum of `gen c` over every case.

## 2. The oracle

From `AbiLean.Layout`: old/new offsets, sizeof and alignment, then the
product contract for a record passed by pointer, header lane:

* the edit's own finding: `type_field_removed` or `type_field_type_changed`
  on the edited field (a swap has none);
* `type_field_offset_changed` on every old field still present whose offset
  changed, the retyped field included;
* `type_size_changed`, `type_alignment_changed` on the record when they
  changed;

all `breaking` in the catalog. Typed values: offsets, sizes and alignments
in bits; type names by identity (`short int` and `short` are one type, see
Findings). The **DWARF lane** expects the same minus `type_alignment_changed`:
that kind is declared observable at {binary, header} only
(`model/change_catalog/types_3.py`, `_OBS_ALIGN_H`), because DWARF records a
record's alignment only when it is explicit.

`test_oracle_layout_matches_reference_layout_rule` and
`test_class_findings_follow_from_the_contract` re-derive every row with code
written in the test. 88 observable classes occur; the reduced set holds the
first case of each.

### Layout facts

| Theorem | Statement | How |
|---|---|---|
| `remove_prefix_not_moved`, `replace_prefix_not_moved` | fields before the edited one never move, for any field list | structural proof |
| `swap_align_unchanged` | a swap never changes alignment | kernel, whole space |
| `swap_moves_one` | a swap moves at least one of the two fields | kernel, whole space |
| `swap_can_keep_one` | …but not always both: `{char f0; char f1; short f2}`, f1 ↔ f2 leaves f2 at 2 | `decide` |
| `every_case_breaking` | every case of R is breaking under the contract | kernel, whole space |
| `same_layout_retype_exists` | a retype can keep every offset and the size (`int → float`): layout alone does not decide a retype | `decide` |

**Same-size retypes**, classified explicitly
(`test_same_size_retypes_are_classified`): in R they are exactly `int →
unsigned int` and `int → float` (the base types exclude `float` and
`unsigned`). Both keep every offset and the size, so `type_field_type_changed`
alone reports them. Size preservation is never used as a compatibility
oracle: the consumer probe below decides what an old binary sees.

## 3. Observation layers

Each layer is observed by its own mechanism and checked against its own
oracle, so one cannot mask another.

| Layer | Observation | Oracle |
|---|---|---|
| Layout | `sizeof`, `_Alignof`, every `offsetof`, old and new, compiled per compiler | Lean layout |
| Finding identity | report `kind`, `symbol`, `field_name` (schema 7.7, below) | Lean findings, per lane |
| Old/new values | report `old_value`, `new_value` | Lean layout (bits), type identity |
| Verdict / gate | report severity per record; per-case runs: the CLI exit code | contract: `breaking`, exit 4 |
| Old consumer, read | consumer built against `old.h`, run against the old and the new provider; `fill_sN` writes a value per field, the consumer reads its old layout's bytes | byte model (Python, from the Lean offsets) |
| Old consumer, write | the consumer stores its old fields; the provider's `show_sN` prints the values it reads | byte model |
| Affected interface | `affected_symbols` | exactly `{use_sN, fill_sN, show_sN}` (recorded per case) |
| Controls | 340 unchanged records (every base of R) in the same libraries | no finding |

Probe values: each round writes one mathematical value V in every type,
converted as C converts it: round 0 V = −(j+2)−0.5, round 1 V = 70000+j+0.25,
which no narrower integer holds. Both directions are needed: reading, an old
consumer sees the low bytes of a widened integer field, which are exactly
the narrow value again (modular conversion, little endian), so **a widening
is invisible to reads**; writing, it stores the narrow field and the new
provider reads the sentinel in the high bytes
(`test_write_direction_sees_what_the_read_direction_cannot`).

Outcome per case and lane: `agree`, `checker_mismatch` (finding multiset or
verdict differs), `value_mismatch`, `oracle_conflict` (compiler layout ≠
model), `missing` (no report entry). Extractor failure and infrastructure
error abort the run with the tool's output; unsupported configurations
(non-x86-64) skip in the tests.

## 4. Results

Commit, environment and raw results: [Receipt](#receipt). One pinned tree,
Linux x86-64, gcc 13.3.0, clang 18.1.3, castxml 0.7.0, Python 3.13,
`-std=c11 -O0 -shared -fPIC` (`-g` for the `dwarf`/`both` lanes).

```
                                      gcc 13.3          clang 18.1
layout = Lean (old+new, 9252 cases)   18504 / 18504     18504 / 18504
abicheck = oracle, hdr lane            9252 /  9252      9252 /  9252
abicheck = oracle, dwarf lane          9252 /  9252      9252 /  9252
abicheck = oracle, both lanes          9252 /  9252      9252 /  9252
consumer = byte model (read + write)   9252 /  9252      9252 /  9252
consumer-visible change not named           0                 0
affected_symbols exactly the record's own   9252 / 9252       9252 / 9252   (every lane)
controls with a finding               0 / 340 per lane  0 / 340 per lane
```

**Disposition reconciles with Lean.** Summing the oracle over R gives 25963
findings, 1746 of them alignment changes. The reports: `hdr` detected 25963,
deduplicated 0; `dwarf` detected 48434 = 2 × 24217, deduplicated 24217,
effective 24217 = 25963 − 1746; `both` detected 50180 = 25963 + 24217,
deduplicated 24217. So every DWARF-tier `struct_*` finding is detected and
recorded as a duplicate of its `type_*` equivalent, and stays counted in
`disposition_audit` (ADR-067).

**Per-case runs** (each representative its own library pair and `abicheck
compare` per lane, so the exit code is that case's verdict): 88 cases × 2
compilers × 3 lanes = 528 runs, all `agree` with exit code 4. The other
9164 cases were run in batch only: a per-case sweep of the full space is
≈ 6 × 9252 compares, roughly 10 h on this machine, and was not run.

### Other tools, shared vocabulary

`record_edit_competitors.py` runs each tool's documented workflow on the
same `-g` libraries: `abidiff OLD.so NEW.so` (libabigail 2.4.0) and
`abi-dumper` + `abi-compliance-checker -binary -report-format xml` (ABICC
2.3, abi-dumper 1.2). Their raw output stays in the run directory. Each
tool's per-record output is mapped to claims all can express, scored per
claim; a claim a format cannot express is `unsupported`, never a miss:

| Claim | abicheck (`dwarf`) | abidiff | ABICC |
|---|---|---|---|
| record flagged | ✓ | ✓ | ✓ |
| removed/retyped field named as such | ✓ | ✓ | ✓ |
| sizeof change iff it changed | ✓ | ✓ | ✓ (`DataType_Size`) |
| exactly the fields whose offset changed | ✓ | ✓ | unsupported: a removal is one combined problem; `Moved_Field` is the *position* |
| a swap names both swapped fields | unsupported (offsets, not positions) | unsupported | ✓ |

Full space, both compilers (identical counts for gcc and clang builds):

| Claim | abicheck (`dwarf`) | abidiff 2.4.0 | ABICC 2.3 |
|---|---|---|---|
| record flagged | 9252 / 9252 | 9252 / 9252 | 9252 / 9252 |
| edit member (1248 removals + 6260 retypes; a swap has none) | 9252 / 9252 | 9252 / 9252 | 9252 / 9252 |
| sizeof change iff it changed | 9252 / 9252 | 9252 / 9252 | 9252 / 9252 |
| fields whose offset changed | 9252 / 9252 | 9252 / 9252 | unsupported |
| swap names both fields (1744 swaps) | unsupported | unsupported | 1744 / 1744 (n/a on the 7508 others) |

No tool errored, no cell missed. This is
shared-domain agreement on R, not a ranking: abicheck's other lanes (header,
alignment) are capabilities the others' workflows here do not exercise, and
abidiff's header filtering was not used (it matches header *file names*
recorded in DWARF, and the two versions here are named `old.h`/`new.h`). The
first adapter versions had three parsing bugs (an `and offset changed`
continuation line, field-size vs record-size problem ids, `Moved_Field_And_Size`);
each was found by checking a miss against the raw output and fixed in the
adapter, not in the scoring.

## Findings

* **Typed member identity was missing from the JSON report (fixed).** A
  `type_field_*` finding's `symbol` named only the record; the member was in
  `description` alone, which is why earlier harnesses (E10 included) parsed
  prose. `Change.field_name` was already set by the field emitters; report
  schema 7.7 serializes it (`tests/test_report_field_name.py`). E16 checks
  findings by `kind:field_name` only.
* **A finding's type value is spelled by the backend.** `old_value`/
  `new_value` of `type_field_type_changed` read `short int` from castxml and
  gcc's DWARF but `short` from clang's DWARF. Same type; the oracle compares
  identity. Not changed here: `canonical_finding_id` is already
  backend-stable, and the values are display text.
* **`affected_symbols` over-approximated by name prefix (fixed).** Before
  the fix, in every lane 925 of 9252 records listed other records'
  functions (`s1` → `use_s10…`): the enrichment matched type names as
  substrings, the known gap E10 had recorded. A hit now counts only as a
  whole identifier (`compare/type_name_boundary.py`,
  `tests/test_affected_symbols_identifier_boundary.py`, against a lookaround
  oracle); the receipt run reads exact on all 9252.
* **The DWARF lane cannot see alignment, as declared.** `type_alignment_changed`
  is absent from the `dwarf` lane in all 1746 cases where it applies; the
  kind's `observable_at` says so. Verdicts are unaffected here (every case
  also reports another breaking finding).
* **Same-representation retypes are named but unwitnessed by reads.**
  `int → unsigned int` keeps every byte; only the write probe's
  reinterpretation (−2 read back as 4294967294) shows it.

### The ledger's uncovered `struct_*` kinds

The coverage ledger lists six layout kinds as uncovered. E16 classifies four
and leaves the rest:

| Kind | Classification | Evidence |
|---|---|---|
| `struct_field_removed`, `struct_field_offset_changed`, `struct_field_type_changed`, `struct_size_changed` | **evidence-lane equivalent**: the DWARF-tier twin of the `type_*` kind (declared `subsumed_by`); emitted, then deduplicated whenever the header or DWARF type lane reports the same transition, so it reaches `changes[]` only when the two tiers disagree | detected and deduplicated in all 24217 DWARF findings above |
| `struct_alignment_changed` | **not observable from DWARF without explicit alignment**, as declared | never emitted in R |
| `type_field_added`, and the other three ledger entries | outside R | — |

The ledger itself is not changed here: it counts the abiforge corpus, and a
formal-fragment run is not that corpus.

## Cost

One 4-core shared cloud container, wall time, measured while other jobs
(CI-equivalent test runs) shared the machine, so treat these as upper
bounds rather than comparative performance claims:

| Run | Cases | Wall | Breakdown |
|---|---|---|---|
| batch, full space, 2 compilers × 3 lanes + 340 controls | 9252 | 902 s, 901 s (2 samples) | generate 0.3 s; build + layout probe + consumer: gcc 77 s, clang 423 s; compare per lane (gcc/clang): hdr 73/73 s, dwarf 42/44 s, both 81/83 s |
| batch, reduced set | 88 | 26.0, 25.5, 24.6 s (3 samples) | |
| per case, reduced set, 4 jobs | 88 × 6 | 234 s | |
| abidiff / ABICC (abi-dumper + checker), full space | 9252 | gcc 274 / 45 s; clang 271 / 45 s | |

Peak child RSS: 1835 MB (full batch; the largest is one `abicheck compare`
over the 9592-record libraries), 134 MB (reduced). An earlier full batch
on an idle machine took 358 s with the same results. Lean: `lake build` of
the module ≈ 4 min, the export ≈ 2 min; fast tests ≈ 1 s.

## What is and is not proved

| Claim | Status |
|---|---|
| Enumeration = space, no duplicates, 9252 (1248/1744/6260) | **proved** (Lean; the count and the order by kernel computation) |
| Abstract generator is a bijection between the space and its image | **proved** (Lean; `decode ∘ gen = id` checked by the kernel on every case) |
| Fields before the edit point never move (remove, retype) | **proved** for any field list |
| Swap facts, every case breaking | **proved** on the space (kernel computation) |
| C generator realizes Lean's `gen` | **tested** on all 9252 cases (checksum and decode) |
| Layout oracle and findings | **computed** in Lean; re-derived independently in the tests |
| Compilers lay out every case as the model | **tested** exhaustively, gcc 13.3 and clang 18.1, x86-64, `-O0` |
| abicheck = oracle, three lanes | **tested** exhaustively, both builds |
| Consumer observations = byte model | **tested** exhaustively, both builds, two probe values, both directions |
| Every consumer-visible change is named by abicheck | **tested** exhaustively (0 unexplained); a byte-level probe with two values, not a theorem about all values or all consumers |
| The probe itself is well-defined C | **assumed** for the provider: `fill_sN`/`show_sN` access the consumer's byte buffer through a `struct sN *` (the consumer side uses `memcpy` at `offsetof`). That is the cross-version access under test, but even with the unchanged provider it relies on gcc/clang treating the aligned buffer as the struct at `-O0`; the byte model agreeing on every case is the evidence, not a guarantee |
| abidiff / ABICC = oracle on the shared vocabulary | **tested** exhaustively, one version each |
| The layout rule is the ABI | **assumed** (SysV psABI); observed on two compilers |
| Signed narrowing wraps | **assumed**: implementation-defined in ISO C, documented by GCC and Clang |
| A non-representative behaves like its class representative | **not assumed**: every case was run |
| Other optimization levels, other architectures, wider C/C++ (inheritance, packing, bit-fields, templates, by-value passing) | **not tested** |

## Reproduce

```bash
(cd formal/lean && lake build && lake env lean --tstack=4000000 AbiLean/RecordEditFragmentExport.lean)
pytest tests/test_formal_record_edit_fragment.py -q                    # fast checks
pytest tests/test_formal_record_edit_fragment.py -q -m integration     # + compilers, lanes (full space: ABICHECK_E16_FULL=1)
python formal/experiments/record_edit_fragment.py OUT [--reduced] [--per-case] [--repeat N]
python formal/experiments/record_edit_competitors.py OUT               # after a batch run into OUT
```

## Receipt

Raw results: [`receipts/e16/`](receipts/e16/) — `full_summary.json`
(environment, per-compiler/lane outcome counts, disposition totals,
timings), `cases.jsonl.gz` (one line per case and compiler: every lane's
observed and expected `kind:field_name` multiset, typed-value
disagreements, affected-symbol verdict, consumer read/write changes),
`competitors.json`, `percase_summary.json`, `reduced_summary.json`.
Disagreements: none (every list in those files is empty). Findings outside the generated records (a function, another entity):
none in any of the six reports. The harness now counts these per lane
(`unexpected_groups`); for this receipt the count was taken from the six
stored reports after the run, and each report's finding total also equals
the Lean total for its lane (25963 with headers, 24217 DWARF-only).

* **Code that ran.** Started on `a972438a2`; two commits landed during the
  run, touching only `record_edit_competitors.py`'s error handling (loaded
  by the competitor stage, which started after them) and tests. Every file
  the run loads is identical at `38117df82`, which `full_summary.json`'s
  `environment.commit` records; `abicheck_module` is this checkout's
  package.
* **Environment.** Linux x86-64; gcc 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1);
  clang 18.1.3; castxml 0.7.0; Python 3.13.18; abidiff 2.4.0;
  abi-compliance-checker 2.3; abi-dumper 1.2; Lean 4.15.0.
* **Commands.**

```bash
python formal/experiments/record_edit_fragment.py OUT/full --repeat 2
python formal/experiments/record_edit_competitors.py OUT/full
python formal/experiments/record_edit_fragment.py OUT/percase --reduced --per-case --jobs 4
python formal/experiments/record_edit_fragment.py OUT/reduced --reduced --repeat 3
```

### Denominators

| Count | Value |
|---|---|
| modeled edit classes (operations) | 3 (remove, swap, retype) |
| observable classes (distinct expected finding sets) | 88 |
| generated transitions | 9252 (1248 / 1744 / 6260) |
| configurations executed | 2 compilers × 3 lanes, `-O0`, one target |
| oracle-validated cases (layout, findings, consumer) | 9252 per configuration |
| exact matches | 9252 per configuration |
| per-case CLI runs | 528 (88 × 6) |
| xfails | 0 |
| unsupported cells | competitor claims a tool cannot express (table above); no configuration unsupported |
