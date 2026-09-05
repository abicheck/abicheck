---
doc_type: contributor
level: advanced
lifecycle: active
---

# Project-wide duplication assessment and convergence plan

**Type:** Architectural assessment plus a phased convergence plan. Not a
gap-closure plan against a `usecase-registry.yaml` entry — this is a
cross-cutting initiative plan, alongside
[CLI cleanup, phase two](cli-cleanup-phase-two.md),
[G33 (typed API convergence)](g33-typed-api-and-mcp-convergence.md), and
[G32 (comparability contract)](g32-comparability-contract-and-multi-tu-manifest.md),
all three of which this plan builds directly on and names throughout.
**Related:** [ADR-037](../adr/037-cli-interface-contract.md) (CLI interface
contract), [ADR-049](../adr/049-contract-relevance-and-compatibility-configuration.md)
(contract-relevance/compatibility configuration), [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
(comparability contract), [ADR-054](../adr/054-cli-project-integration-surface-consolidation.md)
(root-surface admission), [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)
(typed request/result completeness), [ADR-056](../adr/056-multi-artifact-library-set-scan.md)
(`scan --artifact-set`), [ADR-061](../adr/061-responsibility-package-architecture.md)
Phase 2 (`ReportDocument` — this plan's `ReportEnvelope` in "Phase 4 —
Introduce the canonical report model" below is the generalization of the
same target; ADR-061 Phase 2's own "not met yet" gaps — the Markdown/HTML
prose rewrite and the per-finding verdict consolidation — are this plan's
Phase 4 items 3 and 1 respectively, and its item 5 (post-render mutation)
is this plan's P1 "Reporting composes too late" finding below, almost
word-for-word; the two documents converged on the same diagnosis
independently and should be read together rather than as competing plans)
**Effort:** XL (six phases, each independently landable; Phase 1 alone spans
several PRs, and Phase 6 decomposes into ten parallel tracks) · **Risk:**
high for Phase 1 (touches every extraction call site — `dump`, both
`compare` operands, `scan` candidate and baseline, release fan-out); lower
for Phases 2-5, which mostly consume what Phase 1 and Phase 2 produce;
medium for Phase 6, whose risk is concentrated in deletions rather than new
behavior.

## Why this document exists

This assesses `abicheck` as of `main` at commit `e674d3127634176dc7a41566bd1943bf6f481176`
(2026-08-20) for **semantic duplication**, re-assessed against `main` at the
merge of PR #1062 (2026-09-04, the `--exit-code-scheme` removal) — see
"2026-09-05 re-assessment" below, which supersedes this section's headline
framing where the two differ. **Semantic duplication:** places where the same
responsibility — a decision, a normalization, a workflow — exists in more
than one codepath, expressed as different code that is intended (and mostly
succeeds, at real correctness cost recorded in `AGENTS.md`'s "Known gaps")
to produce equivalent results. That is a materially more dangerous class of
duplication for this codebase than literal copy-paste, because two
independently-evolving implementations of "resolve this input" or "compute
this exit code" drift silently, one bug fix at a time, until a real-world
report surfaces the divergence (see AGENTS.md's L3→L2-fold and
`include_sequence` entries for concrete incidents of exactly this).

The verdict: **the project is midway through convergence, not badly
designed.** The compare pipeline (`service_compare_pipeline.py` +
`service_input_resolution.py`) already demonstrates the right architecture —
parse frontend input, build a typed request, resolve once, execute once,
classify once, project into different outputs. The problem is that this
pattern is not yet the *mandatory* architecture for `dump`, `scan`, release,
aggregate, and `compat`. This plan's job is to make it mandatory.

## Scope summary

| Area | Current state | Duplication risk |
|---|---|---:|
| Binary/debug/header parsers | Mostly appropriately separated by backend | Low |
| Diff detectors and policy classification | Generally shared and registry-driven | Low–medium |
| `compare` resolution | Properly converged around typed service APIs | Low |
| Artifact extraction for `dump`, `scan`, and `compat` | Several partially equivalent paths | **High** |
| Configuration and pack application | One resolver, several runtime representations | **High** |
| Exit and gate calculation | Partially unified, with operation-specific parallel folds | **High** |
| Report construction | Significant post-render mutation and format-specific repair | **High** |
| Release and matrix aggregation | Reinterprets outputs rather than consuming a common result model | Medium–high |
| CLI/service dependency direction | CLI concepts still leak into engine/service layers | **High** |

## What is already right — and why it's the model

**The compare pipeline.** `service_compare_pipeline.py` splits comparison
into `resolve_compare_request()` and `classify_compare_pair()`. The CLI, the
typed Python API, and the former MCP path (see ADR-021, retired) share
resolution and classification without forcing CLI-specific configuration
work into the service layer — `cli_compare_receipt.py`'s ADR-049 resolution
step runs *between* the two phases, exactly because they're split. This is
the template: **parse frontend input → build typed request → resolve once →
execute once → classify once → project into different outputs.**

**Per-side resolution.** `service_input_resolution.py` exists specifically
so a comparison pair and a standalone dump don't each carry their own copy
of "resolve one input into a snapshot" — it owns `resolve_side_snapshot`,
`embed_side_build_source`, `enforce_requested_depth`, and
`reject_hybrid_source_frontend`. That is the correct abstraction boundary;
the gap (Phase 1 below) is that not every caller uses it yet.

**Working single-source-of-truth efforts** worth preserving as patterns:
`snapshot_io.py` (canonical snapshot storage envelope), the `ChangeKind`
registry deriving `BREAKING_KINDS`/`API_BREAK_KINDS`/`COMPATIBLE_KINDS`/
`RISK_KINDS` rather than hand-maintaining four sets, shared CLI option
decorators (`cli_options.py`) for cross-command concepts, and the stored
SONAME mapping in bundle resolution that prevents a later graph traversal
from independently reconstructing a subtly different one.

## 2026-09-05 re-assessment — the remaining gap is authority transfer, not construction

An external re-assessment of `main` (the merge of PR #1062, which removed
`--exit-code-scheme`) was reviewed and **accepted** on 2026-09-05. It
supersedes this document's own headline framing without invalidating any
landed slice: the primitives this plan and
[ADR-063](../adr/063-one-semantic-pipeline.md)/
[one-semantic-pipeline.md](one-semantic-pipeline.md) call for are largely
**built**; what is not done is making them the **sole** authority and
deleting what they replaced.

### What has genuinely closed since the original audit

| Area | Now implemented | What that does *not* yet establish |
|---|---|---|
| Gate configuration | `policy/release_gate_options.py`'s `GateOptions`; the manual `--exit-code-scheme` selector deleted (PR #1062, ADR-064) | That effective-policy *application* has one implementation — see `apply_release_gate_pack` below |
| Semantic consumers | The typedef and constant detector families read `SemanticIRIndex` (`compare/typedefs.py`, `compare/constants.py`), guarded by `scripts/semantic_ir_cutover.py` | That the IR is their *sole* data source |
| Fact semantics | `compare_facts()`/`FactComparison` used by real detectors; base-class comparison refuses incomplete evidence rather than treating `PARTIAL` as a complete list | That every fact-dependent decision handles availability and scope consistently |
| Execution context | `resolve_compare_request`/`execute_dump_request` both build and enrich a `ResolvedExecutionContext` | That execution and classification consume it as their sole authority |
| Evidence depth | `policy/depth_projection.project_pair_to_depth()` is used in compare classification | That projection is provenance-driven rather than a manual field-clearing inventory |
| Storage | Typed section codecs; multi-artifact import/export paths; **`bundle_facts_store.py` now delegates to one `import_bundle_facts`/`export_bundle_facts` implementation instead of keeping two physical layouts** | That standard single-/multi-artifact workflows share one logical flow |
| Reporting | Every renderer family, including both alternate Markdown modes, crosses `ReportDocument` | That every report fact is computed once *before* format-specific construction |

**Bundle storage is the model to copy.** Two tracks had implemented
different physical layouts for the same bundle-composition facts; one now
delegates to the other and the surviving entry points are adapters. That is
what every remaining item below should look like when it is done.

### The completion rule this plan was missing

The review identified a real loophole in how items here and in
one-semantic-pipeline.md have been closed: several migrations were
*investigated and declined* because no currently-demonstrable finding
justified them, and that disposition was then treated as equivalent to
"the removal gate is closed." Those are different things. Separate the two
decisions:

- **Behavioral change** — changing matching precedence, suppressing a
  finding, altering evidence requirements, or changing a published finding
  ID. Needs a demonstrated benefit and correctness evidence. Declining one
  for lack of a demonstrated benefit is *correct*, and this plan's existing
  "attempted twice, reverted twice" discipline (AGENTS.md, `known-gaps.md`)
  stays in force.
- **Implementation consolidation** — moving existing, unchanged semantics
  behind one owner and deleting the duplicate. This does **not** need a new
  bug to justify it. Its benefit is fewer implementations, fewer
  configuration-forwarding paths, and fewer independently mutable
  representations of the same fact.

> **"No current bug found" can justify preserving *behavior*. It cannot, by
> itself, justify preserving a second *implementation* of that behavior.**

The governing prohibition, stated precisely enough to be applied without
over-reaching:

> **No two independently maintained implementations of the same semantic
> decision, and no two independently writable current representations of
> the same semantic fact.**

That is deliberately narrower than "no duplication": a pure import shim is
not a duplicate implementation, a legacy decoder is not a duplicate current
model, a different renderer is not a duplicate analysis engine, and a
stack-loadability check is not the same question as library ABI
compatibility. See "What should explicitly *not* be unified" below, which
this rule refines rather than replaces.

### The four-state status model

The authority ledger (`docs/_meta/one-semantic-pipeline-status.yaml`) and
this plan should both record a concept's position on one ladder:

```text
introduced → wired → authoritative → old implementation retired
```

with **`investigated_declined` as a separate disposition, not a synonym for
`retired`.** A declined *behavioral* change leaves the consolidation item
open; only an actual deletion (or reduction to delegation) closes it.

This matters most concretely for the `vtable`/`TYPE_VTABLE_CHANGED` cluster
(5B's "final closure"): the investigation is genuinely complete, but the
PDB-driven fabricated-finding path it documents is still reachable and the
authority transition still has not happened. Recording that as
`investigated_declined` with an open removal gate is accurate; recording it
as closed is not.

**Landed (track T2, 2026-09-05).** Both halves are now schema, not
convention: the ledger is at `schema_version: 2`, every concept carries a
`lifecycle` rung, and `scripts/pipeline_status_ledger.py` enforces that
`lifecycle` agrees with `authority` (which it refines — `authority` cannot
express either end of the ladder, because two of its three values are
ambiguous across two rungs each: `authority: legacy` covers both
`introduced` and `wired`, and `authority: self` covers both `authoritative`
and `retired`; only `mixed` picks out exactly one rung, `wired`, which
`wired` does not imply in return), that `retired` requires every status
field `complete`, and that a
concept carrying any `investigated_declined` entry cannot sit at `retired`
— the loophole above, closed mechanically rather than restated. Each entry
names `item`, `decided`, `leaves_open` (what the decline does *not* close)
and `tracked_as` (the narrative owner holding the full reasoning);
`leaves_open` is required precisely because an entry that says nothing
about what stays open is the loophole in structured form.

The re-audit that shipped with the schema found **no concept at `retired`**,
and only the three `authority: self` concepts (`public_surface`,
`report_document`, `l5_source_graph_identity`) at `authoritative`; the other
six are `wired`. Three carry a declined disposition: `facts` (the
`vtable`/`TYPE_VTABLE_CHANGED` FactStatus gating above), `identity` (the
`entity:` alias-tier promotion), and `semantic_ir` (the record/function
detector cohort). That distribution is this section's own headline —
primitives built, authority not transferred — recorded as data rather than
prose.

### Corrections to current status prose

Two claims elsewhere in this family of documents are now stale and are
corrected here (drift of exactly the kind the ledger exists to prevent):

1. **Dump/compare execution context is populated, not absent.**
   `execute_dump_request` calls `with_assurance()` and carries per-side
   `compile_contexts`; `resolve_compare_request` attaches a context and
   `classify_compare_pair` reads `requested_depth` off it. The real gap is
   that these objects are largely *unread*, and that `evaluation_config`
   is still `None` on every real `ResolvedComparePair`/`ResolvedDumpRequest`
   — not that the wiring is missing.
2. **`action/run.sh` is partially migrated, not unmigrated.**
   `_report_compat_verdict()` and `_severity_gate_exit()` already prefer
   `run_outcome`'s structured fields. What remains is the residual
   raw-process-exit/stderr-text interpretation, which PR #1062 extended
   again for evidence-contract errors.

### What "done" looks like for the next milestone

Measurable as **fewer independent execution paths, no dual current-data
fallback for the migrated cohorts, and named legacy implementations
deleted** — not "more fields populated," "another phase investigated," or
"all existing tests still pass."

### The explicit retirement table

Every consolidation item below needs a named replacement **and a named
deletion**. Each row was verified against the implementation on
`main` at the time of this update.

| Existing path/representation | Canonical owner to use | Required cleanup |
|---|---|---|
| ~~`cli_dump_helpers.perform_elf_dump()` / `cli_dump_non_elf.handle_non_elf_dump()`~~ — **DONE (T1, 2026-09-05)**: both deleted, along with `abicheck/cli_dump_non_elf.py` and `abicheck/cli_dump_protocols.py` | Typed dump execution (`service_dump_pipeline.execute_dump_request` via `frontends/cli/dump_execute.py`) | Complete. The unique assertions were rehomed to `tests/test_dump_cli_execution_behaviors.py` (against the real `dump` CLI, since `header_roots` and the public-root forwarding are computed on either side of the executor); everything else those tests asserted was already owned at the shared pipeline's own seams, named individually in that module's docstring. `CLI_CONTRACT_ALLOWLIST` lost its `cli_dump_helpers.py:…:dumper.dump` entry — the first line to leave that list, and by deletion rather than rerouting. See the T1 row below for the two facts the retirement surfaced. |
| `cli_buildsource.dump_source_only` — `execute_dump_request` explicitly refuses a binary-less request and redirects here | A source-only *execution variant* inside the shared workflow | Keep CLI parsing/presentation only; one semantic assembler, persistence path, and error contract for both variants |
| `appcompat.check_appcompat()`'s two direct `dumper.dump()` calls | Shared extraction + comparison workflow | Delete the independent per-side header/include resolution; keep only application-specific requirement/impact evaluation |
| `stack_checker._run_abi_diff()`'s direct `dumper.dump()`×2 + `checker.compare()` | Shared per-library comparison operation | Remove the bypass and the generic-failure-to-`None` collapse; carry typed operational outcomes into stack analysis; keep dependency resolution/loadability as stack-specific |
| `compare/typedefs.typedef_index_pair()` / `compare/constants.constant_index_pair()` runtime fidelity selectors — both build **IR-backed *and* legacy-backed** indexes on every comparison and let the legacy projection adjudicate | Canonical family data + a historical-input adapter at the load boundary | Stop rebuilding a legacy index for a current-format snapshot; a disagreement becomes an explicit consistency failure, not a silent fallback |
| Writable legacy value + `*_fact` sibling pairs; current-runtime reliability flags | One canonical fact payload carrying observation-vs-inference, producer, scope, and positive-observation-vs-completeness | Remove dual writes and synchronization machinery once consumers migrate; historical decoding stays permanently in the import adapter |
| ~~`policy/release_gate_options.apply_release_gate_pack()` — folds pack severity into a 5-tuple of **raw strings**, mirroring `pack_application.apply_to_compare_config`'s fold over an already-resolved config~~ **(the mirror is gone, T6, 2026-09-05)** | The fold rule itself now lives once in `policy/gate_pack_fold.py`'s `fold_gate_pack_severity` — a leaf inward of both callers, which is what let both call it without `policy` importing the flat-root `pack_application` (the `_GatePackApplication` `Protocol` stays for the same reason). The two callers' remaining difference is their fold *target*, not the rule | Done for the rule. Still open: the two runtime shapes themselves (four raw optional strings vs. an already-resolved `SeverityConfig`), which is this plan's `EffectiveGate`/`EffectiveEvaluationConfig` target — `tests/test_release_gate_pack_fold_parity.py` now guards those two shapes, and `tests/test_gate_pack_fold.py` states the shared primitive's own contract |
| `action/run.sh`'s residual raw-exit-code/stderr-text verdict reconstruction | Structured `run_outcome`/`exit` reader | Retain only a small transport-level fallback for "no valid result because invocation failed"; `fail-on-*` stays explicit step policy and must not rewrite the reported verdict |
| Four sibling `_exported_symbol_names()`-shaped implementations (`policy/depth_projection.py`, `buildsource/crosscheck_base.py`, `buildsource/snapshot_exports.py`, `post_manifest.py`, plus `diff_unnamed_types`'s own) | One canonical **raw export index** with explicitly named projections | Remove the equivalent local implementations — while preserving the real distinctions (versioned ELF exports, default versions, Mach-O spelling normalization, named PE exports, ordinal imports, *missing* versus *confirmed-empty* tables) as named views, not one universal set-of-strings helper |
| Report semantics recomputed per format (alternate Markdown builders still consult `DiffResult`, filtering, policy, and recommendation helpers, reaching them through `render_markdown_document._reporter_markdown()`'s runtime `importlib` load of `..reporter_markdown` to dodge a cycle) | Shared **report preparation** producing evaluated facts once | Keep only view construction and rendering in format-specific code; layout/grouping/presentation filters stay legitimately per-format. `report/scoped_gate.py`'s own runtime load of `..reporter` is a *separate* dependency — scoped JSON construction, not Markdown — and has its own row below |
| `report/scoped_gate.apply_scoped_gate()` — folds the scoped (`--used-by`/`--required-symbol`) gate into an **already-built** JSON payload as a plain `dict` mutation, and reaches `reporter` through a runtime `importlib` load because `reporter` → `reporter_contract_blocks` → `scoped_gate` would otherwise close a real cycle | The same shared **report preparation** as the row above: evaluate the scoped and full-library views through the shared machinery and construct each, rather than building one payload and mutating it into the other | Delete the post-hoc `dict` fold and, with it, the `..reporter` runtime load — the cycle exists only because the fold runs after construction. Same root cause as `scope_diff_to_app()`'s late enrichment below, so the two are one fix at two sites, not two independent items |

Two adjacent findings that are not retirements but belong with them:

- ~~**`GateOptions.exit_code_scheme` is documented as purely derived but
  remains an independently constructible dataclass field beside
  `severity`.**~~ **Closed (T6, 2026-09-05):** it is a derived property now,
  and so is `ResolvedCompareConfig.exit_code_scheme` beside its own
  `severity_active` — the same defect, one object over. The concern was that
  the *model* permitted disagreement even though the resolver never produced
  one, and the concern was justified: two unit-test helpers
  (`tests/test_config_review.py`, `tests/test_cov95_cli.py`) were
  constructing a `GateOptions` carrying `exit_code_scheme=None` beside a real
  `SeverityConfig`. Every site that re-spelled the `"severity" if ... else
  "legacy"` derivation now calls one `gate_pack_fold.gate_exit_code_scheme`.
- **`DumpResult`'s effective include paths can point into an
  already-deleted inferred-build directory.** Returning more paths is not a
  substitute for owning their lifetime: the shared extraction session must
  own those resources across every consumer that needs to *read* them
  (parsing, enrichment, graph construction, persistence), which is the
  resource-lifetime half of Phase 1 item 1 that is still open.
- **`scope_diff_to_app()` synthesizes findings and mutates shared changes
  after the original comparison**, forcing extra suppression and
  impact-cache handling. A cleaner finalization boundary evaluates those
  findings through the shared machinery and keeps full-library versus
  consumer-scoped views explicit rather than mutating one into the other
  (this is the same finding as P1 "Reporting composes too late" below,
  reached from the consumer-scoping side).

## Hotspots, in priority order

### P0 — Artifact extraction and evidence resolution

The largest duplication and correctness risk. At least ten
partially-equivalent paths exist today, in three groups: seven user-facing
operations (1–7); one internal, backend-level exception (8, the probe
harness, called out below as not itself a user-facing operation, since
nothing outside its own module calls it); and two internal, supplementary
extraction call sites invoked *by* the user-facing `compare`/`scan`
operations above, not separate operations a user invokes directly (9–10,
each its own `service.resolve_input(..., symbols_only=True)` call distinct
from either side's primary resolution):

1. Typed dump: `DumpRequest → resolve_dump_request() → execute_dump_request()`
   (`service_dump_pipeline.py`)
2. Native ELF CLI dump: `dump_cmd → perform_elf_dump()` (`cli_dump_helpers.py`)
3. Native PE/Mach-O dump: a separate non-ELF path (`handle_non_elf_dump`)
4. Scan candidate/baseline: `scan_engine._build_new_snapshot()`, which calls
   `service.resolve_input()` directly rather than routing through
   `service_input_resolution`
5. Dump dry-run: `render_dump_dry_run()`, a second, independent
   approximation of resolution — its own docstring calls this "Cheap,
   read-only resolution only... never runs castxml/clang"
6. Standalone application-compatibility: `appcompat.check_appcompat()` calls
   `dumper.dump()` directly for both sides (its own docstring: "Dumps and
   compares the two libraries itself"), bypassing every one of the five
   paths above — a caller who reaches `check_appcompat()` directly (rather
   than through `compare`'s own app-usage scoping, which calls
   `scope_diff_to_app()` against an already-resolved diff instead) gets none
   of what `ResolvedArtifactPlan` would eventually centralize: resource
   lifetime, the L3→L2 compile-context fold, cache-relevant paths, or
   post-processing hooks.
7. `deps compare`: `stack_checker._run_abi_diff()` calls `dumper.dump()` for
   both sides and `checker.compare()` directly (`abicheck/stack_checker.py`),
   and `cli_stack.py`'s `deps_compare_cmd` independently computes its own
   process exit code from `result.loadability`/`result.abi_risk` rather than
   through any shared exit model — Phase 3 covers this with two new axes
   and a `DepsCompareExitPolicy` (see that section).
8. The probe harness: `probe_harness._snapshot_object_file()` (used by
   `run_probe_matrix(..., snapshot=True)`, the header-only-library
   compile-and-snapshot driver behind G25/G26-family evidence-tier work)
   also calls `dumper.dump()` directly on each compiled probe object.
   Deliberately called out separately from the seven user-facing operations
   above (paths 1–7), since it isn't one — see "backend-level exception"
   below.
9. L0 export-delta re-extraction (an internal supplementary call site, not
   a separate user-facing operation): `l0_export_delta.
   collect_l0_export_delta()` — invoked by both native `compare` and scan
   baseline reconciliation — independently calls `service.resolve_input()`
   twice with `symbols_only=True`, a *supplementary* extraction distinct
   from either side's primary resolution. Missing this from the migration
   would let `compare`/`scan`'s primary-side equivalence tests pass while
   this secondary path still misses the centralized lifetime, fingerprint, and
   post-processing behavior.
10. Scan's POI (point-of-interest) export prepass (also an internal
    supplementary call site, not a separate user-facing operation):
    `scan_engine._load_exports_for_poi()` — a separate, best-effort
    `service.resolve_input(..., symbols_only=True)` call `scan_engine.py`
    makes for both baseline and candidate ahead of the primary extraction,
    when export-delta POI tracking is needed — is a third, independent
    `resolve_input()` call site alongside path 9's L0 delta and the
    primary-side resolution scan candidate/baseline routing already
    covers. Missing it the same way path 9 would be missed: routing
    `_build_new_snapshot()` and the native baseline says nothing about this
    prepass, since it resolves on its own, ahead of either.

`appcompat.check_plugin_host_contract()` — the plugin-host counterpart to
`check_appcompat()` (path 6 above) — is deliberately **not** listed here:
unlike `check_appcompat()`, it takes two already-built `AbiSnapshot`
objects as its own parameters and does no extraction of its own — it
calls `compare_snapshots()` directly and then scopes the result, nothing
more. It belongs to this plan's *comparison*-path duplication instead (see
Phase 2 item 5 and the "Comparison equivalence" acceptance test below,
both of which already name its own pre-scope `compare_snapshots()` call),
not the artifact-extraction list this section enumerates.

`service_dump_pipeline.py` documents directly that native dump behavior
historically lived around `resolve_input()` in CLI code, forcing non-CLI
consumers to reimplement or omit those steps. AGENTS.md's "Known gaps"
section records the concrete cost of this divergence in detail: duplicate
inferred build queries contending on the same lock for up to 600 seconds
(fixed by `seed_includes_and_fold_compile_context`); the L3→L2 compile-
context fold reaching `dump` and `compare`'s implicit-dump operand but not
`scan`'s candidate or baseline resolution (three separate follow-up fixes,
numbered 8/12/13/15 in that entry); derived include directories
participating in some AST cache keys but not others (findings 2, 10, 17);
and a real, still-open `include_sequence` mismatch between a `dump` baseline
and a `scan --against` of it, confirmed against live Bazel/castxml CI
evidence and still not fully root-caused as of this writing.

**Target shape:**

```text
ArtifactRequest
    ↓
resolve_artifact_request()
    ↓
ResolvedArtifactPlan
    ↓
execute_artifact_plan()
    ↓
ArtifactResult
```

`ResolvedArtifactPlan` carries: normalized input type and binary format;
requested and effective evidence depth; selected frontend and compiler;
headers and public-header scope; effective include search; effective
compile context; build/source collection plan; dependency scope;
cache-relevant paths; post-processing steps; provenance inputs.
`ArtifactResult` carries: the snapshot; achieved evidence depth; extraction
contract and fingerprints; effective compiler context; coverage and
degradation; executed stages and timings; advisories; post-processing
results. The same pipeline must serve `dump`, each side of `compare`, scan
candidate and native baseline extraction, release per-library extraction,
ABICC descriptor extraction, standalone appcompat's own dump-both-sides
path, and `deps compare`'s per-dependency-pair extraction.

**Lifetime problem.** Some effective include paths can point into a
temporary inferred-build directory that is deleted once the resolving
function returns (the deferred-cleanup design AGENTS.md's L3→L2-fold entry
documents at length). Returning more paths from a resolve step is not
sufficient — and scoping the resource to `execute_artifact_plan()` alone is
*also* not sufficient, for two reasons the design has to account for
together: the directory can already be at risk of cleanup by the time
`resolve_artifact_request()` returns — before any `execute_...` call ever
starts — and the dry-run path below resolves a plan but deliberately never
executes it, so a scope that only opens at `execute_artifact_plan()` would
never close for dry-run at all. Ownership has to span resolution through
execution (or through dry-run's own inspection), not begin partway through:

```python
with resolve_artifact_request(request) as plan:
    # plan.session owns the inferred-build directory (if any) from here
    if dry_run:
        return render_plan(plan)          # closes on context-manager exit
    with execute_artifact_plan(plan) as result:
        run_header_graph(result)
        attach_build_context(result)
        persist_snapshot(result)
        # result borrows plan.session's resources; still open here
```

`resolve_artifact_request()` returns a context-managed
`ResolvedArtifactPlan` whose `__exit__` releases whatever resources
resolution itself allocated (regardless of whether execution ever runs), and
`execute_artifact_plan()` borrows that same session rather than opening a
second one — so cleanup happens only after every extraction and
post-processing consumer, *and* dry-run's own inspection, has finished. This
removes the need for each call site to re-derive its own ordering and
deferred-cleanup rules — the exact class of bug the L3→L2-fold entry's fifth
finding (self-deadlocking duplicate inferred queries) had to be fixed for
one call site at a time.

One more failure mode this shape has to close, not just the two above:
Python fully evaluates `resolve_artifact_request(request)` — the function
body runs to completion or raises — *before* `with` ever calls `__enter__`
on whatever it returns. If resolution allocates the inferred-build
directory and only *then* fails a later validation step within its own
body, no `ResolvedArtifactPlan` is ever returned for a `with` block to call
`__exit__` on — the directory and its lock leak regardless of how carefully
the caller-side `with` is written, since the caller never sees an object at
all. `resolve_artifact_request()` therefore can't be "allocate, then
return a context manager" — its own body needs a `try`/`finally` (or an
`ExitStack` it owns and only hands off to the returned plan on success) so
a failure partway through resolution tears down whatever it had already
allocated before the exception propagates, rather than leaving that
responsibility for a caller who received nothing.

**Dry-run renders the plan, not a second prediction.** `resolve_dump_request`
(added by the CLI-cleanup-phase-two "PR C" slice) already provides a real
"resolve without executing" mode — the missing piece is wiring
`render_dump_dry_run()` to build from it, through the same
context-managed `ResolvedArtifactPlan` described above, instead of
independently re-deriving depth/collect-mode/backend feasibility (and
instead of any bespoke cleanup of its own — dry-run closes the same session
resolution opened, on the same `with` exit, whether or not execution ever
runs). Dry-run should report one of: definitely valid; definitely invalid;
unresolved until execution; requires trusted build execution — never
maintain its own approximation of execution semantics.

### P0 — Effective configuration and pack application

`pack_application.py` follows the right rule: it reads pack contributions
from the already-resolved `CompatibilityEvaluationConfig` and its
per-field `ValueProvenance` rather than reimplementing precedence or
conflict resolution (see AGENTS.md's own extensive documentation of D7/D8
in the module map). But the resolved answer still gets translated into
several different runtime shapes: single-pair `compare` uses a typed
resolved configuration; `scan` has its own receipt/configuration flow;
policy packs fold into `PolicyFile`; and the release fan-out now has its
own resolved gate object (`policy.release_gate_options.GateOptions`/
`resolve_release_gate_options`, ADR-064, landed 2026-09-02) rather than
raw scheme strings — closing this section's original "no equivalent typed
object at all" premise (Codex review, PR #1050, fresh evidence). What
remains: `GateOptions` is not itself `EffectiveGate`/
`EffectiveEvaluationConfig`-shaped. `apply_release_gate_pack()`'s manual
mirror of `pack_application.apply_to_compare_config`'s fold logic closed
under T6 (2026-09-05): both call one shared
`policy/gate_pack_fold.fold_gate_pack_severity`. What that did *not* close
is the reason the mirror existed — the release fan-out still has no
`ResolvedCompareConfig`-shaped object of its own to fold packs onto, so the
two callers still fold onto different runtime shapes around the one shared
rule. That remains this section's target.

**Target:** one runtime object,

```python
@dataclass(frozen=True)
class EffectiveGate:
    exit_code_scheme: str      # e.g. "legacy" or "severity"
    severity: EffectiveSeverity
    require_complete_analysis: bool
    scope: ScopedGateSelection | None  # ADR-043 --used-by/--required-symbol

@dataclass(frozen=True)
class EffectiveEvaluationConfig:
    policy: EffectivePolicy
    gate: EffectiveGate
    contract: EffectiveContract
    assurance: EffectiveAssurance
    surface: EffectiveSurface
    evidence: EffectiveEvidencePolicy
    suppressions: EffectiveSuppressions
    provenance: ConfigProvenance
    digest: str
```

`suppressions` carries resolved rule identity, not just a policy summary —
the existing `CompatibilityEvaluationConfig` already models `suppressions`
as its own field, separate from `policy`, precisely because a suppression
rule directly changes which findings reach verdict and gate calculation.
Without it here, two runs given different `--suppress` inputs could share
an identical `EffectiveEvaluationConfig` and digest while producing
different findings and exit codes — defeating the digest's whole point as
a parity key.

`gate` carries the resolved `exit_code_scheme` alongside severity, not
severity alone — for a run combining `--exit-code-scheme legacy` with a
severity-only gate pack, severity by itself can't recover which scheme was
selected, so a consumer would otherwise have to keep an out-of-band raw
string (defeating the point of one runtime object) or re-derive the scheme
from severity, reintroducing the exact bug CLI-cleanup-phase-two's PR B
already found and fixed once (a re-derived scheme let a severity-only gate
pack silently override an explicit `--exit-code-scheme legacy`).
`require_complete_analysis` belongs in the same object for the identical
reason, not as a separate follow-on: two otherwise-identical runs differing
only by `--require-complete-analysis` exit successfully in one and fail in
the other on incomplete evidence, and the existing digest implementation
(`effective_config_digest.py`) already records this input as its own
`gate.require_complete_analysis` key — leaving it out of the sole runtime
object this section proposes would mean two runs with genuinely different
gate behavior could still land on the same `EffectiveEvaluationConfig` and
digest. `scope` belongs for the same reason again: two runs selecting
different `--used-by` consumers or `--required-symbol` entrypoints
(ADR-043's scoped-gate selection) can produce different scoped findings,
verdicts, and exit contributions, and the existing digest implementation
already records this input as its own `gate.scope` key
(`effective_config_digest.py`'s `_gate_scope_str()`, encoding the
selection kind and its resolved targets) — omitting it here would recreate
the identical out-of-band-input problem `require_complete_analysis` above
was added to close, letting two differently-scoped runs share one digest.
All four `gate` fields feed the digest.

This object is consumed directly by `compare`, `scan`, the release
fan-out, and bundle/matrix findings alike, with the resolver remaining the
*only* place D7's precedence order (`explicit_cli/api_request >
legacy_alias > run_recipe > run_profile > project_config >
built_in_default`) is decided. The digest becomes a real parity key: same
normalized request + same effective-config digest ⇒ same policy/gate
interpretation, everywhere. (The reporter's existing effective-configuration
digest, landed for PR B, is a real, narrower precursor to this — see
"Relationship to in-flight work" below.)

"Bundle findings alike" is not aspirational here — it names a real,
already-documented gap this phase has to close, not just avoid repeating.
AGENTS.md's own "Known gaps" entry ("Bundle-level (cross-library) findings
on a directory/package `compare` never respect any policy override")
records that `bundle.compare_bundle()` computes `BundleDiffResult.
bundle_verdict` via `checker_policy.compute_verdict(changes)` with no
`policy=` argument at all — always the hardcoded `strict_abi` default —
while `_run_bundle_analysis`/`_collect_bundle_result`
(`cli_compare_release_helpers.py`) have no policy/`PolicyFile` parameter
either. So a release-wide `--policy`, `--policy-file`, or a `kind: policy`
pack overriding a `BUNDLE_*` kind reaches every *per-library* finding
correctly but silently leaves bundle-level findings (`bundle_library_
removed`, `bundle_intra_dep_removed`, ...) governed by the built-in policy —
a bundle finding can keep a release's worst-of verdict at `BREAKING` even
after the same kind was demoted or ignored everywhere else. Phase 2 closes
this specifically by having `compare_bundle`/`_run_bundle_analysis`/
`_collect_bundle_result` accept and classify against the same
`EffectiveEvaluationConfig` every per-library comparison already uses,
rather than a bundle-specific policy parameter threaded through in
isolation — with test coverage asserting policy parity between a release's
per-library and bundle-level verdicts for the same run, which is the gap
the AGENTS.md entry names as still open.

### P0 — Exit and gate decisions

`ExitDecision` (from CLI-cleanup-phase-two's "PR G1") models compatibility/
scoped-gate contribution, contract-coverage contribution, analysis-assurance
contribution, and promoted scan-crosscheck contribution — but explicitly not
yet `NOT_COMPARABLE`, scan budget overflow, release removed-library policy,
release operational errors, or aggregate's missing/unexpected-target
policies. Release therefore still owns its own precedence chain (not
comparable first, then removed-library exit 8, then operational-error
floor, then severity/verdict, then contract coverage folded separately), and
`aggregate` has to reverse-engineer upstream results — e.g. distinguishing
whether a scan's published exit `1` means contract coverage or a genuine
scan failure, because `scan`'s exit code is already an opaque fold of
several axes by the time aggregate sees it.

**Do not** simply extend the current implementation with `max()` over more
numeric codes — exit-code integers are not a reliable cross-operation
priority ordering (a `compare` `4` and a `scan` `4` do not always mean "the
same severity of thing is wrong"). Instead, make contributions and their
precedence explicit:

```python
@dataclass(frozen=True)
class ExitContribution:
    axis: ExitAxis
    active: bool
    code: int
    priority: int
    details: Mapping[str, object]

@dataclass(frozen=True)
class ExitDecision:
    code: int
    primary_reason: ExitAxis  # CLEAN when every contribution is inactive
    contributing_reasons: tuple[ExitAxis, ...]
    contributions: tuple[ExitContribution, ...]
```

with axes covering `clean` (mirroring the existing `ExitDecision`'s own
`ExitReason.CLEAN` — a successful run still needs a real, non-optional
`primary_reason` rather than one implementation fabricating a failure axis
and another making the field optional), `compatibility_gate`,
`scoped_gate`, `contract_coverage`, `analysis_assurance`,
`crosscheck_promotion`, `not_comparable`, `budget_overflow`,
`evidence_contract_error` (`scan`'s own `EVIDENCE_CONTRACT_ERROR` verdict —
`service_scan.run_scan()` returns it, with `exit_code=1`, when an explicitly
pinned `--depth` can't collect its required evidence; documented separately
from `not_comparable`/`budget_overflow` in `run_scan_core()` today, and
without its own axis it can only collapse into a generic
`operational_error` or stay the opaque exit-1 heuristic this phase exists
to remove), `bundle_incomplete` (`scan --artifact-set`'s own
`BUNDLE_INCOMPLETE` verdict — `service_scan.run_scan_set()` returns it,
also `exit_code=1`, when the member scans complete but not every snapshot
needed for the cross-library audit could be built; a second, distinct
scan-only incompleteness signal from `evidence_contract_error`, not a
duplicate of it), `operational_error`, `removed_required_artifact`,
`missing_required_target`, `unexpected_target`, and two axes for `deps
compare`'s own dependency-loadability result — `dependency_load_failure`
and `dependency_abi_risk` — since `cli_stack.py`'s `deps_compare_cmd`
today distinguishes three outcomes (loadability/ABI-break failure → 4,
ABI risk or loadability warning → 1) that don't map onto any axis above;
and per-operation policies (`NativeCompareExitPolicy`, `ScanExitPolicy` —
now also covering `evidence_contract_error` and `bundle_incomplete` —
`ReleaseExitPolicy`, `AggregateExitPolicy`, `AbiccExitPolicy`, and
`DepsCompareExitPolicy` for the two new dependency axes plus the existing
`not_comparable`) that read the same evaluated result but keep each
operation's own external exit-code contract — `compat`'s `0/1/2/...`
mapping in particular should
be derived through `AbiccExitPolicy`, not bypass the shared model.

### P1 — Reporting composes too late

Several renderers currently render a string, then reparse and patch it:
`service_render._render_json_output()` serializes via `to_json()`, parses
the JSON back, inserts dependency information, and re-serializes; dump
provenance is folded into already-rendered JSON text
(`fold_dump_provenance_into_dict`/`_into_json`); scoped-gate handling parses
rendered JSON, swaps full/scoped verdicts, adds findings not present in the
original `changes` array, recomputes summaries, and carries separate repair
logic per format (one-line, Markdown, review, SARIF, JUnit). Each of these
is well-commented precisely because each documents an incident where one
format disagreed with another because business semantics were applied
during or after formatting rather than before it.

**Target:** a canonical report intermediate representation, computed before
any serialization:

```python
@dataclass(frozen=True)
class ReportEnvelope:
    operation: OperationKind
    schema_version: str
    operational_state: OperationalState  # SUCCESS / NOT_COMPARABLE / ERROR / UNAVAILABLE
    inputs: InputReport
    resolution: ResolutionReport
    effective_config: EffectiveConfigReport
    evidence: EvidenceReport
    findings: tuple[ReportFinding, ...]
    full_evaluation: EvaluationSummary | None
    effective_evaluation: EvaluationSummary | None
    exit_decision: ExitDecision
    dependencies: DependencyReport | None
    timings: StageTimings
    advisories: tuple[Advisory, ...]
```

with `full_evaluation` (the whole-library result) and `effective_evaluation`
(post-scoping, e.g. `--used-by`/`--required-symbol`) kept distinct, and
every renderer (JSON, Markdown, SARIF, JUnit, HTML, one-line) as a pure
projection — none of them modifying verdicts, inventing findings,
recomputing gate status, or parsing another renderer's output.
`operational_state` is its own field, not folded into `exit_decision` or
either `EvaluationSummary` — the "Smaller, concrete duplication" section
below states the rule this field exists to satisfy: `OperationalState`
(`SUCCESS`/`NOT_COMPARABLE`/`ERROR`/`UNAVAILABLE`) must stay a distinct
axis from `CompatibilityVerdict` ordering, never spliced into it. Encoding
it only as an `ExitDecision` contribution would still leave aggregate and
renderers reconstructing operational status from exit-code semantics — the
exact drift this envelope exists to end; placing it inside either
`EvaluationSummary` would conflate a compatibility result with whether a
comparison could be evaluated at all, which is precisely what
`aggregate.py`'s existing, correct separation of these concerns already
gets right and this envelope must not regress.

`full_evaluation`/`effective_evaluation` are `| None` for the identical
reason the existing `compare_report.schema.json`'s own `verdict` property
is nullable and documents three operational sentinel values alongside the
five real ones: a library that failed during extraction or comparison in
a release fan-out, or a pair the comparability gate rejected before any
diff ran, never produces a `DiffResult` — an `ERROR`/`UNAVAILABLE`/
`NOT_COMPARABLE` `operational_state` report carries whatever partial
context (`inputs`, `resolution`, `advisories`) is actually available, not
a fabricated evaluation. Only `operational_state == SUCCESS` obligates
both evaluation fields to be present.

### P1 — Aggregate consumes representations, not decisions

`aggregate.py` correctly keeps compatibility, gate, target availability,
contract coverage, and analysis assurance conceptually separate, and
correctly refuses to treat a missing required report as a compatibility
verdict. But because upstream report shapes differ, it maintains
format-specific extraction rules for native-compare severity blocks, scan's
nested diff decisions, scan's top-level exit mapping, contract-coverage and
analysis-assurance contribution recovery, legacy-verdict fallbacks, and
release's operational-error sentinel. Once the `ReportEnvelope`/
`ExitDecision` work above lands, aggregate should read one uniform
`evaluation`/`exit` shape and stop inferring meaning from an integer based
on which command produced the document.

### P1 — ABICC compatibility is a parallel frontend and engine path

`abicheck compat` intentionally keeps a distinct user-facing contract — that
part is correct and should stay. But its implementation calls
`dumper.dump` and `checker.compare` directly rather than through the typed
dump/compare pipelines, then applies its own post-comparison
transformations, report dispatch, and exit mapping — so it structurally
cannot receive fixes that live only in the typed orchestration layer (evidence
resolution, contract/assurance processing, canonical exit decisions). Target:
an ABICC adapter that builds `DumpRequest`/`CompareRequest`/
`EffectiveEvaluationConfig` and hands off to the shared pipelines, keeping
only genuinely ABICC-specific concerns (flag aliasing, XML descriptor
parsing, suppression translation, report shape, exit-code mapping) in the
adapter itself. Post-comparison transformations (strict mode, source-only
filtering, warn-on-new-symbol) should become declared evaluation-policy
inputs rather than `DiffResult` mutations applied after classification.

### P1 — Compiler invocation handling needs one typed model

Recent fixes (referenced in this repository's git history and in
AGENTS.md's compiler-invocation and toolchain-profile entries) centralized
launcher stripping, environment-prefix handling, driver recognition,
split-operand decoding, and canonical encoding across build adapters,
header-compile-context derivation, L2 replay, L4 source replay, and include
graph collection — because equivalent compiler commands were being
interpreted differently by different consumers. The next step is moving
from shared *helper functions* to a shared *parsed object*:

```python
class CompileAction(Enum):
    OBJECT = "object"        # -c: source -> object file, compile stops here
    ASSEMBLE = "assemble"    # -S: source -> assembly text file
    PREPROCESS = "preprocess"  # -E: source -> preprocessed text (file or stdout)
    LINK = "link"             # no stop flag: compile straight through and
    # link -- the source's own intermediate object is an internal,
    # unnamed temporary the compiler manages itself, never a file this
    # model names; the invocation's own `output` field is the real result


@dataclass(frozen=True)
class SourceOperand:
    path: str
    language: Language  # effective language for THIS operand specifically
    action: CompileAction  # what this source's own compile stops at
    effective_output: Path | None  # this source's own resolved output; None
    # when the action streams to stdout (bare -E, no -o, no per-source
    # default target the way -c/-S have)


@dataclass(frozen=True)
class CompilerInvocation:
    original_argv: tuple[str, ...]
    recorded_directory: Path
    effective_directory: Path
    environment: EnvironmentOverlay
    launchers: tuple[str, ...]
    driver_token: str
    resolved_driver: Path | str
    driver_mode: DriverMode
    standard: str | None
    target: TargetConfig
    defines: tuple[DefineOp, ...]
    include_search: tuple[IncludeSearchEntry, ...]
    forced_includes: tuple[Path, ...]
    abi_flags: tuple[AbiFlag, ...]
    sources: tuple[SourceOperand, ...]
    link_inputs: tuple[str, ...]  # positional .o/.a/.so/... operands, in
    # argv order, for a link-shaped invocation (empty for a compile-only one)
    output: Path | None
    opaque_flags: tuple[str, ...]
```

`sources` — every positional argv token naming a translation unit, in
argv order, mirroring the existing `sources_from_argv()`'s own "return
**every** one" contract (`gcc -c a.c b.c` compiles two TUs in one
invocation, and a caller that wants all of them must not stop at the
first). `opaque_flags` cannot stand in for this: it is scoped to
unrecognized *flag* tokens, not positional operands, and nothing about a
bare filename argument marks it as source rather than some other kind of
operand. Without a dedicated field, an L2/L4 replay or build-attribution
consumer would have no way to recover which TU(s) an invocation actually
compiled without rescanning `original_argv` itself — exactly the
"parse once" contract this model exists to establish, and the omission
this field closes. Each entry carries its own resolved `language`, not
just a path: a single invocation can force a different `-x <language>`
between sources (`gcc -c -x c first -x c++ second` validly compiles
`first` as C and `second` as C++ — confirmed against real `gcc --help`,
which documents `-x` as applying to the input files that follow it), so
the top-level `language` field alone can't describe every entry. This is
a genuine improvement over the existing `effective_language()` helper,
not merely a restatement of it: that helper is documented as correct only
"for a single-source TU" — it returns the *last* forcing token seen
anywhere in `argv`, not the one that was actually in effect at a given
source's position, so a naive per-invocation reuse of it already gets a
multi-source case like this one wrong today. `SourceOperand.language`
must be resolved positionally (tracking `-x` state as it walks `argv`,
the way `sources_from_argv()`'s own forced-language tracking already
does) rather than by calling `effective_language()` once per source.
`CompilerInvocation` itself deliberately carries no invocation-wide
`language` field alongside this: a top-level field left undefined for a
mixed-language invocation like the one above, or defined only as some
other value (the driver's initial default, say), would give consumers two
sources of language truth that can legitimately disagree — and since this
model's whole premise is that a consumer must never rescan `original_argv`
to resolve ambiguity, a second, lower-fidelity authority would just invite
some consumer to read it instead of `sources[i].language` and reproduce
the exact divergence this parsed model exists to eliminate. Any caller
that only cares about a single-source TU's language reads
`sources[0].language`.

`output` — the resolved `-o <file>` operand *for a link-shaped invocation*
(or `None` when absent). A link invocation produces exactly one artifact
(an executable or shared library from `.o`/`.a` inputs), so a single field
is correct there, and real build attribution already needs and stores this
today (`CompileUnit.output`/`LinkUnit.output`, both populated by
`_output_from_argv()` in every build adapter and consumed by the source
graph and `link_attribution.py`) — omitting it from the parsed model would
mean output-to-source/link attribution has no way to recover it except by
rescanning `original_argv`, the same parse-once violation `sources` was
added to avoid.

`link_inputs` — every positional `.o`/`.a`/`.so`/`.lib`/... operand of a
link-shaped invocation (`g++ a.o libb.a -o app`), in argv order. `sources`
alone cannot carry these: a link invocation's positional operands are
already-compiled objects/archives, not translation units, so they belong
in neither `sources` (which names only source files with a resolved
`language`) nor `output` (a single field for the one produced artifact).
Without this field the existing `LinkUnit.inputs` this model is meant to
replace — consumed by `link_attribution.py` to match a link line's inputs
against the `CompileUnit.output`s that produced them — would have no way
to recover them except rescanning `original_argv`, exactly the parse-once
violation this model exists to end. Empty for a compile-only invocation.

For a **compile** invocation, `output` alone cannot describe the result:
`gcc -c a.c b.c` (multi-source, compile-only, no explicit `-o` — and GCC
itself rejects combining an explicit `-o` with `-c`/`-S`/`-E` across more
than one source file, so this is the *only* legal shape for a multi-source
compile-only invocation) implicitly produces `a.o` and `b.o`, one per
source, from each source's own basename — a single `Path | None` cannot
hold both. `SourceOperand.effective_output` carries this per-source
instead: the resolved output path this specific source compiles to,
whether that's the invocation's single explicit `-o` (only ever legal
paired with exactly one source) or this source's own default naming when
no explicit `-o` applies to it. The existing `CompileUnit.output`/
`_output_from_argv()` this model is meant to replace already assumes one
source per compile unit in practice (every build adapter's own
compile-line parser extracts one `source`/`output` pair per recognized
line, matching how real build-system-generated compile databases and
recipe lines are emitted — a build system splitting its own multi-source
invocations before recording them, not this model inventing new
semantics), so `effective_output` generalizes that existing, per-source
assumption rather than contradicting it.

`effective_output`'s naming and even its *kind* depend on
`SourceOperand.action`, not just `-c`: `gcc -c foo.c` defaults to
`foo.o`/`foo.obj`, but `gcc -S foo.c` (confirmed against real gcc) instead
produces `foo.s` (assembly text, `CompileAction.ASSEMBLE`), and `gcc -E
foo.c` (also confirmed) writes preprocessed text to **stdout** when no
`-o` is given at all — `CompileAction.PREPROCESS` with `effective_output
= None` in that shape, since there is no default *file* target the way
`-c`/`-S` have one; an explicit `gcc -E foo.c -o foo.i` still resolves to
`foo.i`. `gcc foo.c -o app` — no `-c`/`-S`/`-E` at all — is a fourth,
distinct shape: GCC's own `-c` documentation is explicit that it means
"compile and assemble, but do not link", so its *absence* compiles
straight through the assembler and linker in one invocation, producing
the linked `app` directly, never a persisted `foo.o` a build system could
name. `CompileAction.LINK` models this: the source's own intermediate
object is the compiler's internal, unnamed temporary (its exact path is
implementation-defined and not stable across compiler versions), so
`effective_output` is `None` here too — the real result lives on
`CompilerInvocation.output` (the invocation-level artifact `output`
already models for a link-shaped command). A model that always assigned
an `.o`-shaped default output regardless of `-S`/`-E`/no-stop-flag would
silently mismatch what the invocation actually produces.

`recorded_directory` is the compile-database entry's own `directory` field
(or the equivalent for a live build-adapter query) — not optional, since a
relative source or include operand's meaning depends on it. Two
otherwise-identical `argv` values executed from different directories can
resolve entirely different headers, so recording `original_argv` alone
would force replay and include normalization to either fall back to
abicheck's own process cwd (silently wrong whenever that differs from the
compile unit's own recorded directory) or keep an out-of-band value next to
the parsed object — exactly the "parse once" contract this model exists to
establish. It participates in the invocation's own identity, the same way
this field already has to for the L3→L2 fold's cache-key/relative-path
handling described in AGENTS.md's own entry for that work. `recorded_directory`
alone is not sufficient, though — the two fields split for a real reason
the existing `_argv.py`/`clang.py` parsing machinery already had to solve:
a launcher prefix can itself change the effective directory (`env -C build
clang ...`, GNU `env`'s documented `-C`/`--chdir`) independently of any
`recorded_directory` compose (`env -C a env -C b ...` → `a/b`), so
`effective_directory` — mirroring the existing `effective_directory()`
helper — is what replay must actually resolve relative operands against,
**including a compile-database entry's own `@response-file` expansion**:
`build_context.py`'s existing parser currently expands response files
against the recorded directory before stripping/applying launcher
prefixes, which is the identical class of bug this field exists to
close — response-file expansion belongs after launcher-prefix resolution,
against `effective_directory`, exactly like every other relative operand.
`recorded_directory` stays the raw, unmodified compile-database value for
identity/provenance. `environment` is a small structured type, not a bare
mapping (or a mapping-vs-cleared-sentinel union — a first pass at this
model tried exactly that and it still couldn't represent GNU `env`'s real
grammar, `env [OPTIONS] [NAME=VALUE]... COMMAND`, which lets `-i`/`-u` and
inline assignments compose in one invocation: `env -i CPATH=/sdk clang
...` clears the inherited environment and then sets one variable on top;
`env -u CPATH clang ...` removes a single named variable, independent of
any clearing; either shape can carry any number of ordinary `NAME=VALUE`
assignments alongside it):

```python
@dataclass(frozen=True)
class EnvironmentOverlay:
    clear_inherited: bool                    # a clear occurred somewhere in the prefix chain
    unset: frozenset[str]                    # names removed AND NOT later reset — final state
    assignments: tuple[tuple[str, str], ...] # NAME=VALUE surviving to the driver, in argv order
```

A plain `{}` (or a single cleared-vs-not sentinel) can't carry this: it
can't distinguish "no override recorded" from a real `env -i` clear, can't
hold an assignment applied *on top of* a clear in the same invocation, and
can't name which variable(s) an `-u` removed — and the distinction matters
concretely, since `CPATH`/`C_INCLUDE_PATH`/`CPLUS_INCLUDE_PATH` directly
alter compiler include search, not merely driver lookup the way `PATH`
does. Without this, replay would have to rescan `original_argv` itself
(defeating the parse-once contract again) or risk resolving a different
include search than the real build used. This generalizes, rather than
replaces, `_argv.py`'s existing `_EnvPathCleared` sentinel and
`path_cleared` state — but the equivalence is narrower than "OR the two
flags": `"PATH" in unset` alone is always correct (an explicit unset always
means "no `PATH`"), while `clear_inherited` alone is **not** — a clear
followed by a later `PATH=...` assignment in the same folded chain (`env -i
PATH=/sdk clang ...`) leaves `PATH` genuinely set, not cleared, since the
fold (per the ordering note above) already applied the assignment on top
of the clear. The correct equivalence is `"PATH" in unset or
(clear_inherited and "PATH" not in {name for name, _ in assignments})` —
"cleared and never reassigned." The parsed model should carry what a real
`env` invocation can express, not a simplified projection of it.

`unset` and `assignments` are **not** independently-collected sets that a
consumer merges later — nested launchers can make identical raw field
*contents* describe opposite final environments depending on order (`env
FOO=x env -u FOO clang ...` ends with `FOO` absent; `env -u FOO env FOO=x
clang ...` ends with `FOO=x`; both are two real, distinct, legal GNU `env`
invocations). The single parse must fold the whole launcher-prefix chain
*in argv order* — the same left-to-right traversal `_argv.py`'s
`_traverse_env_and_launcher_prefix()` already performs — into one
normalized final state before populating this dataclass: `assignments`
holds only the value each name has *after every later operation in the
chain*, `unset` holds only a name removed and never subsequently
reassigned, and a later `-i` clears whatever `assignments`/`unset` state
the fold had accumulated so far, not merely the ones this dataclass would
otherwise expose. Once folded, the two fields are safe to read
independently — the ordering risk lives entirely in how they are
*produced*, not in what they represent once parsing is done.

Raw compiler-command parsing happens once; replay, ambiguity detection,
build-option drift, and reporting all consume the structured fields instead
of re-scanning argv.

### P1 — Dependency direction and CLI leakage

`scan_engine.py` — documented as the shared engine for both CLI and typed
API — still imports `click`, raises `click.ClickException`, prints via
`click.echo`, and imports helpers from `cli_scan_baseline`/
`cli_scan_helpers`. `service_input_resolution.py` imports
`_is_inputs_pack_dir` from a CLI helper module. `service.py` uses a dynamic
`importlib` import specifically to stay invisible to the static
import-cycle checker rather than resolve a real cycle. Target architectural
rule, to become a real `check_ai_readiness.py`-style gate (Phase 0 below):

```text
models / leaf utilities
        ↑
domain primitives
        ↑
artifact / compare / scan engines
        ↑
service/application operations
        ↑
CLI / Python facade / Action / compat adapters
```

Engine modules may not import `click`; engine/service modules may not
import `cli_*`; CLI modules may not call `dumper.dump`, `checker.compare`,
or `service.resolve_input` directly (the `cli-contract` gate now enforces
all three, allowlist-and-shrink over today's pre-existing call sites — see
Phase 0 item 2 below); frontends only build requests, call
application operations, render results, and translate exceptions;
pack detection belongs under `buildsource`, not a CLI helper module;
progress notification uses callbacks/events, not `click.echo`.

## Smaller, concrete duplication

**Verdict ordering** is independently re-derived in `BundleDiffResult`,
the release summary rollup, and the aggregate rollup (aggregate additionally
carries its own legacy exit map). Separate `CompatibilityVerdict` ordering
(`NO_CHANGE < COMPATIBLE < COMPATIBLE_WITH_RISK < API_BREAK < BREAKING`)
from `OperationalState` (`SUCCESS / NOT_COMPARABLE / ERROR / UNAVAILABLE`) —
an operational state should never be spliced into a string-keyed
compatibility ordering; let rollup policy decide how one dominates or
coexists with the other.

**Bundle findings are lowered too early.** `BundleFinding` mirrors `Change`
and then flattens bundle attribution (consumer/provider) into the
description string purely to reuse existing reporters. A `FindingLike`
protocol that keeps `subject`/`attribution` as structured fields would let
bundle-specific data stay data instead of becoming a formatted prefix.

## What should explicitly *not* be unified

This plan is about collapsing decisions that are duplicated, not about
erasing legitimate domain differences. Keep separate:

- ELF, PE, Mach-O, DWARF, PDB, BTF, and CTF parsers
- CastXML and Clang extraction backends
- pair-only decisions (old/new extraction concurrency, pair-wide
  language-standard reconciliation) — `service_compare_pipeline.py`'s own
  module docstring already explains why these stayed out of the per-input
  primitives in `service_input_resolution.py`
- bundle symbol-resolution analysis versus per-library ABI detection
- ABICC's report shape and external exit-code compatibility contract
- JSON, Markdown, SARIF, JUnit, and HTML serializers
- identity rules that genuinely differ by entity type (functions,
  variables, types — see `finding_identity.py`'s own tiered design)

The boundary: different backends may collect facts differently, but must
return the same typed domain models, and must never independently decide
configuration, evidence depth, verdicts, gates, or report semantics.

## Target architecture

```text
CLI / Python API / ABICC / GitHub Action
                  │
                  ▼
           Frontend adapter
        parse syntax; no decisions
                  │
                  ▼
           OperationRequest
                  │
                  ▼
         resolve_operation()
                  │
                  ▼
         ResolvedOperation
     ┌────────────┼────────────┐
     │            │            │
 artifact plan  effective    set/matrix
                config        plan
     └────────────┼────────────┘
                  ▼
          execute_operation()
                  │
                  ▼
             RunResult
       snapshots + raw findings
                  │
                  ▼
          evaluate_result()
                  │
                  ▼
           EvaluatedResult
     verdicts + scope + coverage
          + ExitDecision
                  │
                  ▼
          build_report_model()
                  │
                  ▼
           ReportEnvelope
     ┌──────┬──────┬──────┬──────┐
     ▼      ▼      ▼      ▼      ▼
   JSON    MD    SARIF   JUnit   HTML
```

One producer, many projections — not several producers kept equivalent
through ongoing parity fixes.

## Implementation sequence

### Phase 0 — Architectural guardrails first

Add tests establishing the desired ownership *before* moving more code,
mirroring how `scripts/check_ai_readiness.py`'s `import-cycle-growth` and
`cli-contract` checks already work (baseline-and-shrink, not
block-everything-immediately):

1. No `scan_engine`, `service*.py` (including bare `service.py`),
   `artifact_*`, or `buildsource` engine module imports `click` or `cli_*`.
   **Implemented**: `scripts/
   check_ai_readiness.py`'s `engine-cli-boundary` check (allowlist-and-shrink,
   `ENGINE_CLI_BOUNDARY_ALLOWLIST`; `tests/test_engine_cli_boundary.py`).
   Known residual gap, not yet closed: the check is AST-based and can only
   see a real `import`/`from` statement, so it cannot catch
   `service.py`'s own `importlib.import_module()` escape hatch — used
   specifically because that dynamic form is invisible to `import-cycle-
   growth`'s AST walk too (see AGENTS.md's own note on why). Closing that
   needs either a literal-string-argument scan for `importlib.import_module`
   calls naming a `cli_*`/`click` target (a real but narrower AST pattern
   than the static-import case) or removing the dynamic import once Phase 1
   resolves the cycle it was working around — left as a follow-up rather
   than attempted as a drive-by widening of this check's first version.
2. No CLI or `compat` module calls `checker.compare`, `dumper.dump`, or
   `service.resolve_input` directly (extends the existing `cli-contract`
   gate, which previously only covered `checker.compare`). **Implemented**:
   `scripts/check_ai_readiness.py`'s `check_cli_contract` now walks a
   generic `_TIER1_TARGETS` table (`checker.compare`, `dumper.dump`,
   `service.resolve_input`) over every `cli*.py`, `appcompat.py`, and
   `compat/cli.py` module (the last was previously outside this check's own
   `_iter_cli_contract_sources()` scan entirely, despite being exactly the
   "nested front end" this item names), allowlist-and-shrink
   (`CLI_CONTRACT_ALLOWLIST`, pre-populated with the seven pre-existing,
   already-documented call sites this same plan's P0/P1 sections name:
   `cli_dump_helpers.perform_elf_dump`, `appcompat.check_appcompat`'s two
   dump calls, `cli_scan_baseline`'s baseline resolution, and
   `compat/cli.py`'s three direct calls). `cli_resolve.py`'s own
   `_resolve_input()` — the CLI's designated, framework-aware wrapper over
   `service.resolve_input` (see its module docstring) — is the one
   exemption from the `service.resolve_input` rule, the same role
   `service.py` itself already plays for `checker.compare`.
   `tests/test_cli_contract.py` mirrors both the generalized detection (an
   aliased-import and an aliased-module-call case per new target, plus a
   not-flagged case for the sanctioned wrapper) and a
   `test_cli_contract_allowlist_entries_are_real_violations` freshness
   check so a fixed call site can't leave a stale allowlist entry behind
   unnoticed.
3. Every artifact extraction call site *for the seven user-facing
   operations and the two internal supplementary call sites named in
   Phase 1* routes through the future artifact application service.
   Deliberately excludes `probe_harness.
   _snapshot_object_file()`: it has no CLI/API entry point today (nothing
   outside `probe_harness.py` and its own tests calls
   `run_probe_matrix()`), so it is a backend-level exception recorded here
   explicitly, not a call site this guardrail can silently forget — if a
   real user-facing command starts calling `run_probe_matrix()`, that
   command's routing becomes a Phase 1 item at the same time, not a
   drive-by addition to this guardrail's allowlist.
4. Every *completed-operation exit of a modeled compatibility-analysis
   command* — one of the operations `ExitDecision`'s axes actually cover
   (`compare`, `scan`, release, aggregate, `compat check`, `deps
   compare`) — derives from an `ExitDecision` (Phase 3). Named as `compat
   check` specifically, not bare `compat`: the group has a second
   subcommand, `compat dump`, which only creates an ABI snapshot and has no
   evaluated compatibility result — the same fabricated-state problem the
   next sentence already rules out for native `dump`, so it gets the
   identical treatment rather than being silently swept in under the
   group's name. Standalone `appcompat` is deliberately excluded from this
   *command*-exit list, not merely unmentioned: `appcompat.check_appcompat()`
   has no registered CLI command or process exit of its own to derive one
   from (`cli_options_contract.py`'s `VERDICT_EMITTING_COMMANDS` records that
   it was folded into `compare --used-by` per ADR-043, and
   `check_appcompat()` is a Python helper returning `AppCompatResult`, not an
   exit-code-bearing command) — a caller reaching it through `compare
   --used-by` is already covered by `compare`'s own `ExitDecision`, and a
   direct Python-API caller of `check_appcompat()` wants a *result-shape*
   guarantee (does the comparison `check_appcompat()` runs agree with every
   other comparison path), not a process-exit guarantee this guardrail
   models. That is a distinct, API-level requirement, already tracked
   separately below under "Comparison equivalence" (which names
   `appcompat.check_appcompat()`'s and `check_plugin_host_contract()`'s own
   pre-scope comparisons explicitly) — not folded into this command-exit
   list. `dump` (native) is deliberately excluded from this list,
   not merely unmentioned: a plain `dump` performs no compatibility
   evaluation at all — its own target
   pipeline (P0's artifact-resolution section above) ends at
   `ArtifactResult`, never at an evaluated compatibility result, and Phase
   3's per-operation policy list has no `DumpExitPolicy` for exactly that
   reason. Requiring `dump`'s exit to derive from `ExitDecision` would force
   fabricating compatibility-evaluation state a bare extraction command
   never has. Scoped deliberately in two further directions: (a) a bad
   invocation or an aborted run has no evaluated result to derive a
   decision from, and `cli.py`'s `_AbicheckGroup.main` already, correctly,
   maps those before any operation runs (Click `UsageError` →
   `_EXIT_USAGE_ERROR`/64, `click.exceptions.Abort` → 1); (b) a `project
   validate`/`project validate-build`/`project plan`-family command
   (`cli_project.py`) builds its own evaluated report and exits `0 if
   report.ok else 1` on a question `ExitDecision`'s axes don't model at all
   (config/build-manifest validity, not ABI compatibility) — this guardrail
   must not force any of these three shapes into `operational_error`, or
   demand a permanent, unreviewable exception for any of them.
5. Every persisted *compatibility-analysis* report (the same modeled
   operations as item 4) is built from a `ReportEnvelope` (Phase 4).
   Scoped identically and for the identical reason: `project validate` and
   `project validate-build` (`cli_project.py`) persist their own validation
   report via `--output` too, but that report has no ABI findings, no
   full/effective evaluation, and no compatibility `ExitDecision` to carry
   — it answers a config/build-manifest validity question, not this
   guardrail's question. Forcing it through `ReportEnvelope` would mean
   fabricating compatibility semantics a config-validity report doesn't
   have; a project-config report is either out of scope for this check or,
   if it should eventually gain its own generic envelope, that is its own
   design question left to a `project`-specific follow-up, not solved by
   stretching `ReportEnvelope` to cover it.
6. Every effective evaluation carries a digest (Phase 2). **True for its
   natural scope** (`compare`, `scan`, `release`, `aggregate`) as of this
   audit: `compare` (`service_render.py` → `reporter.to_json`/
   `to_stat_json` → `add_effective_config_digest`,
   `reporter_contract_blocks.py`), `scan` (`cli_scan_baseline.py` calls the
   same helper directly on its own summary dict), and `release` (the
   per-library fan-out's own persisted reports go through the same
   `reporter.to_json`) already carried the digest before this audit.
   `aggregate` did not — `AggregateResult.to_dict()`'s per-target roll-up
   (`_LoadedReport` → `TargetReport`) silently dropped each target's own
   already-computed digest rather than carrying it through. Closed: both
   dataclasses gained an `effective_config_digest: str | None = None`
   field (declared last, matching this file's own established
   positional-construction-safety convention for `TargetReport`/
   `_LoadedReport`), read straight off the loaded per-target report JSON in
   `_load_report_file` — never recomputed, since `aggregate.py` holds none
   of the `DiffResult`/`SeverityConfig` evidence
   `effective_config_fields`/`add_effective_config_digest` are typed
   against. Regression coverage:
   `tests/test_aggregate_effective_config_digest.py` (a sibling of
   `test_aggregate_analysis_assurance.py`'s own split, for the identical
   file-size-cap reason — `test_aggregate.py` was already at 1982 lines).
   **Two operations are deliberately excluded from this item's scope, not
   silently unmentioned** (mirroring item 4's `dump`/`appcompat`
   exclusions above): `compat check` doesn't get the digest by *design*,
   not oversight — `compat/cli.py`'s `_generate_compat_report` calls
   `to_json(r, include_exit_decision=False)` deliberately, since the
   existing digest's `gate.exit_code_scheme`/`gate.severity.*` fields
   describe only `compare`'s legacy/severity scheme and say nothing about
   `compat`'s own `-strict`/`-source`/`-binary`/`-warn-newsym` transform
   options — emitting the same digest for two behaviorally-different
   `compat` runs would be actively misleading, not merely incomplete.
   `deps compare` has no digest at all: it builds its report from a
   `StackCheckResult` (`abicheck/stack_checker.py`/`stack_report.py`), not
   a `DiffResult` — `effective_config_fields`/`add_effective_config_digest`
   are typed against the latter, so closing this gap needs either
   generalizing that machinery to accept `deps compare`'s own
   policy/severity-equivalent knobs or a new stack-specific digest
   function mirroring the existing one's shape, a moderate-to-large design
   task left as a documented residual rather than attempted here.

Each check starts with a reviewed allowlist of acknowledged pre-existing
violations, the same pattern `IMPORT_CYCLE_ALLOWLIST` already uses — the
list must only shrink, and a new entry requires the same sign-off bar
AGENTS.md already sets for that allowlist (an ADR or explicit maintainer
sign-off, not a routine PR).

### Phase 1 — Finish artifact-resolution convergence

1. **Started (Milestone A).** `abicheck/artifact_plan.py` introduces
   `ResolvedArtifactPlan` — a real, independently-tested context-managed
   session (`tests/test_artifact_plan.py`) owning the
   `list[Callable[[], None]]` cleanup accumulator every resolution call site
   used to thread and drain by hand. Wired into exactly one already-isolated,
   already-well-tested call site as proof: `cli_dump_helpers.
   perform_elf_dump()`'s `_l2_pending_cleanups` accumulator is now a
   `ResolvedArtifactPlan` instance, with identical cleanup thunks
   (`seed_includes_and_fold_compile_context(..., pending_cleanups=
   _artifact_plan.pending_cleanups)`) and identical timing (drained via
   `run_cleanups()` at the exact two points the old code called
   `_run_cleanups()` — immediately on a failed header parse, or after the
   whole post-dump pipeline completes) — a behavior-preserving refactor, not
   a new resolve/execute split. Existing `perform_elf_dump` coverage
   (`tests/test_cli_dump_helpers_coverage.py`, including
   `test_perform_elf_dump_wraps_dump_errors_still_cleans_up_seeded_dirs`)
   passed unmodified against the migration. **Milestone A follow-up.**
   `handle_non_elf_dump`'s identical `_l2_pending_cleanups` accumulator (the
   PE/Mach-O dump path) is now migrated the same way — its own, separate
   `ResolvedArtifactPlan` instance (the two functions are independent dump
   paths never invoked together, so nothing here makes them share a
   session), identical single-drain timing (the one `finally` this
   function's cleanup ever ran from). Existing coverage
   (`tests/test_non_elf_dump_l2_seed.py`,
   `tests/test_cli_dump_helpers_coverage.py`) passed unmodified.
   **Milestone A completion.**
   `service_input_resolution._resolve_side_snapshot_impl`'s own hand-rolled
   `cleanups: list[...] = []`
   + manual `if cleanups: _run_cleanups(cleanups)` finally block — the third
   and, per this item's own earlier audit, last known call site with this
   exact pattern — is migrated the same way: its own `ResolvedArtifactPlan`
   instance, `_seeded_includes_and_compile_context`'s returned cleanups list
   extended onto `_artifact_plan.pending_cleanups` (that helper's own return
   contract is unchanged — only what the caller does with the returned list
   changes), drained via `run_cleanups()` at the identical point the old
   code called `_run_cleanups()`. This is the shared primitive `compare`'s
   implicit-dump operand and `dump`'s typed `execute_dump_request` both
   already route through (`resolve_side_snapshot`), so this migration reaches
   both without touching either of their own call sites. Existing coverage
   (`tests/test_header_compile_context.py`,
   `tests/test_bazel_root_targets_l2_seed.py`,
   `tests/test_bazel_root_targets.py`,
   `tests/test_scan_l2_cleanup_ordering.py`,
   `tests/test_typed_dump_request.py`,
   `tests/test_dump_cli_typed_api_parity.py`,
   `tests/test_header_compile_context_gcc_path.py`,
   `tests/test_header_compile_context_merge.py`,
   `tests/test_clang_public_roots_coverage.py`)
   passed unmodified. All three call sites the plan's own earlier audit
   named now share this one primitive.

   **Milestone B (this slice): investigated item 1's "full shape" against
   the real code before writing anything, and found a real conflict with an
   already-documented, deliberate design decision — not an oversight to
   fix.** Item 1's own text (kept below) asks `ResolvedArtifactPlan` to own
   any resource resolution allocates "from `resolve_artifact_request()`
   onward," and to carry the resolved-fact fields the target-architecture
   section lists. Two of those fields — *effective include search* and
   *effective compile context* — are only known once the L3→L2
   compile-context fold runs
   (`buildsource.l2_seed.seed_includes_and_fold_compile_context`), and that fold is the one step
   in this whole pipeline that can allocate the inferred-build temp
   directory this session type exists to own. `service_dump_pipeline.py`'s
   own `resolve_dump_request()`/`execute_dump_request()` split (G33 Phase 5,
   already landed, independent of this plan) already answers where that
   fold runs today: deliberately inside `execute_dump_request`, never
   `resolve_dump_request` — `ResolvedDumpRequest`'s own docstring states why
   verbatim: `dump --dry-run`'s existing contract is to never raise on
   anything but a usage error, and the fold can raise
   `HeaderCompileContextAmbiguousError` on genuinely ambiguous build
   evidence. Moving the fold into resolution to satisfy item 1's literal
   text would mean either breaking that already-reasoned dry-run guarantee,
   or redesigning it to tolerate a raise — a real behavior change to
   already-shipped, already-reviewed code, not a clean generalization.

   **What was safe to land, and did:** the fields item 1's target shape
   names that genuinely *are* knowable without the fold — normalized
   binary format, language, requested/effective header-AST backend,
   requested depth, effective collect mode, public-header scope — are
   exactly the facts `resolve_dump_request()` already computes for
   `ResolvedDumpRequest`. `abicheck/artifact_plan.py`'s `ResolvedArtifactPlan`
   (Milestone A's cleanup-owning session type) now also carries these as
   optional, keyword-only fields, all defaulting to `None`/`()` so the three
   existing Milestone A call sites' bare `ResolvedArtifactPlan()`
   construction is completely unaffected. `resolve_dump_request()` attaches
   one, populated verbatim from the same values it already returns on
   `ResolvedDumpRequest` — a new, additive `artifact_plan` field on that
   dataclass (defaulted, so no existing caller breaks), built with an empty
   `pending_cleanups` (resolution allocates nothing today, so that is an
   honest report, not a placeholder). This is genuinely inert data today —
   nothing yet reads `resolved.artifact_plan` — but it means a future
   consumer of the general shape (the eventual `render_dump_dry_run()`
   migration named in AGENTS.md's "PR C" entry, most concretely) has one
   object to build from instead of `ResolvedDumpRequest`'s own dump-specific
   one, without this dataclass's field surface changing again when that
   lands. Regression coverage: `tests/test_artifact_plan.py` (default
   construction unaffected; resolved facts stored verbatim; a plan carrying
   resolved facts is still a fully functional cleanup session) and
   `tests/test_typed_dump_request.py::TestResolveExecuteDumpRequestSplit::test_resolve_attaches_a_matching_artifact_plan`
   (the attached plan's
   fields never independently drift from `ResolvedDumpRequest`'s own —
   confirmed to fail against the pre-change code).

   **Still not done, and now understood to be blocked on a real design
   decision rather than merely unattempted**: there is still no
   `resolve_artifact_request()`/`execute_artifact_plan()` *function* pair
   (only `resolve_dump_request()`/`execute_dump_request()`, dump-specific);
   `dump --dry-run` still doesn't build from or render a
   `ResolvedArtifactPlan`; and none of items 2–10 below have been
   attempted. Closing the resource-lifetime half of item 1 for real needs
   one of: (a) redesigning dry-run's own contract to tolerate the fold's
   possible raise (a considered, documented behavior change, not a
   generalization); or (b) accepting that the fold's resource lifetime
   stays scoped to execution only, and treating item 1's own "from
   resolve_artifact_request() onward" language as applying to the
   resolved-*fact* fields (now done) rather than to every resource a
   pipeline might ever allocate. Neither was decided here — recorded as an
   open design question for whoever picks up item 1's remaining resource-
   lifetime half, rather than guessed at under continued session pressure,
   per this plan's own "known gaps over risky reactive patches" convention.

1. (Full shape, not yet reached) Introduce `ResolvedArtifactPlan` as a
   context-managed session that owns any resource resolution itself
   allocates (e.g. an inferred-build directory) from
   `resolve_artifact_request()` onward — not scoped to
   `execute_artifact_plan()` alone, since dry-run resolves without ever
   executing and must still close the same session.
2. Move `perform_elf_dump`'s remaining post-processing hooks (ADR-039
   build-context collection, the header-graph second pass, the optional
   clang-layout-tool attach) into explicit post-processing stages against
   the new plan/result shape.
3. Route native `dump` through the typed artifact pipeline (closing the
   long-open "`dump` doesn't build a `DumpRequest`" gap named in G33 and
   CLI-cleanup-phase-two's PR C).
4. Route scan candidate and native baseline through the same pipeline.
5. Route PE/Mach-O through the same orchestration while preserving
   backend-specific extraction.
6. Route `appcompat.check_appcompat()`'s standalone dump-both-sides path
   through the same pipeline too, so a direct caller of that function gets
   the same resource lifetime, compile-context fold, and cache-relevant
   paths `compare`'s own app-usage scoping already benefits from.
7. Route `deps compare`'s per-dependency `_run_abi_diff()` through the same
   pipeline, and fold its loadability/ABI-risk exit computation
   (`cli_stack.py`'s own `sys.exit` calls) into Phase 3's `ExitDecision`
   work rather than leaving it as yet another independent exit-code path.
8. Route `l0_export_delta.collect_l0_export_delta()`'s `symbols_only=True`
   supplementary re-extraction, *and* `scan_engine._load_exports_for_poi()`'s
   own `symbols_only=True` POI prepass, through the same pipeline. Explicit,
   separate step rather than assumed-covered by steps 3/4: `compare`'s and
   `scan`'s own *primary*-side resolution moving onto
   `ResolvedArtifactPlan` does nothing for either of these secondary calls,
   since both resolve independently, ahead of or after the primary sides
   are already settled.
9. Make dry-run render the resolved plan.
10. Delete the now-redundant duplicated seed/fold/resolve paths this closes
    over (several are already named as follow-ups in AGENTS.md's L3→L2-fold
    entry).

Highest-value phase: it removes both correctness duplication (the
`include_sequence`/comparability-mismatch class of bug) and real
performance duplication (redundant inferred build queries).

### Phase 2 — Make resolved configuration the runtime contract

1. **Started.** Item 1's own target shape needed re-scoping before any code
   could land — recorded below rather than guessed at, per this plan's own
   "known gaps over risky reactive patches" convention (AGENTS.md), and the
   first slice of the corrected scope has since landed.
   Investigating item 1 (introduce `EffectiveEvaluationConfig`) against the
   actual codebase — not just this document's own sketch — found the
   target shape overlaps far more with an *already-existing* object than
   this section's original text accounted for:
   `abicheck/compatibility_evaluation_config.py`'s
   `CompatibilityEvaluationConfig`
   (ADR-049 D7) already composes `policy` (`CompatibilityPolicyConfig`),
   `gate` (`GateConfig` — `exit_code_scheme`, `severity: SeverityConfig`,
   `preset`, `packs`), `contract` (`ContractConfig`), `assurance`
   (`AssuranceConfig`), `surface` (`SurfaceConfig`), `evidence`
   (`EvidenceConfig`), and `suppressions` (`SuppressionConfig | None`) —
   essentially every sub-object this section's own `EffectiveEvaluationConfig`
   sketch names, under different but directly corresponding field names,
   plus real per-field `ValueProvenance` (D7's precedence-tier record) this
   section's sketch only gestures at via a single
   `provenance: ConfigProvenance` field. The "Relationship to in-flight work"
   section
   below already says as much at the plan level ("Public contract default
   (ADR-049) is the compatibility-configuration resolver Phase 2 makes the
   sole runtime contract... this plan does not change ADR-049's own D7/D8
   precedence rules, only how uniformly the result of applying them reaches
   every operation") — but item 1's own code sketch, written before this
   closer look, reads as "introduce a new, separate dataclass," which would
   recreate exactly the kind of duplication this whole plan exists to
   eliminate, and directly contradicts the file it sits in a few lines
   below its own sketch.

   **What was genuinely missing from `CompatibilityEvaluationConfig`,
   confirmed by grep rather than assumed:** (a) `GateConfig` had no
   `require_complete_analysis` field — that flag is threaded as a raw
   `bool` through a long, independent chain of function signatures
   (`cli.py`, `cli_compare_helpers.py`, `cli_compare_options.py`, ~15+ call
   sites) with no typed home at all; (b) no `scope`
   field for ADR-043's `--used-by`/`--required-symbol` scoped-gate
   selection either, and there was no existing `ScopedGateSelection`-shaped
   type anywhere in the codebase to reuse — representing that scope
   correctly (a real union of "no scope" / "used-by consumer" /
   "required-symbol entrypoint", each carrying its own resolved target
   list) needed its own small design pass, not a one-line addition; (c) no
   whole-object `digest` field or method — `effective_config_digest.py`
   already computes one, but as an external function over a *two-tier*
   input (`DiffResult`'s own fields, only reading a
   `CompatibilityEvaluationConfig` when one happens to be attached under
   `--contract`/`--pack`), not a method on this object; and (d) — the
   deepest gap, still open — `CompatibilityEvaluationConfig` is
   deliberately *opt-in* today (built only when `--contract`/`--pack`
   selected something, documented as an explicit ADR-049 Phase 1-6 design
   choice in both that module's docstring and `effective_config_digest.py`'s
   own "two tiers" docstring), while this phase's whole point is a runtime
   contract resolved for *every* comparison unconditionally.

   **First slice landed: (a) and (b), additively.** `GateConfig` gained
   `require_complete_analysis: bool = False` and
   `scope: ScopedGateSelection | None = None`, both defaulting to "no
   effect" so every existing zero/keyword-arg `GateConfig(...)` constructor
   (`compatibility_evaluation_frontend.py`, `contract_context.py`,
   `contract_context_io.py`) keeps working unchanged — confirmed by the
   full existing test suite for this module passing unmodified (153 tests)
   plus the broader compatibility-evaluation/digest/pack/contract-context
   suites (1224 tests). `ScopedGateSelection` is a new frozen dataclass in
   the same module (`kind: str` validated against
   `{"used_by", "required_symbol"}`, `targets: tuple[str, ...]`), typed to
   match the
   encoding `effective_config_digest._gate_scope_str` had already
   established informally (reading `DiffResult.gate_scope`/`used_by`/
   `required_symbols` and JSON-encoding `{"kind": ..., "targets": ...}`) —
   this new type doesn't invent a shape, it names one that already existed
   as an untyped convention in one function. New test classes
   (`TestGateConfigRequireCompleteAnalysisAndScope`,
   `TestScopedGateSelection`) in
   `tests/test_compatibility_evaluation_config.py` cover both fields'
   defaults, validation, and (for `ScopedGateSelection`)
   frozen/equality/order-preservation semantics.

   **Deliberately not yet done, in this slice:** neither field is wired to
   anything yet — no resolver reads the raw `require_complete_analysis`
   bool or the raw `used_by_apps`/`required_symbols` tuples
   (`cli_compare_helpers._apply_scoped_gating`) and constructs a
   `ScopedGateSelection`/populates `GateConfig.scope` from them; the ~15+
   raw-bool call sites are untouched. That wiring is real, separate work —
   it's exactly what items 2-6 below need to do anyway (`compare`/`scan`
   *resolving* this object, not merely consuming one that happens to
   exist), so doing it here piecemeal, ahead of a real resolver, would mean
   redoing it once that resolver lands. (c) (the `digest` computation) and
   (d) (making resolution unconditional) remain fully open — the latter is
   the deepest and largest remaining piece, described next.

   **A round of review on this slice's JSON round-trip (`contract_context_io.py`)
   converged through four increasingly narrow findings before surfacing a
   fifth that is real but not yet reachable — worth recording rather than
   chasing with a fifth patch.** The first four (both new `GateConfig`
   fields omitted from the round-trip entirely; a present JSON `null`
   silently defaulting instead of being rejected; a `schema_version >= 2`
   payload silently defaulting an *absent* key instead of rejecting it;
   the identical absence gap one level down in a present `gate.scope`
   object's own `targets` key) were all fixed, each with regression tests
   confirmed to fail pre-fix — see the four `fix:` commits on PR #817 for
   the detail. The fifth: `evaluation_context_to_dict()` writes a block's
   `schema_version` verbatim (by design — see the module's own "version
   fields survive verbatim" docstring), so a hypothetical caller that
   decoded a genuinely legacy (`schema_version == 1`) persisted context
   and then attached real, non-default `require_complete_analysis`/`scope`
   values to its `resolved_config.gate` before re-serializing would
   produce a payload that mislabels itself — a real, genuinely-populated
   v2 gate field under a v1 stamp, which a hypothetical old reader
   (predating this PR) would silently ignore regardless of the label,
   since it has no code path for the field's *existence* at all, not just
   its absence. **Confirmed unreachable by any current production code
   path**: the one call site that reconstructs a `GateConfig` post-decode
   (`with_resolved_gate`, `cli_compare_receipt.py`) always operates on a
   freshly-built `PersistedContractContext` from a live `compare()` run
   (via `build_persisted_context`/`with_resolved_config`), never on one
   decoded from an old persisted payload — and, per this same slice's own
   "Deliberately not yet done" note two paragraphs up, *nothing* in the
   current codebase constructs a non-default `require_complete_analysis`
   or a real `scope` from real input yet, so the specific combination this
   finding describes cannot occur today regardless of which context a
   caller starts from. A correct fix also can't simply auto-upgrade the
   label on write (silently re-stamping `schema_version` to 2 the moment a
   v2 field is populated) without contradicting this module's own
   documented "version fields survive verbatim" invariant one paragraph
   above the `evaluation_context_to_dict` this finding is about — the
   right fix is a write-side *rejection* of the mismatched combination,
   which is a real design decision (where the check lives, what error type
   it raises, whether it belongs in this leaf serializer at all versus a
   caller-side invariant) rather than a mechanical extension of the
   absent-key pattern the first four rounds established. Left for the
   resolver work items 2-6 below to pick up once a real caller populates
   these fields from real input — the point at which this combination
   first becomes reachable and a concrete design has real call sites to be
   verified against, rather than being designed against a hypothetical one
   now.

   **The right further scoping for item 1** is additive to the existing
   object rather than a parallel one: add a `digest` computation onto
   `CompatibilityEvaluationConfig` itself (folding in
   `effective_config_digest.py`'s existing hashing logic rather than
   duplicating it), and make resolving one *unconditional* for every
   comparison — collapsing `effective_config_digest.py`'s two-tier
   "rich or baseline" split into the rich tier always, once every
   comparison path actually populates the object it currently only
   populates opportunistically. That last part is the real work items 2-6
   below already describe (`compare`, `scan`, the release fan-out, and the
   four comparison call sites item 5 names all need to *resolve* this
   object, not just consume one that happens to exist) — so this
   re-scoping doesn't shrink the phase, it corrects what object items 2-6
   are migrating *onto*. `docs/contribute/plans/public-contract-default.md`
   (ADR-049's own plan doc) is the right place to check for whether "make
   `CompatibilityEvaluationConfig` universal" is already an open item
   there — a keyword sweep of it (`universal`/`opt-in`/`unconditional`)
   found no existing item phrased that way; its own phases (through
   Phase 7's "default flip") are about whether *contract evaluation*
   defaults on, a related but distinct axis from whether the
   *configuration object itself* always resolves regardless of
   `--contract`/`--pack` — so this looks like a genuinely new item for
   that plan or this one to own, not a duplicate of an existing one, but
   the sweep was keyword-based over a 4000+-line document, not a full
   read, so treat that as a strong lead rather than a settled fact before
   committing to it.
2. Move `compare` to consuming it directly.
3. Move `scan` to the same object.
4. Move the release fan-out off its six raw gate/severity strings.
5. Move `appcompat.check_appcompat()`'s and `check_plugin_host_contract()`'s
   own direct `compare_snapshots()` calls, `deps compare`'s
   `stack_checker._run_abi_diff()`'s direct `checker.compare()` call, and
   `cli_compare_release._collect_matrix_result()`'s own direct
   `compare_snapshots()` call (release's probe-matrix build-configuration
   comparison, which independently loads suppression/policy/pack state
   rather than reusing the per-library release fan-out's already-resolved
   configuration) onto `EffectiveEvaluationConfig` too — without this step,
   the Comparison equivalence acceptance test's requirement that these four
   paths produce identical configuration digests, contract/assurance
   decisions, and exit contributions stays unimplementable even after every
   other Phase 2 item lands, since item 4 (the release fan-out's six raw
   gate/severity strings) covers only the per-library comparisons, not this
   separate release-global one, and none of the other items touch it either.
6. Include the effective-config digest in every report and every
   aggregate input (building on the reporter's existing digest work from
   CLI-cleanup-phase-two's PR B).
7. Keep compatibility wrappers only at public API boundaries (the typed
   Python API's existing dataclasses stay stable; only their internal
   plumbing changes).

### Phase 3 — Complete `ExitDecision`

1. Model every exit axis named above.
2. Separate priority from numeric code.
3. Add the per-operation exit policies.
4. Publish all contributions in reports.
5. Remove aggregate's scan/report-type-specific heuristics.
6. Keep ABICC's external exit-code mapping as `AbiccExitPolicy`.

### Phase 4 — Introduce the canonical report model

1. Build findings, scope, verdict, coverage, assurance, dependencies, and
   exit before any rendering happens.
2. Migrate JSON rendering first (it's the format every other renderer and
   `aggregate` itself already treats as authoritative).
3. Migrate SARIF, JUnit, Markdown, review, and HTML.
4. Remove the "render → parse → patch → render" functions this obsoletes.
5. Make release and aggregate consume or embed the same envelope.

**Item 1's per-finding verdict is not a fresh design question — ADR-061
Phase 2 already scoped it and flagged the one real hazard.** Today,
`junit_report.py`/`html_report.py`/`reporter_markdown.py` each independently
resolve a per-change verdict via `effective_verdict_for_change`/
`DiffResult._effective_verdict_for_change` at their own call sites (some,
like `junit_report.py`, call it twice for the same `Change` from
`_is_failure` and `_failure_type`). `ReportFinding` (the sketch above) is the
right place to hold that verdict pre-resolved, exactly as this phase's item 1
already says — but the one implementation hazard the ADR names is real and
applies here unchanged: `Change` is a mutable dataclass and is not hashable
(its own `__hash__` is `None`), so a naive "cache resolved verdicts in a
dict keyed by `Change`" design type-checks fine but raises `TypeError` the
first time a real `Change` instance is used as a key — `mypy` does not
enforce `Hashable` on `dict`'s key type, so this is a runtime failure, not
a caught-at-review-time one. A `ReportFinding` built once per `Change`
during envelope construction (rather
than a separate cache keyed off identity) sidesteps the problem entirely —
build the tuple of `ReportFinding` by iterating `DiffResult.changes` once,
resolving each verdict inline, with no separate cache/index structure
needed. `DiffResult`'s own public `breaking`/`source_breaks`/`compatible`/
`risk` properties are unaffected either way (ADR-061 already ruled out
touching them — a breaking change to the documented public Python API) and
should stay computed the way they are today; `ReportFinding.verdict` is a
new, additional field, not a replacement for them.

**A bare `verdict` field is not enough by itself — a Codex review round on
this same PR caught that a pre-resolved `Verdict` alone still leaves
`junit_report.py` unable to become a pure projection.** When a
`SeverityConfig` is active, `_is_failure`/`_failure_type` decide pass/fail
and the reported failure `type` from `classify_effective_change`'s
`IssueCategory` — a distinct axis from `Verdict` (it separately
distinguishes, e.g., a compatible addition from a compatible quality issue,
and a demoted preset can make even a BREAKING/API_BREAK verdict pass) — not
from `effective_verdict_for_change`'s `Verdict` alone. A `ReportFinding`
carrying only `verdict` would still force JUnit to call
`classify_effective_change` itself under a `SeverityConfig`, which is
exactly the per-renderer re-resolution this envelope exists to eliminate,
and risks the renderer's own category resolution silently disagreeing with
whatever the envelope's `verdict` implies. `ReportFinding` therefore needs a
second field alongside `verdict` — the resolved `IssueCategory` (or
equivalently, both branches' final failure classification) — computed the
same way, once per `Change`, in the same envelope-construction pass; every
renderer reads both pre-resolved fields instead of any one of them
re-deriving the other.

### Phase 5 — Migrate compatibility and multi-artifact operations

1. Make ABICC descriptors adapters into typed requests.
2. Express `compat`'s strict/source-only/new-symbol behavior as evaluation
   configuration where the shape allows it.
3. Introduce shared `ArtifactSet`, `ArtifactPair`, and
   `SetComparisonResult` types.
4. Share matching and rollup primitives between release, `scan
   --artifact-set` (ADR-056), and bundle operations.
5. Keep distributed report aggregation a distinct operation, but have it
   consume the same envelope as everything else.

### Phase 6 — Authority transfer and retirement (2026-09-05)

Phases 0-5 build owners; this phase makes them *the* owners and deletes what
they replaced. Every item names both a canonical owner and a deletion — see
"The explicit retirement table" above, which is this phase's work inventory.
Ordered by dependency, not by size:

1. **Remove already-obsolete implementations.** ✅ **Done (T1, 2026-09-05.)**
   `perform_elf_dump()` and `handle_non_elf_dump()` had no production caller;
   only their own unit tests kept them alive. Both are deleted, with
   `cli_dump_non_elf.py` and `cli_dump_protocols.py`. Two things worth
   carrying forward from doing it:

   - *Most of what those tests pinned was not unique.* The large majority
     re-asserted, at a retired call site, behaviour the shared pipeline
     already owns and already tests at its own seam (the ADR-039 collector
     and the L2 seed/fold cleanup ordering in `test_typed_dump_request.py`;
     the `parsed_with_build_context` stamp and its unmatched-database
     negative in `test_header_compile_context.py`; the header-graph attach,
     its `--dwarf-only`/`lang` normalisation, the Python/NumPy attach and
     the AST-memoize scope in `test_service_unit.py`; the
     explicit-`-I`-only provenance-widening rule in
     `test_service_input_resolution.py`; the legacy `-p` token precedence
     in `test_legacy_compile_db_typed_threading.py`). "Rehome the unique
     assertions" was mostly an exercise in establishing which ones those
     were — worth budgeting for on the remaining rows.
   - *One assertion could not be rehomed, because the behaviour it pinned
     changed with the migration.* `perform_elf_dump` routed a `-H <dir>`
     operand into `dumper.dump`'s `scope_header_dirs` (extraction-contract
     scope only, ADR-015 provenance tagging deliberately off); the typed
     request splits `-H` with `header_utils.split_public_header_inputs` and
     passes the directory as a real `public_header_dirs` entry, so `dump`
     now tags provenance for it — the same thing `compare` has always done
     with its own `-H` list. Nothing populates `scope_header_dirs` from a
     typed request at all any more. That is a convergence, not a
     regression: the two commands agree where they previously did not, and
     `dump`'s own `--public-header-dir` flag (removed earlier as a second
     way of saying the same thing) is what the behaviour now matches. It is
     recorded rather than reverted, and
     `test_dump_cli_execution_behaviors.py` pins the current channel with
     the reasoning attached.

   The roadmap loophole half of this item — a declined *behavioral* change
   no longer closing a *consolidation* item (see "The completion rule this
   plan was missing" above) — landed separately as track T2 on 2026-09-05,
   enforced by the ledger's schema-2 validator rather than by convention.
2. **Finish one complete data-authority cutover.** Typedefs and constants
   are closest. Establish one stored semantic state per migrated family
   (reusing the existing typed `Function`/`RecordType` payloads inside the
   canonical entity/occurrence model — *not* copying their fields into a
   parallel generic representation), move legacy adaptation to the **input**
   boundary, then delete the runtime dual-index selection. Acceptance is
   concrete: *typedef/constant comparison no longer builds a legacy index
   for a current-format canonical snapshot, and no producer can update the
   same family through two writable representations.* Do not delete the
   fidelity gate first — it currently protects anonymous-scope, identity,
   missing-data, and ordering cases; preserve each in the canonical model
   and the legacy adapter, then remove the double construction.
3. **Finish execution/configuration ownership.** Eliminate
   `execute_dump_request`'s out-of-band semantic kwargs
   (`build_config`/`build_query`/`build_compile_db`/`changed_paths`/
   `allow_build_query`/`legacy_compile_db_tokens`/
   `legacy_compile_db_matched`/`seed_collect_mode`/
   `source_frontend_from_folded_context`) by moving each into the typed
   request/resolution model with its provenance; leave only operational
   services (progress reporting, cancellation) as parameters. Keep the three
   stages distinct — **preflight** (what is decidable without running
   tools), **executable plan** (inputs, compile contexts, backend selection
   *and fallback policy as separate fields*, scope, resources), **observed
   result** (what ran, what evidence was obtained, what failed). Do not push
   post-execution assurance into a pre-execution object, and do not make
   dry-run secretly execute. Represent backend selection as
   preferred-backend + fallback-policy rather than preserving `"auto"` as a
   magic string carrying hidden control state (a prior premature
   substitution to `"castxml"` changed behavior). Then route standalone
   `check_appcompat()`, `stack_checker._run_abi_diff()`, and source-only
   dump through the shared operations, and consolidate the release
   gate-pack fold onto one effective-evaluation object.
4. **Migrate fact semantics with explicit provenance and scope.** A fact
   must record *what was observed versus inferred or supplied through a
   legacy compatibility path*, *which producer and observation scope
   support it*, and *whether the claim is a positive observation or a
   completeness/absence claim* — because status alone cannot separate
   "I observed this virtual method" from "I established the complete set of
   virtual methods." That is what the PDB `vtable_fact=NOT_COLLECTED` case
   needs and what a bare `FactStatus` guard could not deliver (round 2
   landed, round 3 reverted). Fix it at the model/import boundary, along
   with the legacy-hybrid backfill blocker holding the seven
   `fact_provenance`-gated fields, rather than adding more snapshot-level
   booleans or reliability side channels. Give declined comparisons a route
   into shared analysis accounting (`observed changes` / `evaluated
   requirements` / `unresolved requirements` / `unsupported requirements`)
   so a `list[Change]`-returning detector cannot silently discard the reason
   it emitted nothing — a missing *optional* producer must not make the
   whole scan incomplete; assurance is relative to the requested analysis
   contract. Then retire the raw legacy fields and reliability flags. For
   public Python constructors, choose and document the compatibility
   behavior explicitly instead of leaving every detector to infer whether an
   omitted argument meant "unknown" or "empty".
5. **Expand the proven pattern.** Remaining detector families, the export
   index and its named views, shared report preparation, and standard
   multi-artifact flows. **Every cohort must reduce the number of live
   legacy readers and writers**; a cohort that ends with another populated
   sidecar has not landed. The **record and function families** are the
   concrete instance of that last clause and are named here explicitly so
   they have an owner: `SemanticIR` already populates their occurrences on
   every header-AST and DWARF producer while their detectors still read
   `AbiSnapshot.types`/`.functions`. Migration was investigated on
   2026-09-03 and declined — but the decline is of the *behavioral* change
   (their matching already resolves through ADR-045's `TypeMap` and
   `resolve_function_identity`'s CANONICAL tier, so changing matching
   precedence or published finding IDs would close no defect), not of the
   consolidation, which needs no new bug to justify it. Track T3 does not
   cover this: its scope is typedefs and constants.

Extend the existing architecture checks rather than adding a planning
system. `scripts/semantic_ir_cutover.py`'s per-cohort registration is the
right shape, but a module-level "no legacy attribute read" rule is **not**
proof that a family has one authority — the typedef fidelity selector
satisfies that rule while the legacy projection still adjudicates. Extend
each cohort's guard to cover the actual dependency path (callers, adapters,
selectors, producers) and the legacy-*writer* retirement condition.

A phase item reaches **retired** only when all four hold:

1. all applicable production callers use the owner;
2. the replaced decision/data implementation is removed, or reduced to
   delegation;
3. old persisted inputs still enter through an explicit compatibility
   adapter;
4. an architecture check prevents reintroducing the old dependency/read
   path.

## Parallel execution tracks

The Phase 6 items above are dependency-ordered, but they are not one serial
queue. These tracks touch disjoint modules, have separate test surfaces, and
can be executed concurrently by different contributors or sessions. Within a
track, the steps are ordered.

| Track | Scope | Touches | Depends on |
|---|---|---|---:|
| ~~**T1 — Dead-implementation retirement**~~ ✅ **done (2026-09-05)** | Rehomed `perform_elf_dump`/`handle_non_elf_dump`'s unique assertions onto the live path; deleted both functions, `cli_dump_non_elf.py` and `cli_dump_protocols.py` | `cli_dump_helpers.py` (-661 lines), `cli_dump_non_elf.py` + `cli_dump_protocols.py` (deleted), their tests, `architecture/{modules,debt}.yaml`, `CLI_CONTRACT_ALLOWLIST` | nothing |
| ~~**T2 — Ledger/status-model change**~~ ✅ **done (2026-09-05)** | Added the `introduced → wired → authoritative → retired` ladder and a separate `investigated_declined` disposition to `docs/_meta/one-semantic-pipeline-status.yaml` + `scripts/pipeline_status_ledger.py`'s field/enum validation; re-audited every concept row against it. Shipped as ledger `schema_version: 2` with the cross-field rules and the re-audit described under "The four-state status model" above | `scripts/pipeline_status_ledger.py`, the ledger, `tests/` | nothing |
| ~~**T3 — Typedef/constant authority cutover**~~ ✅ **done (2026-09-05)** | Deleted the runtime dual-index construction: `typedef_index_pair`/`constant_index_pair` now decide each side of a comparison independently, reading a side's real `SemanticIR` directly whenever it has one (never both-or-neither — a Codex review round found the first both-or-neither cut would starve an IR-carrying side of its own real evidence whenever the *other* side lacked one) and falling back to the legacy adapter's projection of that side's own flat collection only when it has none; the identity half of the old fidelity gate (a real IR disagreeing with its own `typedef_entity_ids`/`constant_entity_ids` sidecar) moved to the canonical model's load boundary (`AbiSnapshot.__post_init__`, and re-run explicitly after `serialization.snapshot_from_dict` decodes a stored IR — a second Codex finding, since that decode bypasses `__post_init__`), now a hard `SemanticIrAuthorityError` rather than a silent fallback. A related fix in the same PR: `diff_constants` was silently dropping a constant addition/removal whenever its value was `Fact.unsupported()`, since only now reachable with the dual-index gate gone (Codex finding). The old gate's name/value equality half against the legacy alias/value collections is deliberately *not* preserved anywhere — requiring it would make a populated legacy collection an accidental prerequisite of `SemanticIR`-only construction, the opposite of authority transfer | `compare/typedefs.py`, `compare/constants.py`, `model/semantic_ir_legacy_adapter.py`, `model/snapshot.py`, `errors.py`, `serialization.py`, `scripts/semantic_ir_cutover.py` | nothing (T2 records it) |
| **T4 — Dump request contract** ◐ *(partial, 2026-09-05: see note below)* | Fold `execute_dump_request`'s nine semantic kwargs into the typed request; split backend selection from fallback policy; give source-only dump an execution variant | `service_dump_pipeline.py`, `cli_dump_request.py`, `cli_buildsource.py`, `frontends/cli/dump_execute.py` | ~~T1~~ — satisfied (T1 landed 2026-09-05) |
| **T5 — Direct-bypass migration** | Route `appcompat.check_appcompat()` and `stack_checker._run_abi_diff()` through the shared extraction/comparison workflow; shrink `CLI_CONTRACT_ALLOWLIST` accordingly | `appcompat.py`, `stack_checker.py`, `cli_stack.py`, `scripts/check_ai_readiness.py` | T4 for the dump half; the compare half is independent |
| **T6 — Effective gate/policy convergence** ✅ *(landed 2026-09-05; the shared fold and the derived scheme are done, the two runtime shapes remain P0's own job)* | Collapse `apply_release_gate_pack`'s raw-string mirror of `pack_application.apply_to_compare_config` onto one shared fold **without inverting the dependency direction** — `policy/release_gate_options.py` deliberately consumes a `_GatePackApplication` `Protocol` rather than importing the flat-root `pack_application`, since `policy` may not import it (ADR-061; `policy/AGENTS.md`'s "Permitted imports"), so the shared fold belongs in an inward module both may import, or an outer layer invokes both halves — never a `policy → legacy root` call. Also make `GateOptions.exit_code_scheme` derived rather than independently constructible | `policy/release_gate_options.py`, `pack_application.py`, a new inward fold owner, `tests/test_release_gate_pack_fold_parity.py` | nothing |
| **T7 — Canonical export index** | One raw export index plus named projections (versioned ELF / default versions / Mach-O normalization / named PE / ordinal imports / missing-vs-empty); delete the five sibling implementations | `policy/depth_projection.py`, `buildsource/crosscheck_base.py`, `buildsource/snapshot_exports.py`, `post_manifest.py`, `diff_unnamed_types.py` | nothing |
| **T8 — Action boundary** | Remove the residual raw-exit/stderr verdict reconstruction; keep only a transport-level no-result fallback; keep `fail-on-*` as step policy that never rewrites the verdict | `action/run.sh`, `action/` tests | nothing |
| **T9 — Fact provenance and scope** (first slice landed 2026-09-05 — see note below the table) | Extend the fact model with observation-vs-inference, producer/scope, and positive-observation-vs-completeness; fix the PDB `vtable` and legacy-hybrid backfill blockers at the model/import boundary; add shared analysis accounting for declined comparisons | `model/fact*.py`, `diff_types_vtable.py`, `diff_cxx_rules.py`, the import adapter | ~~T2~~ — satisfied (T2 landed 2026-09-05, so the ladder and `investigated_declined` are available to record this work's status); otherwise independent |
| **T10 — Shared report preparation** | Compute evaluated findings/outcomes once ahead of format-specific construction; remove **both** runtime cycle escape hatches, which are distinct sites with distinct fixes: `render_markdown_document._reporter_markdown()`'s `..reporter_markdown` load (the Markdown cycle) and `report/scoped_gate.py`'s `..reporter` load (scoped-JSON construction, whose cycle exists only because `apply_scoped_gate` mutates an already-built payload); give consumer scoping an explicit finalization boundary instead of mutating shared changes | `report/render_markdown_document.py`, `report/render_markdown_alternate.py`, `report/scoped_gate.py`, `reporter_markdown.py`, `appcompat.py`'s `scope_diff_to_app` | T5's appcompat half for the scoping item |

**T4 status note (2026-09-05, stated precisely rather than as a blanket
"done" so it can't be mistaken for closing the whole item — Codex review on
the PR that landed this slice, correctly, caught an earlier draft of this
row overclaiming):** `execute_dump_request`'s own nine keyword parameters
are folded into one typed `service_dump_pipeline.DumpExecutionOptions`,
passed as a single `options=` argument — that part of item 1 is done.
**Not done**, and still fully open: `DumpExecutionOptions` is not a field
on `DumpRequest` or `ResolvedDumpRequest` — it is assembled at the
`execute_dump_request` call boundary itself
(`frontends/cli/dump_execute.py`'s `execute_dump_cli_run`, which still
takes the nine values as its own separate parameters and only builds the
typed object immediately before calling `execute_dump_request`). So the
*resolved plan* `dump --dry-run` renders from still cannot represent any of
these nine values — a caller inspecting a `ResolvedDumpRequest` has no way
to see what a real execution would pass. Closing that gap (folding the
values into the typed request/resolved-request model itself, not just into
one options value at the final call) is unstarted, as are item 1's other
two clauses (splitting backend selection from fallback policy; a
source-only dump execution variant).

**Recommended first wave (fully parallel, no shared files):** ~~T1~~ (done),
~~T2~~ (done), T6, T7, T8. **Second wave:** ~~T3~~ (done), T4, T9 (each large
enough to be its own multi-PR effort). **Third wave:** T5, T10, once T4/T5's
shared surfaces settle.

**T9's first slice (2026-09-05): the PDB `vtable` fabrication is closed;
the rest of the item's scope is not.** `Fact[T]` gained a `producer: str |
None` field (`model/fact.py`) — additive, defaulting to `None`, and
round-tripped through `storage/fact_codec.py` unversioned (a document
predating the field simply has no key, decoding to the same default every
pre-existing construction site already carries). `pdb_model.py`'s
`_record_from_layout` now constructs every record's `vtable_fact`/
`vptr_offset_bits_fact` as an explicit `Fact.unsupported(...,
producer="pdb")` rather than omitting the fields — closing the exact gap
the 2026-09-04 5B closure diagnosed: PDB's own structural non-evidence and
a hand-built/typed-API `RecordType`'s `vtable=` omission previously both
resolved to the identical `NOT_COLLECTED` status, which is why a blanket
`FactStatus` pre-check (round 2 of that closure) could not tell them apart
and had to be reverted. `compare/vtable_evidence.
vtable_transition_is_evidenced` now declines outright, before consulting
either fallback evidence stream, whenever either side's
`vtable_fact.status is FactStatus.UNSUPPORTED` — a status a typed-API
omission never produces, only an explicit incapability claim does — which
closes both fabrication paths the 2026-09-04 closure named (the size/base
fallback, and the owned-virtual-function fallback, since PE/PDB's own
`Function.is_virtual` also defaults `False` unobserved and that stream is
gated by the identical check). `NOT_COLLECTED`/`FAILED` handling is
untouched, so the leaf-class regression that closure's round 3 protects
stays exactly as it was.

**What this slice does not close, left for the item's remaining scope:**
the DWARF per-translation-unit completeness gap (`Fact.present([])`,
genuinely `PRESENT`, for a class whose virtuals live in a TU only the
*other* side's debug info covers) — a producer-*capability* signal like
`producer`/`UNSUPPORTED` cannot express this, since DWARF genuinely can
capture the family; the gap is per-TU *scope*, which is the
observed-vs-inferred / positive-observation-vs-completeness half of this
item's own stated scope, still unimplemented. Also untouched: the
legacy-hybrid backfill blocker holding the seven `fact_provenance`-gated
case-(a) fields (5B's own fourth-through-seventh-slice finding), and the
shared analysis accounting for declined comparisons (`observed changes` /
`evaluated requirements` / `unresolved requirements` / `unsupported
requirements`) this item's own text calls for. Each remains real,
scoped, separately-actionable work — recorded here rather than implied
closed by the row above.

## Acceptance tests

The highest-value tests here are cross-path *equivalence* tests, not more
example-specific regression tests — this is what a token/AST-based clone
detector cannot catch, since the class of bug this plan targets is
different code intentionally computing the same thing.

**Artifact-resolution equivalence.** Two tiers, since a `symbols_only=True`
call genuinely skips work a full resolution does (`service.resolve_input()`
guards its header-graph attach and debug-info resolution behind `and not
symbols_only`) — comparing both against the full field list would be
comparing a supplementary, header-free L0/L1 extraction against a
full-resolution result, which are not expected to agree on fields neither
one has any way to have populated identically.

*Full-resolution paths* — for one artifact and equivalent options, `dump`,
compare-side resolution, scan candidate resolution, ABICC descriptor
resolution, `appcompat.check_appcompat()`'s own per-side resolution, `deps
compare`'s per-dependency-pair resolution, and release's own per-library
extraction — must produce identical: snapshot semantic fingerprint;
extraction-contract fingerprint; effective evidence depth; effective
compile-context digest; public-surface scope fingerprint; dependency
scope; build/source coverage; cache-relevant directory set.

*`symbols_only=True` paths* — `l0_export_delta.collect_l0_export_delta()`'s
supplementary re-extraction (invoked by both native `compare` and scan
baseline reconciliation, independent of either side's primary resolution)
and `scan_engine._load_exports_for_poi()`'s own prepass (a third
independent `resolve_input()` call site scan makes ahead of the primary
candidate/baseline extraction, when export-delta POI tracking applies) —
must produce identical exported-symbol projections against each other and
against the corresponding full resolution's own export set, plus the
subset of the full-resolution fields a symbols-only call actually
populates: effective evidence depth and resource lifetime/session
handling. Fields that require header/L2 evidence (extraction-contract
fingerprint, effective compile-context digest, public-surface scope
fingerprint) are not claimed for this tier, since a `symbols_only=True`
call never computes them. Nor is a *configuration* digest claimed here at
all: that's `EffectiveEvaluationConfig.digest` (Phase 2), a whole-run
object a CLI/API frontend resolves once before `compare`/`scan` executes
— `resolve_input(..., symbols_only=True)` itself accepts no configuration
parameter and returns no digest of any kind (confirmed by reading its
signature), so this artifact-resolution-tier test has nothing of that
shape to compare. This tier's own producer/consumer, by contrast, is
exactly `resolve_input(..., symbols_only=True)` itself — `ArtifactResult`
(Phase 1's target shape) is what would eventually carry the fields listed
above as its own resolution-level output, once this migration lands.

Release belongs here specifically because its adapter does real,
release-only work ahead of the shared resolution step:
`cli_compare_release._run_compare_pair()` follows GNU ld linker scripts and
applies its own per-library header/include mapping before ever calling
`service.run_compare()` — the target shape's promise to "serve... release
per-library extraction" is otherwise untested at the one place release's
extraction can still diverge even when ordinary compare-side resolution
passes cleanly. Standalone appcompat, `deps compare`, release, and the L0
re-extraction all belong in this matrix, not just in Phase 1's routing
list — a phase that satisfies every equivalence test here except one
direct caller's path would still leave that caller resolving depth,
compile context, cache paths, or resource lifetime differently from
everything else. Run this matrix once per **binary backend** (ELF,
PE/COFF, Mach-O), not once against a single ELF fixture: Phase 1
deliberately keeps PE/Mach-O's own backend-specific extraction path
(`handle_non_elf_dump`) rather than folding it into the ELF pipeline, so an
ELF-only fixture can satisfy every assertion above while a PE or Mach-O
caller still resolves depth, compile context, scope, or cache inputs
differently — exactly the class of divergence this gate exists to catch.
Not every one of the ten paths above has a PE/Mach-O caller today (e.g.
`deps compare`'s `stack_checker._run_abi_diff()` and the L0/POI supplementary
re-extractions are ELF-specific in practice), so the backend axis applies
wherever a path is genuinely reachable on more than one platform — `dump`,
compare-side resolution, and scan candidate/baseline resolution all are.

**Comparison equivalence.** For one comparison, native `compare`, `scan
--against`'s own nested baseline comparison, release per-library compare,
release's own global probe-matrix comparison (when `compare-release`
receives `--probe-matrix-old`/`--probe-matrix-new`,
`cli_compare_release._collect_matrix_result()` separately loads
suppression/policy/pack state and calls `compare_snapshots()` over
synthetic snapshots carrying `extra_changes` — a comparison distinct from
every per-library one this same command also runs), the Python API,
`appcompat.check_appcompat()`'s pre-scope `compare_snapshots()` call
(before its own `scope_diff_to_app()` step applies app-usage narrowing),
`appcompat.check_plugin_host_contract()`'s identical pre-scope comparison
(the plugin-host counterpart to `check_appcompat()`), each dependency
pair's `_run_abi_diff()` inside `deps compare`, and `compat`'s ABICC
adapter's own comparison *before* its intentional
strict/source-only/new-symbol-warning transformations
(`compat/_helpers.py`'s `_apply_strict()` and siblings) apply must produce
identical: canonical finding IDs (`finding_identity.py`); effective
verdicts; configuration digest; contract decisions; assurance decisions;
compatibility exit contribution. Naming appcompat, `deps compare`,
release's matrix comparison, and ABICC's pre-transformation comparison here
matters independently of Phase 1's own migration list — that phase, and
Phase 5's ABICC migration, only guarantee their *extraction* moves onto the
shared pipeline; without this equivalence test also covering their
*comparison* step, their finding IDs, contract decisions, or verdict
processing could still silently diverge even after extraction converges.
ABICC's own strict/source-only/new-symbol transformations are intentional,
documented ABICC-compatibility behavior (not divergence to eliminate) —
test them as before/after pairs against the shared pre-transformation
result, the same way `scan`'s crosscheck promotion is tested below, rather
than requiring the *post*-transformation result to match `compare`'s.
Scoped deliberately to `scan`'s baseline comparison rather than its overall
result: `scan --against --crosscheck KEY=error` intentionally lets
`scan_engine._crosscheck_severity_exit` promote an otherwise-clean run to
`API_BREAK` (recorded as `promoted_crosscheck`) — a real, scan-only axis
Phase 3's `crosscheck_promotion` contribution deliberately preserves, not
a divergence to eliminate. Requiring `scan`'s *overall* effective verdict
to match `compare`'s would either fail on this correct behavior or invite
removing the promotion; test scan-specific contributions (crosscheck,
budget overflow) separately from this equivalence check.

**Renderer equivalence.** Every renderer, given the same `ReportEnvelope`,
must expose the same effective verdict, finding IDs, blocking findings,
exit code, and exit reasons.

A token/AST-based clone detector may still be worth adding as a secondary,
advisory CI signal, but it should be understood as catching a different
(much smaller) risk than the equivalence tests above.

## Relationship to in-flight work

This plan does not compete with the existing initiative plans — it names
where they converge and what remains once each is fully landed:

- **[CLI cleanup, phase two](cli-cleanup-phase-two.md)** already names three
  of the same prerequisites this plan generalizes (one typed `dump`
  resolution path — its "PR C"; one effective pack/gate configuration —
  its "PR B"; one canonical exit decision — its "PR G1", already merged).
  Phases 1–3 here are the full generalization of those three PRs across
  every operation, not just `compare`/`scan`/release.
- **[G33](g33-typed-api-and-mcp-convergence.md)** built the schema registry
  and `CompareRequest`/`CompareResult` completeness this plan's Phase 1–2
  extend to `dump` and the artifact-resolution surface generally; its own
  "Phase 6" note (a standing sequencing constraint on ADR-049's rollout)
  applies unchanged to this plan's Phase 2.
- **[G32](g32-comparability-contract-and-multi-tu-manifest.md)**'s
  comparability contract (`ExtractionContract`, `scope_fingerprint`) is
  exactly the fingerprint machinery Phase 1's acceptance tests reuse — this
  plan does not propose a second comparability mechanism.
- **[Public contract default](public-contract-default.md)** (ADR-049) is
  the compatibility-configuration resolver Phase 2 makes the sole runtime
  contract; this plan does not change ADR-049's own D7/D8 precedence rules,
  only how uniformly the *result* of applying them reaches every operation.

## Out of scope

- Rewriting binary/debug/header parsers, or merging CastXML and Clang
  backends into one implementation (see "What should explicitly not be
  unified" above).
- A general-purpose clone-detection tool; noted as an optional secondary
  signal, not a replacement for the equivalence tests this plan specifies.
- Any change to ABICC's or `compat`'s external, user-facing contract
  (flags, exit codes, report shape) — Phase 5 changes only *how* that
  contract is implemented internally.
- Any change to the ChangeKind registry, detector registration pattern, or
  identity-resolution tiers (`finding_identity.py`) — these are cited
  throughout as examples of the target pattern already working correctly.
