---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Vision workstreams — visible, intentional, traceable API/ABI evolution

**Status:** Proposed — planning document. Nothing in this plan is
implemented; each workstream below records what *already exists* (verified
against the tree at `2f5ef696` on 2026-09-05, file references included),
what is missing, the ADR that owns the decision, the slices, and the
acceptance tests. The product decisions themselves are in the repository
root [`vision.md`](../vision.md); the technical decisions are
[ADR-065](../adr/065-comparison-scope-selection-and-completeness.md),
[ADR-066](../adr/066-longitudinal-history-and-versioning-policy.md), and
[ADR-067](../adr/067-change-intent-acknowledgment-and-disposition-audit.md)
(all Proposed) plus amendments to existing ADRs named per workstream.
**Origin:** maintainer vision discussion and the nine-prompt agent pack
prepared from it (2026-09-04), reconciled against the current tree.

## Problem

abicheck's compatibility engine is mature, but seven product rules the
vision states are not yet true of every path, and each failure mode has
the same shape: a configuration, a missing input, or a presentation choice
turns an observed change into an invisible one, or a missing artifact into
a fabricated finding.

| Rule (vision) | Where it fails today (verified) |
|---|---|
| Unselected/unproduced is not removed | Release fan-out: `removed = old − new` by filename stem feeds `--fail-on-removed-library` (`abicheck/cli_compare_release_helpers.py`, `_match_release_keys`) |
| Zero comparisons is not success | Same path: no matched pairs → warning, `NO_CHANGE`, exit 0 (`cli_compare_release_pairwise.py`) |
| Detected vs. allowed stay both visible | JSON suppression ledger drops the matching rule and reason (`abicheck/reporter.py`, `_suppressed_change_entry`); one-line/review views carry no suppression totals |
| A disabled detector is "not evaluated" | `DetectorRegistry.run_all` records `enabled=True, changes_count=0` for a detector that returned early (`abicheck/detector_registry.py`, `abicheck/diff_platform.py` `dwarf` detector) |
| Failure is never an empty surface | Stranded old-side library degrades to an ELF-only snapshot persisted into the baseline with a stderr line only (`abicheck/cli_compare_release.py`, `_resolve_stranded_library`) |
| Policy decides acceptance, never facts | `allow_public_break` removes the break from `changes` and the release recommendation silently reads "no bump needed" (`abicheck/semver.py`) |
| Optional inputs stay optional | Header-only project: no binary-less L2 operand; `dump_source_only()` discards `-H` (G45 assessment) |

## Goal & acceptance criteria

Each workstream lands as reviewable vertical slices — design, one useful
end-to-end behavior through a public entry point, migrations, deletion of
the replaced path — and reports completion only when the CLI, typed API,
Action, and every report projection agree. The cross-scenario table in the
"Tests" section is the shared definition of done; the per-workstream
acceptance lists are its detail.

## Design — workstreams

Sequencing (from the execution plan): **A** and **E** first, under one
integration owner for shared request/plan/outcome changes; then **C**'s
audit half with **G**'s first reporting slice; then **B**, **D**, **F** as
bounded slices; **G**'s later slices as upstream fields appear.

### A. Comparison scope, member selection, and completeness — ADR-065

**Existing.** Aggregate already has the whole model: `ExpectedTargets`
(required/optional), synthesized missing-cell reports, `OnMissingRequired`/
`OnUnexpectedTarget`, and a `finding_matrix` with an `undetermined` third
state (`abicheck/workflows/aggregate/{resolve,execute,matrix,gate}.py`).
Baseline-set resolution has a typed outcome vocabulary including
`ambiguous`/`wrong_profile`/`new_target`
(`abicheck/buildsource/baseline_set.py`, `actions/resolve-baseline`).
Multibuild variant pairing is exact-fingerprint, never a union, with
same-side collision detection (`abicheck/bundle_multibuild.py`).
`compare_product_directories` has identity-tiered pairing
(`abicheck/product_baseline.py`). Comparability refusal exists
(`abicheck/comparability.py`, `ScopeMismatchError`/`ProfileMismatchError`).
`RunOutcome`/`ExitDecision` carry compatibility, assurance, gate,
operational, lifecycle, coverage axes (`abicheck/policy/outcome.py`,
`exit_decision.py`, `exit_decision_precedence.py`).

