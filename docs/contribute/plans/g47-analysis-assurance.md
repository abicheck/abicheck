---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G47 — Analysis assurance and evidence adequacy (vision workstream E, ADR-063)

**Registry:** `UC-WF-analysis-assurance` (`partial`) · **Owner ADR:** [ADR-063](../adr/063-one-semantic-pipeline.md) (with ADR-028/049/050/064 amended by workstream E) · **Source:** [vision workstream E](vision-api-abi-evolution.md)

## Problem

A verdict can state "complete" or "no change" while the evidence behind it is
partial. The two cases that matter most are a detector that could not run and
read as clean, and a missing or failed input that feeds an absent value rather
than a failure. [Known gaps](../known-gaps.md) under the `false-clean` tag
record several concrete instances, for example a `debug = 1` DWARF build that
has no function types, so a return-type break reads `NO_CHANGE` with
`assurance: complete`. Workstream E makes each evidence shortfall visible on
the analysis-assurance record and keeps it from being read as a clean claim.

## Goal & acceptance criteria

**Goal.** Every verdict states the evidence it rests on. Missing evidence
lowers assurance and is reported; it never fabricates a break and never
upgrades to a clean compatibility claim.

**Acceptance criteria.**

- A detector that cannot run because its evidence is absent is reported as
  `not_evaluated` or `failed` on the assurance record, never as `NO_CHANGE`.
- A failed extractor (including a compiler probe) yields `FactStatus.FAILED`
  and makes the comparison refuse, rather than feeding an absent toolchain
  identity.
- Comparability is reported per dimension (`COMPARABILITY_DIMENSIONS`) in JSON,
  Markdown and HTML, so a partially trusted comparison is distinguishable from
  a fully trusted one.
- Contract-source conflicts (exported-but-undeclared, manifest narrowing,
  package claim versus contained binary) are recorded with provenance, and
  never change the verdict or exit code by themselves (ADR-067 "record before
  disposing").
- Each `known-gaps` entry tagged `false-clean` in the evidence area either has
  a regression test that proves the shortfall is reported, or is still listed
  as open.

## Current state (landed slices)

The "Existing" inventory in the vision already has `FactStatus` with six
states (`abicheck/model/availability.py`), `AnalysisAssurance`
(`abicheck/policy/analysis_assurance/`), the `--require-complete-analysis`
floor, `AnalysisPlan` pre-flight (`abicheck/workflows/plan.py`), depth floor
and ceiling (`abicheck/policy/depth_projection.py`), and castxml failures that
raise rather than return an empty surface.

- **S0 — capability table.** The requested-capability × availability ×
  input-type × policy × result table, mapped to owners.
- **S1 — no-DWARF, missing-header and failed-extraction semantics (landed,
  [#1099](https://github.com/abicheck/abicheck/pull/1099)).** Per-detector
  unverified-layout rows from `abicheck/policy/analysis_assurance/_layout.py`
  (`layout_unverified_detectors`, which names `dwarf`, `advanced_dwarf` and
  `layout_descriptor` when neither side carries DWARF), and `FAILED` toolchain
  identity from `abicheck/extract/toolchain_identity.py`, which
  `abicheck/comparability_profile.py` refuses on.
- **S2 — per-dimension comparability record (landed,
  [#1098](https://github.com/abicheck/abicheck/pull/1098)).**
  `DiffResult.comparability_assurance`, populated from
  `ComparabilityMismatch.dimensions` (`abicheck/policy/analysis_assurance/_comparability.py`),
  reaches JSON (schema 3.1), Markdown and HTML. Comparability refusal itself
  still yields one `kind`. Profile-delta explanation beyond the per-dimension
  record remains open.
- **S3 — multi-source contract conflicts with provenance (landed,
  [#1126](https://github.com/abicheck/abicheck/pull/1126)).**
  `abicheck/model/contract_conflicts.py`,
  `abicheck/policy/contract_conflict_detectors.py` (cases 1-2), and
  `abicheck/workflows/package_claim_conflicts.py` (case 3, kept in workflows
  for layering reasons). Surfaced as `DiffResult.contract_conflicts` (JSON
  schema 3.5, Markdown "Contract Source Conflicts"), outside `Change` and
  outside the verdict.
- **S4 — parity and retirement (not started).** Parity through scan, project,
  bundle, API, Action and report; retiring conflicting legacy decisions.

Tests that pin this behaviour include `tests/test_analysis_assurance.py`,
`tests/test_analysis_assurance_graph_completeness.py`,
`tests/test_analysis_assurance_depth_and_graph_overlap.py`,
`tests/test_comparability_dimensions.py`, `tests/test_contract_conflicts.py`
and `tests/test_comparability_gate.py`.

## Remaining steps

1. **Evidence-state vocabulary.** Add an `INCONSISTENT`/`CONFLICTING` fact
   status (none exists today), and separate "not requested" from "capped",
   which both currently collapse onto `NOT_COLLECTED`. Unify the two
   provider-status vocabularies (`FactStatus` and `EvidenceProviderStatus`).
2. **Per-detector failure.** `DetectorRegistry.run_all` has no per-detector
   `FAILED` state. Add one so a crashing detector cannot read as no change.
3. **Profile-delta explanation.** Explain a comparability mismatch beyond the
   per-dimension record, for example which profile field differs and why it
   matters.
4. **Ceiling coverage.** An out-of-band build or source pack can bypass the
   depth ceiling, and `dump` never applies it. Close both paths or record
   them as known gaps with an owner.
5. **Unfingerprinted pairs.** A GCC/Clang pair with a missing fingerprint on
   either side is compared silently. Report it as unverified on the
   comparability record.
6. **S4 parity** across scan, project, bundle, API, Action and report, and
   retirement of the conflicting legacy decisions the vision names.
7. **Known `false-clean` entries.** Work through the open entries in
   [known-gaps.md](../known-gaps.md) that the evidence model covers, for
   example the `debug = 1` function-type gap and the refused-clang-AST type
   pass, each with a regression test that exercises the bug class rather than
   the single reported input.

## Files & surfaces

- `abicheck/model/availability.py`, `abicheck/policy/analysis_assurance/`
  (`_layout.py`, `_comparability.py`, `_compute.py`)
- `abicheck/extract/toolchain_identity.py`, `abicheck/comparability_profile.py`
- `abicheck/policy/depth_projection.py`, `abicheck/workflows/plan.py`
- `abicheck/model/contract_conflicts.py`, `abicheck/policy/contract_conflict_detectors.py`,
  `abicheck/workflows/package_claim_conflicts.py`
- `abicheck/schemas/compare_report.schema.json` (comparability and contract-conflict fields)

## Tests

- Existing: the files listed under "Current state".
- Each remaining step adds a test that asserts the assurance record, not only
  the verdict, and that the shortfall is not read as clean.
- A schema bump is needed when a new fact status or record field reaches JSON;
  the schema-staleness test already covers that path.

## Effort & risk

**Effort:** L. **Risk:** medium-high for step 1, because a new fact status
reaches every consumer of `FactStatus`. Add it behind the existing registry
rather than as a parallel vocabulary.

## Out of scope

- Scope and member selection across a release: see [G46](g46-scope-completeness.md).
- Versioning policy and release history: see [G48](g48-release-history.md).
- A new `ChangeKind` for a fact that is merely unknown. Unknowns are reported
  on the assurance record, not as changes.
