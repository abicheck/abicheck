# Formal model: `TYPE_FIELD_OFFSET_CHANGED` (Lean 4 experiment)

A small, core-only Lean 4 project that states **exactly when** abicheck's
field-offset rule may conclude BREAKING, proves that statement, and ties the
model to the implementation through fixtures and conformance tests.

This is an experiment, not a verified analyzer. What is proved, what is
tested, and what is assumed are kept separate in [§ Boundaries](#boundaries).

## Layout

| Path | What |
|---|---|
| `lean/AbiLean/FieldOffset.lean` | model, rule, theorems |
| `lean/AbiLean/Examples.lean` | non-vacuity witnesses, negative checks N1–N6, `#print axioms`, truth-table `#eval` |
| `lean/AbiLean/Layout.lean`, `lean/AbiLean/LayoutExport.lean`, `lean/layout_table.json` | layout model with alignment, class-reduction theorems, exported 1248-case table (experiments E2–E6) |
| `tla/`, `experiments/`, `EXPERIMENTS.md` | experiments E1–E9 and their results |
| `lean/fires_truth_table.json` | the rule's truth table as computed by Lean (test oracle) |
| `fixtures/` | old/new C library (exposed and opaque record), four old consumers, `run_experiment.sh` |
| `../tests/test_lean_field_offset_rule.py` | conformance + regression tests |

## Reproduce

```bash
# Lean 4.15.0, core only (no Mathlib). Any install route works; this one
# avoids running an installer script:
curl -sSfLO https://github.com/leanprover/lean4/releases/download/v4.15.0/lean-4.15.0-linux.tar.zst
# sha256 af71a2569a9f68337de2434829b3008cd8e32c436e9cb6bd8c84a2c2ba3585c9
tar --zstd -xf lean-4.15.0-linux.tar.zst && export PATH=$PWD/lean-4.15.0-linux/bin:$PATH
(cd formal/lean && lake build)        # prints #print axioms and the truth table

formal/fixtures/run_experiment.sh /tmp/out      # gcc + abicheck (castxml)
pytest tests/test_lean_field_offset_rule.py -q  # add -m integration for the fixture run
```

Recorded run (2026-11-11): Lean `4.15.0` (commit `11651562caae`), no
dependencies (`lake-manifest.json` has `packages: []`); `lake build` →
`Build completed successfully`; gcc 13.3.0 (clang 18.1.3 also present);
castxml 0.7.0. `grep -rn 'sorry\|admit\|^axiom\|native_decide\|implemented_by\|extern' formal/lean/AbiLean` is empty.

## Why this rule

`abicheck/diff_types.py::_diff_type_field_pair` is the smallest rule with a
precise consumer-level meaning:

```python
if f_old.offset_bits is not None and f_new.offset_bits is not None \
   and f_old.offset_bits != f_new.offset_bits:
    emit TYPE_FIELD_OFFSET_CHANGED        # default_verdict BREAKING (types_2.py)
```

Its catalog impact text — "Old code reads/writes fields at stale offsets" —
is a statement about a previously compiled consumer, which is exactly what can
be modelled. The record-size alternative (`TYPE_SIZE_CHANGED`) needs an
allocation/out-of-bounds model and the result is UB, not an observable value.

## The model

| Lean | Meaning | abicheck data / code |
|---|---|---|
| `Field`, `Layout` | ground-truth record layout: name ↦ start offset | `RecordType.fields: list[TypeField]` (`model/entities.py`) |
| `FieldEvidence.oldOff/newOff : Option Nat` | extracted offset per side; `none` = unknown | `TypeField.offset_bits: int \| None` |
| `EvSound` | **assumption**: a present value is the true offset | extractor correctness (castxml `records.py`, DWARF `dwarf_snapshot.py`) — *not proved* |
| `NamesUnique` | fields matched by name are the same field | `_diff_type_fields` keys `{f.name: f}`; anonymous members are flattened to their member names |
| `OffsetsUnique` | struct, not union (needed only for the *safe* direction) | — (flattened anonymous unions violate it: see N5) |
| `readAt` | a load at offset `o` from a library-filled object sees that field's value, or padding | the C fixture's `r->b` |
| `Op.read f` / `Op.passOpaque` | consumer dereferences `f` / only passes the pointer around | consumer source |
| `Consumer.built` | layout the consumer was compiled against | old headers |
| `Visibility.opaque`, `Admissible` | forward-declared only in both versions ⇒ consumers cannot name fields | `compare/opaque_struct_downgrade.py`, `diff_filtering._downgrade_opaque_type_changes` |
| `fires` | the raw rule, literally | `_diff_type_field_pair` |
| `emitsBreaking` | raw rule + opaque downgrade | report severity `breaking` |

Three different claims are kept apart:

* **(a)** `SomeConsumerBroken v old new pad` — *some* admissible consumer
  compiled against `old` has its contract violated on *some* input;
* **(b)** `Violated c new pad` — the contract of *this* consumer `c` is
  violated on some input;
* **(c)** `EveryCallFails c new pad` — *every* input violates it.

## Theorems (all checked, no `sorry`)

| Theorem | Statement |
|---|---|
| `fires_sound` | `EvSound ∧ NamesUnique new ∧ fires e` ⇒ for **every** consumer built against `old` that reads `e.name`: (b) holds |
| `emitsBreaking_sound` | `emitsBreaking v e` ⇒ (a). **This is the exact meaning of the BREAKING verdict** |
| `not_every_call_fails` | (c) never holds, for any layouts (the all-`pad` input reads correctly) |
| `opaque_never_broken` | under `opaque`, no admissible consumer is broken (justifies the downgrade) |
| `unchanged_reads_safe` | if every field a consumer reads kept its offset (and `OffsetsUnique`), its contract holds on every input — unused fields and accessor-only consumers |
| `unknown_old_not_fires`, `unknown_new_not_fires` | unknown on either side ⇒ no finding |
| `unknown_is_undetermined` | with the old offset unknown, the evidence is consistent with a broken *and* an unbroken world: neither BREAKING nor "compatible" is entailed |
| `violated_mono` | adding operations to a consumer (same `built`) never repairs a violation |
| `findings_perm`, `anyBreaking_perm`, `anyBreaking_dup` | permuting/duplicating identical evidence does not change findings or the aggregate |

So the rule is **sound for (a)**, sound for (b) **only for consumers that
read the moved field**, and **never** justifies (c).

### `#print axioms`

```
fires_sound, emitsBreaking_sound, Examples.readerB_broken : [propext, Classical.choice, Quot.sound]
unchanged_reads_safe, unknown_is_undetermined, anyBreaking_perm,
  Examples.N1/N5/N6                                       : [propext, Quot.sound]
findings_perm                                             : [propext]
not_every_call_fails, opaque_never_broken, violated_mono  : (none)
```

These are Lean's three standard axioms (propositional extensionality,
quotient soundness, choice) pulled in by core `simp`/`decide` lemmas; no
`sorryAx`, no project-declared axiom. The proofs are constructive in content
(the violating input is an explicit `fun x => if x = f then pad+1 else pad`).

