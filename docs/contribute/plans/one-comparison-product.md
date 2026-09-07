---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# One comparison product — retiring `scan`, consolidating the CLI

**Owner ADR:** [ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md).
**Status:** In progress. Phase 0 (the parity harness, `tests/parity/`, #1114),
Phase 1 item 3 (exit axes, #1116), Phase 1 item 2 (`FindingEvolution`, the
cross-comparison-*chain* correspondence primitive — `checker_policy.
FindingEvolution`, `Change.evolution`/`DiffResult.resolved_findings`,
`policy/finding_evolution.py`, `report/finding_evolution.py`; not yet wired
into any CLI command, see that item's own entry below), and Phases 2c/2d/2e
have all landed. §5 P2's own evolution-state prerequisite is **partially**
landed too, under a second, deliberately distinct enum
(`checker_policy.CrossSourceEvolution` + `Change.cross_source_evolution`)
for the *same-comparison* per-side axis `FindingEvolution` does not cover:
two of the eleven cross-source checks are migrated onto it so far —
`unversioned_exported_symbol` and `private_header_leak` (§3 #3-#4) — proving
out the **correctness crux** (a pre-existing problem never reads as newly
introduced when one side's evidence is insufficient) via a property test for
each. Both checks now run **automatically** on every `compare()` invocation
(`cross_source_checks` defaults to `True`, no front end exposes a way to
disable it — ADR-068 D4/D5 reject "a flag that merely enables useful
analysis"), so both checks' `tests/parity/gaps.py` rows are deleted — the
scan-vs-compare parity harness confirms `compare` now finds them too. The
remaining nine cross-source checks, the pattern/preprocessor scans, and the
abi3 audit's `scan`-only enrichment paths are unaffected; `scan` itself is
untouched and not yet retired. Originally verified against `main` at
`309c8a82` on 2026-09-06 by Click introspection and call-site inspection,
not by help text or status prose; re-verified against `main` at `f7b4fdcc`
on 2026-09-07, and again against `main` at `2a64dc3c` on 2026-09-07 after
merging the automatic-default change with #1125's `private_header_leak`
migration — re-verify again against current `main`
(`git log -1 --format=%H origin/main`) before trusting any capability-loss
table row above as still accurate.
**Effort:** XL · **Risk:** high — this deletes a public command and moves
capabilities between analysis paths. Phase ordering is the safety mechanism.

**Audit cross-reference:** [`product-gaps-2026-09-audit.md`](product-gaps-2026-09-audit.md)
§§4-5 re-verified this plan's status against `main` on 2026-09-07 (open PR
#1125 continues Phase 1/2 work — see that doc for what it adds) and found
no contradiction with what's recorded here; it adds no new phases, only a
dated confirmation and a pointer for anyone auditing product-gap coverage
end to end.

**Subordinate to** [`vision-api-abi-evolution.md`](vision-api-abi-evolution.md)
for anything about what a *result means*. Where the two disagree, that plan
wins. This plan owns the **interface and its capability topology**: which
command owns which analysis, and what a user has to type.

**Supersedes the remaining scope of**
[`cli-cleanup-phase-two.md`](cli-cleanup-phase-two.md). That file's three open
items are re-homed here (§6): PR H (`scan --artifact-set` member identity)
is *cancelled* — the mode is being deleted; PR I (one operand driver) and
PR J (bundle topology out of CLI flags) become Phase 7 slices. Its closed
items stay closed and are not re-opened.

---

## 1. Current state

Three findings from the audit of `main`, each verified directly:

**1. `compare` cannot reach `scan`'s checks.** Verified by call site: the
only production callers of `buildsource.crosscheck.run_crosschecks`,
`buildsource.pattern_scan.scan_files` and
`buildsource.preprocessor_scan.run_preprocessor_scan` anywhere under
`abicheck/` are `scan_engine.py:1292`, `:1126` and `:1275`. Check by call
site, not import: `workflows/extraction.py` imports two of the three and
calls neither. The eleven cross-source checks —

`private_header_leak`, `public_not_exported`, `exported_not_public`,
`rtti_for_internal_type`, `odr_type_variant`, `unversioned_exported_symbol`,
`compile_context_conflict`, `header_build_context_mismatch`,
`source_surface_dso_mismatch`, `public_to_internal_dependency`,
`identity_collision_detected`

— plus the lexical pattern pre-scan, the preprocessor scan, changed-path
localization and the `abi3` audit are **`scan`-only**. The command the vision
names as the product owns the smaller check set. This is a correctness and
discoverability defect, not a tidiness one, and it is what makes this
migration a capability *gain* rather than a cleanup.

**2. Two of everything.** `scan` owns ~10,000 lines across
`cli_scan.py` (1981), `cli_scan_baseline.py` (1380), `cli_scan_helpers.py`
(598), `cli_scan_receipt.py` (204), `scan_engine.py` (1496),
`service_scan.py`, `scan_abi3_resolve.py`, `workflows/scan_*.py` (5 modules),
`frontends/cli/scan_*.py` (2), `pr_comment_scan*.py` (2); a separate typed
API (`ScanRequest`/`ScanResult`); a separate JSON schema
(`SCAN_SCHEMA_VERSION` 1.23, nesting `compare`'s result under `diff`);
~40 branches in `action/run.sh`; 40 test modules.

**3. Surface size, in that order of priority.** 78 accepted `compare`
options (4 hidden), 45 on `scan`, 39 on `dump`. Roughly 30 of them are
stable project properties retyped every run. Surface size is the *third*
problem, not the first — a short invocation is not clean if it reports a
pre-existing leak as newly introduced.

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
| **Advanced** | `project {validate,validate-build,validate-use-cases,plan}` | Read one project-integration artifact and report on it. |
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
abicheck compare OLD NEW --write json=result.json --write markdown=summary.md

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

| # | Capability | Current owner | Target owner | Class | Prerequisite |
|---|---|---|---|---|---|
| 1 | Baseline comparison (`--against`) | `cli_scan_baseline.py`, `scan_engine.run_scan_core` | `compare OLD NEW` | DELETE (it *is* `compare`) | Parity suite (Phase 3) |
| 2 | Audit-only mode (no `--against`) | `scan_engine._audit_exit_code` | `compare --no-baseline` | COMPARE-STAGE | ADR-065 `declared_absent` acquisition state (Phase 1) |
| 3 | Cross-source checks (11) | `buildsource/crosscheck.py`, run only from `scan_engine` | `compare` pipeline, per side | COMPARE-STAGE | Evolution-state model (Phase 1); `not_evaluated` correctness (F-7). **1 of 11 landed**: `unversioned_exported_symbol` runs automatically inside `compare()` (`cross_source_checks`, default `True`, no flag — ADR-068 D4/D5); the other 10 remain scan-only |
| 4 | Private-header leakage | `crosscheck.private_header_leak` | as #3 | COMPARE-STAGE | Public/internal boundary from `-H` provenance (#22) |
| 5 | public-vs-exported (`public_not_exported`, `exported_not_public`) | `crosscheck` | as #3 | COMPARE-STAGE | as #4 |
| 6 | Pattern checks (lexical pre-scan) | `buildsource/pattern_scan.py` | `compare` pipeline, per side | COMPARE-STAGE | Phase 2 |
| 7 | Pattern verdict modulation | `compare --pattern-verdicts` (already on `compare`) | `compare`, always-on where evidence exists | AUTOMATIC | Decouple `--explain-patterns` (ADR-068 D4) |
| 8 | Preprocessor checks | `buildsource/preprocessor_scan.py` | `compare` pipeline, per side | COMPARE-STAGE | Phase 2 |
| 9 | Build-context analysis / reconciliation | `scan` L3 collection; `compare --reconcile-build-context` | `compare --depth build`, reconciliation always-on when build context is present | AUTOMATIC + MERGE | ADR-039 reconciliation already shipped |
| 10 | Source-ABI replay (L4) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | Parity on POI scoping (#11) |
| 11 | Source-graph analysis (L5) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | ADR-037 D6 keeps L5 internal |
| 12 | Changed-path localization (`--since`, `--changed-path`) | `cli_scan.py`, `buildsource/poi.py` | `compare --since` / `--changed-path` | COMPARE-STAGE (per-run input, ADVANCED KEEP) | Phase 2 |
| 13 | Risk-driven evidence selection (`--depth auto`) | `risk.py`, `buildsource/scan_levels.py` | `compare --depth` (`auto` rung) | AUTOMATIC | Depth resolution shared (already `scan_levels.resolve_level`) |
| 14 | Risk rule overrides (`--risk-rules`) | `cli_scan_baseline._load_risk_rules` | `.abicheck.yml` `risk:` | CONFIG | Phase 7 |
| 15 | CPython/`abi3` audit (`--abi3`) | `scan_abi3_resolve.py`, `scan_engine._run_abi3_audit` | `compare` candidate-side enrichment stage | COMPARE-STAGE; floor value is CONFIG (`python.abi3_floor`) | Phase 2 |
| 16 | Artifact-set / multi-library audit (`--artifact-set`) | `service_scan.run_scan_set`, `bundle.py` | `compare --no-baseline DIR` over ADR-065 members | DELETE (mode); capability preserved | ADR-065 S3 component inventories |
| 17 | Set member-identity/provider manifest (`scan --manifest`) | `cli_scan_helpers.load_artifact_set_manifest`; ADR-056; cli-cleanup PR H | `.abicheck.yml` bundle/provider contract, read by the same path | CONFIG | ADR-056 superseded; G42 provider resolution |
| 18 | Analysis completeness/assurance | `analysis_assurance`, `--require-complete-analysis` (both commands) | `compare` (already present) | MERGE | Vision E-S1/S2 landed |
| 19 | Budget guard (`--budget`) | `scan_engine._check_scan_budget`, `_BudgetOverflow`, exit `5` | `compare --budget`, `ExitDecision` operational axis | ADVANCED KEEP; default in CONFIG | ADR-064 axis already modelled |
| 20 | Finding cap (`--max-findings`) | `cli_scan_baseline` summary truncation | — | DELETE | `--write json=` guarantees the full result (ADR-068 D4) |
| 21 | JSON resource budget (`--max-json-object-nodes` on `compare`) | `bundle_facts` decode | execution/storage config, calibrated `resource_limits:` | CONFIG | A real bytes-per-node calibration (was cli-cleanup PR J) |
| 22 | Public-header boundary (`--public-header-dir`) | `cli_scan_baseline._public_provenance_set` | `-H` directory provenance + `.abicheck.yml` `scope.public` | MERGE | Directory-vs-file provenance rule preserved verbatim |
| 23 | Per-check severity (`--crosscheck KEY=LEVEL`) | `CrosscheckConfig` | `--policy` / `.abicheck.yml` `policy.overrides` (they are `ChangeKind`s) | MERGE into policy | Each check has a registry entry |
| 24 | Severity / gate / policy / packs | shared decorators | unchanged on `compare` | MERGE | — |
| 25 | Contract evaluation (`--contract`) | shared | unchanged on `compare` | MERGE | — |
| 26 | Suppression display (`--show-suppressed`) | `cli_scan` | disposition ledger, always computed | AUTOMATIC | ADR-067 S1 landed |
| 27 | Not-comparable outcome (exit `6`) | `scan_engine` | `compare` (already has `_EXIT_NOT_COMPARABLE`) | MERGE | — |
| 28 | Evidence-contract abort (exit `7`) | `scan_engine._check_scan_evidence_contract` | `compare` `ExitDecision` axis | COMPARE-STAGE | `compare` does not emit `7` today — must be added before deletion |
| 29 | Coverage/depth reporting lines | `cli_scan_helpers.render_*` | `report/` compute/render pair | INTERNAL | ADR-061 `report/` pair rule |
| 30 | `scan` report schema (`SCAN_SCHEMA_VERSION`) | `service_scan`, `workflows/scan_abort_result.py` | one `ReportDocument` | DELETE | Every consumer migrated (Phase 4) |
| 31 | `ScanRequest` / `ScanResult` typed API | `service_scan.py` | `CompareRequest` / `CompareResult` | DELETE (fields absorbed) | ADR-055 registry update |
| 32 | Action `mode: scan` | `action/run.sh` (~40 branches) | `mode: compare` (+ `baseline-channel: none`) | DELETE after absorption | Phase 4; Action input lifecycle (ADR-047) |
| 33 | `pr-comment` scan projection | `pr_comment_scan.py`, `pr_comment_scan_abort.py` | one PR-comment projection over `ReportDocument` | INTERNAL (merged) | Phase 4 |
| 34 | Depth vocabulary/resolution | `buildsource/scan_levels.py` | keep as engine primitive, renamed off `scan` | INTERNAL | Rename only after callers move |
| 35 | Cost/dry-run estimation | `frontends/cli/scan_dry_run.py`, `artifact_set_dry_run.py` | `compare --dry-run` (ADR-043 D9 shared model) | MERGE | Phase 2 |
| 36 | `scan`-specific tests (40 modules) | `tests/test_*scan*` | rewritten against `compare`, or deleted with the mode | DELETE last | Phase 6 |

**Nothing in this table is classified DELETE for a capability a user
currently gets.** The five DELETE rows are: a mode that duplicates
`compare` (#1), a mode whose capability is preserved by another spelling
(#16), a truncation knob that contradicts D4 (#20), and two internal
artifacts — a schema (#30) and a typed API (#31) — replaced by canonical
equivalents.

---

## 4. Flag retirement

All modern CLI commands. `compat` excluded (ADR-068 D7). Hidden options are
marked **H** and counted. Counts from Click introspection on `309c8a82`.

Classes: **KEEP** (common per-run input) · **ADV** (advanced/exceptional
per-run input, `--help-all` only) · **CONFIG** (stable project property,
CLI spelling removed) · **AUTO** (tool determines/reports it, no flag) ·
**MERGE** (duplicate concept, represented once) · **REMOVE** (internal,
debug, or obsolete).

### 4.1 `compare` — 78 accepted today (4 hidden)

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
| `--keep-extracted` | REMOVE | — | Debug detail | — |
| `--no-bundle-analysis` | REMOVE | — | "Escape hatch" that disables real analysis; policy/suppression is the supported route | — |
| `--bundle-facts-out` | REMOVE | `dump` writes evidence | Evidence capture belongs to `dump` (D2) | Phase 7 |
| `--bundle-facts-library-manifest` | CONFIG | `.abicheck.yml` per-library headers | cli-cleanup PR J, unchanged intent | G42 |
| `--instantiation-manifest` | CONFIG | contract document | A declared contract is a project property | — |
| `--max-json-object-nodes` | CONFIG | `resource_limits:` | Internal storage detail (#21) | Calibration |
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
| `--ast-frontend` | CONFIG | `compile.ast_frontend` | Extraction backend is a host/toolchain property | — |
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

### 4.2 `dump` — 39 accepted today (0 hidden)

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

### 4.3 `deps` — 15 accepted today

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
--manifest`/`--run-plan`/`--discovered-only` are genuine per-run CI inputs
(ADR-047) and stay.

---

### 4.5 Target counts

The number is a consequence of the model, not the goal.

| Surface | Today | Target | Notes |
|---|---|---|---|
| Root commands | 7 | **6** | `scan` retired |
| `compare` — options shown in `--help` | ~30 | **≤18** | The KEEP set in C.1 |
| `compare` — total accepted (incl. hidden) | **78 + 45 (`scan`)** | **≤40** | Absorbs `scan`'s capabilities while shrinking |
| `compare` — hidden accepted | 4 | **0** | Hidden ≠ removed (ADR-068 D5) |
| `dump` — total accepted | 39 | **≤16** | Shares the `compile.*`/`debug.*` demotion |
| `deps` — total accepted | 15 | **15** | Unchanged by design (D6) |
| `aggregate` / `project` | 6 / 3–8 | unchanged | — |
| `compat` | frozen | **excluded** | Not in any target or metric (D7) |
| Modern-CLI accepted total (excl. `compat`) | ~180 | **≤95** | — |

Deleted implementation surface, once Phase 6 completes: ~10,000 lines of
`scan`-specific CLI/engine/service/report code, one JSON schema, one typed
request/result pair, ~40 `action/run.sh` branches.

---

## 5. Prerequisites

Six cross-cutting blockers. Each gates a whole phase, not one row — the
per-row prerequisites in §3 and §4 are the detail beneath these.

| # | Prerequisite | Gates | State today |
|---|---|---|---|
| P1 | An acquisition state for "OLD declared absent" in ADR-065's vocabulary, with its completeness/outcome consequences | `--no-baseline` (§3 #2), and therefore the whole audit half of the retirement | Not started; ADR-065 S2's record exists to extend |
| P2 | An evolution state on the canonical finding model, `not_evaluated` included, carried by `report/`'s compute/render pair | Every one-sided check migration (§3 #3-#8, #15) | **Partially landed**: a second, deliberately distinct enum from Phase 1 item 2's cross-comparison-chain `FindingEvolution` above — `checker_policy.CrossSourceEvolution` + `Change.cross_source_evolution` state how a cross-source check behaves across OLD/NEW *within one* `compare()` call, with `workflows.cross_source_evolution.compute_cross_source_evolution` as the model's first real producer and `report.cross_source_evolution`'s compute/render pair as its first real JSON projection (schema 3.7) — now covering **two** checks, `unversioned_exported_symbol` and `private_header_leak` (§3 #3-#4), the latter added in the same PR that generalized the matching primitive's per-finding identity (a symbol-only key silently collapsed two distinct leaked types flagged on the same function; identity is now a per-check function, defaulting to `symbol` for the original check). Both checks are now reachable by every front end: `compare()` runs the whole stage automatically (`cross_source_checks` defaults to `True`), no opt-in flag anywhere (ADR-068 D4/D5). **The correctness crux** — a pre-existing problem must never read as newly introduced when a side's evidence can't confirm it — is exercised as a property test for both checks. The other cross-source checks (§3 #5, #15) remain unmigrated |
| P3 | `compare` emitting the budget-overflow (`5`) and evidence-contract (`7`) exit axes | `scan`'s deletion (they are `scan`-only today; `cli_stack.py`'s own `5` is unrelated) | ADR-064 already models the precedence; `compare` does not emit them |
| P4 | A public/internal boundary derivable from `-H` directory provenance plus `.abicheck.yml` `scope.public` | The leakage and public-vs-exported checks (§3 #4, #5, #22) | `scan --public-header-dir` has the rule; it must survive the move verbatim, file-vs-directory asymmetry included |
| P5 | ADR-065 S3's package component inventories | Folding `--artifact-set`'s members into the one selection model (§3 #16, #17) | Not started — workstream A's next slice |
| P6 | `EntityId`-based public closure (ADR-063 Phase 2) and `public-contract-default.md` Phase 6's two open relevance defects plus two uncovered measurement lanes | Phase 9 only — the `--scope-public-headers` → `--contract public` collapse | Open. **Not a string heuristic, and never traded for a shorter CLI** |

Two of these (P1, P5) are ADR-065 work this plan consumes rather than owns;
starting them here would fork the model workstream A is building. P2 and P3
are this plan's own Phase 1.

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

Nine phases, each independently reviewable. The ordering is the mechanism
that prevents capability loss (ADR-068 D9) — **no phase may be reordered
ahead of its predecessor**, and no `scan` code is deleted before Phase 6.

### Phase 0 — Parity harness first (no behavior change)

Build `tests/parity/` running `scan` and `compare` over the same fixture
corpus and diffing the *finding sets* (kind, identity, severity, evidence
refs) while both commands still exist. It starts red on every check in
§3 #3–#8, #12, #15 — that red set *is* the migration's definition of done.
**Deliverable:** an executable capability-loss detector, not a document.

### Phase 1 — Canonical primitives where `scan` owns unique behavior

1. **`declared_absent`** joins ADR-065's `MemberAcquisition` vocabulary, with
   its completeness/outcome consequences (never a removal, never a pass).
2. **`FindingEvolution`** (`introduced`/`resolved`/`persistent`/
   `not_evaluated`) on the canonical finding model, with `report/`
   compute/render support. **Done** — `checker_policy.FindingEvolution`,
   `Change.evolution`/`DiffResult.resolved_findings`, the correspondence
   primitive (`policy/finding_evolution.py`:
   `compute_finding_evolution`/`compute_resolved_findings`/
   `apply_finding_evolution`), and the JSON projection
   (`report/finding_evolution.py`, `report_schema_version` 3.5). Not yet
   wired into any CLI command or into `workflows/history.py` (that consumer
   wiring, and Markdown/HTML rendering, are follow-up work, matching this
   phase's own "No CLI change" scope) — the primitive itself is what this
   item asked for.
3. **Evidence-contract abort (exit `7`)** and **budget overflow (exit `5`)**
   become `compare` `ExitDecision` axes (ADR-064's precedence already models
   them; `compare` does not emit them yet).
   *No CLI change in this phase.*

### Phase 2 — Move the enrichments onto `compare`

One PR per capability group, each landing with parity tests going green:

- **2a** cross-source checks (§3 #3–#5), per side, evolution-stated —
  **2 of 11 landed**: `unversioned_exported_symbol` and
  `private_header_leak` (§3 #3-#4) run automatically inside `compare()`'s
  pipeline (`checker.compare`'s `cross_source_checks`, default `True`, no
  CLI/API/Action opt-in flag — ADR-068 D4/D5), via
  `workflows/cross_source_evolution.py`'s `compute_cross_source_evolution`
  — deliberately distinct from Phase 1 item 2's cross-comparison-*chain*
  `FindingEvolution` above, since this axis states a check's OLD-vs-NEW
  behavior *within one* `compare()` call, not across separate calls over
  time. That module's own per-finding identity is a per-check function (not
  a bare `symbol` key): a check whose findings are not uniquely keyed by
  symbol alone (`private_header_leak`, where one function can leak two
  distinct private types) registers its own. Both checks'
  `tests/parity/gaps.py` rows are deleted — the scan-vs-compare parity
  harness confirms `compare` finds them under its ordinary, default
  invocation. The other nine checks (`exported_not_public`,
  `public_not_exported`, `header_build_context_mismatch`,
  `odr_type_variant`, `public_to_internal_dependency`,
  `rtti_for_internal_type`, `identity_collision_detected`,
  `compile_context_conflict`, `source_surface_dso_mismatch`) remain
  scan-only and blocked on P4 (public/internal boundary) for the ones that
  need it;
- **2b** pattern + preprocessor scans (#6, #8);
- **2c** changed-path localization `--since`/`--changed-path` (#12) and POI
  scoping parity for `--depth source` (#10, #11) — **landed**: the seed and
  ADR-043 D7's scoping rule now have one owner (`workflows/changed_paths.py`)
  that both commands resolve through, and the `changed_path_localization`
  gap entry is gone from `tests/parity/gaps.py`;
- **2d** `abi3` candidate-side enrichment (#15) — **landed**: `compare
  --abi3` folds the audit's findings into the same result document, marked
  `candidate_side_enrichment`, through the pre-classification
  `extra_changes` channel so policy/suppression/verdict score them; its
  precondition failure reuses Phase 1's evidence-contract exit axis (`7`).
  The floor is `.abicheck.yml`'s `python.abi3_floor` with the flag as the
  per-run override (ADR-068 D5). `abi3_audit` is gone from the gap registry;
- **2e** `--no-baseline` (#2) — the audit-only comparison, on top of Phase 1.1;
- **2f** dry-run/cost preview parity (#35).

### Phase 3 — Parity is the gate

Phase 0's suite goes fully green: for every scenario, `compare` produces the
same or a strictly richer finding set than `scan`, with no finding lost and
no finding manufactured (the `not_evaluated` cases in F-7/F-8). This phase
lands **no** feature; it lands the proof and the fixtures.

### Phase 4 — Migrate the consumers

- **Action:** `mode: scan` re-implemented internally as `mode: compare`
  (+ `baseline-channel: none` for S5, per ADR-047 §8), keeping every
  documented Action input working; ~40 `run.sh` branches collapse.
- **Typed API:** `ScanRequest`/`ScanResult` fields absorbed into
  `CompareRequest`/`CompareResult`; ADR-055's schema registry updated.
- **Docs:** every page that presents `scan` as a supported user workflow is
  rewritten — `docs/start/choose-your-workflow.md`,
  `docs/integration/scenarios/single-build-audit.md`,
  `docs/use/scan-levels.md`, `docs/use/github-action-source-scans.md`,
  `docs/reference/exit-codes.md`, the example catalog rows, and the
  `skills-src/` skill body.
- **Examples/eval/validation:** corpora re-driven through `compare`.

### Phase 5 — Presentation/analysis separation

- `--explain-patterns` stops implying `--pattern-verdicts`; modulation
  becomes automatic and explanation becomes rendering.
- `--report-mode`/`--show-only`/`--demangle`/`--explain-patterns` collapse
  into `--view`.
- `--show-filtered`/`--show-suppressed`/`--audit-suppressions`/
  `--surface-metrics` become always-computed, rendered on request.
- `--write` becomes repeatable.
- **Executable invariant test:** the canonical result block is byte-identical
  across every rendering permutation (F-16).

### Phase 6 — Remove `scan`

The command, `cli_scan*.py`, `scan_engine.py`, `service_scan.py`,
`workflows/scan_*.py`, `frontends/cli/scan_*.py`, `pr_comment_scan*.py`,
`SCAN_SCHEMA_VERSION`, the Action's `scan` mode branches, and the 40
`scan` test modules — deleted in that order, only after Phases 3 and 4 have
proven no caller remains. `buildsource/crosscheck.py`,
`pattern_scan.py`, `preprocessor_scan.py`, `poi.py`, `risk.py` and
`scan_levels.py` **survive** as engine primitives (§3 #34) and are renamed
off the `scan` identity in the same PR. `tests/test_cli_root_surface.py` is
updated to the six-verb set in the same commit as the registration removal
(ADR-043 D12 / ADR-054 #6).

No deprecated alias is kept. `abicheck scan` exits `64` with `No such
command`, with an error message naming `compare --no-baseline`.

### Phase 7 — CLI/config cleanup

Only now, with one analysis path: the CONFIG/AUTO/MERGE/REMOVE rows of §4,
in small PRs grouped by concept —
7a hidden flags (4) · 7b `compile.*` demotion (shared `compare`+`dump`) ·
7c `debug.*` demotion · 7d release/bundle topology (absorbs cli-cleanup
PR J) · 7e `--profile` removal · 7f `dump` provenance merge ·
7g resource limits (needs the calibration cli-cleanup PR J identified) ·
7h `--required-symbols`, `-j`, `--keep-extracted`, `--no-bundle-analysis`.

Every PR in this phase meets the merge criteria recorded in
[`cli-cleanup-phase-two.md`](cli-cleanup-phase-two.md) — old spelling exits
`64` with no hidden alias, front-end parity in the same PR, schema bump where
a machine contract changes, and verdict/gate/exit/coverage/assurance asserted
separately. That list is carried forward unchanged; it is not restated here.

### Phase 8 — `deps` convergence (ADR-068 D6)

`StackVerdict` → `RunOutcome`/`ExitDecision`; the stack report becomes a
`ReportDocument` projection; `cli_stack.py`'s inline `0/1/4/5` exits are
replaced. No user-visible flag change. A later `compare`/`deps` unification
is recorded as **future direction**, not scoped here.

### Phase 9 — Contract-mechanism consolidation (gated, may not start early)

`--scope-public-headers` → `--contract public`, `--post-manifest` → a
contract overlay. **Blocked** on
[`public-contract-default.md`](public-contract-default.md) Phase 6's two open
relevance defects and two uncovered measurement lanes. Never trade a possible
false negative for a shorter CLI (ADR-068 context).

### Re-homed from `cli-cleanup-phase-two.md`

| Item there | Disposition here |
|---|---|
| PR H — `scan --artifact-set` member-identity manifest | **Cancelled** — the mode is deleted (§3 #16/#17); the declared-provider capability becomes config, read by the canonical path |
| PR I — one operand driver / one evaluation-gate-report-dry-run path | Phase 7d, narrowed: with `scan` gone there are fewer operand shapes to unify |
| PR J — bundle topology out of CLI flags; `--max-json-object-nodes` | Phase 7d + 7g, unchanged in intent |

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
| F-5 | Private header leak | `private_header_leak` reported by `compare` (today: `scan` only) |
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
| F-22 | `--no-baseline` audit | Candidate-side findings present; evolution axis `not_evaluated`; **no** additions, removals, or compatibility verdict |
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