**Missing.** No request type carries a selection or expected inventory;
no outcome axis for input completeness; the release fan-out's set
difference and `unmatched_old` naming; the canonical fallback's silent
non-pairing on ambiguity; `bundle_variants:`/`required:` with no
production caller and every capture stamping the default fingerprint;
package extraction without a component inventory (`abicheck/package.py`
returns directories); the Action's typed outcomes ending at the composite
boundary; the degraded stranded-library snapshot.

**Slices.** S0 executable scenario table (`tests/scenarios/`, existing
catalogue). S1 selection by identity/coordinates with a `--dry-run` plan
view, on the typed API and the release/bundle CLI. S2 acquisition states
and the completeness axis on `RunOutcome`/`ExitDecision`; `no comparison
completed` outcome; stranded-library persistence marked. S3 package
component inventories; support-promise findings under a contract-policy
field. S4 Action/project/aggregate parity; scalar/bundle operand
convergence as a slice of `cli-cleanup-phase-two.md` PR I/J; delete the
set-difference pairing and the silent canonical fallback.

**Deletion gates.** `_match_release_keys`'s set-difference removal path is
deleted in S4 once every removal finding flows from proven completeness;
`bundle_variants_config` either gains its consumer in S2 or is deleted in
S2 — not left as dead code.

### B. Longitudinal history and versioning policy — ADR-066

**Existing.** `abicheck/semver.py` (`recommend_release`, strict-SemVer
table, SONAME action, `actionable/review/unavailable`); a **persisted
deprecation attribute** — `Function`/`Variable`/`RecordType`/`EnumType`
carry `deprecated`/`deprecated_fact` (`abicheck/model/declarations.py`,
`entities.py`), stored since snapshot schema v40 (`storage/fact_codec.py`),
with the per-pair transition kinds (`*_deprecated_added|removed`,
header-AST only) derived from them; baseline
tuples `channel × target × profile` with an opaque `project_ref`;
`AbiSnapshot.version/git_commit/git_tag/created_at`, `dump_provenance`;
storage v2 `PackageManifest`/`VariantRef` with declared-vs-captured
coordinates (ADR-062); `EntityId`/`OccurrenceId`/`canonical_finding_id`;
`compute_snapshot_content_hash`. ADR-022's registry is confirmed deleted
(ADR-043 D4) and stays so.

**Missing.** Any N>2 comparison; any ordering of releases; a `versioning:`
config namespace; a history index and any *lifecycle evaluation* over the
stored deprecation facts (the attribute exists; nothing reads it across
releases — S1/S2 consume `deprecated_fact` as-is and introduce no second
deprecation representation); a version window on suppressions
(`version_range` does not exist in `abicheck/`).

**Slices.** S0 model trade-offs on real fixtures (three-release sequences
built from `examples/` cases); retention design. S1 offline history:
`N` user-supplied snapshots in, machine-readable events + coverage out,
through the typed API and one CLI surface chosen per ADR-054's admission
bar (an option or `project` subcommand, not a new root command). S2 the
versioning policy model in `policy/`, resolved through ADR-049 D7's
precedence; support/deprecation evaluation; integration with the existing
advice. S3 CI publication/resolution via the existing baseline channels.
S4 timeline projections through `ReportDocument`; bounded retention;
cached-comparison reuse under complete keys.

### C. Policy-disposition audit and change acknowledgment — ADR-067

**Existing.** One suppression application point
(`abicheck/checker.py`, `_filter_suppressed_changes`), one selector grammar
(`abicheck/policy/selectors.py`), `Suppression` fields (`reason`, `label`,
`expires`, `reachability`, `allow_public_break`, `finding_id`),
`DiffResult.suppressed_changes`, `SuppressionAudit`; `Change.reclassified_by`;
the `scope` (out-of-surface) block; `redundant_count`; contract coverage
failures structurally unsuppressible; `effective_config_digest` with policy
and suppression content hashes; `report_finding_id`/
`report_canonical_finding_id`.

**Missing.** Rule provenance in the JSON suppression ledger; a
disposition-keyed ledger for reclassified/reconciled changes; suppression
totals in the one-line and review-digest views; a `not_evaluated` detector
state; any acknowledgment concept beyond `allow_public_break` (which then
degrades the release recommendation silently); a unique-per-run,
backend-stable acknowledgment key; the suppression file path in the
report; base/head policy-delta analysis.