## Non-vacuity and near misses (`Examples.lean`)

Concrete instance mirroring the fixture: `v1 = [a@0, b@4]`, `v2 = [a@0, x@4, b@8]`.

* `fires evB = true`, `readerB_broken`, `exposed_broken` — premises are
  satisfiable; the violating input `a=1, x=7, b=42` makes the old reader see
  `7` (checked by `decide`).
* Near misses where a premise is absent and the conclusion is *not* allowed:
  `readerA_safe` (reads unmoved `a`), `opaqueUser_safe` (accessor only),
  `emitsBreaking .opaque evB = false`, `¬ EveryCallFails readerB …`.

## Negative checks (counterexamples, not tactic failures)

Each is a *proved negation* of the weakened statement, with an explicit witness:

| | Weakening | Witness |
|---|---|---|
| N1 | treat unknown offsets as `0` (drop the `is not None` guards) | old unknown (truly 4), new 4: weak rule fires, reader is not broken |
| N2 | (b) for every old consumer, not just readers of `f` | `readerA` |
| N3 | ignore opacity | opaque record: `SomeConsumerBroken` is false |
| N4 | strengthen to (c) | all-`pad` input |
| N5 | drop `OffsetsUnique` from the safe direction | union `{a@0, b@0}`: unmoved `b` reads `a`'s value |
| N6 | drop `NamesUnique` from soundness | layout listing `b` at 8 and at 4 |

## Product link

