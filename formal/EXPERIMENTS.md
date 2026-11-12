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
| E7 | Can a finding vanish from the disposition pipeline unrecorded? | A *sufficient* condition for the count trigger: no step drops and adds in the same call. Without it, a shortest counterexample exists, including the *add-to-bucket* variant the code comment does not name. No real step violates the condition on the corpus |
| E8 | Can a path report a clean pass after zero comparisons, or a removal without inventory? | **I1 and I2 hold** on the real aggregation over all ≤3-member releases. **I3 was violated** under the default `warn` policy (a degraded `failed` member beside a completed one exited `0`); **fixed**, now 0 violations ([§ E8](#e8-scope-completeness-adr-065)) |
| E9 | Does unknown evidence create findings? | Header channel: no manufactured or changed-value claim in 12 erasure sweeps. The DWARF channel **cannot be swept**: it has no "unknown" value. Two latent "unknown → 0" encodings found there (not reachable from valid DWARF or from snapshots abicheck wrote) |

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

E5 shows abicheck handles the insertion cases correctly today. Removal is
outside the E1 edit space, so it is **not tested**. For the missing two,
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
same transition relation, and by **TLC** (2026-11-12, tla2tools v1.7.4) on
`MC_noadds.cfg`/`MC_adds.cfg`. TLC's reachable graph equals the BFS graph
state for state and edge for edge (`formal/tla/compare_tlc.py`, CI job `tlc`;
see [README § TLA+ models](README.md#tla-models-tlc-and-the-python-checkers)).
TLC's `MC_adds` counterexample is the add-to-`changes` twin of the trace below.

```
no same-call additions : 3100 states, NothingVanishes holds
with additions         : 676 states, VIOLATED
  step 1: changes={1}  -> drop 1, add NEW object 2 to a bucket
          changes={}, buckets={2}, ledger={}   -- 1 vanished
          len(after) + Δbuckets = 0 + 1 = len(before)  => trigger silent
```

- **Sufficient precondition.** The trigger is sound if no step that does not
  record its own drops introduces a new object in the same call. That is
  sufficient, not necessary: a mixed call that drops more findings than it
  adds still trips the trigger and records them. The unsafe shape is a call
  whose additions are at least as many as its drops. The code comment names
  "adds and drops"; the model also shows that adding into a *bucket* (not
  only into `changes`) defeats the trigger.
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

`formal/experiments/unknown_evidence_sweep.py` erases one **header-layer**
layout fact family on one side of the real E5 snapshot pairs (castxml and
DWARF-only): `TypeField.offset_bits`, and `RecordType` size and alignment,
including their copy in the snapshot's semantic IR. It then checks every
header-channel claim, as (kind, subject, old value, new value), against the
full-evidence truth set validated in E5:

```
12 sweeps (3 facts x 2 sides x 2 backends):
  header claims not in truth (incl. changed values) = 0
  header claims surviving their own erasure         = 0
```

- **What is not covered.** The DWARF `StructLayout` channel is **not erased
  and not tested**. `FieldInfo.byte_offset` is an `int` with no "unknown"
  value to erase to, which is itself finding 1 below. Its findings
  (`struct_field_offset_changed`, `struct_size_changed`) keep reporting the
  same true changes from the unerased debug info. The sweep prints them
  separately and counts none of them as a pass.
- **Correction to an earlier revision of this report.** That revision erased
  only `RecordType.size_bits` and `alignment_bits`, not their semantic-IR
  copy, so the erasure never reached the detector. The "true claims still
  made" numbers it reported for size and alignment were an artifact of that
  bug, not corroboration.

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

## E8. Scope completeness (ADR-065)

Files: `formal/tla/ScopeCompleteness.tla`, `formal/tla/MC_scope.cfg`,
`formal/tla/check_scope.py` (BFS over the spec), `formal/experiments/scope_completeness_audit.py`
(conformance against the real code). Tests: `tests/test_formal_experiments.py`.

**What the spec models.** Each release member ends in one acquisition state:
`unselected` (code `OUT_OF_SCOPE`), `expected_not_produced`, `failed`,
`not_supplied` (absent, NEW's inventory unproven), `retired` (absent, NEW's
inventory proven), `compared_ok`, `compared_break`. `retired` is not a
separate code state: the code keeps `NOT_SUPPLIED` and derives removal from
`ScopeAcquisitionRecord.proven_removed_members`, so the spec makes that
derivation an explicit transition. `not_supplied` is added to the six states
the task named because "mere absence" is what I2 is about. `UNSUPPORTED` and
`AMBIGUOUS` are out of vocabulary (both are "unchecked" in the code and do
not change any invariant). Exit is the max over: compatibility (4), removal
(8), operational failure (4, ADR-065 D8), D6 incomplete scope (1, under
`block` only), D7 no comparison completed (1, policy-independent).

**Real-code transitions** (from reading `abicheck/`):

| Step | Code |
|---|---|
| state assignment per member | `workflows/release_scope.py::build_release_scope_record` |
| `unchecked` = expected-not-produced, failed, not-supplied, unsupported, ambiguous, minus proven removals/additions | `model/scope_acquisition.py::unchecked_members` |
| removal = present on OLD, absent on NEW, NEW inventory proven | `proven_removed_members` |
| D6 / D7 | `policy/scope_completeness.py::resolve_scope_decision` |
| exit fold | `policy/release_exit_decision.py::resolve_release_exit_decision_with_assurance` → `policy/exit_decision_precedence.py::resolve_release_exit_decision` → `policy/exit_decision.py::resolve_exit_decision` (`max` over contributions) |
| operational error | `release_exit_decision` sets `operational_error_contribution = 4` when a library result's `verdict` is `"ERROR"` or `"failed"` (or the aggregate `worst_verdict`), `max`ed with `ScopeDecision.failed_member_exit_contribution` (a `FAILED` member in the record; fixed, see Finding below) |

**Checked results** (`scope_completeness_audit.py`, 11 member kinds, all
evidence/policy/`fail_on_removed` settings):

```
members 0..3 : 46848 scenarios, 26240 with zero completed comparisons
  I1 NoCleanZeroComparison : 0 violations
  I2 RemovalNeedsProof     : 0 violations
  I3 FailedNeverClean      : 1396 violations before the fix; 0 after (all policy=warn, exit 0 before)
```

The 1396 violations all have the same shape: a member whose failure is
recorded as `failed` in the scope record but does not produce a library result
with `verdict == "ERROR"`, beside at least one completed member. Minimal
counterexample (2 members, one degraded, default `warn`), with the real
functions and no harness code:

```python
rec = build_release_scope_record(
    {"libA.so": Path("/o/libA.so"), "libB.so": Path("/o/libB.so")},
    {"libA.so": Path("/n/libA.so"), "libB.so": Path("/n/libB.so")},
    ["libA.so", "libB.so"],
    [{"library": "libA.so", "verdict": "NO_CHANGE"},
     {"library": "libB.so", "verdict": "failed", "reason": "degraded capture"}],
    release_inventory_evidence(old_stored=False, new_stored=False))
d = resolve_scope_decision(rec, "warn")       # completeness: incomplete
ex = resolve_release_exit_decision_with_assurance(
    "NO_CHANGE", False, [], None,
    library_results=[...same two entries...],
    incomplete_scope_exit_contribution=d.incomplete_scope_exit_contribution,
    no_comparison_completed_exit_contribution=d.no_comparison_completed_exit_contribution)
# ex.code == 0, reasons == ["clean"]; libB.so is FAILED on the record
```

Under `block` the same run exits `1` (`incomplete_scope`), so the failure is
not hidden there, but the operational-error exit `4` that a real `ERROR`
member gets is not reached. The gap is in the glue, not in the scope
record: `workflows/release_compare.py` records a degraded matched member as
`"verdict": "failed"` (ADR-065 D8 path), and the operational fold only reads
`"ERROR"`. Two more paths carry the same shape: a degraded OLD-only member
(`degraded.old_unmatched` goes into `old_failed` with no library result) and
a matched member with no library result recorded at all (`no_entry`). I did
not establish that `no_entry` is reachable from the fan-out; the other two
are reachable through stored ProjectSnapshot packages marked degraded.

**Finding fixed.** `ScopeCompleteness`-side fix in `policy/scope_completeness.py`:
`failed_member_exit_contribution(record)` returns `4` iff some member is
`FAILED`, carried on `ScopeDecision` and folded with `max` into
`operational_error_contribution` by `policy/release_exit_decision.py`
(`resolve_release_exit_decision_for_report` and
`resolve_release_exit_decision_with_assurance`). The operational scan also
reads library results with verdict `"failed"`, so a caller with no scope record
is covered. Every caller that already passed the incomplete-scope contribution
now passes this one too, and `report/audit_set.py` folds it into the existing
`operational_error` axis. The exit reason is `operational_error`, not `clean`.
The strict xfail was flipped to a normal test, and the pin test for the old
gap was removed. Re-run: `I3 FailedNeverClean: checked 46848, violated 0`
(members 0..3).

Note: running `formal/experiments/scope_completeness_audit.py` directly
imports whichever `abicheck` is first on `sys.path`. From a worktree, set
`PYTHONPATH=$PWD` or the audit reads the editable install of another checkout.

**TLC status.** TLC (2026-11-12) first rejected the spec: `Max` was used
before its definition, then every finished run was reported as a deadlock.
Both are fixed in the spec (definition order, explicit `Finished`
stuttering). TLC now holds `TypeOK`, I1, I2 and I3 on 1710 states, and its
graph equals the BFS graph (`formal/tla/compare_tlc.py`). The faithful spec holds on all 1710 reachable states of
3 members. Each seeded fault is caught (`--mutant classify-unguarded` →
I2, `warn-clean-zero` → I1, `failed-ops-dropped` → I3), so the invariants are
not vacuous.

**Not modelled** (stated, not silently missing): new-only members (additions;
the spec models the removal direction only), `UNSUPPORTED`/`AMBIGUOUS`, the
severity scheme (`resolve_release_exit_decision` ranks removal above the
verdict under severity mode; the spec uses a plain max), `fail_on_removed`
(fixed to `true` in the spec, both values in the harness), and the CLI
end-to-end path (the harness calls the same functions the release path calls,
with the glue transcribed; it does not run `abicheck compare` on a directory).

## Not done

- **SMT** for E2. (The TLC run of E7 and E8 is done, see README § TLA+ models.) The bounded spaces were small enough
  for exhaustive enumeration, so SMT added nothing here. It becomes relevant
  once the space is too large to enumerate.

## Boundaries

| Claim | Status |
|---|---|
| Layout theorems (E4) | **proved** in Lean for the model |
| Model = gcc layout (E6a) | **tested** exhaustively on the bounded fragment |
| Extraction = gcc (E6b) | **tested** on the same fragment, two backends |
| abicheck = model (E5) | **tested** exhaustively on the fragment; nothing outside it |
| Disposition trigger precondition (E7) | **model-checked** (TLC and BFS, graphs equal, small bounds); implementation **observed** on 3 pairs |
| No manufactured findings (E9) | **tested** for 3 layout fact families on the fragment |
| Scope completeness I1 (zero comparisons never pass), I2 (removal needs proof) (E8) | **model-checked** (TLC and BFS, graphs equal, 3 members, faithful spec) and **tested** on the real aggregation (≤3 members, all settings) |
| Scope completeness I3 (failed never clean) (E8) | **model-checked** on the spec; **fixed** on the real code (0 violations over 46848 combinations ≤3 members, all settings) |
