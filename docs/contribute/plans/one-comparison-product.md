---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# One comparison product — retiring `scan`, consolidating the CLI

**Owner ADR:** [ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md).
**Status:** Phases 0–6 and 8 are **done**. `scan` was deleted on 2026-09-11
(`abicheck scan` exits `64`, naming `compare`/`compare --no-baseline`), after
every capability it exclusively owned reached `compare` behind a parity gate.
Phase 7 has **nothing executable left**: each remaining item is gated on a
named prerequisite or owned by another workstream (see
[What remains in Phase 7](#phase-7l-external-cli-audit-2026-09-12-reconciled)). Phase 9's two named
relevance-defect blockers in
[`public-contract-default.md`](public-contract-default.md) Phase 6 are
**closed** (2026-10-01; see [Phase 9](#phase-9-contract-mechanism-consolidation-gated-may-not-start-early)),
the lane-coverage bound was accepted (2026-10-02), and slices 9a (the
`--contract all` scope fix) and 9b (`--scope-public-headers`/`--no-` deleted,
2026-10-03) and 9c (`--post-manifest`'s config home,
`contract.overlays.post_manifest`, 2026-10-03) and 9d (`--post-manifest`
deleted, 2026-10-03) are done: `--contract` is `compare`'s one contract
mechanism and Phase 9 has no executable slice left. Retiring
`CompareRequest.scope_public` is a separate, deferred Python-API decision.
**Effort:** XL · **Risk:** high — this deleted a public command and moved
capabilities between analysis paths. Phase ordering was the safety mechanism.

**This is a compacted record (2026-09-29).** The per-slice checkpoint
narrative — review rounds, intermediate counts, re-verification receipts — is
in git history; the last full-length revision is commit
`5ba6a5c84c07e504d4b1df8cbed0cf59abd5aa71`. What stays here: what landed and
where, decisions that must not be re-litigated, what is still open and who
owns it, and the merge criteria. Durable technical findings live in
[known gaps](../known-gaps.md) and in the owning modules' docstrings.

**Subordinate to** [`vision-api-abi-evolution.md`](vision-api-abi-evolution.md)
for anything about what a *result means*. Where the two disagree, that plan
wins. This plan owns the **interface and its capability topology**: which
command owns which analysis, and what a user has to type.

**Supersedes the remaining scope of** `cli-cleanup-phase-two.md` (retired;
see its row in the [plans index](index.md)). Its three open items were
re-homed here (§6): PR H was cancelled, PR I and PR J became Phase 7 slices.
Its closed items stay closed.

### Cleanup reconciliation (2026-09-29)

Stale facts corrected while compacting, each verified against the tree:

- **`FindingEvolution` is wired.** Phase 1 item 2's primitive is consumed by
  `project history` (`abicheck/cli_project.py` → `workflows/history.py`'s
  `apply_finding_evolution` per adjacent pair) and by `compare`'s own report
  (`reporter.py`/`report/build.py` → `report/finding_evolution.py`'s
  `finding_evolution` block). The earlier "not yet wired into any CLI
  command" claim was stale.
- **`skills-src/evaluation/validation/scripts/run_oneapi_scan.py` and `skills-src/evaluation/field/scan_level_scaling.py`
  no longer exist** — both were deleted in Phase 6 (list A). The earlier
  "stays on `scan`" note is void.
- **`tests/parity/gaps.py` and `tests/parity/test_gap_registry_contract.py`
  are deleted.** The registry had emptied; `tests/parity/` is now a
  compare-only regression corpus (see `tests/parity/__init__.py`).
- **The retired `--crosscheck` promotion exit axis.**
  `ExitDecision.crosscheck_promotion_contribution` has no resolver parameter
  any more (`policy/exit_decision.py`): its only producer, `scan --crosscheck
  KEY=error`, is gone, so the resolved value is always `0`. The field is kept
  read-only so stored pre-0.6 reports still load.
- **Import-cycle allowlist.** `IMPORT_CYCLE_ALLOWLIST` in
  `scripts/check_ai_readiness.py` dropped ten members that named deleted CLI
  modules.
- **Stale-comment sweep.** Scan-era references in `abicheck/` comments and
  docstrings were rewritten to describe `scan` as retired rather than live.

### Status re-evaluation (2026-10-01)

Re-checked against the tree with Click introspection and the contract gates:

- **Live counts match §4.5** (`compare` 43, `dump` 20, `aggregate` 5, no
  hidden options). `abicheck scan` exits `64` with the migration hint. The
  rulings bijection (`tests/test_config_rebalance.py`), the root-surface pin,
  the CLI contract and the export-grammar tests pass, and
  `docs/reference/cli-reference.md` is in sync.
- **A new subcommand this table did not list:** `project capture-variants`
  (7 options, storage-format-v2 A1.6). It is now in §4.5.
- **Per-option rulings covered only `compare` and `dump`.** Closed
  2026-10-02 by slice 7s (which also found `deps` unruled).
- **Phase 9's blockers moved.** The template-instantiated-parameter seed
  mismatch had already been closed, but this plan and the plans index still
  listed it. The `ambiguous_namespaced_leaf` identity gap is closed by
  schema v54. The `package` lane's recorded reason ("compare rejects
  `--contract` for package operands") was stale: the release fan-out does
  apply it per library.

---

## 1. Current state

*Historical: the audit of `main` that started this plan (2026-09-06). All
three findings are now closed; kept as the audit record, not rewritten.*

**1. `compare` could not reach `scan`'s checks.** The only production callers
of `run_crosschecks`, `find_pattern_facts` and `collect_preprocessor_facts`
were in `scan_engine.py`. The eleven cross-source checks —
`private_header_leak`, `public_not_exported`, `exported_not_public`,
`rtti_for_internal_type`, `odr_type_variant`, `unversioned_exported_symbol`,
`compile_context_conflict`, `header_build_context_mismatch`,
`source_surface_dso_mismatch`, `public_to_internal_dependency`,
`identity_collision_detected` — plus the lexical pattern pre-scan, the
preprocessor scan, changed-path localization and the `abi3` audit were
`scan`-only. That was a correctness and discoverability defect, which is what
made the migration a capability *gain*. **Closed** (Phase 2).

**2. Two of everything.** `scan` owned ~10,000 lines (10,074 across 16
modules, verified 2026-09-09), a separate typed API (`ScanRequest`/
`ScanResult`), a separate JSON schema (`SCAN_SCHEMA_VERSION`), 17 explicit
`mode == "scan"` conditionals in `action/run.sh`, and 33 scan-only test
modules. **Closed** (Phase 6).

**3. Surface size.** 78 accepted `compare` options (4 hidden), 45 on `scan`,
39 on `dump`; roughly 30 were stable project properties retyped every run.
Surface size was the *third* problem — a short invocation is not clean if it
reports a pre-existing leak as newly introduced. **Largely closed** (Phase 7;
see §4.5 for the live counts).

---

## 2. Target state

### Root surface — six verbs, three tiers

| Tier | Command | Question it answers |
|---|---|---|
| **Primary** | `compare OLD NEW` | How did this component's supported surface evolve, and is that acceptable? |
| **Primary** | `compare --no-baseline NEW` | Same product, OLD side declared absent: what can be said about this build alone? |
| **Primary** | `dump INPUT` | Capture this component's evidence as a reusable snapshot. No verdict. |
| **Primary** | `deps tree BINARY` | What will the loader resolve for this binary here, and does everything bind? |
| **Primary** | `deps compare BINARY --old-root --new-root` | Will this consumer still load/work when its deployment environment changes? |
| **Advanced** | `aggregate REPORTS_DIR` | Fold many per-target reports into one CI gate verdict. |
| **Advanced** | `project {validate,plan,history}` | Read one project-integration artifact and report on it. |
| **Frozen** | `compat …` | ABICC drop-in. Untouched by this work (ADR-068 D7). |

`scan` is gone. `check` is **not** introduced.

### The target experience

```bash
# The everyday case
abicheck compare old/libfoo.so new/libfoo.so

# With header evidence, side-scoped
abicheck compare old/libfoo.so new/libfoo.so -H old=old/include -H new=new/include

# Reusable baseline
abicheck dump old/libfoo.so -H old/include -o baseline.abi.json.zst
abicheck compare baseline.abi.json.zst build/libfoo.so -H new=include

# PR review with source evidence, localized to the diff
abicheck compare baseline.abi.json.zst build/libfoo.so \
  -H new=include --sources . --depth source --since origin/main

# One analysis, several artifacts
abicheck compare OLD NEW -o json=result.json -o markdown=summary.md

# No baseline exists yet
abicheck compare --no-baseline build/libfoo.so -H include/ --sources .

# A whole release
abicheck compare old-release/ new-release/ --select-required libfoo.so
```

Nothing in that set requires the user to know `L0`–`L5`, `BundleFacts`,
`ProjectSnapshot`, `SemanticIR`, extractor names, or gate algorithms.

---


## 3. Scan retirement

Classification vocabulary (ADR-068 D3/D5):
**COMPARE-STAGE** — becomes an ordinary analysis/enrichment stage of the
comparison pipeline; **AUTOMATIC** — internal execution behavior, no enable
flag; **CONFIG** — stable project property in `.abicheck.yml`;
**INTERNAL** — stays a reusable engine primitive, loses public `scan`
identity; **DELETE** — leaves the product.

The "Current owner" column is the owner **at the 2026-09-06 audit**; every
`scan`-side module named there is deleted. The last column is the outcome.

| # | Capability | Current owner | Target owner | Class | Prerequisite / outcome |
|---|---|---|---|---|---|
| 1 | Baseline comparison (`--against`) | `cli_scan_baseline.py`, `scan_engine.run_scan_core` | `compare OLD NEW` | DELETE (it *is* `compare`) | **Done** (Phase 6). Parity gate (Phase 3) passed first |
| 2 | Audit-only mode (no `--against`) | `scan_engine._audit_exit_code` | `compare --no-baseline` | COMPARE-STAGE | **Done.** Rides ADR-065's `declared_absent` state; `policy/no_baseline_findings.py` partitions the self-diff; audit gating via `policy/audit_gate_exit.py` (exit `3`, opt-in through `--severity-preset`) |
| 3 | Cross-source checks (11) | `buildsource/cross_source_checks.py`, run only from `scan_engine` | `compare` pipeline, per side | COMPARE-STAGE | **Done, 11 of 11.** Run automatically in every `compare()` (`cross_source_checks` default `True`, no flag — D4/D5), evolution-stated via `workflows/cross_source_evolution.py`. D3's authority rule holds: findings stay `RISK`/`API_BREAK`, never advisory-only (the 2026-09-09 amendment deleted `scan`'s stripping of them as the bug it was) |
| 4 | Private-header leakage | `crosscheck.private_header_leak` | as #3 | COMPARE-STAGE | **Done.** Boundary from `-H` directory provenance + `.abicheck.yml` `scope.public_header_dirs` (#22) |
| 5 | public-vs-exported (`public_not_exported`, `exported_not_public`) | `crosscheck` | as #3 | COMPARE-STAGE | **Done.** As #4 |
| 6 | Pattern checks (lexical pre-scan) | `buildsource/pattern_facts.py` | `compare` pipeline, per side | COMPARE-STAGE | **Done** (Phase 2b). `checker.compare`'s `pattern_preprocessor_scan` (default `True`, no flag) via `workflows/pattern_preprocessor_scan.py`; advisory `pattern_preprocessor_scan` report block, no `ChangeKind` |
| 7 | Pattern verdict modulation | `compare --pattern-verdicts` (already on `compare`) | `compare`, always-on where evidence exists | AUTOMATIC | **Done** (Phase 5): `--explain-patterns` no longer implies modulation; the ledger is unconditional disclosure (7o) |
| 8 | Preprocessor checks | `buildsource/preprocessor_facts.py` | `compare` pipeline, per side | COMPARE-STAGE | **Done** (Phase 2b) — as #6 |
| 9 | Build-context analysis / reconciliation | `scan` L3 collection; `compare --reconcile-build-context` | `compare --depth build`, reconciliation always-on when build context is present | AUTOMATIC + MERGE | **Done** (7i): reconciliation forced on inside `compare_snapshots`; flag and `CompareRequest.reconcile_build_context` removed |
| 10 | Source-ABI replay (L4) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | **Done** (Phase 2c, POI scoping parity) |
| 11 | Source-graph analysis (L5) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | **Done** (Phase 2c). ADR-037 D6 keeps L5 internal |
| 12 | Changed-path localization (`--since`, `--changed-path`) | `cli_scan.py`, `buildsource/poi.py` | `compare --since` / `--changed-path` | COMPARE-STAGE (per-run input, ADVANCED KEEP) | **Done** (Phase 2c). One owner, `workflows/changed_paths.py` |
| 13 | Risk-driven evidence selection (`--depth auto`) | `risk.py`, `model/evidence_depth_levels.py` | — | **DELETE** | **Done** (2026-09-09, ruled (b) by ADR-068's second amendment). Unpinned `--depth` resolves to `headers` (`evidence_depth_levels.resolve_unpinned_level`). Documented breaking change: a job relying on escalation must pin its rung |
| 14 | Risk rule overrides (`--risk-rules`) | `cli_scan_baseline._load_risk_rules` | — | **DELETE** | **Done** (ruled (b)): nothing left to tune once #13 selects nothing; no `risk:` config key |
| 15 | CPython/`abi3` audit (`--abi3`) | `scan_abi3_resolve.py`, `scan_engine._run_abi3_audit` | `compare` candidate-side enrichment stage | COMPARE-STAGE; floor value is CONFIG (`python.abi3_floor`) | **Done** (Phase 2d). Precondition failure uses the exit-`7` axis. Arming from a declared floor alone is G26's (§6 Phase 7 table) |
| 16 | Artifact-set / multi-library audit (`--artifact-set`) | `service_scan.run_scan_set`, `bundle.py` | `compare --no-baseline DIR` over ADR-065 members | DELETE (mode); capability preserved | Mode **deleted**. `compare --no-baseline DIR` still waits on ADR-065 S3 component inventories (P5) |
| 17 | Set member-identity/provider manifest (`scan --manifest`) | `cli_scan_helpers.load_artifact_set_manifest`; ADR-056; cli-cleanup PR H | `.abicheck.yml` bundle/provider contract, read by the same path | CONFIG | `scan` side deleted; PR H cancelled. Provider resolution is G42's |
| 18 | Analysis completeness/assurance | `analysis_assurance`, `--require-complete-analysis` (both commands) | `compare` (already present) | MERGE | **Done.** Flag demoted to `assurance.require_complete` after Phase 6 |
| 19 | Budget guard (`--budget`) | `scan_engine._check_scan_budget`, `_BudgetOverflow`, exit `5` | `compare --budget`, `ExitDecision` operational axis | ADVANCED KEEP; default in CONFIG | **Done** (Phase 4 commit 2). `deadline.deadline_scope` across resolve + classify; `DiffResult.budget_overflow`; typed API `CompareRequest.budget_s` |
| 20 | Finding cap (`--max-findings`) | `cli_scan_baseline` summary truncation | — | DELETE | **Done.** Complete machine data is never truncated (7m) |
| 21 | JSON resource budget (`--max-json-object-nodes` on `compare`) | `bundle_facts` decode | execution/storage config, calibrated `resource_limits:` | CONFIG | **Done** (7g). `resource_limits.max_bundle_facts_decode_nodes`; only an explicit `--config` may raise it, an auto-discovered file may lower it. Default deliberately unchanged and node-based — see 7g |
| 22 | Public-header boundary (`--public-header-dir`) | `cli_scan_baseline._public_provenance_set` | `-H` directory provenance + `.abicheck.yml` `scope.public_header_dirs` | MERGE | **Done.** `provenance.apply_provenance`, fed from `-H` directories and `scope.public_header_dirs`; directory-vs-file rule preserved |
| 23 | Per-check severity (`--crosscheck KEY=LEVEL`) | `CrosscheckConfig` | `--policy` / `.abicheck.yml` `policy.overrides` (they are `ChangeKind`s) | MERGE into policy | **Done.** `KEY=error` syntax ruled (b) and deleted with `scan`; `crosscheck_promotion_contribution` kept read-only for stored reports (see reconciliation note) |
| 24 | Severity / gate / policy / packs | shared decorators | unchanged on `compare` | MERGE | — |
| 25 | Contract evaluation (`--contract`) | shared | unchanged on `compare` | MERGE | — |
| 26 | Suppression display (`--show-suppressed`) | `cli_scan` | disposition ledger, always computed | AUTOMATIC | **Done** (ADR-067 S1; unconditional disclosure, 7o) |
| 27 | Not-comparable outcome (exit `6`) | `scan_engine` | `compare` (already has `_EXIT_NOT_COMPARABLE`) | MERGE | — |
| 28 | Evidence-contract abort (exit `7`) | `scan_engine._check_scan_evidence_contract` | `compare` `ExitDecision` axis | COMPARE-STAGE | **Done** (Phase 4 commit 2): `policy/depth_evidence_contract.py`, CLI + typed pipeline. Closed a real gap (`compare --depth build` with no evidence used to exit 0) |
| 29 | Coverage/depth reporting lines | `cli_scan_helpers.render_*` | `report/` compute/render pair | INTERNAL | **Done** with Phase 6 |
| 30 | `scan` report schema (`SCAN_SCHEMA_VERSION`) | `service_scan`, `workflows/scan_abort_result.py` | one `ReportDocument` | DELETE | **Done** (Phase 6): constant and its `schemas.py`/`check_report.py` call sites removed |
| 31 | `ScanRequest` / `ScanResult` typed API | `service_scan.py` | `CompareRequest` / `CompareResult` | DELETE (fields absorbed) | **Done** (2026-09-09). One field absorbed (`CompareRequest.allow_build_query`); the rest covered or ruled (b). ADR-055's amendment carries the ledger |
| 32 | Action `mode: scan` | `action/run.sh` (~40 branches) | `mode: compare` (+ `baseline-channel: none`) | DELETE after absorption | **Done** (2026-09-10). No `CMD+=(scan)` site; every `mode: scan` request assembles `compare`/`compare --no-baseline`; `--severity-preset default` injected on the audit shape (`AUDIT_GATE`, exit `3`) |
| 33 | `pr-comment` scan projection | `pr_comment_scan.py`, `pr_comment_scan_abort.py` | one PR-comment projection over `ReportDocument` | INTERNAL (merged) | **Done** (Phase 6: both modules deleted) |
| 34 | Depth vocabulary/resolution | `model/evidence_depth_levels.py` (was `buildsource/scan_levels.py`) | keep as engine primitive, renamed off `scan` | INTERNAL | **Done** (Phase 6 rename step). `poi.py`/`risk.py` had no `compare` caller and were deleted instead |
| 35 | Cost/dry-run estimation | `frontends/cli/scan_dry_run.py`, `artifact_set_dry_run.py` | `compare --dry-run` (ADR-043 D9 shared model) | MERGE | **Done** (Phase 2f): cost preview via `workflows/compare_cost_preview.py`; the surviving model is `dry_run_estimate.py` |
| 36 | `scan`-specific tests (33 modules — corrected from "40"; see Phase 6's own deletion-order checklist for the exact list and the false positives the glob over-counted) | `tests/test_*scan*` | rewritten against `compare`, or deleted with the mode | DELETE last | **Done** (Phase 6), plus the extra files listed there |

The seven DELETE rows are: a mode that duplicates `compare` (#1), a mode
whose capability is preserved by another spelling (#16), a truncation knob
that contradicts D4 (#20), two internal artifacts — a schema (#30) and a
typed API (#31) — replaced by canonical equivalents, and the two ADR-068's
second 2026-09-09 amendment added: risk-driven evidence selection (#13) and
the `--risk-rules` profile that fed it (#14). The first five removed nothing
a user gets; **the last two do**, and the amendment accepted that cost
explicitly (ruling (b): dropped, with no `compare` equivalent coming).

---

## 4. Flag retirement

All modern CLI commands. `compat` excluded (ADR-068 D7). Hidden options are
marked **H** and counted. The tables below are the **2026-09-06 audit's** classification (counts from Click introspection on `309c8a82`); many rows have since landed. The per-option outcome of record is `abicheck/frontends/cli/options/rulings.py` (§4.5).

Classes: **KEEP** (common per-run input) · **ADV** (advanced/exceptional
per-run input, `--help-all` only) · **CONFIG** (stable project property,
CLI spelling removed) · **AUTO** (tool determines/reports it, no flag) ·
**MERGE** (duplicate concept, represented once) · **REMOVE** (internal,
debug, or obsolete).


### 4.1 `compare` — 78 accepted at the 2026-09-06 audit (4 hidden)

| Flag | Class | Target representation | Rationale | Prerequisite |
|---|---|---|---|---|
| `--help`, `--help-all` | KEEP | unchanged | — | — |
| `-H/--header` | KEEP | unchanged (side-aware) | The canonical evidence input | — |
| `-I/--include` | KEEP | unchanged | — | — |
| `--sources` | KEEP | unchanged | Real per-run evidence | — |
| `--build-info` | KEEP | unchanged | Real per-run evidence | — |
| `--depth` | KEEP | unchanged, gains `auto` rung | One dial (ADR-037 D5) | #13 |
| `--config` | KEEP | unchanged | The project contract's own operand | — |
| `--policy` | KEEP | unchanged | — | — |
| `--suppress` | KEEP | unchanged | — | — |
| `--severity-preset` | KEEP | unchanged | — | — |
| `--contract` | KEEP | unchanged | The canonical contract mechanism | ADR-049 defects |
| `--used-by` | KEEP | unchanged | Genuine consumer evidence (vision D-S1) | — |
| `--required-symbol` | KEEP | unchanged | Genuine consumer evidence | — |
| `--required-symbols` | MERGE | `--required-symbol @file` | Two spellings of one input | — |
| `--format` | KEEP | unchanged | — | — |
| `-o/--output` | KEEP | unchanged | — | — |
| `--write` | KEEP | **repeatable** `FORMAT=PATH` | One analysis, N artifacts (D4) | — |
| `--dry-run` | KEEP | unchanged | — | — |
| `-v/--verbose` | KEEP | unchanged | — | — |
| *(new)* `--no-baseline` | KEEP | declares OLD absent | Replaces `scan` audit mode (D2) | Phase 1 |
| *(new)* `--since`, `--changed-path` | ADV | from `scan` | A PR's diff is genuinely per-run | Phase 2 |
| *(new)* `--budget` | ADV | from `scan`; default in config | Per-run guard on a CI job | #19 |
| `--report-mode` | MERGE | `--view` | 4 spellings of "render which parts, how" | — |
| `--show-only` | MERGE | `--view` | as above | — |
| `--explain-patterns` | MERGE | `--view patterns` | **and stops implying `--pattern-verdicts`** (D4) | Phase 5 |
| `--demangle/--no-demangle` | MERGE | `--view` | as above | — |
| `--show-filtered` | AUTO | disposition/scope ledger | Accounting is already unconditional | ADR-067 S1 |
| `--audit-suppressions` | AUTO | suppression accountability | A forgotten optional switch on an accountability feature | ADR-067 S1/S2 |
| `--surface-metrics` | AUTO | always emitted, informational | Free, `COMPATIBLE`-only, changes no verdict | — |
| `--pattern-verdicts/--no-` | AUTO | on where evidence exists | Evidence-gated already; the off-switch becomes policy | Phase 5 |
| `--reconcile-build-context` | AUTO | on when build context present | Clearing false positives should never be opt-in | ADR-039 |
| `--select`, `--select-required` | ADV | unchanged | ADR-065 S1, just landed | — |
| `--on-incomplete-scope` | CONFIG | `scope.on_incomplete` | A project's CI strictness, not a per-run choice | — |
| `--fail-on-removed-library` | CONFIG | `gate.fail_on_removed_library` | Stable project policy; **input fixed first** (vision A-S2/S4) | A-S4 |
| `--dso-only` | CONFIG | `release.dso_only` | Stable release topology | — |
| `--include-private-dso` | CONFIG | `release.include_private_dso` | as above | — |
| `--output-dir` | ADV | unchanged | Genuine per-run output location | — |
| `-j/--jobs` | REMOVE | auto-detect only | Already auto-detects and memory-clamps; the override is a tuning detail | — |
| `--keep-extracted` | REMOVE | — (**done**) | Debug detail | — |
| `--no-bundle-analysis` | REMOVE | — (**done**) | "Escape hatch" that disables real analysis; policy/suppression is the supported route | — |
| `--bundle-facts-out` | KEEP (ruled 7d) | unchanged | Per-run operand, `-o`'s shape; `dump` has no release fan-out to hold it (7d) | — |
| `--bundle-facts-library-manifest` | KEEP (ruled 7d) | unchanged | Document operand like `--policy`/`--suppress`; no config home for its override shape (7d) | G42 |
| `--instantiation-manifest` | CONFIG | contract document | A declared contract is a project property | — |
| `--max-json-object-nodes` | CONFIG (**done**) | `resource_limits.max_bundle_facts_decode_nodes` | Internal storage detail (#21) | Calibration — done, see #21 |
| `--debug-info` | ADV | unchanged (side-aware) | Real per-run evidence | — |
| `--devel-pkg` | ADV | unchanged (side-aware) | Real per-run evidence | — |
| `--version` | ADV | unchanged | Labels a bare `.so` operand | — |
| `--dump-manifest` | ADV | unchanged | Multi-TU operand (ADR-050) | — |
| `--old-variant`, `--new-variant` | ADV | unchanged | Variant selection is per-run scope (ADR-065) | — |
| `--probe-matrix` | ADV | unchanged | Per-run evidence | — |
| `--diagnostic-comparison` | ADV | unchanged | ADR-050 D2's sanctioned escape hatch | — |
| `--debug-root` | ADV | unchanged | Per-run artifact location | — |
| `--search-path`, `--ld-library-path` | ADV | unchanged | Per-run environment | — |
| `--follow-deps` | AUTO | on when dependency evidence is usable | "Enable a useful analysis" flag | Cost check |
| `--pack` | ADV | unchanged | ADR-049 D8 | — |
| `--env-matrix` | CONFIG | `deployment:` | Declared deployment constraints are stable | — |
| `--use-cases` | CONFIG | `use_cases:` | A stable declared manifest | — |
| `--post-manifest` | CONFIG | contract overlay | Duplicate contract/scope mechanism | ADR-049 |
| `--scope-public-headers/--no-` | MERGE | `--contract public` / `--contract all` | One contract mechanism — **not before** ADR-049's relevance defects close | `public-contract-default.md` Phase 6 |
| `--require-complete-analysis` | CONFIG | `assurance.require_complete` | Project CI strictness (vision E-S1 meaning preserved) | — |
| `--profile` | REMOVE | — | Bundles depth + rendering + gate policy; D4/D5 separate them (reverses ADR-040 Lever 3) | Phase 7 |
| `--ast-frontend` | CONFIG | `compile.frontend` | Extraction backend is a host/toolchain property | — |
| `--allow-ast-frontend-fallback` | CONFIG | `compile.ast_frontend_fallback` | as above | — |
| `--allow-unsupported-castxml` | CONFIG | `compile.allow_unsupported_castxml` | as above | — |
| `--compiler` | CONFIG | `compile.compiler` | Toolchain identity is stable per project/target | — |
| `--compiler-prefix` | MERGE | into `compile.compiler` | Convenience spelling of the same thing | — |
| `--compiler-option` | CONFIG | `compile.options` | as above | — |
| `--sysroot` | CONFIG | `compile.sysroot` | as above | — |
| `--nostdinc/--no-nostdinc` | CONFIG | `compile.nostdinc` | as above | — |
| `--frontend-context` | CONFIG | `compile.frontend_context` | as above | — |
| `--lang` | CONFIG | `compile.lang`, inferred by default | Inferable from headers/sources | Inference check |
| `--pdb-path` | CONFIG | `debug.pdb_path` | Debug-resolution tuning | — |
| `--debug-root` | *(ADV above)* | — | — | — |
| **H** `--dwarf-only/--no-` | REMOVE | `debug.dwarf_only` only | Already demoted; the hidden flag is the deprecation window this repo doesn't run | — |
| **H** `--debuginfod/--no-` | REMOVE | `debug.debuginfod` only | as above | — |
| **H** `--debuginfod-url` | REMOVE | `debug.debuginfod_url` only | as above | — |
| **H** `--debug-format` | REMOVE | `debug.format` only | as above | — |
| `--include-system-declarations` | ADV | unchanged | ADR-061 dependency-scope selector, per-run | — |


### 4.2 `dump` — 39 accepted at the 2026-09-06 audit (0 hidden)

`dump` stays evidence capture. Its toolchain/debug block is the *same*
concept as `compare`'s and moves the same way (ADR-037 D8.1: they share the
L2 compile context and must not drift).

| Flag | Class | Target | Rationale |
|---|---|---|---|
| `--help`, `--help-all`, `-v` | KEEP | unchanged | — |
| `-H/--header`, `-I/--include` | KEEP | unchanged | Evidence input |
| `--sources`, `--build-info` | KEEP | unchanged | Evidence input |
| `--depth` | KEEP | unchanged | One dial |
| `-o/--output` | KEEP | unchanged | — |
| `--config` | KEEP | unchanged | — |
| `--dry-run` | KEEP | unchanged | ADR-043 D9 |
| `--version` | ADV | unchanged | Snapshot label |
| `--dump-manifest` | ADV | unchanged | Multi-TU operand |
| `--git-tag`, `--build-id`, `--no-git` | MERGE | one `--provenance k=v` (repeatable) | Three spellings of "stamp this snapshot" |
| `--compression` | AUTO | inferred from `-o` suffix | `auto` is already the default and already correct |
| `--debug-root` | ADV | unchanged | Per-run artifact location |
| `--search-path`, `--ld-library-path` | ADV | unchanged | Per-run environment |
| `--follow-deps` | AUTO | on when usable | as `compare` |
| `--build-target` | CONFIG | `build.targets` | Already has a config key; CLI is the duplicate |
| `--compile-db-filter` | CONFIG | `build.compile_db_filter` | Stable project property |
| `--include-system-declarations` | ADV | unchanged | — |
| `--dwarf-only`, `--debug-format`, `--debuginfod`, `--debuginfod-url`, `--pdb-path` | CONFIG | `debug.*` | Debug resolution is a host/project property (matches `compare`) |
| `--ast-frontend`, `--allow-ast-frontend-fallback`, `--allow-unsupported-castxml`, `--compiler`, `--compiler-prefix`, `--compiler-option`, `--sysroot`, `--nostdinc`, `--frontend-context`, `--lang` | CONFIG / MERGE | `compile.*`, identically to `compare` | ADR-037 D8.1 — one shared compile context, no drift |


### 4.3 `deps` — 15 accepted at the 2026-09-06 audit

Reviewed for convergence (ADR-068 D6), not reduction.

| Flag | Class | Target | Rationale |
|---|---|---|---|
| `deps tree`: `--search-path`, `--sysroot`, `--ld-library-path` | KEEP | unchanged | *The environment is the operand* here — not a toolchain property |
| `deps compare`: `--old-root`, `--new-root` | KEEP | unchanged | The two environments being compared |
| `deps compare`: `--search-path`, `--ld-library-path` | KEEP | unchanged | as above |
| both: `--format`, `-o`, `--dry-run`, `-v` | KEEP | unchanged; `--format` gains the shared renderer set | Shared projection over `ReportDocument` |

No `deps` flag is removed. The `deps` work is internal: `StackVerdict` →
`RunOutcome`/`ExitDecision` axes, and its report → a `ReportDocument`
projection.

### 4.4 `aggregate` (6) and `project` (4 subcommands)

Advanced integration surface; left as-is by this plan except that
`--format`/`-o`/`-v` follow the shared projection contract. `aggregate
--manifest`/`--discovered-only` are genuine per-run CI inputs (ADR-047)
and stay (`--run-plan` folded into `--manifest`, 7q).

---

### 4.5 Target counts

The number is a consequence of the model, not the goal.

**Source of truth.** The per-option record is
`abicheck/frontends/cli/options/rulings.py`: every visible `compare` and
`dump` option carries a written ADR-068 D5 ruling (`per_run_operand` or
`deferred` with a mandatory named blocker), and
`tests/test_config_rebalance.py` asserts an **exact bijection** between those
rulings and the live options — an unruled option fails, and so does a ruling
naming an option that no longer exists (Phase 7k). Counts in this document are
therefore snapshots, not a gate.

**Live snapshot, 2026-09-29** (Click introspection, one logical option per
Click parameter, excluding `--help`/`--help-all`):

| Command | Live | Audit end state (7l) | 2026-09-06 audit |
|---|---|---|---|
| `compare` | **41** (9b + 9d, 2026-10-03) | 25 | 78 accepted (4 hidden) |
| `dump` | **20** | 13 | 39 |
| `aggregate` | **5** | 4 | 6 |
| `deps tree` | **6** | 6 | — |
| `project capture-variants` | **7** | — | (added by storage-format-v2 A1.6, after this audit) |
| `deps compare` | **7** | 7 | — |
| `project history` | **4** | 4 | — |
| `project plan` | **6** | 6 | — |
| `project validate` | **3** | 3 | (three subcommands, folded by 7p) |
| `compat check` / `compat dump` | frozen | frozen | excluded from every count (ADR-068 D7) |

**Growth since the previous revision's 40 / 18 / 4.** New options added by
other work after 7q/7r, each already ruled: `compare --diagnostic-comparison`,
`compare --version` and `dump --version` (keep rulings in `rulings.py`);
`compare --performance-profile` (#1383, keep ruling in `rulings.py`);
`dump --provenance` (#1355/#1377, keep ruling in `rulings.py`); and
`aggregate --analysis-context` (#1355). `rulings.py` covers only `compare` and
`dump`, so `aggregate --analysis-context` has **no** ruling there — nothing in
this repository rules `aggregate` options per flag.

**The gap to the audit's end state** (`compare` 25, `dump` 13) is entirely
the contract/capture-config question this plan has already gated or ruled
against with a measurement — there is no unexamined bucket:

- `compare`: the `deferred` rulings, plus the CONFIG demotions this plan
  declines or gates (`--dump-manifest`, `--include-system-declarations`,
  `--abi3`, `--severity-preset`, `--select-required`), plus the
  `--environment` collapse (G42), plus the options added since (above).
- `dump`: `--dump-manifest`/`--include-system-declarations` to a capture
  contract (G34), `--compression` (ruled a keep, 7k), `--environment` (G42),
  plus the options added since.

Original targets, kept as pressure rather than arithmetic: root commands
7 → **6** (met: `scan` retired); `compare` hidden accepted 4 → **0** (met,
7a); `compare` total ≤40 and `dump` ≤16 (met at 40 / 18 before the additions
above; `dump` ≤16 was never reachable through this plan's own rulings);
`deps` 15 → 15 (unchanged by design, D6).

Deleted implementation surface (Phase 6): the 10,074 lines of scan-only
`abicheck/` code across 16 modules, one JSON schema, one typed request/result
pair, `action/run.sh`'s 17 `mode == "scan"` conditionals, and 33 scan-only
test modules plus the extra files listed in Phase 6.

---

## 5. Prerequisites

Six cross-cutting blockers. Each gated a whole phase, not one row.

| # | Prerequisite | Gates | State |
|---|---|---|---|
| P1 | An acquisition state for "OLD declared absent" in ADR-065's vocabulary, with its completeness/outcome consequences | `--no-baseline` (§3 #2) | **Landed** (2026-09-09): `declared_absent` is a real `AcquisitionState`, stated as `old_acquisition_state` in every audit report |
| P2 | An evolution state on the canonical finding model, `not_evaluated` included, carried by `report/`'s compute/render pair | Every one-sided check migration (§3 #3-#8, #15) | **Landed** as a deliberately distinct enum from Phase 1's cross-comparison `FindingEvolution`: `checker_policy.CrossSourceEvolution` + `Change.cross_source_evolution`, produced by `workflows.cross_source_evolution.compute_cross_source_evolution`, projected by `report.cross_source_evolution`. Per-finding identity is a per-check function (a bare `symbol` key collapsed distinct findings for several checks). The correctness crux — a pre-existing problem never reads as newly introduced when one side's evidence is insufficient — is a property test for all eleven checks |
| P3 | `compare` emitting the budget-overflow (`5`) and evidence-contract (`7`) exit axes | `scan`'s deletion | **Landed** (Phase 4 commit 2); §3 #19/#28 |
| P4 | A public/internal boundary derivable from `-H` directory provenance plus `.abicheck.yml` `scope.public_header_dirs` | §3 #4, #5, #22 | **Solved** for both sources, threaded into `InputSpec.public_header_dirs`/`provenance.apply_provenance`. No new CLI flag. The file-vs-directory asymmetry is preserved verbatim (see `workflows/cross_source_evolution.py`'s module docstring) |
| P5 | ADR-065 S3's package component inventories | `compare --no-baseline DIR` (§3 #16, #17); `--select-required` merge | **Open** — workstream A's, not this plan's |
| P6 | `EntityId`-based public closure (ADR-063 Phase 2) and `public-contract-default.md` Phase 6's two relevance defects plus two uncovered measurement lanes | Phase 9 only | **Defects closed (2026-10-01).** The seed mismatch was closed earlier; the identity gap is closed by schema v54 slot identities captured at extraction, not by a string heuristic. The `package`/`real_binaries` coverage bound was **accepted** (2026-10-02, integration-tested rather than in the always-on measurement) |

P1 and P5 are ADR-065 work this plan consumes rather than owns; starting them
here would fork the model workstream A is building.

**Where the implementation lands is ADR-061's question, not this plan's.**
[ADR-061](../adr/061-responsibility-package-architecture.md) owns
responsibility ownership and dependency boundaries; this plan owns
capability topology. Three of its remaining acceptance gaps meet this plan
directly, and each has one owner rather than two:

- **No `workflows/scan` package, ever.** ADR-061's own `Request ->
  ResolvedPlan -> Result` examples were written around `ScanRequest`/
  `ResolvedScanPlan`/`ScanResult`; they now name the compare shapes instead,
  precisely because migrating a command scheduled for retirement into a
  permanent typed contract is work this plan's Phase 6 would then have to
  undo. `scan`'s surviving capabilities route to the compare workflow.
- **The canonical report replaces the scan schema** (§3, Phase 5) only once
  ADR-061's [gap C](../adr/061-responsibility-package-architecture.md#c-one-result-one-document-several-projections)
  closes — one completed evaluation producing one document that every format
  projects. "One analysis, several artifacts" is not deliverable while six
  formats each build their own document from a `DiffResult`.
- **One operand driver** (Phase 7d, re-homed from `cli-cleanup-phase-two.md`
  as PR I) is the frontend half of ADR-061's
  [gap D](../adr/061-responsibility-package-architecture.md#d-typed-requestplan-and-operand-convergence):
  selection, inventory and acquisition state belong on the shared
  request/plan, not in command-level orchestration. Do it once, in the
  shared contract.

The sequencing constraint runs the other way too: ADR-061's own closure
package 6 (facade and legacy retirement) may not delete a `scan`-related
surface ahead of this plan's Phase 6. A shorter facade is never a reason to
lose a capability.

## 6. PR sequence

Nine phases, each independently reviewable. The ordering was the mechanism
that prevented capability loss (ADR-068 D9): **no phase could be reordered
ahead of its predecessor**, and no `scan` code was deleted before Phase 6.

**Merge criteria every removal PR applied** (carried forward from
`cli-cleanup-phase-two.md`, unchanged): the old spelling exits `64` with no
hidden alias and is registered in `scripts/retired_surfaces.py`; front-end
parity (CLI, typed API, Action) in the same PR; a schema bump where a machine
contract changes; verdict, gate, exit code, coverage contribution and
assurance asserted separately from any display change. Phase 7l adds the
stricter bar: *the old user task still has a simple, supported invocation,
with the same relevant evidence and a truthful result* — a test asserting
only that a removed flag now errors proves deletion, not simplification.

### Phase 0 — Parity harness first (no behavior change)

**Done** (#1114). `tests/parity/` ran `scan` and `compare` over one fixture
corpus and diffed *finding sets* (kind, identity, severity, evidence refs),
starting red on every scan-only capability — that red set was the migration's
definition of done. Since Phase 6 the package is a compare-only regression
corpus (`tests/parity/__init__.py`): it pins `compare`'s behavior for those
capabilities plus the call-site pin that each engine primitive is reached from
exactly one `compare`-side workflow (`test_engine_primitive_call_sites.py`).
The `gaps.py` registry and its contract test are deleted.

### Phase 1 — Canonical primitives where `scan` owns unique behavior

**Done.**

1. **`declared_absent`** joined ADR-065's acquisition vocabulary (P1) — never
   a removal, never a pass.
2. **`FindingEvolution`** (`introduced`/`resolved`/`persistent`/
   `not_evaluated`): `policy.evidence_status.FindingEvolution` (re-exported
   via `checker_policy`), `Change.evolution`/`DiffResult.resolved_findings`,
   the correspondence primitive `policy/finding_evolution.py`
   (`compute_finding_evolution`/`compute_resolved_findings`/
   `apply_finding_evolution`) and the projection `report/finding_evolution.py`.
   **Consumed** by `compare`'s JSON report (`reporter.py`/`report/build.py`)
   and by `project history` (`cli_project.py` → `workflows/history.py`, one
   `apply_finding_evolution` per adjacent pair; `PairwiseSummary` carries
   `evolution_counts`/`resolved`). Markdown/HTML rendering of the evolution
   state is not built.
3. **Exit axes** (#1116): evidence-contract abort (`7`) and budget overflow
   (`5`) modeled as `ExitDecision` axes; `compare` began emitting them in
   Phase 4 commit 2.

### Phase 2 — Move the enrichments onto `compare`

**Done.** One PR per capability group, each landing with its parity tests
green. Decision that must not be re-litigated: **every enrichment runs
automatically, with no enable flag** on any front end (ADR-068 D4/D5 reject
"a flag that merely enables useful analysis").

- **2a** cross-source checks (§3 #3–#5) — all eleven, via
  `workflows/cross_source_evolution.py`, on the `CrossSourceEvolution` axis
  (P2; distinct from Phase 1's cross-comparison `FindingEvolution` because it
  states OLD-vs-NEW behavior *within one* `compare()` call). Checks whose
  findings are not uniquely keyed by symbol register their own identity
  (`private_header_leak`, `public_to_internal_dependency`,
  `rtti_for_internal_type`, `odr_type_variant`,
  `identity_collision_detected`, `compile_context_conflict`).
- **2b** pattern + preprocessor scans (#6, #8) — `workflows/
  pattern_preprocessor_scan.py`, derived from what each snapshot already
  records (no second collection pass), surfaced as the advisory
  `pattern_preprocessor_scan` report block rather than new `ChangeKind`s,
  since neither primitive ever produced a verdict-bearing finding.
- **2c** changed-path localization (#12) and POI scoping for
  `--depth source` (#10, #11) — one owner, `workflows/changed_paths.py`.
- **2d** `abi3` candidate-side enrichment (#15) — `compare --abi3` folds the
  audit through the pre-classification `extra_changes` channel, marked
  `candidate_side_enrichment`; floor from `python.abi3_floor`, flag as the
  per-run override.
- **2e** `--no-baseline` (#2), on top of Phase 1.1.
- **2f** dry-run cost preview (#35) — `compare --dry-run`'s "Cost preview",
  summed across operands by `workflows/compare_cost_preview.py` over the one
  surviving cost model (now `dry_run_estimate.py`). Advisory only.

### Phase 3 — Parity is the gate

**Done.** For every scenario `compare` produced the same or a strictly richer
finding set than `scan`, with no finding lost and none manufactured (the
`not_evaluated` cases). The no-baseline statement is *directional*: `compare
--no-baseline` reports at least every kind `scan` did, and every addition is
a real candidate-side finding in a permitted D3 state
(`tests/parity/test_no_baseline_audit_corpus_parity.py` over the eleven G20
audit fixtures; `tests/test_no_baseline_d3_properties.py` states D3 as a
property over generated candidates).

### Phase 4 — Migrate the consumers — done (Action deletion 2026-09-10)

**Done.** Governing ruling (ADR-068's second 2026-09-09 amendment): the
Action does **not** keep a compatible interface with every `scan` capability.
Each condition that used to force the legacy CLI was ruled (a) already
covered, (b) dropped as a documented breaking change, or (c) genuinely
required and built on `compare`. The ruling table lives in the ADR; the
condensed version in [known gaps](../known-gaps.md).

- **(c) built:** `compare --budget` (§3 #19) and the `--depth build`/`source`
  evidence-contract floor (§3 #28, `policy/depth_evidence_contract.py`).
- **(b) dropped:** `--risk-rules`, risk-driven `auto` depth, `--crosscheck
  KEY=error`, `scan --build-target`, `--artifact-set`/`new-library-set`
  (pending ADR-065 S3). The Action rejects each with an explicit `::error::`
  naming the replacement or blocker — never a silent downgrade.
- **Action.** `action/run.sh` has no `scan` invocation; every `mode: scan`
  request assembles `compare`/`compare --no-baseline` through one branch.
  The audit shape injects `--severity-preset default` when the caller states
  no preset, preserving `mode: scan`'s default gating through the audit-gate
  axis (`policy/audit_gate_exit.py`, exit `3`, `AUDIT_GATE` verdict;
  ADR-068's 2026-09-10 amendment). Coverage:
  `tests/test_action_run_sh_audit_gate.py`.
- **Typed API.** `ScanRequest`/`ScanResult`/`ScanArtifactResult`/
  `ScanSetResult`/`Budget`/`LayerResult` and `run_scan`/`run_audit`/
  `run_scan_set` deleted; `CompareRequest` → `CompareResult` is the one typed
  contract. One field absorbed: `CompareRequest.allow_build_query` (default
  `False` keeps "never run a build system as a side effect of resolving an
  input"). An unpinned depth resolves to the fixed `headers` rung, **not** the
  `--mode` preset (which would run a full source replay; caught in review,
  PR #1186).
- **`compare --no-baseline` fixed along the way.** It had aborted on its own
  `assert not diff.changes` (the self-diff invariant Phase 2's per-side stages
  legitimately violate), passed `-H` as parse input but not as public-header
  provenance, and ignored `--contract`/`--sources`/`--build-info`/`--depth`/
  `--dry-run`. `policy/no_baseline_findings.py` partitions the self-diff
  (comparison half still enforced, as a raised error that survives
  `python -O`); `report/no_baseline.py` gained its compute/render split,
  `findings[]`, and `sarif`/`junit`/`oneline`. `html`/`review` stay a usage
  error **by ruling**: both render a comparison, and an audit has none
  (`report.no_baseline.NO_BASELINE_UNSUPPORTED_FORMATS`).
- **Docs, examples, eval.** `docs/use/scan-levels.md` became
  `docs/use/evidence-depth.md`; the user docs, the nine G20 catalog case
  READMEs, `examples/workflows/audit-release` and `skills-src/` moved to
  `compare`/`compare --no-baseline`. `skills-src/evaluation/field/scan_level_scaling.py` was
  re-driven onto `compare` and later deleted in Phase 6 together with
  `skills-src/evaluation/validation/scripts/run_oneapi_scan.py`.

### Phase 5 — Presentation/analysis separation — **done**

- `--explain-patterns` stopped implying `--pattern-verdicts`; modulation is
  automatic, explanation is rendering.
- `--report-mode`/`--show-only`/`--demangle`/`--explain-patterns` collapsed
  into `--view` (whose internal grammar 7o later reduced further).
- `--surface-metrics` removed outright (metric-drift findings are ordinary
  `Change`s every projection renders; `compare_snapshots` forces it on and no
  longer takes the keyword); `--show-filtered` → `--view filtered` and
  `--audit-suppressions` → `--view suppressions` (both later made
  unconditional by 7o). D4 holds in its strong form: **no surviving `compare`
  flag decides whether a piece of the canonical result is computed.**
- `--write` became repeatable (later folded into `-o` by 7m).
- **Executable invariant (F-19; numbered F-16 in an older draft of this
  section).** `TestF19CanonicalResultIsInvariantOverTheWholeRenderingSpace`
  in `tests/test_presentation_analysis_separation.py` enumerates the full
  product of rendering axes (a seeded random sample in the default lane, all
  points under `slow`, plus a guard pinning the space's size), and its oracle
  is the Tier-2 verb `workflows.compare_policy.compare_snapshots` called
  directly — not the report projection under test. The predecessor test
  sampled instead of crossing the axes and used the implementation as its own
  oracle; do not regress to either. The space shrank from 6048 points to 189
  when 7o removed four axes.

### Phase 6 — Remove `scan`

**Done (2026-09-11).** No deprecated alias: `abicheck scan` exits `64` with
`No such command`, naming `compare`/`compare --no-baseline`.
`tests/test_cli_root_surface.py` pins the six-verb root set.

- **Deleted (list A):** `cli_scan.py`, `cli_scan_baseline.py`,
  `cli_scan_helpers.py`, `cli_scan_receipt.py`, `scan_engine.py`,
  `scan_abi3_resolve.py`, `pr_comment_scan.py`, `pr_comment_scan_abort.py`,
  `frontends/cli/scan_against.py`, `frontends/cli/scan_dry_run.py`,
  `workflows/scan_abi3_dry_run.py`, `workflows/scan_abort_result.py`,
  `workflows/scan_config.py` (`workflows/scan_gate_options.py` and
  `workflows/scan_subprocess.py` had already gone in Phase 4);
  `service_scan.py`'s surviving cost model became `dry_run_estimate.py`;
  `skills-src/evaluation/field/scan_level_scaling.py` and `skills-src/evaluation/validation/scripts/run_oneapi_scan.py`.
  `SCAN_SCHEMA_VERSION` and its call sites are gone.
- **Deleted (lists B and D):** the 33 scan-only test modules, and
  `buildsource/poi.py`/`risk.py` with `tests/test_poi.py`,
  `test_poi_scenarios.py`, `test_risk.py` — an AST call-site audit found
  neither module had a real `compare` caller, so they were deleted rather
  than renamed. Beyond the pre-staged lists, test files importing scan-only
  internals without "scan" in their name were pruned (not gutted —
  `test_compile_context_parity.py`/`test_compile_context_security.py`
  exercise the live `cli_options.merge_compile_config` and were kept minus
  their dead cases), and `tests/parity/test_baseline_gate_parity.py` was
  deleted outright (it pinned a scan/compare divergence with no second tool
  left to diverge from).
- **Renamed ahead of deletion (list C, the rename step):**
  `buildsource/crosscheck*.py` → `cross_source_checks*.py`,
  `pattern_scan.py`/`preprocessor_scan.py` → `pattern_facts.py`/
  `preprocessor_facts.py` (all stay in `buildsource/`: the main file's
  `extract`/unclassified dependencies forbid a move under `compare/`'s
  `may_import: [model]`), and `buildsource/scan_levels.py` →
  `model/evidence_depth_levels.py`.
- **Kept, not `scan`'s (list E):** `workflows/pattern_preprocessor_scan.py`,
  `report/pattern_preprocessor_scan.py`, `docs/use/evidence-depth.md`,
  `changelog.d/*scan*.md` (history). Naming-sweep false positives kept:
  `tests/test_scan_accuracy.py`, `tests/test_realworld_scan.py`,
  `tests/test_header_scan_deadline_integration.py`,
  `tests/scenarios/compliance_scanning.yaml`.
- **Unblocked demotions.** `compare --require-complete-analysis` →
  `assurance.require_complete` has landed; `compare --env-matrix` and
  `dump --build-target` were recorded in `rulings.py` as tracked follow-ups
  needing real feature work (a `deployment:` key; rewiring onto
  `build.targets`) rather than a ruling-table edit. Neither appears in the
  live option counts in §4.5.
- **Follow-up cleanup (2026-09-29):** see
  [Cleanup reconciliation](#cleanup-reconciliation-2026-09-29).

### Phase 7 — CLI/config cleanup

Only after one analysis path existed: the CONFIG/AUTO/MERGE/REMOVE rows of
§4, in small PRs grouped by concept. Every sub-slice is done except where the
table at the end of [Phase 7l](#phase-7l-external-cli-audit-2026-09-12-reconciled) says otherwise.

**7a — hidden debug flags. Done.** `compare`'s four hidden
`--dwarf-only`/`--debug-format`/`--debuginfod`/`--debuginfod-url` deleted
outright; each already had a `debug.*` key.

**7b — `compile.*` demotion. Done.** The whole L2 compile-context family
(`--ast-frontend`, `--allow-ast-frontend-fallback`,
`--allow-unsupported-castxml`, `--compiler`, `--compiler-prefix`,
`--compiler-option`, `--sysroot`, `--nostdinc`, `--frontend-context`,
`--lang`) removed from `compare` and `dump` as one unit (ADR-037 D8.1: the two
commands share one compile context and must not drift);
`--compiler`/`--compiler-prefix` merged into `compile.compiler`.

**7c — `debug.*` demotion on `dump`. Done.** 7a's treatment applied to
`dump`'s `--dwarf-only`/`--debug-format`/`--debuginfod`/`--debuginfod-url`/
`--pdb-path` (`--debug-format` was visible there; removed anyway for parity).

**7d — release/bundle topology. Done** (`1e9d59698`; absorbs cli-cleanup
PR J). `--on-incomplete-scope`/`--fail-on-removed-library`/`--dso-only`/
`--include-private-dso` → `scope.on_incomplete`/
`gate.fail_on_removed_library`/`release.dso_only`/
`release.include_private_dso`. `--keep-extracted` and `--no-bundle-analysis`
removed with no replacement (a tempdir knob; an escape hatch disabling real
analysis). **Not to re-litigate:** `--bundle-facts-out` stays — it is
`-o`'s shape for this invocation's evidence capture, and `dump` has no
release fan-out to hold it (a *missing implementation* with a named owner,
bundle capture in `dump`, per 7l); `--bundle-facts-library-manifest` stays as
a document operand like `--policy`/`--suppress` until G42 gives its
per-library override shape a config home.

**7e — `--profile` removal.** Scheduled in this phase; this document never
recorded the landing PR, but `compare` has no `--profile` option today (Click
introspection, 2026-09-29).

**7f — `dump` provenance merge. Done.** `--git-tag`/`--build-id`/`--no-git`
→ one repeatable `--provenance KEY=VALUE` (`git-tag=`, `build-id=`,
`git=auto|off`; last-one-wins per key). Grammar in
`frontends/cli/options/provenance.py`, validated eagerly by a Click callback
so every malformed token is exit `64` before extraction.

**7g — resource limits. Done.** `--max-json-object-nodes` →
`resource_limits.max_bundle_facts_decode_nodes`. Measured first
(`scripts/benchmark_scaling._build_onedal_large_surface`: ~9.3 bytes/node,
stable across scale; a 25k-function oneDAL-scale library needs ~5.8M nodes
against the 1M default). **Not to re-litigate:** the default stays
`DEFAULT_MAX_JSON_OBJECT_NODES=1_000_000` — raising it would raise every
unconfigured, possibly untrusted decode's ceiling (PR #1174 review); only an
explicit `--config` may raise the budget, an auto-discovered file may only
lower it; and the unit stays **nodes**, not memory, because a memory budget
converted at the legitimate-payload ratio would under-protect against an
adversarial payload's far lower bytes/node. Full table in `bundle_facts.py`'s
docstring.

**7h — small removals. Done.** `--required-symbols` folded into
`--required-symbol @FILE`; `-j/--jobs` removed (auto-detect and memory clamp
only).

**7i — whole-surface audit against D5's three guards. Done.** Every surviving
`compare`/`dump` option ruled, including the keeps. Removed:
`--reconcile-build-context` (AUTO: evidence-gated reconciliation can only
clear a phantom finding, never manufacture one; forced on in
`compare_snapshots`), `compare --pdb-path` → `debug.pdb_path` (per-side use
preserved through `--debug-info`'s debug-root transport),
`--support-promise` → `release.support_promise`, `dump --compile-db-filter` →
`build.compile_db_filter`. **Measured keep, not to re-litigate:**
`--follow-deps`/`--search-path`/`--ld-library-path` — the dependency walk
embeds absolute host paths in `provenance.dependency_info`, so making it
unconditional would break dump reproducibility and ADR-050 comparability and
let findings depend on the runner's installed libraries. Revisit only if the
dependency graph becomes host-independent (a snapshot-schema change).

**7j — `--variant`. Done.** `--old-variant`/`--new-variant` → one side-scoped
`--variant [old=|new=]VARIANT_ID`, matching every other two-sided input
(ADR-040 Lever 1). 7i's decline was reversed because its rationale was an
effort argument, which AGENTS.md rules out. An empty id is a usage error;
`_resolve_sided_variant` carries `TestResolveSidedVariantProperties`. The
unregistered release engine keeps its per-side spellings.

**7k — exhaustive per-option ruling registry. Done.** The old D10.5 budget
(`visible <= BASE + len(RAISES)`) had nine flags of slack and had let
`--budget` land unruled. Replaced by `frontends/cli/options/rulings.py`: one
`OptionRuling` per visible `compare` *and* `dump` option, `per_run_operand`
or `deferred` (blocker mandatory for the latter, rejected for the former, in
`__post_init__`), with the exact bijection test described in §4.5.
`test_the_superseded_budget_shape_would_have_missed_an_unruled_flag` asserts
the old shape's failure. Fresh rulings: `--search-path` and
`--ld-library-path` are **distinct** (they insert at different loader steps
in `resolver._candidate_dirs` and record different `resolution_reason`s);
`dump --compression` is a **keep** against §4.2's AUTO row
(`-o build/abi.json --compression zstd` is a real case the suffix cannot
express). Declined merges: `--write` into `--format`/`-o` (later superseded
by 7m's single grammar), `--select` into `--select-required` (the latter
declares a completeness obligation), `--used-by-manifest` into
`--used-by @FILE` (a manifest carries provenance a path cannot).

### Phase 7l — external CLI audit (2026-09-12), reconciled

An external static audit of the whole CLI at `31cbd4a` was reconciled
against this phase. Adopted as a standing principle: **a necessary capability
does not require a dedicated flag, and a removal that costs the user three
commands or an invented manifest is not a simplification**; "no replacement
today" is a migration prerequisite, not permanent ownership. Also adopted:
**auto-detection is a useful default, not proof an explicit override is
unnecessary**, and **environment variables must not become the new hidden
CLI** — a demoted flag lands in `.abicheck.yml`, never in an undocumented
variable.

**7m — one export request. Done.** `--format`/`-o/--output`/`--write`/
`--output-dir`/`--max-findings-per-library` → one repeatable
`-o FORMAT=DESTINATION` with `-` for stdout (`frontends/cli/options/
export.py`); a trailing `/` is a directory destination; machine data is never
truncated. Net −4 on `compare`, −1 on each other report-rendering command.
Consequences not to rediscover: a display filter applies to every export
(every machine projection still carries full disposition accounting); a
directory export on a single pair is a usage error; the Action's
`extra-args` export set replaces its own. Invariants in
`tests/test_cli_export_grammar.py`.

**7n — one input per evidence role. Done.** `--debug-root` → `--debug-info`,
`--devel-pkg` → `-H`, `--probe-matrix` → typed `--build-info`; net −3 on
`compare`, a rename on `dump`. Routing is content-only
(`workflows/evidence_transport.py`, `frontends/cli/options/evidence_roles.py`),
which forced `abicheck/package.py`'s extractors to detect by magic bytes, not
filename; `extract/detached_debug.py` adds the bare detached-debug-file
transport with build-id validation. **Explicitly not merged:** `--sources`
and `--build-info`.

**7o — `--view`'s internal grammar. Done** (net 0 options; fewer decisions).
`patterns`/`filtered`/`suppressions` are unconditional disclosure (the
pattern ledger echoes only when something was modulated); demangling is
automatic (human formats show `demangled [mangled]`, machine formats carry
both `symbol` and `demangled_symbol`; PE/MSVC stays undemangled by design);
display dimensions come from `ChangeKindMeta.entity`/`operation` (mandatory,
with `entity_from_field` for polymorphic kinds), replacing the name-prefix
tables that missed 238 of 407 kinds; `--view leaf` retired after a
129-pair measurement showed it never exposed a finding `root-cause` lacked
(report schema 5.0). The `impact` mode folds in `RenderOptions.__post_init__`.
Follow-up rounds closed disposition disclosure on the release path (release
schema 1.5) and Markdown pipe-escaping after demangling. Lessons kept: a
per-finding dimension is only tested by a finding the real detector produced;
disclosure has no single choke point, so it is tested per projection
(`tests/test_pattern_modulation_disclosure.py`). Remaining gap: the release
renderer shows no per-library scope ledger or suppression audit in human
output (machine projections unaffected).

**7p — `project validate` consolidation. Done.** `validate`/
`validate-build`/`validate-use-cases` → one `project validate INPUT`,
classified by `buildsource/validation_input.py` on schema/shape, never
filename. An empty document is validated under every reading; unparseable
YAML fails at classification.

**7q — `aggregate --run-plan` into `--manifest`. Done.** Classifier
`workflows/aggregate/expected_input.py`; an untagged plan is recognized by
its `checks` list, and one declaring both `checks` and `targets` without a
schema is rejected. `--discovered-only` stays **explicit**.

**7r — `project plan --allow-empty` retired. Done.** An empty plan records a
`skipped` block (`abicheck.run-plan/v3`): no `checks[]` declared is a valid
plan (exit 0); declared `checks[]` resolving to no cell stays an error with no
bypass.

**Declined, with the reason recorded:** `dump --compression` → retire (7k's
measurement); `--dry-run` → `--plan` (ADR-054 folded a `plan` vocabulary back
into `--dry-run`); `--used-by-manifest` → `--used-by @FILE` (7k);
`--severity-preset` into policy selection (for now — gate activation must
converge across pairwise, bundle and no-baseline first);
`--select-required` into an expected inventory (P5). The
`--follow-deps`/`--search-path`/`--ld-library-path` → `--environment
old=|new=` operand is **future direction owned by G42**, not a reduction; its
acceptance bar keeps same-run enrichment and forbids defaulting to the
current host.

**Smaller audit items.** `--abi3` armed from a declared floor — adopted in
part, owned by G26, never inferred from sniffing the binary.
`--dump-manifest`/`--include-system-declarations` as capture contract — a
live disagreement pending G34's capture specification (one spec for `dump`,
`abicheck-cc` and the Clang plugin). `deps`' defaulted `/` root — **done**:
`StackCheckResult.baseline_env_defaulted`/`candidate_env_defaulted`, from
Click's parameter source, stated in every projection. `ABICHECK_CC_DISABLE`
treating `"0"` as disable — **fixed over the class**: `abicheck/env_flags.py`
is the one boolean parser and registry (bug class
`config.env_flag_value_domain`). `compat`'s options stay frozen (D7).

**7s — per-option rulings for every command. Done** (2026-10-02).
`rulings.py` now rules every visible option of `aggregate`, the four
`project` subcommands and both `deps` subcommands (keyed by command path,
e.g. `"project plan"`), in addition to `compare`/`dump`. All 35 are keeps;
none duplicates a config key. Notable reasons: `--toolchain-bindings` is a
trust-boundary operand that a `.abicheck.yml` key would hand to the
untrusted config it validates; `aggregate --discovered-only` is 7q's
explicit no-inventory declaration, not a gate-disabling hatch (there is no
gate without a manifest); `project plan --project` is runtime provenance a
config copied into a fork would misstate. `compat` is excluded on purpose
(D7: frozen ABICC spellings). `tests/test_config_rebalance.py` closes the
table's *domain*, not only its rows: it walks the real Click tree and fails
on any option-bearing command with no ruling table — the gap that let these
commands go unruled after 7k, and that the earlier open-item note (which
missed `deps`) did not catch either.

**7t — release suppression audit. Done** (2026-10-02). 7o's remaining
gap, re-measured: the per-library scope ledger was already in the release
Markdown (`🔕 Disposed Findings`, one row per scoped-out finding with its
reason), but the suppression audit (stale, expired and near-expiry rules,
and rules that hid a BREAKING change) reached stderr only — and, contrary to
the earlier note, the release JSON did not carry it either. Each
`libraries[]` entry now has the scalar report's `suppression_audit` block
(same builder, release schema 1.12), and the release Markdown ends with a
per-library `🧾 Suppression Audit` section rendered from it, after the
document's demangle pass so rule labels stay verbatim.
`tests/test_release_suppression_audit.py` uses the single-pair report as the
oracle across four rule sets.

**What remains in Phase 7.** Nothing executable. Every remaining item is
gated on a named prerequisite or owned elsewhere — this table is the
authoritative open list:

| Item | Where it is |
|---|---|
| ~~`--scope-public-headers`, `--post-manifest`~~ | **Done** — Phase 9b/9c/9d (2026-10-03); `--contract` is the one contract mechanism |
| `--instantiation-manifest`, `--use-cases`, `--bundle-facts-library-manifest`, `--bundle-facts-out` | `deferred`/keep rulings with named blockers in `rulings.py` |
| `--follow-deps`/`--search-path`/`--ld-library-path` → one `--environment` operand | G42 |
| `--abi3` armed from a declared floor | G26 |
| `--dump-manifest`, `--include-system-declarations` → capture contract; one capture specification for `dump`/`abicheck-cc`/the Clang plugin | G34 |
| `--severity-preset` merged into policy selection | declined for now — needs gate-activation convergence first |
| `--select-required` merged into an expected inventory | ADR-065 P5 (package component inventories) |
| `dump --compression`, `--dry-run`→`--plan`, `--used-by-manifest`→`--used-by @FILE` | declined with a measurement (7k/7l) |

### Phase 8 — `deps` convergence (ADR-068 D6) — done

`StackVerdict` → `ExitDecision`: `stack_checker.exit_decision_for_stack_compare`/
`exit_decision_for_stack_tree` fold `deps`'s loadability/ABI-risk/
not-comparable axes through `policy.exit_decision.resolve_exit_decision` (new
`ExitReason.LOADABILITY`/`ExitDecision.loadability_contribution`;
`NOT_COMPARABLE` reused for ADR-050 D2 mismatches). `cli_stack.py` exits with
`decision.code`; every documented exit code is unchanged and pinned
(`tests/test_cli_deps_stack.py`, `tests/test_stack_checker_unit.py`). The
stack JSON report is a `report.stack.compute_stack_report_document` →
`ReportDocument` → `render_json` projection. No user-visible flag or
exit-code change. A later `compare`/`deps` unification is **future
direction**, not scoped here.

### Phase 9 — Contract-mechanism consolidation (gated, may not start early)

`--scope-public-headers` → `--contract public`, `--post-manifest` → a
contract overlay. Never trade a possible false negative for a shorter CLI
(ADR-068 context).

**Blocker status (2026-10-01).** Both relevance defects named in
[`public-contract-default.md`](public-contract-default.md) Phase 6 are closed:

- *Template-instantiated-parameter seed mismatch*: closed earlier
  (directly-referenced stdlib spellings threaded through
  `contract_pipeline.build_contract_stage`). This plan had not recorded it.
- *`ambiguous_namespaced_leaf` identity gap*: closed by schema v54. castxml
  records which record/enum each type slot resolves to; the exact
  public-surface walk and the evaluator use it. Verified on real binaries for
  the single pair and the directory fan-out: the reached record's break goes
  from `UNKNOWN_UNRESOLVED`/exit 1 to `IN_CONTRACT`/exit 4, and an unreached
  sibling stays unconfirmed.

`public`'s unresolved-loss budget is now **1**, an explained case: the
spelling-only shape that the clang JSON backend, DWARF and pre-v54 baselines
still produce (`docs/contribute/known-gaps.md`). **What still gates Phase 9:**
accepting that the `package` and `real_binaries` lanes are covered by
integration tests (`tests/test_contract_type_identities_integration.py`,
`tests/test_abi_examples.py`) rather than by the always-on
`measure_contract_shadow.py` measurement. Also, under `--ast-frontend clang`
a same-leaf record's break still only reaches the coverage floor. Both are
maintainer acceptance decisions, not engineering defects.

**Accepted (2026-10-02, maintainer).** Both decisions are taken: the
`package` and `real_binaries` lanes are covered by the named integration
tests rather than by `measure_contract_shadow.py`, and the clang-frontend
same-leaf case reaching only the coverage floor is a recorded known gap
(`docs/contribute/known-gaps.md`), not a Phase 9 blocker. Phase 9 is
unblocked.

**9a — the mapping, measured and fixed. Done** (2026-10-02). Every case
of the labelled FP-rate corpus was run through the `compare` CLI under all
four spellings. `--contract public` loses no real break: every one keeps its
legacy exit except the spelling-only same-leaf case (exit 1, the budgeted
loss), and no internal-noise case scores as a break (exit 1 only where the
public coverage floor fires — the expected migration difference, since
legacy scoping never exits 1). `--contract all`, however, was **not** the
exact `--no-scope-public-headers` alias it is documented as: the legacy
filter ran at its *default* value ahead of the evaluator, so ten internal
breaks came out exit 0 instead of 4. Fixed in `checker.compare` (shared by
the CLI and the typed API): an explicit `all`/`exports` domain disables
header-origin demotion, which is the `public` domain's question. Pinned by
`tests/test_contract_legacy_scope_mapping.py`.

**9b — the legacy scope flags deleted. Done** (2026-10-03).
`--scope-public-headers`/`--no-scope-public-headers` are gone from `compare`
(exit 64, `No such option`, no alias). Header-origin scoping stays on for a
run with no `--contract`: `.abicheck.yml`'s `scope.public` (built-in `true`)
is now the whole answer, so a no-flag run is unchanged. `--contract all`
replaces the opt-out for one run; `scope.public: false` keeps the unscoped
reading without contract evaluation; `--contract auto` takes its domain from
`scope.public`. What went with the flag: the `scope` option family and its
decorator (`cli_options.scope_options`, the `cli-contract` gate's required
family), `resolve_compare_config`'s `cli_scope_public` argument, the
receipt's typed `scope_public_headers` parameter, its `rulings.py` entry, and
the stored-bundle-facts rejection of the flag (config `scope:` was already
rejected there). Kept on purpose: the `LegacyScopeFlag` receipt vocabulary
and `legacy_alias_*` reason codes, which stored reports reference, and
`CompareRequest.scope_public`. User-facing text that named the flag now says
"public-header scoping". `tests/test_contract_legacy_scope_mapping.py` keeps
the 9a oracle on the setting that now spells each reading (no flag;
`scope.public: false`) and pins that the no-flag run still scopes, and
`tests/_legacy_scope.py` is the one spelling of the opt-out for tests. The
F2 route-parity harness's axis moved from a Click parameter to a config key
(`Axis.config_keys`), keeping the typed-API default guard through
`CONFIG_DEFAULT_MAP`.

**9c — `--post-manifest`'s config home. Done** (2026-10-03).
`.abicheck.yml`'s `contract:` block, `overlays: {post_manifest: PATH}`, feeds
the same allowlist and the same `post_manifest` evidence provider the flag
does; a relative path resolves against the project root
(`project_root_for_config`), and the Action's config relocation rewrites it
like `compile.include_dirs`. Flag > config for one run. Schema and parsing
live in `buildsource/build_config_contract.py` (strict loading: unknown
overlay kinds and empty paths fail), route decisions in
`frontends/cli/contract_overlays.py`. Found while wiring it: the release
fan-out never received `--post-manifest`, so a directory/package comparison
accepted and silently ignored it. The flag is now exit 64 there, and the
config key is a stderr note (a project property, not this invocation's;
an unapplied narrowing overlay can only add findings). Same split on the
`--no-baseline` audit; a stored-bundle-facts baseline rejects the `contract:`
block with the other blocks it cannot honour. `tests/test_post_manifest_config_overlay.py`
uses the flag as the oracle across three sibling pair shapes. The resolved
`ContractConfig.overlays` field is still not populated from either spelling,
and a pack assigning it stays rejected (`UNAPPLIED_PACK_FIELDS`); the
persisted `post_manifest` provider record (under `--contract`) carries the applied allowlist and its digest, identically for either spelling — it does not record which spelling or path selected it.

**9d — `--post-manifest` deleted. Done** (2026-10-03). Exit 64 (`No such
option`, no alias); `contract.overlays.post_manifest` is the only spelling.
With the flag gone, 9c's "flag is exit 64 on the release fan-out/audit"
branch went too: `frontends/cli/contract_overlays.py` now reads the config
alone and only ever notes, never rejects. Removed with it: the option, its
`rulings.py` entry, the audit's and stored-bundle driver's flag rejections,
and the F2 route-parity row. The tests' oracle moved from the flag to
`service.compare_snapshots(..., public_surface_allowlist=...)` called
directly; `tests/_legacy_scope.post_manifest_config_args` is the one test
spelling.

**Trust boundary (Codex security review on #1477).** Moving the overlay
from an operator-typed flag into project config also moved it across a
trust boundary: an auto-discovered `.abicheck.yml` is editable by the pull
request being judged, and an empty or partial manifest would move a real
export removal out of the gate. The overlay therefore applies only from an
explicitly named `--config` (the `build.query`/`compile.compiler`
precedent); a discovered value is a stderr note, and
`action_config_overlay.strip_untrusted_execution_keys` drops it from a
discovered config. `test_a_discovered_config_cannot_narrow_the_gate` uses
the no-overlay run as its oracle.

**Remaining (not slices of this plan).** Extending overlays to the release
fan-out would need a per-library manifest shape, which no project has asked
for. Retiring `scope.public`/`CompareRequest.scope_public` is a separate
Python-API decision. One asymmetry that decision should settle: the typed
API always states `scope_public` (default `True`) at the `legacy_alias`
layer, while a no-flag CLI run now resolves `contract.mode` from the
built-in default, so the two receipts name different layers for the same
value (they already did for any untyped CLI run before 9b).

### Re-homed from `cli-cleanup-phase-two.md`

| Item there | Disposition here |
|---|---|
| PR H — `scan --artifact-set` member-identity manifest | **Cancelled** — the mode is deleted (§3 #16/#17); the declared-provider capability becomes config, read by the canonical path |
| PR I — one operand driver / one evaluation-gate-report-dry-run path | Phase 7d, narrowed: with `scan` gone there are fewer operand shapes to unify |
| PR J — bundle topology out of CLI flags; `--max-json-object-nodes` | Phase 7d + 7g — **done** |

---

## 7. Acceptance criteria

Behavioral scenarios proving `scan`'s removal loses nothing. Each is an
executable test, run through the **public** entry point (CLI and Action),
over both live operands and stored snapshots. `scan`-derived rows also run
under Phase 0's parity harness while both commands exist.

| # | Scenario | Must hold |
|---|---|---|
| F-1 | Stripped binary, no DWARF, no headers | Symbol-level result with the layout dimension reported `unverified`; never an empty surface, never a clean claim |
| F-2 | Binary + public headers | Full L2 declaration result; header-origin scoping applied |
| F-3 | Build + source evidence (`--depth source`) | L3–L5 findings identical to `scan --depth source` on the same inputs |
| F-4 | Source-only/API change invisible in the binary | Detected through the available evidence, classed `API_BREAK`, not `BREAKING` (authority rule) |
| F-5 | Private header leak | `private_header_leak` reported by `compare` |
| F-6 | Public declaration not exported | `public_not_exported` reported by `compare` |
| F-7 | Exported symbol not publicly declared | `exported_not_public` reported by `compare` |
| F-8 | **Pre-existing** leak, baseline lacking headers | `not_evaluated` on OLD — reported as a candidate-side finding with baseline evidence absent, **never** as `introduced` |
| F-9 | Leak present in OLD, fixed in NEW | `resolved`, and visible on a passing run |
| F-10 | Preprocessing/build-context inconsistency | `compile_context_conflict` / `header_build_context_mismatch` reported by `compare` |
| F-11 | Known consumer affected | `consumer_scope` reports the impact **beside** the full-library result (vision D-S1); the gate still reflects the library |
| F-12 | Known consumer unaffected, library ABI still breaks | Exit code and verdict describe the library break; consumer section is informational only |
| F-13 | Multi-library package pair | One comparison, per-member results, one gate — no separate mode |
| F-14 | Deliberately selected one-of-many local variant | Unselected members are `not_supplied`; **no** removals manufactured |
| F-15 | Missing CI matrix member | `incomplete` scope; `warn`→0 / `block`→1 (ADR-065 D6); never a removal |
| F-16 | Proven removed release component | Exit `8` only with NEW's inventory proven complete (ADR-065 D2) |
| F-17 | Incomplete evidence under `--contract` | Coverage failures listed, unsuppressible, exit contribution `1` folded with `max` |
| F-18 | Suppression accounting | Raw vs. effective totals with rule provenance in every projection, on a passing run |
| F-19 | **Output-format invariance** | Canonical result block byte-identical across `--format`/`--view`/`--write`/demangle permutations; exit code identical |
| F-20 | `--explain-patterns` does not change the verdict | Verdict/exit identical with and without it, on a fixture where the old implication changed both |
| F-21 | Live vs. snapshot parity | `compare A.so B.so` ≡ `dump A.so && compare A.json B.so` for the same evidence |
| F-22 | `--no-baseline` audit | Candidate-side findings present; every one carries an evolution state drawn from `{persistent, not_evaluated}` — ADR-068 D3 permits both against a `declared_absent` OLD and forbids only `introduced`/`resolved`; **no** additions, removals, or compatibility verdict |
| F-23 | `--no-baseline` over a directory | Replaces `scan --artifact-set`: per-member audit findings, one result document |
| F-24 | Budget overflow / evidence-contract abort | Exits `5`/`7` from `compare` with the same precedence `scan` had (ADR-064) |
| F-25 | Not-comparable operands | Exit `6`, no fabricated findings |
| F-26 | Usage errors | `compare NEW` (one operand, no flag) and `compare --no-baseline OLD NEW` both exit `64` — arity is declared, never inferred |
| F-27 | Removed spellings | Every flag deleted in Phase 7 exits `64` with `No such option`; no silent ignoring |
| F-28 | Action parity | Every documented Action input produces the same verdict/exit/annotations before and after the `scan`→`compare` re-implementation |

**The migration gate (Phase 3):** F-3 through F-10, F-22 and F-23 must show
`compare` producing a finding set equal to or richer than `scan`'s on the
same inputs, with every difference explained by an evolution state — not by
a missing check.

---

## Non-goals

Restated so they are checkable in review:

- Not preserving `scan` because ~10,000 lines exist for it.
- Not introducing `check`, or any command whose semantics depend on argument
  count (§A, ADR-068 D2).
- Not moving every removed flag into YAML one-for-one (§4, ADR-068 D5.1).
- No `--set internal.path=value` escape hatch.
- Not copying `scan`'s 45 options onto `compare`; five are added, and
  `compare` still shrinks from 78 to ≤40.
- Not making `dump` produce compatibility verdicts.
- Not merging `deps` into `compare` (ADR-068 D6).
- Not modernizing `compat`.
- Not flipping the contract default before its false-negative prerequisites
  close (Phase 9).
- Not shortening the CLI by hiding behavior or ignoring supplied input.

