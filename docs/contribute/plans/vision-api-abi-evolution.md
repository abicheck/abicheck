---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Vision workstreams — visible, intentional, traceable API/ABI evolution

**Status:** Proposed — planning document, partially implemented. Several
slices have landed since it was written: workstream **A**'s S1 (explicit
release-member selection + dry-run plan) and S2 (scope acquisition and
completeness), **C**'s S1 (the scalar policy-disposition audit) and part of
S2 (release/aggregate audit folding, reclassification, scope-reason
accounting), **D**'s S1 gate-enrichment slice (`--used-by`/
`--required-symbol(s)` report consumer impact beside the global gate
instead of replacing it), and **E**'s S1 (`FAILED` toolchain identity,
no-DWARF layout-unverified rows) and S2's per-dimension comparability
record. See each workstream's own section below for what landed and what
remains — this line is a summary, not a second source of truth. Each
workstream below records what *already exists* (originally verified
against the tree at `2f5ef696` on 2026-09-05, file references included;
`2f5ef696` is an ancestor of, and therefore older than, the slices named
above — those were independently re-verified against a later tree and are
called out with **updated 2026-09-06** wherever they change what the
2026-09-05 sweep found; the two baselines mark two different sweeps of the
same document, not a contradiction), what is missing, the
ADR that owns the decision, the slices, and the
acceptance tests. The product decisions themselves are in the repository
root [`vision.md`](../vision.md); the technical decisions are
[ADR-065](../adr/065-comparison-scope-selection-and-completeness.md) and
[ADR-066](../adr/066-longitudinal-history-and-versioning-policy.md)
(both Proposed) and
[ADR-067](../adr/067-change-intent-acknowledgment-and-disposition-audit.md)
(Accepted, partially implemented) plus amendments to existing ADRs named
per workstream.
**Origin:** maintainer vision discussion and the nine-prompt agent pack
prepared from it (2026-09-04), reconciled against the current tree.