**Slices.** S1 (audit, first): raw-versus-effective counts and rule
provenance on native `compare`, every projection, with a
100-suppressed-removals fixture; `not_evaluated` in `DetectorRegistry`.
S2: bundle/consumer/aggregate parity; reclassification, scoping, and
disabled-upstream coverage. S3: acknowledgment records (YAML, same
loader), the additions review gate (`allow` default), shared record ids
with B. S4: policy-delta and suppression-growth warnings.

### D. Optional prebuilt-consumer lifecycle — amend ADR-005/047/052/057, extend G29/G30

**Existing (verified in code, beyond ADR-057's index row).** `--used-by`
static scoping (`abicheck/appcompat.py`: `parse_app_requirements`,
`scope_diff_to_app`, `check_appcompat`); `--required-symbol(s)`; the
consumer graph and join (`abicheck/impact/consumer_graph.py`); **use-case
manifests are implemented** (`abicheck/impact/use_cases.py`,
`use_case_impact.py`, `compare --use-cases`); Action inputs `used-by`/
`required-symbol(s)` and `actions/check-target`'s `app-consumer` kind;
no consumer code is ever executed (ADR-060 deferred; the only subprocess
adjacency is a demangler prewarm). `--used-by` currently *replaces* the
gate (`scoped_verdict`, `gate_scope="used_by"`, worst-app-wins) while the
full verdict survives as context.

**Missing.** A consumer input is a single binary path only — no manifest,
no digest, no platform/profile, no provider-baseline provenance; an
unreadable consumer is a hard error, not an advisory/required distinction;
no "N of M consumers affected" statement; no staging/caching of consumer
artifacts in the Action; runtime-trace ingestion unimplemented.

**Slices.** S1 consumer specification (identity, exact artifact/digest,
platform/profile, provider baseline known/unknown, source channel,
optional use-case manifest, `required`/`advisory`), resolved by the
existing loaders; local/prebuilt artifacts only; per-consumer
`confirmed/potential/unresolved` impact reported **beside** the global
contract status (the consumer result enriches, never overwrites — a
change from today's gate replacement, sequenced with a migration note);
real compiled consumer/provider fixture. S2 existing Actions
acquisition/publishing channels; exact-version selection; missing
advisory/required handling. S3 declared source/use-case enrichment with
coverage-qualified reports. S4 separately designed, opt-in compile/link/
runtime validation with its own execution design review — never implied
by S1–S3, and not a reauthorization of ADR-060.

### E. Evidence adequacy, contract-source conflicts, cross-profile comparison — amend ADR-028/049/050/063/064

**Existing.** `FactStatus` with six states (`PRESENT`, `PARTIAL`,
`NOT_COLLECTED`, `UNSUPPORTED`, `FAILED`, `NOT_APPLICABLE`;
`abicheck/model/availability.py`) and the fact registry (ADR-063 Phase 5);
`AnalysisAssurance` per-axis statuses and `--require-complete-analysis`
floor (`abicheck/analysis_assurance.py`); `AnalysisPlan` pre-flight
(`abicheck/workflows/plan.py`); depth floor and ceiling
(`enforce_requested_depth`, `abicheck/policy/depth_projection.py`);
castxml failures raise, never return an empty surface
(`abicheck/dumper_castxml_probe.py`); `PUBLIC_NOT_EXPORTED` (declared but
not exported, L4-gated); ADR-049's `EvidenceSearchRecord` statuses;
comparability refusal on differing `compiler_family` when both sides
carry a profile fingerprint; the G13 arch guard; G34's producer/consumer
toolchain split; `aggregate`'s per-profile reconciliation with
`undetermined`.

**Missing.** No `INCONSISTENT`/`CONFLICTING` fact status, and "not
requested" vs "capped" collapse onto `NOT_COLLECTED`; two provider-status
vocabularies (`FactStatus` vs `EvidenceProviderStatus`) not unified;
no per-detector "layout unverified" row when both sides lack DWARF (the
`dwarf` detector reads `enabled=True, 0`); `DetectorRegistry.run_all` has
no per-detector `FAILED`; a compiler-probe failure feeds an *absent*
toolchain identity rather than `FAILED`; no reverse declared/observed
detector (exported but undeclared) and no manifest-narrowing detector;
`configuration_coverage` always `NOT_STARTED`; a GCC/Clang pair with a
missing fingerprint on either side is compared silently; comparability
yields one `kind`, not a per-dimension record; an out-of-band build/source
pack can bypass the depth ceiling; `dump` never applies the ceiling.

**Slices.** S0 the requested-capability × availability × input-type ×
policy × result table mapped to owners (this section's seed). S1
no-DWARF/missing-header/failed-extraction semantics on binary and snapshot
paths: per-detector `not_evaluated`/`failed`, unverified-layout rows,
`FAILED` toolchain identity. S2 per-dimension comparability record and
profile-delta explanation; preserve already-known changes through an
incomplete later stage. S3 multi-source contract conflicts with
provenance (exported-but-undeclared, manifest narrowing since baseline,
package-claim vs. contained-binary). S4 parity through scan/project/
bundle/API/Action/report; retire conflicting legacy decisions.

### F. Header-only comparison; bounded static-archive investigation — revise G4, extend G45

**Existing.** `dump_source_only()` (L3–L5 only, discards `-H`);
castxml/clang L2 backends behind one parser surface; per-side
defines/includes/dialect and the comparability profile; G45's assessment
that `project_targets.py` hard-requires `binary_pattern` and `compare.py`
has no header-only operand shape. G4 still sketches a flat
`dumper_libclang.py`, writes to the former `model.py`, and a `--header-ast`
selector — historical, superseded by ADR-061/063 and the existing
`--ast-frontend` selector (see the replan note at the top of G4).

**Missing.** A binary-less L2 operand through the common typed pipeline;
honest `NOT_APPLICABLE` L0/L1 semantics for that task; the deeper
macro/inline/template evidence beyond what L4 already gives.

**Slices.** S1 route header-only inputs through `DumpRequest`/
`CompareRequest` with explicit parse context (no compile database
required, no synthesized binary); exercise unchanged headers, removed
declaration, added API, changed enum/constant, signature change,
access/qualifier/default-argument change; emit source-compatibility
findings, versioning advice, scope, and unsupported-capability rows. S2
extend only demonstrably missing macro/inline/template capability;
record frontend differences rather than switching backends silently.
Fixture consumers compiled against old/new headers are test oracles only
(ADR-060 stays deferred). **Static archives**: a separate, lower-priority
investigation note (full rebuild vs. relinking precompiled objects,
archive members, thin archives, LTO objects, import libraries vs. static
libraries, what the current object readers retain) delivering feasible
questions, evidence requirements, and failure cases — no change to archive
acceptance or defaults, G8's registry state unchanged until a separately
scoped decision.

### G. Surface-first reports and cross-scenario acceptance — extend ADR-036/042/061/064

**Existing.** `ReportDocument` as a frozen root with renderer-owned
ordering; verdict-first headline in every view; additions itemized only
inside severity groups; PR comment and review digest with trailing
suppression/out-of-surface notes; `--report-mode`, `--show-only`
(display-only, exit unaffected), `--profile quick` one-line;
`detectors[]` with `coverage_gap`.

**Missing.** A "what changed / review actions" surface-first section; a
scope/selection section; the raw-versus-effective row in compact views;
grouped additions/removals/modifications with inspectable old/new
declarations; component/dependency impact; lifecycle timelines (needs B).

**Report invariants (the facts every view must carry; a renderer never
infers them — `abicheck/report/AGENTS.md` points here):**

- Compatible additions are visible changes: a compatible run still
  itemizes what was added; "0 breaking" is not "nothing happened".
- Raw versus effective totals: every view — compact, review digest,
  one-line, PR comment included — carries the detected total, the effective
  (gating) total, and the per-disposition counts with rule provenance.
  Collapsing detail is fine; dropping these counts is not.
- Qualified uncertainty: unavailable, unsupported, not applicable, not
  requested, and failed evidence render as distinct states; a disabled
  detector reads as *not evaluated*, never as zero findings.
- Global versus consumer results: a known consumer's impact enriches the
  report beside the global contract status, never replaces it, and one raw
  change is counted once however many consumers it affects.
- Scope and selection are stated: which members/variants were selected,
  out of scope, or expected but missing, and why.
- Rendering never changes a gate: report profiles, modes, and `--show-only`
  reorder or hide detail; they cannot alter a verdict, disposition, exit
  code, or coverage contribution.

**Slices.** S1 surface-delta and raw/effective/audit summary on existing
reports using C-S1's fields. S2 scope/completeness (A), consumer (D), and
versioning (B) blocks as the upstream typed fields land. S3 history and
relationship visualization from canonical events — HTML/Markdown/JSON
only, text alternatives and non-color status labels required, every
node/edge/count mapped to a recorded fact. S4 cross-scenario public-
workflow validation over the existing fixtures and harnesses, including a
real Action run in an authorized lab workflow where available; any item
that cannot be executed is marked unverified in the receipt.

## Files & surfaces

Owners per ADR-061; new code goes to the target package, never a new root
`*_helpers.py` family:

- `model/` — acquisition state, selection record, lifecycle event,
  disposition, acknowledgment record, `FactStatus` extension.
- `workflows/` — scope resolution in the plan; history assembly;
  completeness on `RunOutcome`.
- `policy/` — versioning policy, acknowledgment matching, additions gate,
  disposition audit, per-dimension comparability.
- `storage/` — history index, member status in `PackageManifest`.
- `report/` — new document sections and projections.
- `frontends/` — request fields and Action inputs, one resolution.
- Schemas: additive `compare_report`/`ScanResult`/aggregate bumps per
  slice; `.abicheck.yml` namespaces (`versioning:`, acknowledgment, review
  gate) registered with the config generator and `topics.yaml` in the
  same slice that implements them.

## Tests

Shared cross-scenario acceptance (each through CLI, typed API, and the
Action where applicable; live and stored operands; scalar and one-member
package):

| Scenario | Essential assertion |
|---|---|
| Bare binary | Valid; missing package metadata is not an error |
| Binary + headers, no DWARF | Useful analysis; layout limitation explicitly scoped |
| Scalar vs. one-member package | Same applicable findings and decisions |
| Multi-library package | Internal relationships checked without hiding global changes |
| One candidate / multi-variant baseline | Selected match only; unrelated variants out of scope |
| Expected CI artifact absent | Incompleteness warning or configured gate, not a removal |
| Confirmed component/support retirement | Contract change with inventory evidence |
| 100 suppressed deletions | Counts and rule provenance visible despite a passing gate |
| Acknowledged break | Still incompatible; acceptance explicit |
| Unacknowledged additions | Optional review policy, never a reclassification |
| Relaxed versioning | Same observed incompatibilities; different acceptance |
| Missing history | Unknown interval / first observed, not invented continuity |
| Optional prebuilt consumers | No package-manager prerequisite; no execution |
| Header-only | Real source checks; no fake binary or complete-ABI claim |
| Cross-profile comparison | Dimension-specific facts and limitations |

Properties (state once in `tests/regressions/manifest.py` as bug classes
when the first slice lands): raw-change conservation under policy/view
changes; cardinality invariance; pairing order-independence; unmatched
never implies removed without completeness proof; unavailable ≠ empty;
totals reconcile across views.

## Example fixtures

Real compiled fixtures from `examples/` (a scalar pair, a bundle case, an
`--used-by` consumer case, a stripped binary) plus controlled mutations from
`tests/_detector_mutations.py`; three-release sequences assembled from
existing example versions for B; a synthetic twelve-variant baseline set
for A built with the existing baseline-set writer.

## Effort & risk

XL overall, sequenced as independent M/L slices. Risks: shared
request/plan/outcome types touched by A, D, and E at once (mitigated by
one integration owner and an agreed schema before parallel coding);
acknowledgment-key stability depends on ADR-063 Phase 2B identity work;
behavior corrections (release fan-out exit 8, `allow_public_break`
recommendation, `--used-by` gate replacement) need migration notes and a
changelog fragment each.

## Boundaries

Not in scope: a generic release-management platform; a hosted history
service or a revived baseline registry; a second suppression grammar, gate
algorithm, request family, snapshot format, or report framework;
package-manager integrations as a prerequisite for consumers; automatic
consumer execution; changing archive acceptance; renaming L0–L5; touching
the repository's own merge policy.
