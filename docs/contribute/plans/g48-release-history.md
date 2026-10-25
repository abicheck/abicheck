---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G48 — Release history and versioning policy (vision workstream B, ADR-066)

**Registry:** `UC-WF-release-history` (`partial`) · **Owner ADR:** [ADR-066](../adr/066-longitudinal-history-and-versioning-policy.md) · **Source:** [vision workstream B](vision-api-abi-evolution.md)

## Problem

A single pairwise comparison answers "does this release break that one?". It
does not answer "how did this symbol's compatibility evolve across releases",
"was this removal announced by a deprecation first", or "does the release
number match what the change set requires". abicheck has the pieces
(a per-release recommendation, persisted deprecation facts, release-labelled
snapshots) but no N-release view and no project-defined versioning policy
that decides whether a release is accepted. ADR-066 fixes the model; this plan
tracks the slices.

## Goal & acceptance criteria

**Goal.** A project can compare an ordered series of stored releases and see,
per public entity, when it was first observed, introduced, deprecated, removed
and reintroduced, with a project-defined versioning policy deciding whether a
release is accepted. Policy changes acceptance only; it never changes what was
observed (ADR-066, "Policy decides acceptance, not facts").

**Acceptance criteria.**

- `abicheck project history SNAPSHOTS... --format {json,text}` emits
  `first_observed`, `introduced`, `deprecated`, `removed` and `reintroduced`
  events, and reports a coverage gap where an intermediate release is missing
  rather than inferring absence.
- The three-release add, deprecate, remove sequence from ADR-066 is covered by
  a typed-API test and a CLI end-to-end test.
- A versioning policy declared under the `versioning:` namespace is resolved
  through ADR-049 D7's precedence, and it changes the accepted/not-accepted
  outcome without changing any observed event.
- Deprecation evaluation reads the stored `deprecated_fact` as-is; no second
  deprecation representation is introduced.
- A SemVer-shaped version label gets the strict check; a non-SemVer label is
  handled without a false "wrong bump" claim.

## Current state (landed slices)

- **Existing.** `abicheck/semver.py` (`recommend_release`, strict-SemVer table,
  SONAME action, `actionable` / `review` / `unavailable`); persisted
  deprecation attributes on `Function`, `Variable`, `RecordType` and `EnumType`
  (`abicheck/model/declarations.py`, `abicheck/model/entities.py`, stored since
  snapshot schema v40 via `abicheck/storage/fact_codec.py`); baseline tuples
  `channel × target × profile`; `AbiSnapshot.version`, `git_commit`, `git_tag`,
  `created_at`; storage v2 `PackageManifest` and `VariantRef`.
- **S0 — done.** Model trade-offs validated against real snapshots built from
  `examples/workflows/compare-release/{v1,v2}/mathutils.h`, with synthetic
  third and fourth releases. Recorded as a dated amendment in ADR-066
  (2026-09-06): correspondence-key narrowing, a bounded
  `DiffResult.confidence`-based absence proxy, a bounded SemVer-shape gap
  heuristic, and retention deferred to S4.
- **S1 — landed.** `abicheck/workflows/history.py` (`run_history_request`,
  `build_longitudinal_history`), composing the existing pairwise
  `checker.compare`. CLI `abicheck project history` in
  `abicheck/cli_project.py`, as a `project` subcommand under ADR-054's
  admission bar. Tests: `tests/test_workflows_history.py` and
  `tests/test_cli_project_history.py`.
- **S2 — recorded as implemented in ADR-066.** The `versioning:` `--policy`
  namespace, resolved through `abicheck/policy/versioning_policy.py`, with
  `abicheck/policy_file_versioning.py` and the
  `abicheck/report/versioning_policy_section.py` report section.
  **Discrepancy to resolve:** the vision's B section still lists the policy
  model and support/deprecation evaluation as missing. ADR-066's dated
  amendment is the newer record, so this plan follows it; confirm against
  the tests before closing S2.

## Remaining steps

1. **Full D2 correspondence algorithm.** S1 uses existing `EntityId` /
   `(kind, symbol)` correspondence. Implement overload disambiguation with
   corroboration as ADR-066 D2 describes, and emit the `changed` event that
   S1 does not yet produce.
2. **Scheme-derived release ordering.** S1 takes an explicit release order from
   the user. ADR-066 D4's version-scheme ordering is not implemented.
3. **Real version scheme.** Replace the bounded SemVer-shape heuristic with
   D4's scheme handling.
4. **Lifecycle over deprecation facts.** Evaluate support windows and
   deprecation transitions across releases using the stored facts, and feed
   the existing advice path.
5. **Suppression version window.** `version_range` does not exist in
   `abicheck/`. Decide whether a version window on suppressions is in scope
   for this workstream or belongs to the suppression owner.
6. **S3 CI publication and resolution** through the existing baseline channels
   (`channel × target × profile`).
7. **S4 timeline projections** through `ReportDocument`, bounded retention,
   and cached-comparison reuse under complete keys.

## Files & surfaces

- `abicheck/workflows/history.py`, `abicheck/cli_project.py`
- `abicheck/policy/versioning_policy.py`, `abicheck/policy_file_versioning.py`,
  `abicheck/report/versioning_policy_section.py`
- `abicheck/semver.py`
- `abicheck/model/declarations.py`, `abicheck/model/entities.py`,
  `abicheck/storage/fact_codec.py` (deprecation facts)
- `abicheck/model/acknowledgment_policy.py` (policy-file coupling, if the
  suppression window in step 5 is taken)

## Tests

- Existing: `tests/test_workflows_history.py`, `tests/test_cli_project_history.py`,
  `tests/test_semver_recommendation.py`.
- New for step 1: a case where two overloads share a name and must not be
  merged by name alone.
- New for step 2: a case where the explicit order and the scheme order differ,
  asserting the scheme order is used once implemented.

## Effort & risk

**Effort:** M for the remaining steps 1-3; L once S3 and S4 are included.
**Risk:** medium. A correspondence change alters which events are emitted, so
each change needs a golden-output review. Policy must stay separate from the
observed events.

## Out of scope

- The release fan-out's member selection and completeness: see [G46](g46-scope-completeness.md).
- Evidence adequacy within one comparison: see [G47](g47-analysis-assurance.md).
- Retiring the ADR-022 registry, which ADR-043 D4 confirmed as deleted and
  stays deleted.