> **Cross-reference (2026-09-06).** This plan remains the owner of what a
> *result means*. [`one-comparison-product.md`](one-comparison-product.md)
> ([ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md)) is
> the new owner of the CLI's **capability topology** — which command owns
> which analysis — and is subordinate to this plan wherever the two touch.
> Two of its phases depend directly on workstreams here: `compare
> --no-baseline`'s `declared_absent` acquisition state extends **A**'s
> ADR-065 model, and the always-on suppression/disposition accounting it
> assumes is **C**'s landed S1 ledger. Its Phase 1 must not invent a second
> outcome vocabulary alongside `RunOutcome`/`ExitDecision`.

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
That PR is now re-homed as
[`one-comparison-product.md`](one-comparison-product.md)'s Phase 7d, and its
engine-side half is
[ADR-061](../adr/061-responsibility-package-architecture.md)'s
[gap D](../adr/061-responsibility-package-architecture.md#d-typed-requestplan-and-operand-convergence)
— "the shared request/plan carries selection, inventory and acquisition
state" is that ADR's closure package 4, and its `Request -> ResolvedPlan ->
Result` example now shows those fields rather than two bare operands. This
workstream keeps deciding what those states *mean*; where the fields live is
settled there.

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

**Slices.** **S0 done.** Model trade-offs validated against snapshots built
from `examples/workflows/compare-release/{v1,v2}/mathutils.h`'s real
`add`/`subtract`/`multiply` surface (extended with a synthetic third/fourth
release for deprecate/remove/reintroduce coverage); recorded as a dated
amendment on `docs/contribute/adr/066-longitudinal-history-and-versioning-
policy.md` (2026-09-06) — correspondence-key narrowing (existing `EntityId`/
`(kind, symbol)`, not D2's full overload-disambiguation-with-corroboration
algorithm), a bounded `DiffResult.confidence`-based absence-uncertainty proxy
in place of ADR-065's full evidence ledger, a bounded SemVer-shape gap
heuristic in place of D4's real version scheme, and retention deferred to S4
(nothing to prune from an offline one-shot run yet). **S1 landed.** `N`
user-supplied stored-snapshot paths in (explicit release order — D4's
scheme-derived ordering is not implemented), machine-readable
`first_observed`/`introduced`/`deprecated`/`removed`/`reintroduced` events +
coverage gaps out (D2's `changed` event is not emitted — see the amendment).
Typed API: `abicheck.workflows.history.run_history_request`/
`build_longitudinal_history` (new module, composing the existing pairwise
`checker.compare` — no second N-way engine). CLI: `abicheck project history
SNAPSHOTS... [--version LABEL]... [--policy NAME] --format {json,text}`
(`abicheck/cli_project.py`), per ADR-054's admission bar (a `project`
subcommand, not a new root command). Tests:
`tests/test_workflows_history.py` (typed API, covering the ADR's mandatory
three-release add/deprecate/remove sequence, missing-intermediate-release
gap, removed-and-reintroduced, first-observed-vs-introduced, and non-SemVer
labels) and `tests/test_cli_project_history.py` (CLI end to end). S2 the
versioning policy model in `policy/`, resolved through ADR-049 D7's
precedence; support/deprecation evaluation; integration with the existing
advice; the full D2 correspondence algorithm S1 deferred. S3 CI
publication/resolution via the existing baseline channels. S4 timeline
projections through `ReportDocument`; bounded retention; cached-comparison
reuse under complete keys.

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

**Missing.** Any acknowledgment concept beyond `allow_public_break` (which
then degrades the release recommendation silently); a unique-per-run,
backend-stable acknowledgment key; the suppression file path in the
report; base/head policy-delta analysis. (S1/S2 closed the rest of this
section's original list — rule provenance, a disposition-keyed ledger for
reclassified/reconciled changes, one-line/review-digest suppression
totals, and `not_evaluated` — see "Slices" below.)

**Slices.** **S1 landed** (scalar `compare` only): one conserved ledger
(`abicheck/policy/disposition_ledger.py`) behind all five application points
— the four below plus `post_processing._merge_findings_respecting_suppression`
— raw-versus-effective counts and rule provenance in every scalar projection
(`abicheck/report/disposition_audit.py`, report schema 2.50), a
`not_evaluated` detector state, and `semver.recommend_release` reading the
conserved delta. S1 (audit, first): inventory the four application points
above and route each through one ledger-recording primitive (converging
them where the call shapes allow, covering each explicitly where they do
not) so the raw-versus-effective totals reconcile by construction; then
raw-versus-effective counts and rule provenance on native `compare`, every
projection, with a 100-suppressed-removals fixture; `not_evaluated` in
`DetectorRegistry`.
**S2 landed**: bundle/aggregate/consumer parity, plus reclassification and
scoping coverage. The release/bundle fan-out's JSON/Markdown reports and
`--output-dir summary.json` sidecar carry a folded `disposition_audit`
block over every library (`abicheck/cli_compare_receipt.py`'s
`release_disposition_audit_block`, `report/disposition_audit.py`'s
`fold_disposition_audits`); `abicheck aggregate --format json` carries the
same folded block plus a `disposition_audit_missing_targets` list for a
target whose report predates report schema 2.51 or came from `scan`
(aggregate schema 1.9, `workflows/aggregate/disposition_axis.py`);
`reclassify:` rules are recorded through the ledger as a from/to overlay
(`DispositionLedger.resolve_reclassifications`, surfaced as
`reclassified_total`/`reclassifications`); an `out_of_contract`/
`unresolved_relevance` scope exclusion carries a `scope_reasons` breakdown
by contract-relevance reason code, the scope counterpart of the existing
suppression `rules` breakdown; and the consumer-scoped path
(`appcompat.py`) reached full parity across *both* its own overlay
mechanisms — `scope_diff_to_app` (`--used-by`) already had it from S1,
and `scope_diff_to_required_symbols` (`--required-symbol(s)`) gained the
identical suppressible, ledger-recorded `CONSUMER_REQUIRED_SYMBOL_REMOVED`
overlay for a missing entrypoint no diff `Change` names (previously only a
bespoke, unsuppressible `missing_entrypoints` string), sharing one
evaluate/record/withheld-rule-diagnostic primitive
(`policy/disposition_close.record_and_maybe_suppress_overlay`) with the
`--used-by` path rather than duplicating it a third time. Report schema
3.0 -> 3.1 (additive). "Disabled-upstream coverage" (a detector never
evaluated at all, e.g. a bundle member whose own comparison failed or was
never selected) is carried by each member's own scalar `disposition_audit`
block folding in its own `not_evaluated` detector rows (S1's mechanism,
unchanged) — the fold above sums those rows across members rather than
re-deriving disabled-detector state at the bundle/aggregate level.
S3: acknowledgment records (YAML, same
loader), the additions review gate (`allow` default), shared record ids
with B. S4: policy-delta and suppression-growth warnings.

### D. Optional prebuilt-consumer lifecycle — amend ADR-005/047/052/057, extend G29/G30

**Existing (verified in code, beyond ADR-057's index row).** `--used-by`
static scoping (`abicheck/appcompat.py`: `parse_app_requirements`,
`scope_diff_to_app`, `check_appcompat`); `--required-symbol(s)`; the
consumer graph and join (`abicheck/impact/consumer_graph.py`); **use-case
manifests are implemented** (`abicheck/impact/use_cases.py`,
`use_case_impact.py`, `compare --use-cases`); Action inputs `used-by`/
`required-symbol(s)`/**`used-by-manifest`** and `actions/check-target`'s
`app-consumer` kind; no consumer code is ever executed (ADR-060 deferred;
the only subprocess adjacency is a demangler prewarm). **`--used-by`/
`--required-symbol(s)` no longer replace the gate (S1's gate-enrichment
slice, landed)**; **the consumer-*specification* half is now also landed
(S1 complete)** — see below.

**Missing.** No staging/caching of consumer artifacts in the Action (S2);
no exact-version selection or existing-Actions-acquisition/publishing-
channel parity beyond the CLI/manifest surface itself (S2); no declared
source/use-case enrichment with coverage-qualified reports (S3);
runtime-trace ingestion unimplemented; a real compiled consumer/provider
fixture (rather than mocked/stubbed test binaries) is still open.

**Slices.** **S1 (complete).** Gate-enrichment half (landed): a supplied
consumer's per-`confirmed/potential/unresolved` impact
(`scoped_verdict`/`scoped_exit_code`/`used_by`/`required_symbol_contract`,
still computed by `abicheck/appcompat.py`'s `scope_diff_to_app`/
`scope_diff_to_required_symbols`) is reported **beside** the global
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
`abicheck/pr_comment_render.py`). This was a **behavior change** from the
prior "scoped gate wins" design (worst-app-wins `sys.exit(scoped_exit_
code)`, JSON `verdict`/`full_verdict` swap) — see that slice's own
changelog fragment.

Consumer-*specification* half (landed): a consumer input is no longer only
a bare binary path. `abicheck/model/consumer_spec.py`'s `ConsumerSpec`
carries identity/provenance beyond `path` — `digest` (an expected content
digest, verified against the real file at resolution time via
`verify_digest`), `platform`, `profile`, `provider_baseline`, and
`requirement` (`ConsumerRequirement.REQUIRED`, the default, or `ADVISORY`).
`--used-by-manifest PATH` (repeatable; `parse_consumer_manifest`) names one
or more consumers via a small JSON document and merges them into the same
`--used-by` pipeline (`cli_compare_helpers.run_compare`), so a manifest
consumer contributes to the same worst-wins scoped gate and the same
`used_by[]` report block as a bare `--used-by <path>` consumer — the two
are one population, not two. An unreadable **required** consumer (the
default, matching every pre-S1 `--used-by <path>`) still raises
(`ConsumerUnreadableError`/`ConsumerDigestMismatchError`, both
`ValueError` subclasses) and aborts the run; an unreadable **advisory**
consumer (`scope_diff_to_app`) instead returns a `NO_CHANGE`-verdict,
`unreadable=True` result that is skipped, reported, and never contributes
to the worst-wins computation. `--format json` gains a `consumer_impact_summary`
object — "N of M consumers affected" (`total`/`evaluated`/`affected`/
`unreadable_advisory`/`unreadable_paths`) across every supplied consumer
(`cli_helpers_compare._consumer_impact_summary`) — and each `used_by[]`
entry gains the new fields only when a manifest consumer actually supplied
them, so a bare `--used-by <path>` consumer's entry is byte-for-byte
unchanged (report schema 3.3, additive; SARIF/JUnit/HTML do not yet carry
`consumer_impact_summary` — JSON only for now, tracked as a small
remaining gap rather than folded into "Missing" above). The GitHub Action
gained a matching `used-by-manifest` input (space-separated, repeated
`--used-by-manifest`), validated the same way `used-by` is
(`action/validate-inputs.sh`, `action/run.sh`).

**S2 (started).** Action-input parity for the new consumer-manifest surface
(landed, above: `used-by-manifest` mirrors `used-by`/`required-symbol(s)`
exactly — same mutual-exclusivity check, same compare-mode-only scoping
warning). Still open: staging/caching consumer artifacts *in* the Action
(so a manifest-named consumer can be fetched/cached across workflow runs
rather than assumed already checked out), exact-version selection against
an existing acquisition/publishing channel, and parity for the advisory/
required distinction in the Action's own `check-target`/`app-consumer`
kind (today a Python-API/CLI-only distinction). **S3** declared source/
use-case enrichment with coverage-qualified reports. **S4** separately
designed, opt-in compile/link/runtime validation with its own execution
design review — never implied by S1–S3, and not a reauthorization of
ADR-060.

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

**S1 and part of S2 have landed** (below); the "Missing" list here is the
**original assessment** and has not been fully re-swept since — items S1/S2
closed are called out explicitly rather than silently dropped from the list:
No `INCONSISTENT`/`CONFLICTING` fact status, and "not
requested" vs "capped" collapse onto `NOT_COLLECTED`; two provider-status
vocabularies (`FactStatus` vs `EvidenceProviderStatus`) not unified;
~~no per-detector "layout unverified" row when both sides lack DWARF~~ —
**closed by S1**: `analysis_assurance_layout.py`'s `layout_unverified_detectors`
names `dwarf`/`advanced_dwarf`/`layout_descriptor` as unverified when
neither side carries DWARF; `DetectorRegistry.run_all` has
no per-detector `FAILED`; ~~a compiler-probe failure feeds an *absent*
toolchain identity rather than `FAILED`~~ — **closed by S1**:
`extract/toolchain_identity.py` now yields `FactStatus.FAILED` on a probe
failure, and `comparability_profile.py` refuses comparison whenever either
side is `FAILED`; no reverse declared/observed
detector (exported but undeclared) and no manifest-narrowing detector;
`configuration_coverage` always `NOT_STARTED`; a GCC/Clang pair with a
missing fingerprint on either side is compared silently; ~~comparability
yields one `kind`, not a per-dimension record~~ — **partially closed by S2**:
`DiffResult.comparability_assurance` now reports a per-`COMPARABILITY_DIMENSIONS`
`"unverified"`/`"trusted"` value (JSON/Markdown/HTML), though comparability
*refusal* (`ComparabilityMismatch`) itself still yields one `kind`; an
out-of-band build/source pack can bypass the depth ceiling; `dump` never
applies the ceiling.

**Slices.** S0 the requested-capability × availability × input-type ×
policy × result table mapped to owners (this section's seed). **S1 —
no-DWARF/missing-header/failed-extraction semantics on binary and snapshot
paths — implemented ([#1099](https://github.com/abicheck/abicheck/pull/1099)):**
per-detector `not_evaluated`/`failed` semantics via
`analysis_assurance_layout.py`'s unverified-layout rows, and `FAILED`
toolchain identity via `extract/toolchain_identity.py`. **S2's per-dimension
comparability record — implemented ([#1098](https://github.com/abicheck/abicheck/pull/1098)):**
`DiffResult.comparability_assurance`, populated from `ComparabilityMismatch`'s
`dimensions` field, reaches JSON (schema 3.1), Markdown, and HTML, and is
proven to preserve an earlier-proven change through a later incomplete
stage; profile-delta explanation beyond the per-dimension record itself
remains open. S3 multi-source contract conflicts with
provenance (exported-but-undeclared, manifest narrowing since baseline,
package-claim vs. contained-binary) — not started. S4 parity through scan/project/
bundle/API/Action/report; retire conflicting legacy decisions — not started.

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

**Slices.** **S1 landed**: a binary-less `DumpRequest` (`dump -H api.h`, no
`SO_PATH`/`--sources`/`--build-info`) now runs a real header-AST parse
through the shared typed pipeline instead of the legacy
`dump_source_only()`, which never read `-H` at all —
`workflows.artifact.execute_header_only.is_header_only_evidence` is the
one dispatch point distinguishing this shape from the pre-existing L3–L5
source-only one, so the two can never overlap or silently disagree.
`header_only_dump.build_header_only_snapshot` runs `dumper_manifest.
resolve_header_ast_result` — the identical function a binary dump's own L2
pass calls — with an empty observed-export set on both sides (no binary to
have exported anything from) and a new explicit tier marker,
`AbiSnapshot.header_only` (schema v44), so a report/policy consumer never
mistakes this shape for the source-only one (both share `platform=None`).
`compare` needs no changes at all: it already consumes any two stored
snapshots, so `dump -H old.h -o old.json` + `dump -H new.h -o new.json` +
`compare old.json new.json` is the whole invocation. `api_types.py`'s
`_path_required_errors` now accepts `headers`/`public_header_dirs` alone as
valid binary-less-dump evidence (previously only `sources`/`build_info`/
`dump_manifest` counted), which is what makes the request reach this path
at all.

A real, load-bearing correctness fix landed alongside the plumbing, not
just the operand-routing scaffold: `extract.headers.{castxml,clang}`'s
shared `visibility()` used to fall back to `HIDDEN` whenever a mangled
name matched neither the (necessarily empty, for this shape)
`exported_dynamic`/`exported_static` sets — correct for an ordinary binary
dump (unexported = hidden), but it silently marked *every* header-only
declaration `HIDDEN`, which made `_public_functions()` filter every
function out and produced a false `NO_CHANGE` for real changes. A new
`no_binary_evidence` flag (threaded through
`dumper._header_ast_parser`/`dumper_manifest.resolve_header_ast_result`
down to both backends' parser contexts, `False` — inert — for every
ordinary binary dump) flips that one fallback to `PUBLIC` instead,
mirroring the identical "declared public in a public header, without
contrary evidence" principle `dumper_castxml.py` already applies to a
constructor/destructor/CPO with no ELF symbol to look up. Verified against
all six named scenarios (unchanged headers, removed declaration, added
API, changed enum value reachable from a public root, a signature change,
and an access-level change), each with a real castxml/clang parse, in
`tests/test_header_only_dump.py`.

Source-compatibility findings, versioning advice, and scope all come free
from the existing pipeline, unextended: `compare()`'s ordinary
`ChangeKind`/verdict machinery, `release_recommendation`, and
`surface_scope`/`out_of_surface_changes` (a header-only snapshot's
unreached types are correctly recorded — never dropped — under
`non-public-type`, same as a binary snapshot's). **Unsupported-capability
rows are the existing `DetectorRegistry`/`not_evaluated` convention
(ADR-067 D3, `_has_any_dwarf`), extended, not a new mechanism**: a new
shared support gate, `diff_platform._has_elf_on_both_sides`, closes a
real pre-existing silent gap this workstream's own testing surfaced —
`elf`/`tls_checks`/`protected_visibility`/`symbol_version_alias`/
`vtable_identity`/`abi_surface`/`elf_deleted_fallback` (the seven
detectors that genuinely read `AbiSnapshot.elf`) each used to substitute
an empty `ElfMetadata()` for a missing side and record a real, evaluated
zero rather than the coverage gap it actually is. The gate is keyed on
real ELF-evidence presence — `.elf` populated on both sides, or
`.elf_only_mode` for the one sub-check (`elf`'s own
`_diff_visibility_leak`) that reads `.functions`/`.elf_only_mode`
directly and never `.elf` — mirroring the `pe`/`macho` gates immediately
alongside it, rather than a `header_only`-only proxy. Two review rounds
each found and reverted a narrower version of this gate that broke
pre-existing tests: first keying it on a bare `elf is None` (broke every
synthetic test snapshot — and every pre-existing L3-L5 source-only dump —
that never bothers populating `.elf` while still representing an ordinary
ELF library or an `elf_only_mode` symbol-table-only dump); then keying it
on `AbiSnapshot.header_only` alone and applying it to `glibcxx_dual_abi`/
`inline_namespace` too (broke every test exercising mass mangled-name
churn via bare `functions=` fixtures, since those two detectors never
read `.elf` at all and were never gated before this workstream).
`glibcxx_dual_abi`/`inline_namespace` are therefore deliberately left
ungated, unchanged from their pre-workstream form — a header-only
snapshot's guessed mangled names carry exactly the same
spelling-not-linkage-proof status an ordinary headers-augmented binary
dump's mangled names already carry, which this workstream did not
newly introduce and is not the one to start gating. Because the real
gate closes a genuine pre-existing bug (silently comparing two fabricated
empty `ElfMetadata()` objects whenever `.elf` was missing on *either*
side, binary or header-only), it surfaces new `not_evaluated` rows for
several golden fixtures that never populate `.elf` — those fixtures were
deliberately regenerated as part of this fix. Now three of the plan's four
capability classes — symbol presence/versioning, ELF layout, and
vtable/RTTI linkage identity — surface as an explicit, reasoned
`not_evaluated` row (`elf`/`tls_checks`/`protected_visibility`/
`symbol_version_alias`/`vtable_identity`/`abi_surface`/
`elf_deleted_fallback`, alongside the pre-existing `dwarf`/
`advanced_dwarf`) in `disposition_audit.not_evaluated_detectors`, never
silently absent; the fourth class (mangled-name linkage-level churn) is
covered by the same `elf` family's own not-evaluated status rather than a
per-detector gate on `glibcxx_dual_abi`/`inline_namespace` themselves, per
the paragraph above. No detector's emitted findings change anywhere (two
empty `ElfMetadata()` objects always compare equal), only whether the
absence is now recorded explicitly.

**Explicitly still open, deferred to S2 or later**: the deeper macro/
inline/template evidence beyond what L4 already gives; layering L3–L5
build/source evidence *on top of* a headers-only base (today `sources`/
`build_info` and bare headers are still two disjoint binary-less shapes,
never combined); frontend-difference recording (S2's own stated scope);
and honest `NOT_APPLICABLE` L0/L1 semantics as a first-class, reusable
concept beyond this slice's own `not_evaluated` detector rows. **Static
archives are untouched by S1** — that investigation remains its own,
separately-scoped, lower-priority note below, with no change to archive
acceptance or defaults.
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