`formal/fixtures/run_experiment.sh` builds v1/v2 shared libraries, compiles the
consumers **once against v1**, runs them against both, then runs
`abicheck compare` with headers. The library allocates and fills the object,
so there is no under-allocation or out-of-bounds read; the divergence is an
observed value, not a crash (compiled with `-O0 -fno-strict-aliasing`; reading
a v2 object through the v1 type is formally a cross-TU type mismatch in ISO C,
so "no crash" is not used as evidence of anything).

```
consumer        lib=v1  lib=v2      model
reader_b        42      7           [.read "b"]  -> Violated   (fires_sound)
reader_a        1       1           [.read "a"]  -> safe       (unchanged_reads_safe)
accessor_user   42      42          [.passOpaque] -> safe
opaque_user     42      42          opaque record -> safe      (opaque_never_broken)
abicheck exposed: type_field_offset_changed rec::b 32 -> 64 bits, severity breaking, exit 4
abicheck opaque : no changes, exit 0
```

Model fact ↔ extracted fact: `evB = ⟨"b", some 4, some 8⟩` (bytes) ↔ report
`old_value "32"`, `new_value "64"` (bits), `contract_relevance IN_CONTRACT`,
`evidence_status artifact_proven`. abicheck's BREAKING corresponds to (a): it
reports the change although two of the three exposed-record consumers are
unaffected — correct, because the verdict is existential over consumers.

## Findings

1. **Mismatch fixed — extractor fabricated offset 0 (N1 in production).**
   `extract/headers/castxml/records.py::expand_anonymous_field` used
   `optional_int_attr(..., "offset") or 0` for both the anonymous member and
   its inner fields, turning *unknown* into *0* — exactly the N1 weakening,
   violating `EvSound`. Fix: propagate `None` (`_add_known_offsets`).
   Regression tests fail before / pass after. castxml normally emits `offset`
   on every `Field`, so this is a latent defect (malformed/foreign XML), not an
   observed field false positive.
2. **Opaque records are not recorded, not disposed.** For the opaque fixture
   `disposition_audit.detected_total` is `0`: the record never enters the
   header surface, so no finding is created and then downgraded. The verdict
   matches the model; whether a DWARF-visible but header-opaque layout change
   should be *recorded* as a non-gating disposition (AGENTS.md "Record before
   disposing") is a product question, left open.
3. **`NamesUnique` holds via flattening; `OffsetsUnique` does not.** Real
   dumps flatten anonymous unions (`s::a`, `s::f` both at 0). That is harmless
   for the firing direction but means the absence of a finding is never a
   proof of compatibility for union members (N5) — consistent with abicheck
   not treating "no finding" as a claim.

## Boundaries

| Layer | Status |
|---|---|
| Rule soundness relative to the model | **proved** in Lean |
| Python rule = Lean `fires` | **tested** exhaustively on `{None,0,32,64}²` against the Lean-computed table; *not* proved |
| Opaque downgrade = `Visibility.opaque` | tested on one fixture; the opacity detector itself is not modelled |
| ELF/DWARF/castxml extraction correct (`EvSound`) | **assumed**; one defect found and fixed; tested on fixtures only |
| Compiler/ABI premises (C layout, SysV x86-64, loads at fixed offsets) | **assumed**; observed with gcc 13.3 on one fixture |
| Not modelled | widths/partial overlap/bitfields, writes by the consumer, consumer-side allocation, inline functions in headers, C++ bases/vptr, alignment |

## Next steps

The roadmap (generator completeness, Lean/TLA+ roles, experiments E1–E9) is
[`docs/contribute/plans/formal-methods-and-generator-completeness.md`](../docs/contribute/plans/formal-methods-and-generator-completeness.md).
Candidate rules if the per-rule line is ever resumed:

### Next rules worth formalising

1. `TYPE_SIZE_CHANGED` with consumer-side allocation (needs a memory model with
   bounds; the conclusion is "out-of-bounds write exists", i.e. UB — state it
   as such, not as an observed value).
2. `TYPE_FIELD_TYPE_CHANGED` (width/representation) — extends `readAt` with widths.
3. `FUNC_PARAMS_CHANGED` / calling-convention rules — small model of argument slots.
4. `ENUM_MEMBER_VALUE_CHANGED` — very small model; good for the switch/serialize consumer split.
5. The evidence-combination layer (tri-state `Fact`, ADR-063) — the "unknown
   never fabricates a finding" property generalised across detectors.
