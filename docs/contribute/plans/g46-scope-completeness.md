---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G46 — Comparison scope and completeness (vision workstream A, ADR-065)

**Registry:** `UC-WF-scope-completeness` (`partial`) · **Owner ADR:** [ADR-065](../adr/065-comparison-scope-selection-and-completeness.md) · **Source:** [vision workstream A](vision-api-abi-evolution.md)

## Problem

A release or multi-member comparison can read cleaner than its evidence
supports. A member that was never selected, a member that was expected but not
produced, a member whose extraction failed, and a member deliberately retired
all used to look alike: each was an absence from one side, and an unmatched
name could be reported as a removal. A run that completed zero comparisons
could exit as a clean pass. ADR-065 separates these states; this plan tracks
what has landed and what remains.

## Goal & acceptance criteria

**Goal.** Every comparison run states which members it was asked to compare,
which it compared, and why any member was not compared. Absence is never read
as removal without inventory evidence, and a run with zero completed
comparisons never reads as a pass.

**Acceptance criteria.**

- A declared selection (`--select` / `--select-required`) is visible in the
  `--dry-run` plan before anything runs, naming what would be compared and
  which members are required versus optional.
- An unmatched member is reported as unmatched, not as removed, unless a
  complete inventory proves its removal (`proven_removed_members`).
- A declared component whose content cannot be reached is reported as
  `expected_not_produced` (an acquisition failure), distinct from a removal.
- `--on-incomplete-scope block` fails the run when a required member is
  missing, and the Action and aggregate report show the same completeness
  state (`SCOPE_INCOMPLETE` verdict tier, `scope_completeness` axis).
- A run that completed zero comparisons is an incomplete outcome, never a
  clean pass (see Remaining steps, item 3).
- Invocations without a selection behave as before: the narrow
  current-artifact inference remains the default.

## Current state (landed slices)

The vision defines slices S0-S4; its assessment records them as follows.

- **S0 — scenario table.** Executable scenario catalogue under `tests/scenarios/`.
- **S1 — selection and dry-run plan (landed 2026-09-06).**
  `abicheck/model/release_selection.py` (`ReleaseSelection`),
  `--select` / `--select-required` on the directory and package fan-out,
  `abicheck/workflows/release_plan.py` (declared-selection record and release
  plan), and `abicheck/frontends/cli/release_dry_run.py` (the "Comparison
  plan" preview). `MemberAcquisition.required` is the one schema addition
  (schema 1.1, additive).
- **S2 — acquisition states and the completeness axis (landed 2026-09-05).**
  `abicheck/model/scope_acquisition.py`, `abicheck/policy/scope_completeness.py`,
  `abicheck/workflows/release_scope.py`, `abicheck/report/comparison_scope.py`,
  and `--on-incomplete-scope warn|block`. `BundleFacts.degraded_members`
  marks stranded-library snapshots.
- **S3 — package component inventories (landed 2026-09-06).**
  `abicheck/model/package_inventory.py` (`PackageInventory`, which keeps
  declared, complete and unproduced components apart), built by
  `abicheck/extract/packages.py`'s `package_component_inventory` from
  `ExtractResult.container_complete`. `abicheck/policy/support_promise.py`
  adds the `--support-promise off|declared` field and the two
  `SUPPORT_PROMISE_COMPONENT_*` kinds, derived only from proven membership;
  `abicheck/workflows/release_support_promise.py` places them in the fan-out's
  `library_results`.
- **S4 — parity and deletion (landed 2026-09-06, except two items).**
  `_match_release_keys` returns only matched keys and maps; the set difference
  that once produced removals is deleted; the JSON `unmatched_old` /
  `unmatched_new` keys read the scope record. The silent canonical fallback
  went with `abicheck/product_baseline.py`. Action and aggregate parity came
  with S2.

Tests that pin this behaviour include `tests/test_release_scope_completeness.py`,
`tests/test_release_scope_bundle.py`, `tests/test_aggregate_scope_completeness.py`,
`tests/test_action_scope_verdict.py` and `tests/unit/workflows/test_consumer_scope.py`.

## Remaining steps

1. **Scalar and bundle operand convergence.** One live/stored driver and one
   evaluation, gate, report and dry-run path across all four operand shapes.
   The vision assigns this to `cli-cleanup-phase-two.md` (PR I); that plan is
   retired and its PR I remainder is re-homed (see [plans index](index.md)).
   The engine-side half is [ADR-061](../adr/061-responsibility-package-architecture.md)
   gap D, re-homed as [`one-comparison-product.md`](one-comparison-product.md)
   Phase 7d.
2. **Typed selection.** Carry the selection and expected inventory as fields of
   `CompareRequest` / `BundleCompareRequest`; today they reach the engine as
   CLI parameters. ADR-061 closure package 4 owns where the fields live; this
   workstream owns only what the states mean.
3. **Zero-comparison outcome.** Confirm the S2 "no comparison completed"
   outcome is reachable and rendered in JSON, Markdown and the Action verdict,
   with a test that a zero-comparison run is not a pass. The vision lists it
   under S2, but its S4 assessment does not record it as closed.
4. **Partial-directory guard.** Keep the rule that a populated directory is not
   a completeness proof (`ExtractResult.container_complete` is set only by
   archive extractors) covered by a test whenever the fan-out changes.

## Files & surfaces

- `abicheck/model/release_selection.py`, `abicheck/model/scope_acquisition.py`,
  `abicheck/model/package_inventory.py`
- `abicheck/policy/scope_completeness.py`, `abicheck/policy/support_promise.py`
- `abicheck/workflows/release_scope.py`, `abicheck/workflows/release_plan.py`,
  `abicheck/workflows/release_support_promise.py`
- `abicheck/report/comparison_scope.py`, `abicheck/frontends/cli/release_dry_run.py`
- `abicheck/policy/outcome.py`, `abicheck/policy/exit_decision.py`
- CLI: `compare` directory/package fan-out (`--select`, `--select-required`,
  `--on-incomplete-scope`, `--support-promise`, `--dry-run`)

## Tests

- Existing: the files listed under "Current state".
- New for remaining step 3: a zero-comparison fan-out run asserting a non-pass
  outcome in JSON and the Action verdict.
- New for remaining step 2: a typed-request round trip of the selection field,
  once the field exists.

## Effort & risk

**Effort:** L. **Risk:** medium. The largest open piece is operand convergence,
which touches every comparison entry point. The schema is additive so far; any
new field must keep `MemberAcquisition.required=True` as the default for
producers that predate the selection.

## Out of scope

- Redefining the completeness states beyond ADR-065 (the ADR owns them; this
  plan implements them).
- Release-history ordering and versioning policy: see [G48](g48-release-history.md).
- Analysis-evidence adequacy within a single member: see [G47](g47-analysis-assurance.md).
