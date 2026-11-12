---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Formal methods: a provably complete edit-corpus space, and where Lean and TLA+ fit

**Status:** Experiments run. E0–E7 and E9 are done, E8 is not; results are in
`formal/EXPERIMENTS.md` (repository root). Headline results:

- one representative per layout edit class is **unsound** (E4);
- abicheck is exact on the bounded layout fragment (E5);
- the generator never produces padding-absorbed edits (E2);
- the disposition count trigger has an exact precondition, and no step breaks
  it on the corpus (E7).

## Problem

We want two claims, and today we can state neither precisely:

1. **The generator is complete.** The generated ABI edit corpus
   (`tests/_edit_corpus_*`, space model in `tests/_edit_corpus_space.py`)
   covers its space without gaps, and every generated case has a correct
   expected outcome.
2. **abicheck detects that space.** Every case in it gets the expected
   finding and verdict.

"Complete" only means something relative to a formally defined space.
"Every ABI change in C/C++" is not a mathematical object, so the target
claim is:

> abicheck detects every class in space **P**. The generator covers **P**
> without gaps, and **P** is complete with respect to model **M**, whose
> boundaries are listed explicitly.

## What can and cannot be proved

```text
[1] Abstract change space M, P           <- Lean: defined and proved
      | totality, surjectivity, oracle soundness, class reduction
[2] Generator operators (abstract)       <- Lean: proved on the model
      | NOT provable: C/C++ text realizes the abstract edit
[3] Real C/C++ + compiler                <- execution oracle, layout probes (tests)
      |
[4] abicheck finds every class           <- exhaustive run over P (tests)
```

Levels 1–2 are mathematics. Levels 3–4 are tests: exhaustive over a finite
space, but tests. Reports must never call a conformance test a proof of the
implementation.

## Decision: not one formal model per ChangeKind

Formalizing all 428 kinds one by one is rejected:

- Most detectors compare records. A per-rule proof there is near-tautological.
- Defects concentrate at the seams instead: extraction, unknown vs. empty,
  disposition (family F1). E0 confirmed this: the rule was sound, the
  extractor was not.
- Hundreds of models drift from the code faster than anyone can maintain them.

Formal work goes only where semantics is shared by many rules, or where the
claim is universal ("for all", "no gaps") and enumeration cannot establish it.

## Tool roles

| Tool | Use for | Do not use for |
|---|---|---|
| **Lean 4** (core, no Mathlib unless arithmetic demands it) | the space definition; totality and surjectivity of operators; soundness of expected outcomes (breaking ⇒ ∃ violated consumer; compatible ⇒ none); **class-reduction theorems** ("one representative per class is enough"); t-wise coverage claims | runtime dependency; C++ semantics in full; per-detector models |
| **TLA+ / TLC** | the pipeline as a state machine: record-before-disposing (ADR-067); scope completeness states (ADR-065); storage generations and migrations; release history (ADR-066); concurrency in multi-artifact runs | ABI semantics; the generator (it enumerates, it is not a process) |
| **Alloy or SMT (z3)** | fast, bounded search for gaps or contradictions in coverage and expectation tables; cheap CI scripts | "for all" claims, which stay in Lean |

Use a bounded checker (Alloy/SMT) to find holes quickly. Prove in Lean only
what must hold universally.

## Experiments

Each one has a yes/no outcome. Stop the line if an experiment says the cost
does not pay.

