# AGENTS.md — `abicheck/compare/`

## Purpose

This package owns matching old and new entities across two snapshots and
identifying a raw change, per ADR-061 D1. It answers "are these two
declarations the same entity" and "what changed between them" — never
"does that change matter" (that is `policy/`) and never "how is it
reported" (that is `report/`).

Most of that behavior still lives in the flat `diff_*` modules that
`architecture/modules.yaml` lists as this layer's `legacy_paths`. Those
stay where they are until a behavior-preserving vertical slice moves
them; `architecture/debt.yaml` holds each one at `no_growth`, so a change
that needs new lines in one of them belongs here instead.

## Permitted imports

Per ADR-061 D1, `compare/` may depend only on `model`, plus the public
root surfaces. It may not import extraction, policy, workflow, report, or
frontend modules — a comparison module that consults a verdict, a
severity, or a CLI flag is in the wrong layer.
`scripts/check_architecture.py` enforces this.

## Modules

- `dedup_key.py` — `hashable_value()`, the shared rule for turning an
  arbitrary finding value into something a `set` can hold. A leaf: it
  imports nothing, so any layer may depend on it.
- `fact_comparison.py` — `compare_facts()`/`FactComparison`/
  `FactComparability` (ADR-063 Phase 5B): decides whether an
  `(old_fact, new_fact)` pair of `model.Fact[T]`s may be compared at all,
  and what it means when it can't. Moved here from `model/fact.py` where
  the primitive first landed (Codex review, PR #1033) — deciding "does this
  differ" is exactly what `model/AGENTS.md`'s own scoped contract reserves
  for this package, not `model/`.
- `base_class_diff.py` — `diff_bases()` (ADR-063 Phase 5B): the
  `FactStatus`-aware `bases`/`virtual_bases` raw-change identification
  moved out of the legacy `diff_types.py` (Codex review, PR #1033) — new
  behavior in a `no_growth`-tracked legacy `compare` module belongs here
  per this file's own Purpose note, not as growth in the legacy file.
  `diff_types._diff_type_bases` is now a delegation-only facade.
- `va_list_diff.py` — `diff_va_list_params()` (ADR-063 Phase 5B): the
  `FactStatus`-aware `Param.is_va_list` per-parameter raw-change loop moved
  out of `diff_param_qualifiers.param_va_list_changes` the same way
  (Codex review, PR #1033) — that function's own evidence-reliability gate
  (a snapshot-level question, not a raw change) stays in
  `diff_param_qualifiers.py` as a thin facade.

## Conventions

- Every module starts with `from __future__ import annotations`.
- The 800-line production cap applies (`scripts/check_architecture.py`).
- A reusable matching, dedup, or grouping primitive added here gets its
  own standalone property-test class stating its contract as invariants,
  not only example tests written through one caller — see the root
  `AGENTS.md`'s "Primitive-level property tests" section for why that
  rule exists and what it has already caught.

## Product invariant (local consequence)

Comparison **finds every change in the selected scope and stops there**: it
emits the complete observed change set with evidence, and leaves relevance,
suppression, classification, and gating to `policy/`. A pairing algorithm
reports *unmatched* with a reason; whether unmatched means *removed*
requires inventory or selection evidence the workflow supplies. Root
`AGENTS.md` "Product decisions and change routing" states the rule.
