# ADR-065: Comparison Scope, Member Selection, and Input Completeness

**Date:** 2026-09-05
**Status:** Proposed — not implemented. Design record for the vision's
"partial matrices" and "scope-sensitive analysis" decisions
(`vision.md`); no code, schema, CLI flag, or default changes with this
document. Implementation is sequenced in
[`plans/vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
(workstream "Comparison scope and completeness"), which also carries the
existing-versus-missing assessment this ADR's Context summarizes.
**Decision maker:** maintainer (product decision recorded in `vision.md`);
technical sign-off pending review of this document.

## Context

abicheck already compares one artifact against another, a release directory
against another, a package against another, a candidate against a
multi-profile baseline set, and folds a CI matrix into one gate. Each of
those paths grew its own answer to three questions this ADR separates:

1. **What is the analysis boundary?** One artifact, a package's component
   set, a declared release matrix.
2. **Which members were expected, which were actually supplied, and which
   were compared?** The difference between *unselected*, *expected but not
   produced*, *failed*, *out of scope*, and *deliberately retired*.
3. **Did the project's support promise change?** A retired platform or a
   component confirmed absent from a complete inventory is a contract
   change; an artifact a partial run never supplied is not.

The current code answers these unevenly (file references are from the
2026-09-05 assessment; see the plan for the full table):

- The release fan-out pairs libraries by filename stem and defines
  `removed = old_keys - new_keys`
  (`abicheck/cli_compare_release_helpers.py`, `_match_release_keys`), then
  reports that set under the JSON key `unmatched_old` **and** feeds it to
  `--fail-on-removed-library`'s exit `8`. A name-normalization miss, a
  SONAME bump, a failed extraction, and a genuine deletion are one state.
- `compare_product_directories` (`abicheck/product_baseline.py`) has richer
  pairing (exact path, then an ambiguity-guarded SONAME/case-folded
  fallback), but an *ambiguous* group is silently left unpaired and then
  surfaces as `BUNDLE_LIBRARY_REMOVED` plus `BUNDLE_LIBRARY_ADDED` — an
  ambiguity reported as a removal.
- A release run with zero matched pairs appends a warning and keeps
  `worst_verdict = "NO_CHANGE"`, exit `0` (`cli_compare_release_pairwise.py`).
- Multibuild variant pairing is exact-fingerprint-only and deliberately
  never a union (`abicheck/bundle_multibuild.py`, G38 Phase 3), with
  same-side collisions detected — but every capture path stamps the default
  fingerprint, `bundle_variants:` (`abicheck/bundle_variants_config.py`)
  has no production caller, and a declared-but-never-captured `required`
  variant is invisible.
- The aggregate workflow is the one place the model this ADR wants already
  exists end to end: `ExpectedTargets` with required/optional members,
  synthesized `TargetReport`s for expected-but-missing cells,
  `OnMissingRequired`/`OnUnexpectedTarget` gates, and a `finding_matrix`
  with an explicit *undetermined* third state
  (`abicheck/workflows/aggregate/`). It is aggregate-only; no per-library
  or per-variant scope consumes it.
- The Action's baseline resolution has a typed outcome vocabulary
  (`resolved / not_found / ambiguous / wrong_profile / stale_schema /
  incompatible_evidence / wrong_project_ref / stale_generation /
  new_target`, `abicheck/buildsource/baseline_set.py`) that stops at the
  Action boundary: a `new_target` or dry-run "baseline unavailable" run is a
  green check with no comparison performed.
- No typed request (`CompareRequest`, `BundleCompareRequest`, `ScanRequest`,
  `AnalysisPlan`) carries an expected inventory or a member/variant
  selection; no `ExitReason`/`RunOutcome` axis expresses "the inventory I
  was asked to cover was not fully covered".
- Package extraction (`abicheck/package.py`) returns directories, never a
  declared component inventory, so a component the extractor failed to
  unpack and a component the package no longer ships are indistinguishable.
- A stranded old-side library in the release fan-out degrades to an
  ELF-only `AbiSnapshot` written into the baseline with only a stderr line
  (`cli_compare_release.py`, `_resolve_stranded_library`) — a degraded
  capture persisted as if complete.

The consequence the vision names directly: a developer who builds one
Linux/GCC variant locally and compares it against a twelve-variant baseline
can be told eleven platforms were removed; a CI matrix whose macOS job
failed can be read as a retired macOS promise; and a release whose one
library failed to extract can pass.

## Decision

### D1 — Four concepts, four representations

The following are distinct and are never collapsed onto one field:

| Concept | Meaning | Where it lives |
|---|---|---|
| **Analysis boundary** | What the user asked to analyze: one artifact, a package/component set, or a declared release matrix | The typed request (existing `CompareRequest`/bundle/scan request types, extended — never a new request family) |
| **Selection** | Which members/variants the user or the resolved plan chose for *this* run, and any explicitly authorized cross-profile pairing | The resolved plan (`AnalysisPlan`/run plan), with the selector and its provenance recorded |
| **Expected inventory** | Which members this run should have produced/consumed, with provenance (a resolved project plan, a package's own manifest, a trusted complete inventory) | The resolved plan; absent for a bare artifact comparison |
| **Acquisition state** | Per expected member: `available`, `expected_not_produced`, `failed`, `not_supplied`, `unsupported`, `out_of_scope` | The typed result, per member, separate from any policy verdict |

A **support-promise change** (a retired platform, a component confirmed
absent from a *complete* inventory) is a fifth thing: a contract change
with evidence, emitted as a finding under a configurable policy, never
inferred from acquisition state alone.

### D2 — Unmatched is not removed

A member present on one side and absent on the other is `unmatched` with a
recorded reason. It becomes a *removal* finding only when the selected
domain's completeness is proven on the side that lacks it: the new side's
inventory is complete for the boundary (a full package inventory, a matrix
whose expected members all resolved, or an explicit user statement that
the new side is complete). The rule is symmetric: a new-only member becomes
an *addition* finding only when the **old** side's inventory is proven
complete, since a partial old input cannot prove the member was absent from
the old release. In a partial or selected run, an unmatched member on
either side is `out_of_scope` and contributes nothing to the verdict. The
existing `BUNDLE_LIBRARY_REMOVED`/`_ADDED` kinds and
`--fail-on-removed-library` keep their meaning and become consumers of the
proven-complete state of the relevant side rather than of a raw set
difference.

### D3 — Ambiguity is a diagnostic, never a guess

When a candidate could pair with more than one baseline member (or vice
versa), the run reports an actionable `ambiguous` selection diagnostic
naming the candidates and the coordinates that would disambiguate, and does
not compare. Neither "first", nor "latest", nor an arbitrary canonical
fallback is chosen silently. This generalizes the aggregate/baseline-set
`ambiguous` outcomes and the multibuild same-side-collision error to every
pairing site, including `compare_product_directories`' canonical fallback.

### D4 — Pairing is by identity and coordinates

Members pair on a stable target identity plus variant coordinates (the
existing `VariantRef` declared/captured coordinates, `variant_fingerprint`,
profile fingerprint, and the baseline-set `channel × target × profile`
tuple), not on filenames alone. Filename-based matching remains a
last-resort tier that is reported as such. A cross-profile pairing
(old GCC, new Clang; old x86-64, new AArch64) is allowed only when
explicitly requested, and is then analyzed dimension by dimension per the
comparability contract (ADR-050, extended by the evidence-adequacy
workstream) — never merged into one profile to manufacture coverage.

### D5 — Many baseline members, one candidate selects

"Many baseline members, one candidate" means *select the matching member*.
It is not a Cartesian product and not a union. An explicitly requested
check of one candidate against several supported baselines (a support
window) is a different task that produces independently attributed
comparisons, each with its own selection record, never merged evidence.

### D6 — Completeness is a run outcome with a default of warn

An expected member that was not produced or not supplied is an
**incompleteness** signal on the run: a warning by default, configurable to
block through the existing outcome/exit machinery (a new axis on
`RunOutcome`/`ExitDecision` beside compatibility, assurance, operational,
and coverage — ADR-064's precedence, extended, not a second gate scheme).
It never becomes an ABI finding. A **retired support promise** is
configured separately (a contract-policy field), so a project can say
"macOS is no longer supported" and have that evaluated as a contract
change, while a missing macOS job stays an incomplete run.

### D7 — Zero completed comparisons is never success

A run whose selected scope produced no valid comparison reports
`no comparison completed` as its operational outcome, whatever the
completeness policy says. A permissive completeness setting can downgrade
*missing members* to a warning; it cannot turn *nothing compared* into
"compatibility checked". This applies to the release fan-out, the Action's
`new_target`/dry-run baseline-unavailable paths, and any future selected
comparison alike.

### D8 — Failed extraction is a failed member, persisted as such

A member whose extraction failed is `failed` in the acquisition record and
an operational error in the outcome. It is never written into a baseline or
`BundleFacts` document as a degraded snapshot without an in-band marker;
the storage document carries the member's status (storage v2's explicit
fact availability, ADR-062) so the next run reads *missing data*, not an
impoverished old side.

### D9 — Scope inference is conservative

The OLD input being a project snapshot or baseline set does not by itself
imply a full-release task. The run infers a narrow current-artifact task
when the selection is unambiguous (one candidate, one matching member) and
requires a minimal selector when it is not. Every run exposes a plan view
(the existing `--dry-run` shape, ADR-043/054) listing each expected member,
its match or exclusion, and the reason.

## Consequences

- Scalar comparisons are untouched: with no inventory and no selector, the
  expected set is the one pair, acquisition is `available`, and no new
  field is populated. The one-member package path and the scalar path must
  produce the same applicable findings (an executable invariant).
- The release fan-out's `unmatched_old` JSON key becomes what its name
  says; the removal finding and exit `8` require D2's completeness proof.
  This is a behavior change for a partial release directory that today
  exits `8`; it is a *correction*, sequenced with a migration note.
- `bundle_variants:`'s `required:` field, currently unread in production,
  gets its consumer through D6, or is deleted — the plan decides which,
  and the deletion gate is explicit.
- Report projections (ADR-036/061) gain a scope/selection section and a
  per-member acquisition table; the compact and one-line views must carry
  the incompleteness and no-comparison notices (the reporting workstream's
  rule).
- The Action's typed baseline outcomes propagate to the run outcome instead
  of ending at the composite step.

## Relationship to existing decisions

Extends, does not replace: ADR-002/006/023 (release/package/bundle
comparison), ADR-047/054 (project lifecycle and `project plan`), ADR-050
(comparability), ADR-055 (typed requests), ADR-062 (storage v2's fact
availability), ADR-063 (`AnalysisPlan`, `RunOutcome`), ADR-064 (exit
precedence). G30/G34/G38 own the profile/variant machinery this reuses.
Nothing here revives the retired baseline registry (ADR-022/043 D4) or a
settable exit-code scheme (ADR-064).

## Implementation slices

See the plan for status. S0: this ADR plus an executable scenario table.
S1: identity-and-coordinates member selection with a plan preview, through
the typed API and CLI. S2: expected/observed inventory, acquisition states,
and the completeness axis on `RunOutcome`/`ExitDecision`. S3: package
component inventories and support-promise findings under contract policy.
S4: Action/project/aggregate parity, scalar-versus-bundle operand
convergence as a slice of the existing convergence plans, and deletion of
the replaced set-difference and canonical-fallback paths.

## Acceptance tests (contract)

- Exact member partitions per run (`available`, `expected_not_produced`,
  `failed`, `not_supplied`, `unsupported`, `out_of_scope`) are pairwise
  disjoint and sum to the expected set.
- One candidate against a twelve-variant baseline yields one comparison
  and eleven `out_of_scope` members, zero removals.
- Adding an unrelated baseline variant cannot change a selected
  comparison's findings; input order cannot change pairing.
- Two plausible baseline members produce an `ambiguous` diagnostic and no
  comparison.
- A complete old/new package with a library absent from the new inventory
  yields a component-removal finding with inventory evidence, without a
  sibling consumer.
- A partial old input against a complete new package yields no addition
  finding for a new-only member (old-side completeness unproven); the same
  pair with a complete old inventory does.
- A declared matrix with a failed expected member yields incompleteness
  (warn by default, block when configured) and no invented API deletion.
- A run with zero valid comparisons reports `no comparison completed`
  under every completeness policy.
- Replacing unavailable evidence with empty evidence fails a test.
- Binary/binary, binary/snapshot, snapshot/binary, snapshot/snapshot, and
  the package counterparts all go through the public paths (CLI, typed API,
  Action) and agree.
