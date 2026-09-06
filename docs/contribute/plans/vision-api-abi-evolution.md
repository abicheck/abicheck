---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Vision workstreams — visible, intentional, traceable API/ABI evolution

**Status:** Proposed — planning document. One slice has landed since it was
written (**workstream C's S1**, the scalar policy-disposition audit — see that
section); everything else here is unimplemented. Each workstream below records
what *already exists* (verified
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

**Still missing after S1-S4** (the list above is the 2026-09-05 assessment,
kept verbatim; this is what it has *not* closed): `compare_product_
directories`' canonical fallback still leaves an ambiguous group silently
unpaired rather than emitting D3's `ambiguous` diagnostic
(`abicheck/product_baseline.py`), and no `CompareRequest`/`BundleCompareRequest`
carries a selection or expected inventory as a typed field -- the release
fan-out's selection and inventory reach the engine as CLI parameters, which
is why scalar-versus-bundle operand convergence stays with
`cli-cleanup-phase-two.md`'s PR I rather than being solved twice here.

**Slices.** S0 executable scenario table (`tests/scenarios/`, existing
catalogue). S1 selection by identity/coordinates with a `--dry-run` plan
view, on the typed API and the release/bundle CLI — **landed 2026-09-06**:
`model/release_selection.py`'s `ReleaseSelection` (`{canonical key:
required}`, mirroring `workflows.aggregate.resolve.ExpectedTargets`'s own
required/optional shape rather than inventing a second one), `--select`/
`--select-required` on `compare`'s directory/package fan-out (forwarded
through `_dispatch_release_compare` to `compare_release_cmd`),
`workflows/release_plan.py`'s `build_declared_selection_record` (a
declared-selection `ScopeAcquisitionRecord` builder, called from
`cli_compare_release.py` instead of `_match_release_keys`'s inferred
partition whenever a selection is given — D9's narrow inference is skipped
entirely in that case) and `build_release_plan`/
`build_release_plan_from_directories` (the `--dry-run` preview,
rendered by `frontends/cli/release_dry_run.py`'s new "Comparison plan"
section). `MemberAcquisition.required` (schema 1.1, additive) is the one
schema change this slice makes: `False` only for a member an explicit
selection declared optional, so its absence never trips
`ScopeAcquisitionRecord.is_incomplete` the way a required member's does —
every pre-S1 producer leaves every member `required=True` by default,
unchanged. D9's narrow current-artifact inference stays the default when no
selection is given. `bundle_variants_config` (see the deletion gate below)
was deleted in this slice, not given a consumer: its `required:` field is
about multibuild *variant* fingerprint identity, a different axis from a
release's *member* selection, and no capture pipeline can tag a variant
name yet (that module's own long-standing gap) — wiring a consumer for it
would have been the parser-only slice this file's own "Finish the
workflow" principle warns against. S2 acquisition states
and the completeness axis on `RunOutcome`/`ExitDecision`; `no comparison
completed` outcome; stranded-library persistence marked — **landed
2026-09-05, ahead of S1** (it has the smallest surface and fixes the two
worst behaviors with no new request field): `model/scope_acquisition.py`,
`policy/scope_completeness.py`, `workflows/release_scope.py`,
`report/comparison_scope.py`, `--on-incomplete-scope warn|block` on the
release fan-out, `BundleFacts.degraded_members`. S3 package
component inventories; support-promise findings under a contract-policy
field — **landed 2026-09-06**: `model/package_inventory.py`'s
`PackageInventory`/`PackageComponent` (declared components, an explicit
`complete` flag, and a separate `unproduced` map — the three answers this
slice exists to keep apart), built by `package.package_component_inventory`
from `ExtractResult.container_complete`, which every archive extractor now
sets and `DirExtractor` deliberately does not: an archive extractor unpacks
its container in full or raises, so a successfully extracted package
operand is a D2 completeness proof, while a directory a user may have
populated partially is not. Threaded through
`cli_compare_release_matrix._prepare_compare_release_inputs` into
`release_scope.release_inventory_evidence` (a new `PROVEN` source beside
S2's stored `inventory_complete` assertion) and into
`build_release_scope_record`/`build_declared_selection_record` as
`old_unproduced`/`new_unproduced`, which gives `EXPECTED_NOT_PRODUCED` —
reserved by S2 with no producer — a real one: a declared component whose
content the extracted tree cannot reach (a dangling link, an unreadable
file) is an *acquisition failure*, never an absence the other side's proof
may read as a removal. The support-promise half is
`policy/support_promise.py` (D1's fifth concept: the `--support-promise
off|declared` contract-policy field, `off` by default so every existing
invocation is unchanged) plus the two new `ChangeKind`s
`SUPPORT_PROMISE_COMPONENT_RETIRED`/`_INTRODUCED`, derived *only* from
`proven_removed_members`/`proven_added_members` and carrying D2's
completeness receipt in their `old_value`/`new_value`;
`workflows/release_support_promise.py` places each one in the fan-out's own
`library_results` so the existing verdict fold, severity aggregation,
renderers and disposition audit see it without a parallel path.
Deliberately not a duplicate of `BUNDLE_LIBRARY_REMOVED`, which fires only
when a surviving sibling imports the missing library. **One consequence
worth stating: `--fail-on-removed-library`'s exit `8` is reachable again
for a package-archive pair** (S2 had left it reachable only for a stored
`ProjectSnapshot`); a directory pair still cannot reach it. S4
Action/project/aggregate parity; scalar/bundle operand
convergence as a slice of `cli-cleanup-phase-two.md` PR I/J; delete the
set-difference pairing and the silent canonical fallback — **landed
2026-09-06 except the two items named below**: `_match_release_keys` now
returns only the matched keys and the two maps, and its last two consumers
were migrated — the JSON `unmatched_old`/`unmatched_new` keys (which already
read `unmatched_names(record)` when a record existed, and now report `[]`
rather than re-deriving a difference when one does not) and the fan-out's
stderr notices, which moved to `report/comparison_scope.release_scope_
warnings` and are emitted after the record exists. That move is what lets
one line distinguish a *proven* removal (naming the inventory that proved
it) from an unmatched member, an `expected_not_produced` one, and an
`out_of_scope` one the set difference reported identically. The
Action/aggregate parity this slice names had already landed with S2
(`action/run.sh`'s `SCOPE_INCOMPLETE` verdict tier, aggregate report schema
1.8's `scope_completeness` axis), so S4 added no second copy.

**Still open in S4, deliberately.** Two items keep their existing owners
rather than being duplicated here: the **scalar/bundle operand
convergence** is `cli-cleanup-phase-two.md`'s PR I (live/stored driver plus
one evaluation/gate/report/dry-run path across all four operand shapes),
still open there and explicitly cross-referenced by that plan's own row as
overlapping this workstream; and **the silent canonical fallback** in
`compare_product_directories` (`abicheck/product_baseline.py`) is not yet
D3's `ambiguous` diagnostic — the deletion gate below covers
`_match_release_keys`'s set difference, and turning an ambiguity into a
refusal-to-compare in the whole-product path is a behaviour change of its
own that needs its own slice and migration note.

**Deletion gates.** `_match_release_keys`'s set-difference removal path was
**deleted in S4** (2026-09-06), once every removal finding flowed from proven
completeness: S2 had already stopped exit `8`, the verdict bump, and the
Markdown/PR-comment "removed" sections from reading it, and S4 migrated the
two remaining readers that still did so by name (the JSON `unmatched_old`
key and the stderr warnings). `bundle_variants_config` — **deleted
in S1** (see above) rather than given a consumer.

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

**Existing.** One selector grammar (`abicheck/policy/selectors.py`) but
**four suppression application points** that S1 must enumerate and either
converge or cover individually — `post_processing.ApplySuppression.apply()`
(the main change list), `checker._filter_suppressed_changes()` and
`checker._filter_pattern_synthetic()` (separately produced changes), and
`appcompat.py`'s consumer-overlay pass over `missing_symbols` — since a
ledger fed from one helper alone would omit most ordinary suppressions and
break raw/effective reconciliation; `Suppression` fields (`reason`, `label`,
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

**Slices.** **S1 landed** (scalar `compare` only): one conserved ledger
(`abicheck/policy/disposition_ledger.py`) behind all five application points
— the four below plus `post_processing._merge_findings_respecting_suppression`
— raw-versus-effective counts and rule provenance in every scalar projection
(`abicheck/report/disposition_audit.py`, report schema 2.50), a
`not_evaluated` detector state, and `semver.recommend_release` reading the
conserved delta. S2-S4 unimplemented. S1 (audit, first): inventory the four application points
above and route each through one ledger-recording primitive (converging
them where the call shapes allow, covering each explicitly where they do
not) so the raw-versus-effective totals reconcile by construction; then
raw-versus-effective counts and rule provenance on native `compare`, every
projection, with a 100-suppressed-removals fixture; `not_evaluated` in
`DetectorRegistry`.
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
adjacency is a demangler prewarm). **`--used-by`/`--required-symbol(s)`
no longer replace the gate (S1's gate-enrichment slice, landed)** — see
below; S1's consumer-*specification* half (identity/digest/platform/
profile/provider-baseline) remains open, tracked under "Missing".

**Missing.** A consumer input is a single binary path only — no manifest,
no digest, no platform/profile, no provider-baseline provenance; an
unreadable consumer is a hard error, not an advisory/required distinction;
no "N of M consumers affected" statement; no staging/caching of consumer
artifacts in the Action; runtime-trace ingestion unimplemented.

**Slices.** **S1's gate-enrichment slice (landed).** A supplied consumer's per-`confirmed/
potential/unresolved` impact (`scoped_verdict`/`scoped_exit_code`/
`used_by`/`required_symbol_contract`, still computed by
`abicheck/appcompat.py`'s `scope_diff_to_app`/
`scope_diff_to_required_symbols`) is now reported **beside** the global
contract status, never in place of it: the compare command's exit code
and JSON `verdict`/`severity`/`run_outcome`/`summary` always describe the
full-library result, exactly as an unscoped run would, and a supplied
consumer's own result is additionally exposed under the JSON `used_by`/
`required_symbol_contract`/`consumer_scope` keys (`abicheck/report/
scoped_gate.py`), SARIF's informational `scopedGate` block, JUnit's
`abicheck.gate_*` properties, the HTML report's "Consumer-scoped verdict"
box, and the PR comment's own consumer summary note — none of which drive
that surface's own pass/fail decision any more (`abicheck/sarif.py`,
`abicheck/junit_report.py`, `abicheck/html_report.py`,
`abicheck/pr_comment_render.py`). This is a **behavior change** from the
prior "scoped gate wins" design (worst-app-wins `sys.exit(scoped_exit_
code)`, JSON `verdict`/`full_verdict` swap): a `--used-by`/
`--required-symbol` run's exit code can differ from before when the
consumer's own result and the full-library result disagree — see the
changelog fragment landing this slice. Identity/digest/platform/profile/
provider-baseline provenance (the rest of the consumer-specification
scope this slice's own name describes) remains unaddressed — a consumer
input is still a single binary path only, tracked under "Missing" above,
not S1. Real compiled consumer/provider fixture: still open. S2 existing Actions
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
