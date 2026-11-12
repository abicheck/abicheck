# Formal-methods experiments E1–E9: results

Roadmap and rationale: [`docs/contribute/plans/formal-methods-and-generator-completeness.md`](../docs/contribute/plans/formal-methods-and-generator-completeness.md).
E0 (the `TYPE_FIELD_OFFSET_CHANGED` rule) is in [`README.md`](README.md).
Run date 2026-11-12. Tools: Lean 4.15.0 (core only), gcc 13.3.0, castxml 0.7.0, Python 3.13.

| Id | Question | Answer |
|---|---|---|
| E1 | Can the record-layout space be stated so that "gap" is decidable? | **Yes**, for a bounded fragment ([§ E1](#e1-the-record-layout-space)). The generator's own space has no *edit* axis, which is why "coverage" could not express what was missing |
| E2 | Do the generator's layout expectations hold? Do they have holes? | All 6 C layout operators agree with the model on their seed. Only 1 is valid **class-wide** (proved). Padding-absorbed sub-classes are barely generated: append-into-tail-padding arrived concurrently in #1999; insert-without-move and remove-without-shrink are still missing |
| E3 | Can expectations be *computed* from a model instead of hand-written? | **Yes** for layout: Lean computes offsets and sizeof for 1248 edit cases, and the result is exported as a table |
| E4 | Does one representative per edit class suffice? | **No.** 19% of "insert before f" cases leave f in place, and 25% of "append" cases keep sizeof. Proved sufficient conditions split the classes correctly |
| E5 | Does abicheck detect all of the bounded space? | **Yes**: 0 mismatches over 3488 field-offset and 1248 size predictions |
| E6 | Are the model and the extraction sound in practice? | Model = gcc on 2496/2496 layouts. castxml and DWARF extraction = gcc on 2496/2496 each |
| E7 | Can a finding vanish from the disposition pipeline unrecorded? | The count trigger is sound **iff** no step drops and adds in the same call. A shortest counterexample was found, including the *add-to-bucket* variant the code comment does not name. No real step violates it on the corpus |
| E8 | Scope completeness state machine | **Not run** (see [§ Not done](#not-done)) |
| E9 | Does unknown evidence create findings? | No manufactured claims in 12 erasure sweeps. Two latent "unknown → 0" encodings found in the DWARF layout channel (not reachable from valid DWARF / abicheck-written snapshots) |

## E1. The record-layout space

A point is `(base layout, edit, consumer use, evidence)`:

| Axis | Values in the bounded fragment | Excluded (stated, not silently missing) |
|---|---|---|
| base layout | lists of 1–3 fields over `{char, short, int, double}` (84 bases) | bit-fields, unions, `#pragma pack`/`packed`, 16-byte-aligned members (`long double`, `__int128`), arrays (modelled only via E2's seed facts), nested records, C++ bases/vptr/`[[no_unique_address]]` |
| edit | insert one field of any of the 4 types at any position 0..n (append = position n) | remove, reorder, widen (only E2 seed facts), several edits at once |
| consumer use | reads field f (old offset); allocates by old sizeof; opaque (no access) | writes, inline functions, by-value ABI classification (register passing) |
| evidence | both sides known; one side unknown (E9) | contradictory evidence across channels |

Target: SysV x86-64 C ABI, gcc. Lean model: `formal/lean/AbiLean/Layout.lean`.

**Why the generator could not express this.** `tests/_edit_corpus_space.py`
says explicitly that its space "has no 'edit' axis: one operator covers its
construct". A covered *construct* (`struct`) says nothing about which edit
sub-classes ran, so the gap in E2 was invisible to the coverage ledger.

## E2. Generator operators vs. the model

Seed `c_core`: `struct rec { int id; double weight; char tag[8]; }`, `struct point { int x; int y; }`.

| Operator | Seed fact (Lean, `decide`) | Valid for the whole class? |
|---|---|---|
| `struct_append_field` (`long extra`) | sizeof 24 → 32 | **Yes, proved**: `append_long_always_grows` (any struct with max alignment ≤ 8) |
| `struct_remove_trailing_field` | 24 → 16 | No: `{double; int; char}` minus `char` stays 16 |
| `struct_swap_fields` | both fields move | not proved in general (a swap can leave one of the two fields in place) |
| `struct_field_widen` (`int`→`long long`) | `y` 4 → 8 | seed only |
| `struct_array_grow` (`tag[8]`→`[16]`) | offsets kept, 24 → 32 | No: `tag[1]`→`[2]` after a `double` keeps 16 |
| `struct_array_shrink_in_padding` | layout identical → operator expects API_BREAK | consistent |

**Finding (coverage gap, not an abicheck defect).** The edits whose change
padding absorbs are exactly the cases where a *false positive* would show up.
In the generator:

- `struct_array_shrink_in_padding` already covers one of them;
- `field_added_in_tail_padding` (C++ seed, landed concurrently in #1999,
  `struct FC { int a; char b; }` + `char c`) covers append-into-tail-padding.
  It is literally the Lean counterexample `append_class_not_determined`, which
  proves sizeof stays 8;
- **still missing**: a field *inserted* in the middle without moving later
  fields (`{char a; int b}` → insert `char`), and a trailing field *removed*
  without shrinking sizeof (`{double; int; char}` → drop `char`).

E5 shows abicheck handles all of these correctly today. For the missing two,
nothing in the generator would catch a regression.

## E3/E4. Computed expectations and class reduction

Lean (`AbiLean.Layout`), no `sorry`, axioms only `propext`/`Quot.sound`:

| Theorem | Statement |
|---|---|
| `insert_prefix_unchanged` | inserting at k never moves fields before k (all layouts) |
| `offsetsFrom_mono` | placement is monotone: fields after an insertion never move *backwards* |
| `insert_moves_next` | **sufficient for breaking**: inserting `t` right before `f` with `align(f) ≤ size(t)` strictly moves `f` |
| `append_grows_size` | **sufficient for size growth**: appending a field with `size ≥ maxAlign(old)` strictly grows sizeof |
| `insert_class_not_determined` | counterexample: `{char a; int b}`, insert `char` at 0 → `b` stays at 4 |
| `append_class_not_determined` | counterexample: `{int a; char b}`, append `char` → sizeof stays 8 |

Over the exported table (`formal/lean/layout_table.json`, 1248 cases):

```
insert before field f : 1407 moved, 337 NOT moved   (19% not moved)
append                : 253 grew,   83 size SAME    (25% same)
immediately-next field moved: 803; covered by insert_moves_next: 570; theorem violations: 0
```

**Consequence for the generator.** "One representative per edit class" is
unsound for layout edits. Classes have to be split by the proved conditions:

- **insert, provably breaking**: `align(f) ≤ size(inserted)`;
- **insert, layout-dependent**: everything else. Expectations must come from
  the computed table, not from the class;
- **append, provably growing**: `size ≥ maxAlign`;
- **append, layout-dependent**: everything else.

## E5/E6. The model, extraction and abicheck against gcc

`formal/experiments/layout_experiments.py` generates every case as a real C
struct (old and new), builds two shared libraries, and compares:

```
E6a  Lean model vs gcc offsetof/sizeof      : 2496 / 2496 agree
E6b  castxml header extraction vs gcc       : 2496 / 2496 agree
E6b  DWARF-only extraction vs gcc           : 2496 / 2496 agree
E5   abicheck compare vs model prediction   :
       field moved   pred=T found=T 1407 | pred=F found=F 2081 | mismatches 0
       size changed  pred=T found=T  850 | pred=F found=F  398 | mismatches 0
```

The pure layout function therefore stands in for gcc on this fragment, and
abicheck's layout rules are exact on it: no false positives, no false negatives.

## E7. Disposition pipeline (TLA+)

`formal/tla/DispositionPipeline.tla` models a pipeline step as an arbitrary
drop / move-to-bucket / add, plus the runner's count trigger from
`policy/finding_disposition/pipeline.py::_record_dropped_duplicates`.
Invariant `NothingVanishes`: every finding is visible, in a bucket, or in the
ledger.

Checked with `formal/tla/check_disposition.py`, an explicit-state BFS over the
same transition relation. **TLC itself was not run**: executing the downloaded
`tla2tools.jar` was blocked in this environment. `MC_*.cfg` are ready for TLC.

```
no same-call additions : 3100 states, NothingVanishes holds
with additions         : 676 states, VIOLATED
  step 1: changes={1}  -> drop 1, add NEW object 2 to a bucket
          changes={}, buckets={2}, ledger={}   -- 1 vanished
          len(after) + Δbuckets = 0 + 1 = len(before)  => trigger silent
```

- **Exact precondition.** The trigger is sound iff a step that does not record
  its own drops never introduces a new object in the same call. The code
  comment names "adds and drops"; the model also shows that adding into a
  *bucket* (not only into `changes`) defeats it.
- **Implementation check.** `formal/experiments/disposition_step_audit.py`
  wraps the runner over real comparisons (3 snapshot pairs, 7209 kept
  findings, 84 step calls): no step dropped and introduced objects in the same
  call, and nothing vanished. This is evidence on a corpus, not a proof. The
  existing test `test_a_pipeline_step_can_never_drop_a_finding_unrecorded`
  uses five `FUNC_REMOVED` inputs only.
- **Proposal (not implemented).** Make the precondition structural: either
  steps declare `introduces_objects`, and the runner falls back to the
  identity check for them, or the identity check runs whenever
  `after is not before`. That is a performance trade-off the code documents
  deliberately, so it is left to the maintainers.

## E9. Unknown evidence

`formal/experiments/unknown_evidence_sweep.py` erases one layout fact family
(`offset_bits`, `size_bits`, `alignment_bits`) on one side of the real E5
snapshot pairs (castxml and DWARF-only) and checks that every layout claim is
in the full-evidence truth set (validated in E5):

```
12 sweeps (3 facts x 2 sides x 2 backends): claims not in truth = 0
```

When the header offset is erased, the *same true* moves are still reported
through the independent DWARF `StructLayout` channel
(`struct_field_offset_changed`). That is correct corroboration, not
manufacture.

Latent "unknown → 0" encodings found by reading the DWARF layout path for E9.
Both are recorded here, neither is fixed:

1. `model/dwarf_facts.py::FieldInfo.byte_offset: int` cannot express
   "unknown". `dwarf_metadata.py` raises on an unevaluable location (good), but
   `snapshot_platform_blocks.py` decodes a stored field without `byte_offset`
   as `0`. abicheck always writes the key, so only hand-written or foreign
   snapshots reach it. Same class as the fixed castxml defect
   (`evidence.malformed_input_yields_value`). A fix needs an `Optional` field,
   which is a schema change.
2. `FieldInfo.byte_size` uses `0` for "unknown", which collides with real
   zero-size members (flexible array members). It needs an audit of every
   reader before it changes.

An absent `DW_AT_data_member_location` decoding to `0` is **not** a defect:
DWARF defines it as offset 0 (union members).

## Not done

- **E8 (scope completeness in TLA+).** Not run. It needs a faithful model of
  `ADR-065`'s state machine (unselected / expected-not-produced / failed /
  retired), and that model has to be extracted from the workflows code first.
  That is a separate piece of work comparable to E7.
- **TLC run** of E7 and **SMT** for E2. The bounded spaces were small enough
  for exhaustive enumeration, so SMT added nothing here. It becomes relevant
  once the space is too large to enumerate.

## Boundaries

| Claim | Status |
|---|---|
| Layout theorems (E4) | **proved** in Lean for the model |
| Model = gcc layout (E6a) | **tested** exhaustively on the bounded fragment |
| Extraction = gcc (E6b) | **tested** on the same fragment, two backends |
| abicheck = model (E5) | **tested** exhaustively on the fragment; nothing outside it |
| Disposition trigger precondition (E7) | **model-checked** (BFS, small bounds); implementation **observed** on 3 pairs |
| No manufactured findings (E9) | **tested** for 3 layout fact families on the fragment |