| Id | Experiment | Question it answers | Exit criterion |
|---|---|---|---|
| **E0** ✅ | Lean model of `TYPE_FIELD_OFFSET_CHANGED` + fixtures (`formal/`) | Does a rule-level model find anything real? | Yes: the castxml unknown-offset-as-0 defect (fixed in #2013) |
| **E1** ✅ | Write the space **M/P** for record layout as a document: axes, values, explicit exclusions (union, bit-fields, templates, inline, LTO, …) | Can the space be stated so that "gap" is decidable? | Every existing layout operator maps to a point; every axis value is named |
| **E2** ✅ | SMT/Alloy check of the current layout `expect(config)` tables against E1 | Do the hand-written tables have holes or contradictions? | A list of uncovered or contradictory cells (possibly empty) |
| **E3** ✅ | Lean pilot on the layout fragment: operators in the model, proofs of totality, surjectivity and oracle soundness; export the class table | Can expectations be *computed* and proved instead of hand-written? | Exported table matches `expect` for 3–4 operators; mismatches triaged |
| **E4** ✅ | The first **class-reduction theorem**: one representative per class gives the same verdict as the whole class | Is the "complete" claim of a reduced corpus provable? | Theorem proved for one layout class, or a counterexample showing the class split is wrong |
| **E5** ✅ | Close level 4: run abicheck over every exported class; triage misses via `reconcile` / `known-gaps.md` | Does abicheck detect all of P? | Zero unexplained misses for the layout fragment |
| **E6** ✅ | Close the extraction assumption: in the corpus, compare extracted `offset_bits`/sizes with compiler layout probes for each cell | How sound is extraction (Lean's `EvSound`) in practice? | Disagreement rate per backend (castxml, clang, DWARF) |
| **E7** ✅ | TLA+ spec of the disposition pipeline (suppression, reclassification, acknowledgment, dedup, scope exclusion) | Can a finding vanish without a recorded rule and reason on some path? | TLC passes for small bounds, or a trace shows the vanishing path |
| **E8** | TLA+ spec of scope completeness (unselected / expected-not-produced / failed / retired; zero comparisons) | Can any path report a clean pass after zero comparisons, or a removal without inventory? | Same as E7 |
| **E9** ✅ | Generalize E0's "unknown never creates a finding" into a Lean property over the tri-state `Fact` layer (ADR-063), with a Python conformance sweep over detectors | Does the property hold across detectors, not just one? | Sweep lists violating detectors (expect a few; F1 history) |

**Order:** E1 → E2 → E3 → E4 → E5 is the main line toward a complete
generator. E6 runs alongside, from E1 on. E7–E8 are independent of it and
can start any time. E9 comes after E3, once the shared model core exists.

**Expansion after the layout fragment**, in decreasing risk: function
signatures and parameters, enums, ELF symbols and versioning, C++ (bases,
vptr, templates). Each fragment is one Lean module on a shared core
(consumer, contract, unknown evidence).

## Next steps after the experiments

1. **Generator:** split the layout operators by the proved conditions, and add
   padding-absorbed operators (insert or append without a layout change) with
   *computed* expectations from `formal/lean/layout_table.json`. This closes
   the false-positive side E2 found uncovered.
2. **Generator space:** add an *edit* axis to `tests/_edit_corpus_space.py`,
   so coverage can say which edit sub-classes ran, not only which constructs.
3. **Disposition:** decide on E7's proposal (a structural precondition for the
   count trigger), and widen
   `test_a_pipeline_step_can_never_drop_a_finding_unrecorded` beyond five
   `FUNC_REMOVED` inputs.
4. **DWARF layout channel:** make `FieldInfo.byte_offset` optional (schema
   change), and audit `byte_size == 0` as "unknown" (E9).
5. **E8**, then the next fragments in the stated order: function signatures,
   enums, ELF versioning, C++ layout.

## Integration rules

- Lean and TLA+ stay **development tools**. They are never an abicheck
  dependency or a runtime component.
- Every exported table (like `formal/lean/fires_truth_table.json`) is
  checked in CI against the generator's own data, so model drift turns the
  build red instead of making the proofs fiction. CI job: optional at
  first, required once E3 lands.
- Each report states what is proved, what is tested and what is assumed,
  using `formal/README.md` § Boundaries as the template.

## Risks

- **Completeness is relative to M.** An axis missing from M (visibility,
  LTO, toolchain flags) is silently uncovered. Mitigation: E1's explicit
  exclusion list, reviewed like an ADR.
- **Abstract → C realization is unproved.** Mitigation: the execution
  oracle and E6's layout probes.
- **Maintenance skill.** Someone must read and maintain Lean/TLA+.
  Mitigation: the drift check above, and small, fragment-scoped modules.
- **Cost.** Each experiment has a stop criterion. If E3 does not produce a
  computed table that is cheaper than the hand-written one, stop the Lean
  line and keep E2-style SMT checks.

## Out of scope

- Proving the Python implementation correct.
- Proving compilers or platform ABIs correct.
- Formal models for individual detectors beyond E0.
