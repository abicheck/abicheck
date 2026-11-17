# ADR-094: One Assessment per Change, on Independent Compatibility Axes, from All the Evidence That Observes It

**Date:** 2026-11-17
**Status:** Proposed — roadmap ADR, not implemented. It changes how a verdict is derived, adds a report field and changes some exit codes (D9), so it needs maintainer acceptance. Its first slice (D8, the call axis) is backed by the closed fragments E13 and E14. The open questions are at the end.

Governed by and extending:

- [ADR-009](009-verdict-system-and-exit-codes.md): verdicts and exit codes, whose mapping is unchanged.
- [ADR-042](042-compatibility-and-gate-decision-separation.md): compatibility is separate from the gate.
- [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md): entity identity and graph reconciliation.
- [ADR-063](063-one-semantic-pipeline.md): `Fact[T]`, `EntityId`, `RunOutcome`.
- [ADR-066](066-longitudinal-history-and-versioning-policy.md): policy decides acceptance, not facts.
- [ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md): record before disposing.
- [ADR-084](084-binary-depth-source-contract-gate.md): the binary-depth source gate.
- [ADR-090](090-unresolved-surface-is-not-a-clean-verdict.md): an unresolved surface is not clean.
- [ADR-093](093-declared-change-kind-relations-and-observability.md): `observable_at` and kind relations.

## Context

abicheck reads up to six evidence sources (ADR-093 D1):

| Source | Content |
|---|---|
| `B` | binary symbols, versions and notes |
| `D` | DWARF or PDB |
| `H` | public headers |
| `BD` | build data |
| `S` | sources |
| `G` | the multi-TU graph |

There is also consumer evidence (`--used-by`). Each source has its own detectors, and each detector emits findings of its own `ChangeKind`. Each kind's verdict is its catalog `default_verdict`, sometimes changed afterwards by one of two mechanisms:

- **a modulation rule**: about 16 of them, in 26 files (`binary_gated_api_break`, `func_removed.export_kept`, `pointer_level.unexported_declaration`, `runtime_floor_contract`, `glibc_baseline_unknown`, `bundle-reachability`, `lambda_closure_never_exported`, …);
- **a `policy_overrides` entry**: about 60 in the catalog.

Each was right where it was added. None of them is part of a model of what the change *is*. So three kinds of incoherence follow.

1. **One edit becomes several unrelated findings, each with its own verdict.** For example, `long f(long)` → `void *f(void *)` yields `func_params_changed`, `param_pointer_level_changed`, `func_return_changed` and `return_pointer_level_changed`, all breaking, from header and DWARF detectors that never compare notes. Nothing states the edit's effect once.
2. **A kind's verdict stands in for a question nobody asked.** `func_params_changed` is breaking because parameter edits *usually* break the call. The fragments measured how often they do not.
   - E13 ([`formal/FUNC_FRAGMENT.md`](../../../formal/FUNC_FRAGMENT.md)) and E14 (`formal/RET_FRAGMENT.md`, in review) are closed spaces of 2160 and 1176 cases, with the register model proved in Lean and confirmed by execution on gcc and clang.
   - In them, 408 parameter and return-type edits keep the binary call interface and are still reported `BREAKING` (exit 4).
   - All 408 break source, so the accurate verdict is `API_BREAK`.
   - The opposite also happens. ADR-084 exists because a source-level kind kept `API_BREAK` from binary evidence that could not support it.
3. **Disagreeing sources do not meet.** When the header and DWARF give a parameter different types, or a build-context reconciliation (ADR-039) and a header parse disagree, the outcome depends on which detector reported first. It is not recorded as a conflict.

The kinds, the evidence sources and their visibility (ADR-093), and the tri-state facts (ADR-063 D2) all already exist. What is missing is the layer between them: **one assessment of each change, stating what it does to each kind of consumer, from all the evidence that observed it.**

## Decision

### D1. A change event per entity, with its findings as observations

A **change event** groups every finding about one entity transition in one comparison. The entity is identified by `EntityId`, ADR-063 D3. The event's observations are the findings that ADR-093 relates as `same_event_as`, `implies` or `subsumed_by`, plus findings on the same entity from other sources.

Each observation keeps:

- its kind;
- its detector;
- the evidence sources it was read from;
- its old and new values.

No finding is removed or merged away (ADR-067). The event is a view over them, held in a `ChangeAssessment` value (`abicheck/policy/assessment/`).

### D2. Independent compatibility axes

An event is assessed on a fixed set of axes. Each axis answers a question about one kind of consumer:

| Axis | Question | Typical evidence |
|---|---|---|
| `load` | Does an old binary still load and bind? (symbol, version node, SONAME, visibility) | `B` |
| `call` | Does an old binary still call it correctly? (arguments and result in place and form, hidden pointers, calling convention) | `B` + types (`D` or `H`) + the ABI model |
| `data` | Do old binaries still agree on memory? (layout, sizes, vtables, constants compiled into the caller, such as enumerator values) | `D` or `H` |
| `source` | Does consumer source still compile, and resolve to the same declarations? | `H`, `S` |
| `behavior` | Is the same input given the same meaning? (pointer ↔ integer reinterpretation, units, documented semantics) | `H`, `S`, contracts; usually unknown |
| `build` | Does a consumer still build in its environment? (toolchain floor, required flags, standard level) | `BD` |

An axis value is one of:

- `Preserved(proof)`;
- `Broken(proof)`;
- `Unknown(reason)`;
- `NotApplicable`.

`Unknown` carries *why*. It may be that the evidence configuration cannot observe the axis (ADR-093 D3's "not observable"). It may be that the evaluator's model does not cover the entity (`out_of_model`). Or the sources may disagree (D5).

A `proof` names the rule that decided it and the facts used.

The axes are deliberately separate. `long` ↔ `void *` is `call: Preserved`, `source: Broken`, `behavior: Unknown`. Each answer is defensible on its own; the verdict alone cannot say all three.

### D3. Kind defaults become priors; evaluators replace a prior only with a proof

Each `ChangeKindMeta` declares the axes it speaks to and its **prior** on each:

- `func_params_changed`: `call: Broken`, `source: Broken`;
- `enum_member_value_changed`: `data: Broken`;
- `func_removed`: `load: Broken`.

Its `default_verdict` becomes a derived check of those priors. A catalog test fails when the two disagree.

An **axis evaluator** is a function from facts to one axis value for one entity class. It may replace a prior only with `Preserved(proof)` or `Broken(proof)`, inside a stated domain. Outside the domain it returns `Unknown(out_of_model)`, and the prior stands.

Today's modulation rules migrate to evaluators, or to policy (D6), one at a time:

- ADR-084's gate becomes the `source` evaluator's evidence requirement;
- `func_removed.export_kept` becomes a `load` evaluator;
- `runtime_floor_contract` becomes `build`.

Each migration is a no-op on the catalog and abiforge corpora: the shadow comparison in D9 checks this.

### D4. The verdict is derived from the axes, in one function

```text
verdict(event) =
  BREAKING    if any of load, call, data is Broken
  API_BREAK   elif source is Broken
  RISK        elif behavior or build is Broken
  COMPATIBLE  elif some axis is Preserved and none is Broken
  NO_CHANGE   otherwise
```

The run verdict is the maximum over events, as today, and feeds `RunOutcome.compatibility` (ADR-063 D6). `Unknown` axes never decide a verdict. They lower assurance (ADR-090 and ADR-064's amendment). A run cannot claim `COMPATIBLE` with full assurance while an axis that the consumer's contract depends on is `Unknown`.

The exit-code mapping (ADR-009) is unchanged. Only which verdict a change receives can change.

### D5. Disagreeing sources are a recorded conflict, not an evaluation order

When two observations of one event give contradictory facts for the same axis, the axis is `Unknown(conflict)` and the event records both sources. Examples:

- the header and DWARF give the parameter different types;
- two TUs give one type different layouts;
- the declared and the built headers differ.

Existing reconciliations decide where they already have a rule, and that rule becomes the proof: ADR-039's build context, ADR-048's graph, ADR-063's rung rule. A conflict left unresolved is reported and lowers assurance. It is never settled by which detector ran last.

### D6. Policy stays on top of the assessment

Acceptance, profile and gate settings never change an axis value. They act on the derived verdict and are recorded as dispositions, as today (ADR-066, ADR-067). The settings in question are `plugin_abi` and `sdk_vendor` overrides, severity presets, suppressions and acknowledgments.

Some `policy_overrides` entries encode a technical claim, such as "a plugin host never calls this". Each of those is moved into an evaluator with its domain. Entries that are acceptance stay as policy. The catalog states which is which.

### D7. Evaluators are checked case by case against a closed fragment

An evaluator that claims `Preserved` or `Broken` for a domain ships with a closed fragment of that domain (E10, E12, E13, E14 show the method). The fragment provides:

- an explicit space;
- a Lean model with its theorems;
- an execution or compiler oracle run on two toolchains with 0 mismatches.

A test then asserts, for every case of the exported space, that the production evaluator returns the model's answer. The domain grows only with a fragment that covers the growth.

| Axis | Evaluator | Fragment |
|---|---|---|
| `call` | SysV x86-64 register and return model (D8) | E13, E14 |
| `data` | scalar struct field insertion | E10 |
| `data` | C enumerator values | E12 |

Evaluators without a fragment stay `Unknown(out_of_model)`, so the prior stands. Their number is a visible metric that may only shrink as fragments land.

### D8. First slice: the `call` axis on x86-64 System V

`abicheck/model/call_interface/` answers:

```text
call_interface_preserved(old_sig, new_sig, abi)
  -> Preserved(reason) | Broken(reason) | Unknown(out_of_model)
```

It may answer `Preserved` only when all of these hold:

- **The ABI** is identified on both sides and is the same: `sysv_amd64`, meaning ELF64 x86-64, not x32.
- **The function** is the same symbol on both sides, prototyped and non-variadic, with at most 6 INTEGER and 8 SSE arguments.
- **Every type is in the fragments' domain:**
  - parameters `int`, `long`, `double`, object pointer;
  - returns `void`, those four, and trivially copyable structs classified INTEGER:INTEGER, SSE:SSE or MEMORY from the snapshot's DWARF facts (`return_value_sizes`, `return_memory_classified`, the value-ABI trait).
- **The proved model says the interface survives.** That is, no parameter was inserted, every surviving argument keeps its register and representation, no hidden sret pointer appears or disappears, and the old caller reads nothing (`void`) or reads the result in the place and form the new function leaves it.

This matches the theorems `set_compatible_iff`, `rem_compatible_iff`, `ins_never_compatible`, `args_intact_iff` and `call_compatible_iff`.

The event's `call` axis becomes `Preserved` and `source` stays `Broken`, so D4 derives `API_BREAK`. That covers the 408 fragment cases:

| Edit | Cases |
|---|---|
| remove the last parameter | 84 |
| remove a parameter with no later one of its register class | 42 |
| parameter `long` ↔ `void *` | 114 |
| `void` → a value returned in registers | 126 |
| return `long` ↔ `void *` | 42 |

The axis is per ABI by construction. `long` ↔ `void *` is not preserved on Windows x64 (LLP64) or x32. Windows x64 also assigns positional slots, so removing a middle `int` moves a later `double`. Those ABIs return `Unknown(out_of_model)` until each has its fragment.

The test `tests/test_call_interface_fragments.py` asserts `call_interface_preserved` equals the exported `call_compatible` on all 3336 fragment cases.

### D9. Report, migration and rollout

- **Report.** `compare_report` gains `assessments`: one entry per event, giving
  - its entity;
  - its axes, each with value and proof or reason;
  - the derived verdict;
  - the ids of its findings.

  `findings` is unchanged. Markdown and HTML show the axes for every event whose verdict differs from its kinds' defaults. A schema minor bump.
- **Shadow first.** The assessment is computed alongside today's verdicts before it decides anything. Differences are counted over the catalog, abiforge's corpus and the fragments. Every difference must be explained by an evaluator proof, or it is a defect to fix before the switch. ADR-049's shadow evaluator is the precedent.
- **Switch per evaluator.** Each evaluator decides only once its shadow differences are all explained. The call axis is first, because its differences are exactly the 408 proved cases. For those, `compare` exits 2 instead of 4. The change gets a changelog entry under "Changed", and the `known-gaps.md` over-severity entry is archived.
- **Migration of modulation rules** (D3) is one rule per change, each a no-op under the shadow comparison.

## Consequences

- A change is explained once, per kind of consumer: "old binaries keep working (x86-64 SysV call model); callers' source breaks (removed parameter); behavior: unknown". That replaces several findings that each carry a verdict.
- The verdict of the same source edit can differ by platform, and the report says why.
- Verdict logic moves from scattered rules into evaluators with stated domains and fragment oracles. What remains a heuristic, and is kept as one, becomes visible as `Unknown(out_of_model)`.
- Disagreeing evidence becomes a reported conflict that lowers assurance, instead of an artefact of detector order.
- Catalog work grows by a little: each kind declares its axes and priors, and a test keeps them consistent with `default_verdict`.

## Alternatives considered

- **Fix the 408 cases by changing `func_params_changed`'s default.** Rejected. Insertions and class-changing or width-changing retypes are real ABI breaks, and a default cannot depend on the platform.
- **A new kind per outcome**, for example `func_params_changed_call_compatible`. Rejected. It splits one fact by a judgment, breaks policy and suppression keyed on the kind, and adds another uncoordinated verdict source.
- **Another modulation rule for the call interface, as in ADR-084.** It would work for the 408 cases. It would also be the seventeenth rule, and would leave incoherences 1 and 3 in place. D8 is that rule's content, placed where the other rules migrate to.
- **Run-level axes only** (ADR-063 D6). These already exist and say how sure the run is. They cannot say which consumer a particular change breaks.

## Open questions

- **Q1. The `behavior` axis.** Should `Broken` ever be claimed there without a declared contract? A pointer ↔ integer reinterpretation is a strong signal; a unit change is invisible. The proposal is that it starts at `Unknown` everywhere except for declared contracts (ADR-049).
- **Q2. Consumer evidence.** With `--used-by`, may an axis be judged *for that consumer*? For example, `void` → value is source-compatible for a consumer that never takes the function's address. The proposal is per-consumer assessments under the consumer workflow, not in the default verdict.
- **Q3. DWARF-only signatures.** Should the `call` axis be claimed from `D` without `H`? The fragments supplied headers. It needs a DWARF-only fragment lane first.
- **Q4. `struct_return_convention_changed`.** It fires only when both sides return a struct, and its description shows the triviality trait instead of the mechanism (E14's `RET_FRAGMENT.md`, "Observations"). Under D8 the `call` axis states the mechanism for scalar ↔ sret as well. Should the kind be widened, or left as a sub-observation?

## Not done

Nothing is implemented. The evidence exists: the fragments, their theorems and execution runs, and the known-gaps entry. The assessment type, the axes in the catalog, the evaluators, the report field, the shadow comparison and the migration do not.
