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
have all landed. §5 P2's own evolution-state prerequisite is **landed**
too, under a second, deliberately distinct enum
(`checker_policy.CrossSourceEvolution` + `Change.cross_source_evolution`)
for the *same-comparison* per-side axis `FindingEvolution` does not cover:
all eleven cross-source checks are migrated onto it now —
`unversioned_exported_symbol` and `private_header_leak` landed first,
`exported_not_public`, `public_not_exported`, `rtti_for_internal_type`, and
`public_to_internal_dependency` (§3 #3-#5) joined next, and
`header_build_context_mismatch`, `odr_type_variant`,
`identity_collision_detected`, `compile_context_conflict`, and
`source_surface_dso_mismatch` close out the row in this slice — proving
out the **correctness crux** (a pre-existing problem never reads as newly
introduced when one side's evidence is insufficient) via a property test for
each. All eleven checks now run **automatically** on every `compare()`
invocation (`cross_source_checks` defaults to `True`, no front end exposes a
way to disable it — ADR-068 D4/D5 reject "a flag that merely enables useful
analysis"), so all eleven checks' `tests/parity/gaps.py` rows are deleted —
the scan-vs-compare parity harness confirms `compare` now finds them too.
`scan --against`'s baseline-compare path scores a cross-source finding
exactly as `compare` does — same verdict, same severity, same exit-code
contribution (2026-09-09, Phase 4 commit 1, ADR-068 amendment; see §3 #3
below for the full account of why the earlier stripping behavior was itself
the bug). `scan`'s own dedicated `crosscheck` report block and
`--crosscheck KEY=error` promotion remain a separate, scan-only surface,
unaffected by this. An
earlier slice also solved §5 P4 for its two directory/config sources: a
project's `.abicheck.yml` `scope.public_header_dirs` list (a new key,
distinct from the pre-existing boolean `scope.public`) is folded,
additively, into the same `-H`-directory-derived public/internal boundary
(`provenance.apply_provenance`) `compare`'s live-binary dumping already
built from a `-H` *directory* argument — see `workflows/
cross_source_evolution.py`'s own module docstring for the exact wiring and
the directory-vs-file asymmetry it preserves verbatim. **Phase 2b is now
landed too**: the lexical pattern pre-scan and the preprocessor pre-scan
(§3 #6/#8) run automatically on every `compare()` invocation the same
way — `checker.compare`'s own `pattern_preprocessor_scan` keyword, default
`True`, no opt-in flag — via `workflows/pattern_preprocessor_scan.py`'s
`compute_pattern_preprocessor_scan`, folded through the same
`CrossSourceEvolution` axis rather than a second one; the result rides a
new, always-present `pattern_preprocessor_scan` report block (advisory
only, since neither primitive ever produced a verdict-bearing finding even
under `scan`) rather than `ChangeKind` findings, and both entries are gone
from `tests/parity/gaps.py`. All fifteen originally-registered scan-only
capabilities are closed now (the eleven cross-source checks, pattern +
preprocessor scan, changed-path localization, and the abi3 audit); `scan`
itself is untouched and not yet retired. Originally verified against `main`
at `309c8a82` on 2026-09-06 by Click introspection and call-site inspection,
not by help text or status prose; re-verified against `main` at `f7b4fdcc`
on 2026-09-07, again against `main` at `2a64dc3c` on 2026-09-07 after
merging the automatic-default change with #1125's `private_header_leak`
migration, again on 2026-09-07 after the four-check slice, again on
2026-09-07 after the five-check slice closed out all eleven checks, and
again on 2026-09-07 after the pattern/preprocessor-scan slice closed
Phase 2b — re-verify again against current `main`
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
`cli-cleanup-phase-two.md`. That file's three open
items are re-homed here (§6): PR H (`scan --artifact-set` member identity)
is *cancelled* — the mode is being deleted; PR I (one operand driver) and
PR J (bundle topology out of CLI flags) become Phase 7 slices. Its closed
items stay closed and are not re-opened.

---

## 1. Current state

Three findings from the audit of `main`, each verified directly:

**1. `compare` cannot reach `scan`'s checks.** Verified by call site: the
only production callers of `buildsource.cross_source_checks.run_crosschecks`,
`buildsource.pattern_facts.find_pattern_facts` and
`buildsource.preprocessor_facts.collect_preprocessor_facts` anywhere under
`abicheck/` are `scan_engine.py:1292`, `:1126` and `:1275`. Check by call
site, not import: `workflows/extraction.py` imports two of the three and
calls neither. The eleven cross-source checks —

`private_header_leak`, `public_not_exported`, `exported_not_public`,
`rtti_for_internal_type`, `odr_type_variant`, `unversioned_exported_symbol`,
`compile_context_conflict`, `header_build_context_mismatch`,
`source_surface_dso_mismatch`, `public_to_internal_dependency`,
`identity_collision_detected`

— plus the lexical pattern pre-scan, the preprocessor scan, changed-path
localization and the `abi3` audit were **`scan`-only** at the time of this
original audit. The command the vision names as the product owned the
smaller check set. This was a correctness and discoverability defect, not a
tidiness one, and it is what made this migration a capability *gain* rather
than a cleanup. **This finding is now closed for all eleven cross-source
checks** — see the page's own `Status:` line above and §3 #3 for current,
maintained status; this section stays as the original audit record rather
than being rewritten in place, since a corrected historical finding would
misstate what the audit actually found at the time.

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
| 3 | Cross-source checks (11) | `buildsource/cross_source_checks.py`, run only from `scan_engine` | `compare` pipeline, per side | COMPARE-STAGE | Evolution-state model (Phase 1); `not_evaluated` correctness (F-7). **11 of 11 landed**: `unversioned_exported_symbol` and `private_header_leak` landed first, then `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`, and `public_to_internal_dependency`, and finally `header_build_context_mismatch`, `odr_type_variant`, `identity_collision_detected`, `compile_context_conflict`, and `source_surface_dso_mismatch` (this PR) — all eleven run automatically inside `compare()` (`cross_source_checks`, default `True`, no flag — ADR-068 D4/D5). **Update (2026-09-09, Phase 4 commit 1, ADR-068 amendment):** `scan --against`'s own baseline-compare path no longer strips these findings back out of its diff — the stripping was itself the bug (D3's own authority rule: these findings stay `RISK`/`API_BREAK`, never advisory-only), not a preserved invariant; `cli_scan_baseline._strip_automatic_cross_source_findings` is deleted. A baseline `scan` now gates a cross-source finding exactly as `compare` does — same verdict, same severity, same exit-code contribution; `scan`'s dedicated `crosscheck` report block and `--crosscheck KEY=error` promotion are unaffected (still scan-only surface). This is a documented breaking change to `scan --against`'s baseline-comparison result, not merely an internal refactor |
| 4 | Private-header leakage | `crosscheck.private_header_leak` | as #3 | COMPARE-STAGE | **Landed.** Public/internal boundary from `-H` provenance + `.abicheck.yml` `scope.public_header_dirs` (#22, now solved for both sources) |
| 5 | public-vs-exported (`public_not_exported`, `exported_not_public`) | `crosscheck` | as #3 | COMPARE-STAGE | **Landed.** As #4 |
| 6 | Pattern checks (lexical pre-scan) | `buildsource/pattern_facts.py` | `compare` pipeline, per side | COMPARE-STAGE | **Landed** (Phase 2b) — `checker.compare`'s `pattern_preprocessor_scan` keyword, default `True`, no flag (ADR-068 D4/D5), via `workflows/pattern_preprocessor_scan.py`; folded through `CrossSourceEvolution`, surfaced as the new `pattern_preprocessor_scan` report block (advisory, no `ChangeKind`, since the primitive never produced one under `scan` either) |
| 7 | Pattern verdict modulation | `compare --pattern-verdicts` (already on `compare`) | `compare`, always-on where evidence exists | AUTOMATIC | Decouple `--explain-patterns` (ADR-068 D4) |
| 8 | Preprocessor checks | `buildsource/preprocessor_facts.py` | `compare` pipeline, per side | COMPARE-STAGE | **Landed** (Phase 2b) — as #6 |
| 9 | Build-context analysis / reconciliation | `scan` L3 collection; `compare --reconcile-build-context` | `compare --depth build`, reconciliation always-on when build context is present | AUTOMATIC + MERGE | ADR-039 reconciliation already shipped |
| 10 | Source-ABI replay (L4) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | Parity on POI scoping (#11) |
| 11 | Source-graph analysis (L5) | `scan --depth source` | `compare --depth source` (already exists) | MERGE | ADR-037 D6 keeps L5 internal |
| 12 | Changed-path localization (`--since`, `--changed-path`) | `cli_scan.py`, `buildsource/poi.py` | `compare --since` / `--changed-path` | COMPARE-STAGE (per-run input, ADVANCED KEEP) | Phase 2 |
| 13 | Risk-driven evidence selection (`--depth auto`) | `risk.py`, `model/evidence_depth_levels.py` | — | **DELETE** | **Done** (2026-09-09, Phase 4's typed-API slice): ADR-068's second 2026-09-09 amendment rules this (b) -- dropped, not mirrored onto `compare`. An omitted `--depth` resolves to the fixed `headers` rung (`evidence_depth_levels.resolve_unpinned_level`), the same default `compare` always used; a job wanting a deeper rung pins it. The risk score is still computed and *reported*, it just selects nothing. Documented breaking change: an unpinned `scan` that used to escalate on a high-risk seed no longer does, so build/source-only findings need an explicit `--depth` |
| 14 | Risk rule overrides (`--risk-rules`) | `cli_scan_baseline._load_risk_rules` | — | **DELETE** | **Done** (2026-09-09, Phase 4's typed-API slice): ADR-068's second 2026-09-09 amendment rules this (b) -- dropped, not moved to `.abicheck.yml`. It existed to tune the risk-driven escalation row 13 retires; with nothing left to select, a `risk:` config key would configure a decision no longer taken. The score itself is still computed and reported |
| 15 | CPython/`abi3` audit (`--abi3`) | `scan_abi3_resolve.py`, `scan_engine._run_abi3_audit` | `compare` candidate-side enrichment stage | COMPARE-STAGE; floor value is CONFIG (`python.abi3_floor`) | Phase 2 |
| 16 | Artifact-set / multi-library audit (`--artifact-set`) | `service_scan.run_scan_set`, `bundle.py` | `compare --no-baseline DIR` over ADR-065 members | DELETE (mode); capability preserved | ADR-065 S3 component inventories |
| 17 | Set member-identity/provider manifest (`scan --manifest`) | `cli_scan_helpers.load_artifact_set_manifest`; ADR-056; cli-cleanup PR H | `.abicheck.yml` bundle/provider contract, read by the same path | CONFIG | ADR-056 superseded; G42 provider resolution |
| 18 | Analysis completeness/assurance | `analysis_assurance`, `--require-complete-analysis` (both commands) | `compare` (already present) | MERGE | Vision E-S1/S2 landed |
| 19 | Budget guard (`--budget`) | `scan_engine._check_scan_budget`, `_BudgetOverflow`, exit `5` | `compare --budget`, `ExitDecision` operational axis | ADVANCED KEEP; default in CONFIG | **Landed** (2026-09-09, Phase 4 commit 2): `deadline.deadline_scope` around `compare`'s resolve + classify phases, remaining-time-aware; sets `DiffResult.budget_overflow`. Typed API: `CompareRequest.budget_s` |
| 20 | Finding cap (`--max-findings`) | `cli_scan_baseline` summary truncation | — | DELETE | `--write json=` guarantees the full result (ADR-068 D4) |
| 21 | JSON resource budget (`--max-json-object-nodes` on `compare`) | `bundle_facts` decode | execution/storage config, calibrated `resource_limits:` | CONFIG | **Landed** (Phase 7g). Real bytes-per-node calibration against a synthetic oneDAL-scale corpus (`scripts/benchmark_scaling._build_onedal_large_surface`, ~9.3 bytes/node, stable across scale) found the existing `DEFAULT_MAX_JSON_OBJECT_NODES=1_000_000` already ~6x below the single-library, 25k-function oneDAL-scale case's own 5.8M nodes — the CLI flag's own documented "can need well over this" escape hatch confirmed as the common case, not an edge one, for its own named scenario. The CLI flag is gone; `.abicheck.yml`'s `resource_limits.max_bundle_facts_decode_nodes` (int) is the only way to change the budget now, and only an *explicit* `--config` may *raise* it past the default -- an auto-discovered `.abicheck.yml` may still *lower* it (Codex review, PR #1174, second round; `resolve_max_json_object_nodes_cfg()`). The *default* itself is deliberately left unchanged, not recalibrated up to match the measurement (Codex review, PR #1174: raising the ambient default would raise every unconfigured/untrusted run's own decode-bomb ceiling by the same factor) — see `bundle_facts.py`'s own docstring for the full measurement table and this reasoning. Deliberately kept node-based rather than re-expressed as a memory size (the design cli-cleanup-phase-two.md's own now-superseded text proposed) — converting a memory budget to a node budget via this measured *legitimate-payload* ratio would size the node budget for an adversarial payload's much lower bytes/node density too, silently weakening the exact container-count defense `storage.json_budget` exists to provide |
| 22 | Public-header boundary (`--public-header-dir`) | `cli_scan_baseline._public_provenance_set` | `-H` directory provenance + `.abicheck.yml` `scope.public_header_dirs` | MERGE | **Landed** for `compare`'s directory/config sources (`provenance.apply_provenance`, fed from a `-H` directory argument and/or the new `scope.public_header_dirs` config key — distinct from the pre-existing `scope.public` boolean). Directory-vs-file provenance rule preserved verbatim; `scan --public-header-dir` itself is untouched |
| 23 | Per-check severity (`--crosscheck KEY=LEVEL`) | `CrosscheckConfig` | `--policy` / `.abicheck.yml` `policy.overrides` (they are `ChangeKind`s) | MERGE into policy | Each check has a registry entry |
| 24 | Severity / gate / policy / packs | shared decorators | unchanged on `compare` | MERGE | — |
| 25 | Contract evaluation (`--contract`) | shared | unchanged on `compare` | MERGE | — |
| 26 | Suppression display (`--show-suppressed`) | `cli_scan` | disposition ledger, always computed | AUTOMATIC | ADR-067 S1 landed |
| 27 | Not-comparable outcome (exit `6`) | `scan_engine` | `compare` (already has `_EXIT_NOT_COMPARABLE`) | MERGE | — |
| 28 | Evidence-contract abort (exit `7`) | `scan_engine._check_scan_evidence_contract` | `compare` `ExitDecision` axis | COMPARE-STAGE | **Landed** (2026-09-09, Phase 4 commit 2): new `policy/depth_evidence_contract.py`, wired into both the native CLI and the typed pipeline — closes a real, previously-undocumented gap (`compare --depth build` with no evidence silently exited 0) |
| 29 | Coverage/depth reporting lines | `cli_scan_helpers.render_*` | `report/` compute/render pair | INTERNAL | ADR-061 `report/` pair rule |
| 30 | `scan` report schema (`SCAN_SCHEMA_VERSION`) | `service_scan`, `workflows/scan_abort_result.py` | one `ReportDocument` | DELETE | Every consumer migrated (Phase 4) |
| 31 | `ScanRequest` / `ScanResult` typed API | `service_scan.py` | `CompareRequest` / `CompareResult` | DELETE (fields absorbed) | **Done** (2026-09-09, Phase 4's typed-API slice): both types deleted, with `ScanArtifactResult`/`ScanSetResult`/`Budget`/`LayerResult` and `run_scan`/`run_audit`/`run_scan_set`. One field absorbed (`allow_build_query`); the rest were already covered or ruled (b) and dropped. ADR-055's amendment carries the ledger; `SCAN_SCHEMA_VERSION` bumped to 1.31 and stays in the D3 registry while the command ships. See Phase 4's own status section |
| 32 | Action `mode: scan` | `action/run.sh` (~40 branches) | `mode: compare` (+ `baseline-channel: none`) | DELETE after absorption | Phase 4; Action input lifecycle (ADR-047) |
| 33 | `pr-comment` scan projection | `pr_comment_scan.py`, `pr_comment_scan_abort.py` | one PR-comment projection over `ReportDocument` | INTERNAL (merged) | Phase 4 |
| 34 | Depth vocabulary/resolution | `model/evidence_depth_levels.py` (was `buildsource/scan_levels.py`) | keep as engine primitive, renamed off `scan` | INTERNAL | **Done** — Phase 6's rename step landed ahead of the command's own deletion; see Phase 6's own section for the full rename list and the `poi.py`/`risk.py` correction |
| 35 | Cost/dry-run estimation | `frontends/cli/scan_dry_run.py`, `artifact_set_dry_run.py` | `compare --dry-run` (ADR-043 D9 shared model) | MERGE | Phase 2 |
| 36 | `scan`-specific tests (33 modules — corrected from "40"; see Phase 6's own deletion-order checklist for the exact list and the false positives the glob over-counted) | `tests/test_*scan*` | rewritten against `compare`, or deleted with the mode | DELETE last | Phase 6 |

The seven DELETE rows are: a mode that duplicates `compare` (#1), a mode
whose capability is preserved by another spelling (#16), a truncation knob
that contradicts D4 (#20), two internal artifacts — a schema (#30) and a
typed API (#31) — replaced by canonical equivalents, and the two ADR-068's
second 2026-09-09 amendment added: risk-driven evidence selection (#13) and
the `--risk-rules` profile that fed it (#14).

The first five removed nothing a user gets. **The last two do**, which is why
they are called out rather than folded into that claim: an unpinned `scan`
that used to escalate to `build`/`source` on a high-risk seed now stops at
`headers`, so a job relying on that escalation must pin the rung it needs.
The amendment accepted that cost explicitly (ruling (b): dropped, with no
`compare` equivalent coming).

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
| `--keep-extracted` | REMOVE | — (**done**) | Debug detail | — |
| `--no-bundle-analysis` | REMOVE | — (**done**) | "Escape hatch" that disables real analysis; policy/suppression is the supported route | — |
| `--bundle-facts-out` | KEEP (ruled 7d) | unchanged | Per-run operand naming this invocation's evidence-capture output, the same shape as `-o/--output` — `dump` has no directory/package fan-out to hold this instead | — |
| `--bundle-facts-library-manifest` | KEEP (ruled 7d) | unchanged | Document operand, the same class as `--policy`/`--suppress` — no `.abicheck.yml` home exists for its per-library override shape without inventing one (guard 1) | G42 |
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

> **The "Today" column below is the original 2026-09-06 audit, not current
> state.** Phase 7i re-derived both live counts by Click introspection and
> itemized, per flag, every option standing between them and the targets —
> see its own table in §6 Phase 7. Read that, not this, for where the
> surface actually is: as of that slice `compare` was at **50** (from 78 at
> the original audit, 56 at the start of the slice) and `dump` at **21**; live today, after Phase 7n, **44** and **18** (7l measured 47/18 before that merge)
> (from 39 / 24). Both targets remain reachable, and every residual option
> has a named owner and a named blocker; two of them are *deliberate keeps*
> that the targets below predate.

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
`scan`-specific CLI/engine/service/report code (verified: 10,074 lines
across 16 modules, 2026-09-09), one JSON schema, one typed request/result
pair, and `action/run.sh`'s `MODE == "scan"` branches (17 explicit
conditionals verified 2026-09-09 — "~40" over-counted by including every
substring mention of "scan", not just branch points; see Phase 6's own
deletion-order checklist).

---

## 5. Prerequisites

Six cross-cutting blockers. Each gates a whole phase, not one row — the
per-row prerequisites in §3 and §4 are the detail beneath these.

| # | Prerequisite | Gates | State today |
|---|---|---|---|
| P1 | An acquisition state for "OLD declared absent" in ADR-065's vocabulary, with its completeness/outcome consequences | `--no-baseline` (§3 #2), and therefore the whole audit half of the retirement | **Landed** (2026-09-09) — `declared_absent` is a real `AcquisitionState`, carried through `run_outcome`/`comparison_scope` and stated in every audit report's `old_acquisition_state` |
| P2 | An evolution state on the canonical finding model, `not_evaluated` included, carried by `report/`'s compute/render pair | Every one-sided check migration (§3 #3-#8, #15) | **Landed**: a second, deliberately distinct enum from Phase 1 item 2's cross-comparison-chain `FindingEvolution` above — `checker_policy.CrossSourceEvolution` + `Change.cross_source_evolution` state how a cross-source check behaves across OLD/NEW *within one* `compare()` call, with `workflows.cross_source_evolution.compute_cross_source_evolution` as the model's first real producer and `report.cross_source_evolution`'s compute/render pair as its first real JSON projection (schema 3.7) — now covering all **eleven** checks: `unversioned_exported_symbol` and `private_header_leak` landed first (the latter generalized the matching primitive's per-finding identity — a symbol-only key silently collapsed two distinct leaked types flagged on the same function; identity is now a per-check function, defaulting to `symbol` for a check without that ambiguity), `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`, `public_to_internal_dependency` joined next (two more, `rtti_for_internal_type` and `public_to_internal_dependency`, also needed the per-check identity generalization), and `header_build_context_mismatch`, `odr_type_variant`, `identity_collision_detected`, `compile_context_conflict`, and `source_surface_dso_mismatch` close out the row in this PR (`odr_type_variant`, `identity_collision_detected`, and `compile_context_conflict` also needed their own composite identity). All eleven checks are now reachable by every front end: `compare()` runs the whole stage automatically (`cross_source_checks` defaults to `True`), no opt-in flag anywhere (ADR-068 D4/D5). **The correctness crux** — a pre-existing problem must never read as newly introduced when a side's evidence can't confirm it — is exercised as a property test for all eleven checks. **Update (2026-09-09, Phase 4 commit 1, ADR-068 amendment):** the stripping behavior described above was itself the bug — a cross-source finding is `RISK`/`API_BREAK`-severity by design (D3's authority rule), never advisory-only, so `scan --against`'s baseline-compare path no longer strips it back out; `cli_scan_baseline._strip_automatic_cross_source_findings` is deleted, and a baseline `scan` now gates a cross-source finding exactly as `compare` does — same verdict, same severity, same exit-code contribution. `scan`'s own dedicated `crosscheck` report block and `--crosscheck KEY=error` promotion remain a separate, scan-only surface, unaffected. See §3 #3 above for the full account |
| P3 | `compare` emitting the budget-overflow (`5`) and evidence-contract (`7`) exit axes | `scan`'s deletion (they are `scan`-only today; `cli_stack.py`'s own `5` is unrelated) | **Landed** (2026-09-09, Phase 4 commit 2) — `--budget` and the `--depth build`/`source` evidence-contract floor both emit their axis now; see §3 rows #19/#28 |
| P4 | A public/internal boundary derivable from `-H` directory provenance plus `.abicheck.yml` `scope.public_header_dirs` | The leakage and public-vs-exported checks (§3 #4, #5, #22) | **Solved** for both named sources: `-H` directory provenance already fed `provenance.apply_provenance` (unchanged, file-vs-directory asymmetry preserved verbatim — a lone `-H` *file* still does not, on its own, establish a boundary the same way `scan --public-header-dir` requires a directory; note `compare`'s own pre-existing `-H` handling was already slightly looser than that rule before this PR and is left as it was found, see `workflows/cross_source_evolution.py`'s module docstring); this PR adds `.abicheck.yml`'s `scope.public_header_dirs` (a new key, distinct from the pre-existing `scope.public` boolean the plan's own shorthand risked conflating it with) as a second, additive source, threaded through `cli_compare_helpers.run_compare` → `cli_resolve._resolve_compare_snapshots`'s `config_public_header_dirs` parameter into the same `InputSpec.public_header_dirs`/`apply_provenance` machinery. No new CLI flag. `scan --public-header-dir` itself is untouched |
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
   (`report/finding_evolution.py`, `report_schema_version` 3.5). **Follow-up
   landed:** `workflows/history.py`'s `build_longitudinal_history` is now a
   real consumer — `apply_finding_evolution` runs once per adjacent pair
   against the *previous* pair's own `compare()` result in the chain, and
   `PairwiseSummary` gains `evolution_counts`/`resolved` (projected without
   importing `report/`, since `workflows/` may not depend on it — see that
   module's own debt-ledger entry). `project history --format json`'s
   `pairwise[]` entries carry the fields; still no CLI command consumes
   `compare()`'s own single-pair `finding_evolution` block directly, and
   Markdown/HTML rendering of either remains follow-up work.
3. **Evidence-contract abort (exit `7`)** and **budget overflow (exit `5`)**
   become `compare` `ExitDecision` axes (ADR-064's precedence already models
   them; `compare` does not emit them yet).
   *No CLI change in this phase.*

### Phase 2 — Move the enrichments onto `compare`

One PR per capability group, each landing with parity tests going green:

- **2a** cross-source checks (§3 #3–#5), per side, evolution-stated —
  **11 of 11 landed**: `unversioned_exported_symbol` and
  `private_header_leak` landed first (§3 #3-#4), `exported_not_public`,
  `public_not_exported`, `rtti_for_internal_type`, and
  `public_to_internal_dependency` (§3 #3-#5) joined them next, and
  `header_build_context_mismatch`, `odr_type_variant`,
  `identity_collision_detected`, `compile_context_conflict`, and
  `source_surface_dso_mismatch` close out the row in this PR — all eleven
  run automatically inside `compare()`'s pipeline (`checker.compare`'s
  `cross_source_checks`, default `True`, no CLI/API/Action opt-in flag —
  ADR-068 D4/D5), via `workflows/cross_source_evolution.py`'s
  `compute_cross_source_evolution` — deliberately distinct from Phase 1
  item 2's cross-comparison-*chain* `FindingEvolution` above, since this
  axis states a check's OLD-vs-NEW behavior *within one* `compare()` call,
  not across separate calls over time. That module's own per-finding
  identity is a per-check function (not a bare `symbol` key): a check
  whose findings are not uniquely keyed by symbol alone
  (`private_header_leak`, where one function can leak two distinct private
  types; `public_to_internal_dependency`, where one public declaration can
  reach two distinct internal targets; `rtti_for_internal_type`, whose own
  RTTI symbol could in principle resolve to a different private type
  across OLD/NEW; `odr_type_variant`, whose same type name can carry a
  conflict recorded under more than one header; `identity_collision_
  detected`, whose same qualified name can collide onto more than one
  distinct identity key; `compile_context_conflict`, whose same build
  target can carry both a flag-family conflict and a `#define`-value
  conflict at once) registers its own. The second slice (four checks)
  also solved P4 for its two named sources (`-H` directory provenance,
  already wired before that PR, plus a new `.abicheck.yml`
  `scope.public_header_dirs` config key) — see P4's own row above and
  `workflows/cross_source_evolution.py`'s module docstring for the exact
  wiring; the five closing this row needed no further boundary work, since
  each already gated on an L3/L4 fact a `compare()`-produced snapshot can
  carry today. All eleven checks' `tests/parity/gaps.py` rows are deleted
  — the scan-vs-compare parity harness confirms `compare` finds them
  under its ordinary, default invocation. This slice also found and fixed
  a latent regression the automatic stage exposed in `scan --against`'s
  own, older, separate advisory mechanism for these checks: `_run_baseline_
  compare`'s own docstring already documented that single-version
  cross-source findings must stay advisory unless explicitly promoted
  (`--crosscheck KEY=error`), but its internal `compare_snapshots()` call
  started silently re-adding a migrated check's finding into the old/new
  diff a second time as each one landed on the automatic stage — invisible
  for six `RISK`-severity checks (which never raise the legacy exit code
  on their own), and a real, user-visible false-positive exit the moment
  an `API_BREAK`-severity check (`header_build_context_mismatch`) joined
  them. `cli_scan_baseline._strip_automatic_cross_source_findings` now
  strips the automatic stage's findings back out of that one diff and
  recomputes its verdict, for every migrated check, restoring the
  documented invariant;
- **2b** pattern + preprocessor scans (#6, #8) — **landed**: both
  `buildsource/pattern_facts.py`'s lexical pre-scan and
  `buildsource/preprocessor_facts.py`'s preprocessor pre-scan now run
  automatically inside `compare()`'s pipeline (`checker.compare`'s
  `pattern_preprocessor_scan`, default `True`, no CLI/API/Action opt-in
  flag — ADR-068 D4/D5), via `workflows/pattern_preprocessor_scan.py`'s
  `compute_pattern_preprocessor_scan`. Each primitive runs independently on
  OLD and NEW — file/build evidence derived entirely from what each
  snapshot already recorded (declared `source_header` provenance +
  embedded L3 `build_source.build_evidence`, no second collection pass —
  see that module's own docstring) — and folds into the same
  `CrossSourceEvolution` axis 2a's cross-source checks use. Unlike 2a,
  neither primitive ever produced a `ChangeKind`-bearing finding, even
  under `scan` (both are documented "advisory facts... never a verdict on
  their own"), so the migration keeps that shape: a new, always-present
  `pattern_preprocessor_scan` report block (`report_schema_version` 3.12)
  rather than new `ChangeKind`s — read-only, never reaching the verdict,
  severity, or exit code. Both entries are gone from
  `tests/parity/gaps.py`, and `find_pattern_facts`/`collect_preprocessor_facts`
  are no longer tracked in `test_engine_primitive_call_sites.py`'s
  gap-cross-referenced table (neither backs any remaining scan-only row),
  though their exact two-caller sets stay pinned there;
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
- **2f** dry-run/cost preview parity (#35) — **landed**: `compare --dry-run`
  now shows a "Cost preview" section, reusing `service_scan.estimate_scan`
  directly (summed across both operands via
  `workflows/compare_cost_preview.py`) rather than a second cost model —
  the same L0-L5 per-layer projection `scan --dry-run` already shows. Pure
  UX parity, not a `tests/parity/gaps.py` entry (no finding is gained or
  lost): the projection is advisory only and does not touch `--budget`'s
  runtime enforcement (still `scan`-only).

### Phase 3 — Parity is the gate

Phase 0's suite goes fully green: for every scenario, `compare` produces the
same or a strictly richer finding set than `scan`, with no finding lost and
no finding manufactured (the `not_evaluated` cases in F-7/F-8). This phase
lands **no** feature; it lands the proof and the fixtures.

### Phase 4 — Migrate the consumers — commits 1-3 landed (Action deletion done 2026-09-10)

**Status note (2026-09-09):** an earlier revision of this section described
the Action/typed-API bullets below as already landed. They were not: a
parity audit (`tests/parity/test_baseline_gate_parity.py`) found the
Action's scan-to-compare translation had been disabled entirely
(`9f2166e5c`) because of a real baseline cross-source authority divergence
between `scan --against` and `compare` — see ADR-068's 2026-09-09 amendment
for the full account. That divergence is now closed (`scan --against`'s
baseline path no longer strips cross-source findings to advisory-only), and
the Action's translation is re-enabled for every request shape that
divergence used to force onto the legacy CLI. What follows is this
section's *actual* state, not the target it originally described:

- **Action — commit 1 landed; commit 2 (2026-09-09) re-scopes and closes the
  two real (c) gaps.** The maintainer's explicit ruling for commit 2: the
  Action does **not** keep a compatible interface with every `scan`
  capability — each condition `_SCAN_NEEDS_LEGACY_CLI` checked is ruled (a)
  already covered, (b) dropped (a documented breaking change), or (c)
  genuinely required, per ADR-068's second 2026-09-09 amendment (full table
  there; `docs/contribute/known-gaps.md`'s matching entry has the condensed
  version). Two (c) items are closed for real, on `compare` itself, in this
  commit:
  - `--budget` — `compare` gained a `--budget` option
    (`frontends/cli/commands/compare.py`), enforced via
    `deadline.deadline_scope` (remaining-time-aware across both the resolve
    and classify phases), setting `DiffResult.budget_overflow`/exit 5 on
    overflow. Verified live. Typed API: `CompareRequest.budget_s`.
  - `--depth build`/`--depth source`'s evidence-contract floor — a real,
    previously-undocumented `compare` gap (`compare --depth build` with no
    evidence silently degraded to symbols-only and reported `NO_CHANGE`/exit
    0, verified live), now closed via new `policy/depth_evidence_contract.py`
    (mirrors `workflows.abi3_audit.record_abi3_evidence_contract_error`'s
    existing exit-7-axis pattern), wired into both the native CLI and the
    typed pipeline. Verified live: now exits `7`.

  `--risk-rules`, risk-driven `auto` depth selection, `--crosscheck`'s
  `KEY=error` syntax, and `--build-target` are ruled (b) dropped (each with
  its own documented-breaking-change reasoning in the ADR amendment) rather
  than implemented — none crosses the false-negative bar the (c) admission
  test sets. `--artifact-set`/`new-library-set` is also (b), pending ADR-065
  S3 (still "Not started", a different workstream). The header/include
  additive-vs-overriding divergence, JSON-snapshot `dependency_scope`
  tag-matching, `--output-file`/`-o`/`--write`, the compile-context-option
  `extra-args` family, and the pattern-verdicts default divergence are all
  ruled (a) — already covered by `compare` today (the compile-context family
  was demoted off `compare`'s own CLI before this gap was ever written; the
  Action just hadn't caught up). **`_SCAN_NEEDS_LEGACY_CLI` itself is not
  yet deleted in this commit** — this investigation also surfaced a
  separate, real gap in `compare --no-baseline` (the audit-only path:
  `--sources`/`--build-info`/`--depth`/`--dry-run` were not read by
  `frontends/cli/commands/compare_no_baseline.py`, and only `json`/
  `markdown` formats were supported), outside this commit's file ownership,
  that had to close before every `mode: scan` request — audit-only included
  — could safely route to `compare` unconditionally. **That gap closed on
  2026-09-10**: all four options are read, and the supported format set is
  `json`/`markdown`/`sarif`/`junit`/`oneline` (`html`/`review` remain a
  usage error — an audit has no two-sided document for either to render).
  **`_SCAN_NEEDS_LEGACY_CLI`'s own successor predicate
  (`_SCAN_AUDIT_ONLY_NEEDS_LEGACY_CLI`) and every `MODE == "scan"` branch
  that existed only to serve its legacy-CLI fallback are now deleted
  (2026-09-10)** — the mechanical step this paragraph used to describe as
  remaining. `action/run.sh` carries no `CMD+=(scan)` site at all any more:
  every `mode: scan` request (audit-only or baseline) assembles
  `compare`/`compare --no-baseline` through one shared branch, with
  `--severity-preset default` injected on the audit-only shape whenever the
  caller stated no preset of its own (preserving `mode: scan`'s own
  documented default-gating behavior via ADR-068's 2026-09-10 amendment's
  audit-gate exit axis, exit `3`, published as a new `AUDIT_GATE` verdict).
  See the known-gaps entry and `tests/test_action_run_sh_audit_gate.py` for
  the end-to-end coverage.
- **Typed API — landed (2026-09-09, its own PR).** `abicheck/service_scan.py`
  defines no request or result type any more. `ScanRequest`, `ScanResult`,
  `ScanArtifactResult`, `ScanSetResult`, `Budget` and `LayerResult` are
  deleted, along with `run_scan`/`run_audit`/`run_scan_set` and their
  subprocess harnesses (`workflows/scan_subprocess.py` went with them — its
  only callers were those two harnesses). What remains in that module is the
  ADR-035 D10 dry-run cost model: `estimate_scan` now projects one
  `InputSpec` plus the run-scoped level scalars, because a cost preview is a
  projection over an *input*, not over a request. `CompareRequest` ->
  `CompareResult` is the one typed contract, which is also what kept
  `cross_front_end_differences()` honest — with no second API namespace left,
  `workflows/scan_config.SCAN_REQUEST_SPELLINGS` (the per-request-type
  selector-spelling remap two namespaces forced) is deleted too, as is
  `workflows/scan_gate_options.py` (`ScanRequest`'s own gate resolution).
  `SCAN_SCHEMA_VERSION` is bumped to `1.31` and **stays** in ADR-055's D3
  registry: the `scan` command still ships, so its `--format json` envelope
  still has a live version to publish; what left is the typed half, since the
  marker no longer stamps any Python result object. ADR-055's own amendment
  carries the field-by-field ledger.

  Field fates follow ADR-068's second 2026-09-09 ruling table, not a fresh
  judgement. One field was genuinely absorbed — `allow_build_query` becomes
  `CompareRequest.allow_build_query`, forwarded (with the already-present
  `InputSpec.build_config`, which nothing on that path read) into
  `resolve_side_snapshot`, which has accepted the pass-through since PR 3A;
  `False` keeps the standing "never execute a build system as a side effect
  of resolving an input" default, so every pre-existing request is unchanged.
  Everything else was already covered by `compare` or is ruled (b) and
  **deleted rather than carried forward**: `--risk-rules` and the risk-driven
  `auto` depth escalation it fed (an omitted `--depth` resolves to the fixed
  `headers` rung the amendment names — `model.evidence_depth_levels.
  resolve_unpinned_level` — deliberately *not* the `--mode` preset, which is
  `(S5, SOURCE)` and would run a full source replay on every unpinned scan;
  Codex review, PR #1186, caught the first attempt doing exactly that),
  `scan --build-target`, and
  `--artifact-set`/`new-library-set` (the mode — §3 #16's capability is
  preserved and returns as `compare --no-baseline DIR` once ADR-065 S3's
  component inventories land; `bundle.py`'s audit primitives stay put). The
  Action absorbs all three per the ADR's own "Consequence for
  `action/run.sh`" paragraph: each is rejected with an explicit `::error::`
  naming the blocker or replacement, never silently downgraded.

  **One (b) item deliberately not completed here.** The ruling table also
  retires the `--crosscheck KEY=error` promotion *syntax*. `ScanRequest.
  severities` — the part this slice owns — is gone with the request type, but
  the `scan` CLI flag and its engine half are left standing: that half is
  ADR-064's `crosscheck_promotion_contribution`, a published `ExitDecision`
  axis with its own `exit`-block key, precedence rule
  (`policy/exit_decision_precedence.py`), abort-envelope participant and
  `REPORT_SCHEMA_VERSION` 2.42 entry. Removing a published exit axis is an
  ADR-064 amendment that changes `compare`'s report schema too, not a
  typed-API slice. Tracked in
  [known gaps](../known-gaps.md).
- **Docs — landed.** `docs/use/scan-levels.md` is renamed
  `docs/use/evidence-depth.md` (still the evidence trio's third role, no
  fourth page added) and reworked to lead with `compare`/`dump`, naming each
  surviving `scan`-only capability explicitly rather than implying `compare`
  covers it; `docs/start/choose-your-workflow.md`,
  `docs/integration/scenarios/single-build-audit.md`,
  `docs/start/scanning-conda-packages.md`,
  `docs/use/github-action-source-scans.md` (which gains the full
  `_SCAN_NEEDS_LEGACY_CLI` routing table), `docs/reference/exit-codes.md`
  (`compare`'s real exit-`7` rows plus an ADR-068 D8/D9 hard-removal warning
  on the `scan` section), the nine G20 catalog case READMEs, and
  `skills-src/` all follow. Two verified-live corrections worth not
  re-deriving: `compare`/`dump` accept **no** compile-context flags at all
  (that axis is `.abicheck.yml`'s `compile:` block, D5), and
  `scanning-conda-packages.md`'s worked command had been unrunnable
  (`scan --binary`, `--audit`).
- **Examples/eval/validation — partially landed.**
  `eval/scan_level_scaling.py` is re-driven onto `compare` (it had been
  exiting 64 on every rung: `--binary`/`--baseline`/`--baseline-header`/
  `--source-method` are all removed spellings) and its dead `graph` rung is
  dropped. `tests/scenarios/ci_gating.yaml`'s SC-SCAN-BINARY-DEPTH-MATRIX-ARGS
  `flow:` line is corrected to a command that parses.
  `examples/workflows/audit-release` and the G20 audit cases were annotated
  as blocked rather than dropped from the coverage denominator at the time;
  both moved to `compare --no-baseline` on 2026-09-09/10 once the audit path
  reproduced `scan`'s findings; `validation/scripts/run_oneapi_scan.py` stays on `scan` for
  the network-history reason its own docstring records.
- **Blocker discovered while doing the above — now closed (2026-09-09).**
  `compare --no-baseline` did not reproduce `scan`'s audit-mode findings at
  all: it aborted on `workflows/no_baseline_compare.py`'s
  `assert not diff.changes` for a stored snapshot candidate (all eleven G20
  fixtures) and rendered an empty `changes` list for a live binary. The
  cause was structural — the audit is a self-diff that asserted the diff is
  empty, an invariant the per-side cross-source stages Phase 2a/2b moved
  *into* `compare()` legitimately violate.

  **Fixed**, along with two adjacent gaps the same audit of that path
  surfaced (`--contract` accepted but never forwarded; `--sources`/
  `--build-info`/`--depth`/`--dry-run` parsed but never read). All three are
  written up in full, with live before/after evidence, in
  `docs/contribute/known-gaps.md`. In short:

  - `abicheck/policy/no_baseline_findings.py` (new) partitions the
    self-diff's change set into the comparison half (still provably empty,
    still enforced — as a *raised* error now, so the guard survives
    `python -O`) and the candidate-side half (the audit's reportable
    content), and enforces D3's rule that a `declared_absent` OLD may only
    ever yield `persistent`/`not_evaluated`.
  - A second root cause the original write-up did not name: this path
    passed `-H`/`--header` as parse input only, never as public-header
    *provenance*, so every declaration stayed `ScopeOrigin.UNKNOWN` and the
    four boundary-dependent checks evidence-gated to `NOT_EVALUATED`.
  - `report/no_baseline.py` gained the compute/render split this package's
    `AGENTS.md` requires, a `findings[]` block, and `sarif`/`junit`/
    `oneline` renderings. `html`/`review` stay a declared usage error, by
    ruling rather than deferral: both render a *comparison* (verdict badge,
    OLD → NEW counts, release recommendation) and an audit has none of
    those. The reasoning lives with the code, in
    `report.no_baseline.NO_BASELINE_UNSUPPORTED_FORMATS`.

  **§3 row 2 is closed.** `tests/parity/test_no_baseline_audit_corpus_parity.py`
  is the gate over all eleven G20 audit fixtures (twelve runs — `case151`
  contributes its `thin.abi.json` variant). The statement it makes is
  *directional*, not "the two outputs are equal": `compare --no-baseline`
  reports **at least** every kind `scan` reports (no capability loss), and
  every finding it adds beyond that is a real candidate-side enrichment in a
  permitted ADR-068 D3 state (nothing manufactured). Those are asserted as
  two separate statements, per fixture and once corpus-wide. `tests/test_no_baseline_d3_properties.py`
  states D3 as a property over generated candidates rather than eleven fixed
  cases, per `AGENTS.md`'s bug-class rule. The nine G20 case READMEs and
  `examples/workflows/audit-release` are re-driven onto
  `compare --no-baseline` and their "blocked on this gap" annotations
  removed.

  **The exit-code gating gap is also closed (2026-09-10 ADR-068
  amendment).** `docs/contribute/known-gaps.md`'s "no way to gate a CI job
  on an audit finding" entry — legacy `scan`'s audit mode gates at exit `2`
  on an `API_BREAK`-classified hygiene finding, which `compare
  --no-baseline` could not reproduce without violating D2 — is closed by a
  new orthogonal audit-gate axis (`policy/audit_gate_exit.py`, exit code
  `3`, opt-in via `--severity-preset`). This closes the one named blocker
  that gap put on this phase's retirement work; it does **not** by itself
  retire `scan` or move any deletion-order item below forward — the typed
  API still carries no `--no-baseline` support and the Action still routes
  audit-only `mode: scan` to the legacy CLI (a separate, already-tracked
  gap), both of which remain open prerequisites of their own before Phase 6
  can proceed past what is recorded here.

### Phase 5 — Presentation/analysis separation — **done**

- `--explain-patterns` stops implying `--pattern-verdicts`; modulation
  becomes automatic and explanation becomes rendering. **Done.**
- `--report-mode`/`--show-only`/`--demangle`/`--explain-patterns` collapse
  into `--view`. **Done.**
- `--show-filtered`/`--show-suppressed`/`--audit-suppressions`/
  `--surface-metrics` become always-computed, rendered on request.
  **Done — closing slice.** All three of `compare`'s own flags here are now
  *removed*, not merely inert:
  - **`--surface-metrics`** — removed outright, no replacement selector.
    ADR-027's metric-drift findings (`public_surface_grew`/`_shrank`,
    `undocumented_export_ratio_increased`) are ordinary `Change` entries in
    `result.changes`, which every projection already renders, so there was
    nothing left for the flag to select — not even a rendering choice. It had
    already been reduced to an accepted no-op by an earlier slice; D5 counts
    an accepted-but-ignored spelling as public surface, so it is gone.
    `surface_metrics` is no longer a *parameter* of the Tier-2 verb either
    (`workflows/compare_policy.compare_snapshots` forces it on for every
    caller) — a keyword the documented public Python API accepts and then
    ignores is the same dead surface, one layer down.
  - **`--show-filtered` → `--view filtered`.** The scope/disposition ledger
    has been unconditional since ADR-067 S1 and is always present in
    `--format json`; the flag only ever chose whether the markdown/text
    render echoed it. That is precisely what `--view` is for.
  - **`--audit-suppressions` → `--view suppressions`.** The audit is computed
    on every run given `--suppress` (`cli_compare_helpers._attach_suppression_
    audit`, guarded only on `suppression is not None`) and carried in
    `--format json`/SARIF/JUnit/HTML unconditionally; the flag only gated the
    markdown/text/review section. `--view suppressions` inherits its
    no-op-without-`--suppress` rule verbatim: a rendering selector never
    rejects an otherwise-valid invocation. The one release-fan-out rejection
    that remains (`--view suppressions` *with* `--suppress` on a
    directory/package operand) is unchanged in substance — the per-library
    fan-out still has no single audit result to attach — and now names the
    `--view` spelling, matching `reject_release_incompatible_view_mode`.

  ADR-068 D4 is satisfied in the strong form: **no surviving `compare` flag
  decides whether a piece of the canonical result is computed.** What the
  three retired flags gated is now either always rendered (metrics) or
  selected through the one rendering mechanism (`--view`).
- `--write` becomes repeatable. **Done.**
- **Executable invariant test:** the canonical result block is byte-identical
  across every rendering permutation (numbered F-16 here and F-19 in §7's
  acceptance table — the same requirement; the two numbers are a
  long-standing inconsistency in this document, recorded rather than
  silently renumbered so a reader following either reference lands on this
  paragraph).

  **Verification finding (2026-09-09), stated explicitly because it was a
  deliverable of its own:** the test that existed
  (`tests/test_presentation_analysis_separation.py`) was **not** an
  invariant test, and had to be generalized. Two independent defects:

  1. *It sampled, it did not cross the axes.* The exit-code half enumerated
     6 formats × 2 demangle states × 2 audit flags × 2 pattern flags × 4
     report modes = 192 invocations but asserted **only the exit code**; the
     result-block half asserted the canonical fields over just 8 hand-listed
     combinations, all pinned at `--format json`. `--write` was not an axis
     at all (one separate two-write example), `--view show=` was not an axis,
     and format was never crossed with view for *content*.
  2. *Its oracle was the code under test.* `_canonical_facts` projected the
     JSON report the implementation had just rendered and compared it against
     another rendering of the same report. That can only prove the renderings
     agree with each other — a change moving every projection in the same
     wrong direction passes.

  Both are fixed in
  `TestF19CanonicalResultIsInvariantOverTheWholeRenderingSpace`:

  - **The space is the full product**, enumerated by `_permutations()`:
    7 `--format` values × 4 `--view` report modes × 3 demangle states
    (unset/`demangle`/`no-demangle`) × 2 `--view patterns` × 2
    `--view filtered` × 2 `--view suppressions` × 3 `--view show=` states ×
    3 extra `--write` artifact sets = **6048 invocations**. The `slow`-marked
    test runs every one; the default-lane test runs a *seeded random sample*
    (150 points, `ABICHECK_F19_SEED` to re-seed for a soak run) of that same
    space rather than a hand-picked list, so the fast suite searches the
    space instead of re-checking one corner of it. A third test pins the
    space's own size against the product of the axis tuples, so a silently
    shrunk space — exactly the failure mode the sampled predecessor had —
    fails rather than passing with less coverage.
  - **The oracle is not the report projection.** It is computed by calling
    the Tier-2 engine verb (`workflows.compare_policy.compare_snapshots`)
    directly on the two snapshots and reading verdict, finding identities,
    finding count, suppressed/out-of-surface counts and assurance status off
    the returned `DiffResult` — a different code path from
    `report/render_json.py`, which every CLI invocation goes through. Each
    case extracts its canonical block via its own `--write json=` (itself one
    of the axes, and documented to always render the full unfiltered report),
    so a `--format` whose primary output is unstructured (html/junit/oneline)
    is covered on equal terms.

### Phase 6 — Remove `scan`

**Status: complete (2026-09-11).** The root `scan` command is deleted
(`abicheck scan` exits `64`, naming `compare`/`compare --no-baseline`), all
16 scan-only `abicheck/` modules and both `eval`/`validation` scripts named
in list A below are deleted, `service_scan.py` was split into
`dry_run_estimate.py` (the surviving dry-run cost model, already the state
of that file when this phase started — its own scan half had already been
retired in an earlier PR), `buildsource/poi.py`/`risk.py` are deleted per
the call-site audit's own reclassification, `SCAN_SCHEMA_VERSION` and its
`schemas.py`/`check_report.py` call sites are gone, and `tests/parity/` is
rewritten as a compare-only regression corpus (its `scan`-invoking harness
half -- `scan_json`/`scan_finding_set`/`scan_outcome`/`compare_outcome`/
`assert_full_parity` in `runner.py`, and every test that called them --
deleted; `RunOutcome`/`_outcome_from_findings` kept as a tested,
CLI-independent primitive). Real drift found against this section's own
pre-staged lists, beyond what the checklist below already recorded:
`workflows/scan_gate_options.py`/`workflows/scan_subprocess.py` (named in
list A) no longer existed on disk (already removed by an earlier PR); eight
more test files imported scan-only internals without "scan" in their own
filename (`test_reclassify.py`, `test_exit_decision.py`,
`test_compile_context_parity.py`/`test_compile_context_security.py`
(initially miscategorized as scan-only during this phase -- both actually
exercise the still-live, shared `cli_options.merge_compile_config`, reached
through `cli_scan.py`'s own `_merge_compile_config = merge_compile_config`
re-export; both files were restored and only their genuinely-dead
`scan_cmd`-referencing tests removed, not gutted), `test_gated_build_query_
inputs.py`, `test_docs_cli_flags.py`, `test_project_snapshot_v2_wiring.py`,
`test_depth_vocabulary.py`, `test_explicit_source_extractor_propagation.py`,
`test_schemas_registry.py`, `test_providers.py`) and five more under
`tests/parity/` (`test_evolution_state_gap.py`,
`test_no_baseline_audit_corpus_parity.py`, `test_no_baseline_parity.py`,
`test_source_depth_parity.py`, and the wholly-deleted
`test_baseline_gate_parity.py`, whose entire purpose -- pinning a scan/
compare divergence pending a fix -- no longer has a second tool to diverge
from). Three flag demotions this phase unblocked
(`compare --env-matrix`, `compare --require-complete-analysis`,
`dump --build-target`) were **not** implemented in this PR -- each needed
real, unimplemented feature work (a new `deployment:` config key,
`assurance.require_complete` resolver wiring plus Action-input retirement,
and rewiring `dump --build-target`'s own callers onto `build.targets`
respectively) beyond a ruling-table edit; `frontends/cli/options/
rulings.py`'s own entries recorded this explicitly as a tracked followup,
not a silent gap. `compare --require-complete-analysis` has since landed
(`assurance.require_complete` in `.abicheck.yml`, plus the Action-input
retirement, rulings.py deferred-option followup); the other two remain
open.

The command, `cli_scan*.py`, `scan_engine.py`, `service_scan.py`,
`workflows/scan_*.py`, `frontends/cli/scan_*.py`, `pr_comment_scan*.py`,
`SCAN_SCHEMA_VERSION`, the Action's `scan` mode branches, and the `scan`
test modules — deleted in that order, only after Phases 3 and 4 have
proven no caller remains. **Step 4 of this phase — the rename below — has
landed** (pulled forward ahead of the deletion itself, since it can land
independently and the deletion's own semantics blocker, Phase 3/4, is still
open): `buildsource/crosscheck.py`, `crosscheck_base.py`, and
`crosscheck_coherence.py` are renamed to `cross_source_checks.py`/
`cross_source_checks_base.py`/`cross_source_checks_coherence.py`
(stay physically in `buildsource/`, since the main file's own dependencies —
`export_accounting.py` (`extract`-classified), `source_graph_query.py`
(unclassified) — forbid a physical move into `abicheck/compare/` under that
package's `may_import: [model]` restriction without also reclassifying or
moving those two modules first, out of scope for a pure rename);
`buildsource/pattern_scan.py`/`preprocessor_scan.py` are renamed to
`pattern_facts.py`/`preprocessor_facts.py` (stay in `buildsource/` — reading
build/source evidence is what that legacy package already owns); and
`buildsource/scan_levels.py` physically moved to
`abicheck/model/evidence_depth_levels.py` (a value-type vocabulary with zero
buildsource-specific dependency, consumed at least as widely by `compare`'s
own evidence-depth resolution as by `scan`). **Correction to this section's
own prior text:** `poi.py` and `risk.py` were also named here as surviving
engine primitives, but a call-site audit (AST scan, not grep — see
`tests/parity/test_engine_primitive_call_sites.py`) found neither has a real
`compare`-pipeline caller today; every production caller of both is
scan-only (`scan_engine.py`, plus `service_scan.py`/`cli_scan*.py`/
`workflows/scan_config.py` for `risk.py`). They are **not** renamed in this
slice — see the deletion-order checklist below, which reclassifies them from
"rename" to "delete with `scan`" pending a genuine `compare`-side caller.
`tests/test_cli_root_surface.py` is updated to the six-verb set in the same
commit as the registration removal (ADR-043 D12 / ADR-054 #6) — that removal
itself has not landed yet.

No deprecated alias is kept. `abicheck scan` exits `64` with `No such
command`, with an error message naming `compare --no-baseline`.

#### Deletion-order checklist (pre-staged; nothing below is deleted by this PR)

Verified against `main` at `3930cfce` on 2026-09-09 by direct file
enumeration (`find`/`grep`, not the plan's own prior prose), cross-checked
against this section's own historical claims:

- The prior "~10,000 lines... 40 test modules... ~40 `action/run.sh`
  branches" figures (§1 item 2, §4.5) are **not fully reproducible** today.
  The line-count figure holds almost exactly (10,074 lines across the 16
  scan-only `abicheck/` modules below). The test-module count and the
  `run.sh` branch count do not: `tests/test_*scan*.py` matches 43 files, of
  which 33 are scan-only (7 fewer than "40" — the glob also catches renamed
  primitive tests, a mixed file, and unrelated matches; see below) — and
  `action/run.sh` has 17 explicit `mode == "scan"` conditionals, not ~40 (286
  total substring mentions of "scan", which is what the original count likely
  measured). Use the lists below as the checklist, not the historical prose.

**A. Delete — scan-only, no other purpose (16 files, 10,074 lines under
`abicheck/`):**

`cli_scan.py`, `cli_scan_baseline.py`, `cli_scan_helpers.py`,
`cli_scan_receipt.py`, `scan_engine.py`, `service_scan.py`,
`scan_abi3_resolve.py`, `pr_comment_scan.py`, `pr_comment_scan_abort.py`,
`frontends/cli/scan_against.py`, `frontends/cli/scan_dry_run.py`,
`workflows/scan_abi3_dry_run.py`, `workflows/scan_abort_result.py`,
`workflows/scan_config.py`, `workflows/scan_gate_options.py`,
`workflows/scan_subprocess.py`. Plus, outside `abicheck/`:
`eval/scan_level_scaling.py`, `validation/scripts/run_oneapi_scan.py`.

**B. Delete — scan-only tests (33 files, 21,111 lines):**

`tests/test_cli_scan.py`, `test_cli_scan_abort_report.py`,
`test_cli_scan_baseline.py`, `test_cli_scan_helpers_coverage.py`,
`test_cli_scan_receipt_unit.py`, `test_pr494_scan_regressions.py`,
`test_pr_comment_scan.py`, `test_pr_comment_scan_abort.py`,
`test_service_scan_coverage.py`, `test_scan_abort_result.py`,
`test_scan_artifact_set.py`, `test_scan_artifact_set_coverage.py`,
`test_scan_artifact_set_manifest.py`, `test_scan_baseline_finding_projection.py`,
`test_scan_baseline_headers.py`, `test_scan_compare_parity.py`,
`test_scan_dry_run_abi3.py`, `test_scan_estimate.py`,
`test_scan_l2_cleanup_ordering.py`, `test_scan_writers_run_outcome.py`,
`test_scan_adr039_build_context.py`, `test_scan_analysis_assurance.py`,
`test_scan_same_binary_coverage_warning.py`, `test_scan_levels_integration.py`,
`test_dump_scan_l3_comparability.py`, `test_bazel_root_targets_scan.py`,
`test_perf_binary_scan.py`, `test_action_run_sh_scan_evidence_contract_error.py`,
`test_action_run_sh_scan_not_comparable.py`, `test_action_run_sh_scan_pr_comment.py`,
`test_action_run_sh_scan_pr_json_write.py`, `test_action_run_sh_scan_summary.py`,
`test_action_run_sh_public_header_dir_scan_scope.py`.

**C. Rename, not delete — already done in this PR:**
`buildsource/cross_source_checks.py`/`cross_source_checks_base.py`/
`cross_source_checks_coherence.py`, `buildsource/pattern_facts.py`,
`buildsource/preprocessor_facts.py`, `model/evidence_depth_levels.py`, and
their test siblings `tests/test_cross_source_checks.py`,
`tests/test_pattern_facts.py`, `tests/test_evidence_depth_levels.py`,
`tests/parity/test_cross_source_checks_parity.py`,
`tests/parity/test_pattern_facts_behavior.py`,
`tests/parity/test_preprocessor_facts_behavior.py`,
`tests/test_pattern_preprocessor_scan_coverage.py` (tests
`workflows/pattern_preprocessor_scan.py`, a `compare`-side survivor, not
scan-only), and `tests/_scan_fixtures.py` (a non-`test_` shared-fixture
helper over `cross_source_checks`/`poi`, survives regardless of `poi.py`'s
own fate below since `cross_source_checks` alone keeps it alive).

**D. Reclassified by this PR's own audit — was "rename" in this section's
prior text, corrected to "delete with `scan` unless a `compare`-side caller
appears first":** `buildsource/poi.py`, `buildsource/risk.py`, and their
tests `tests/test_poi.py`, `tests/test_poi_scenarios.py`,
`tests/test_risk.py` (all scan-only per the same call-site audit —
`tests/test_providers.py` also imports both but tests the still-`extract`-
classified `providers.py` contract, not `poi`/`risk` themselves, so it stays
regardless of their fate).

**E. Keep for another reason — not deleted, not scan's:**
`workflows/pattern_preprocessor_scan.py`, `report/pattern_preprocessor_scan.py`
(the `compare`-pipeline survivors §3 #6/#8 already produced — distinct from
the buildsource primitives they call), `docs/use/evidence-depth.md` (renamed
off `scan` in Phase 4's docs slice; it is the `--depth` dial's page, not
`scan`'s), `docs/use/github-action-source-scans.md`,
`docs/reference/exit-codes.md`
(document the still-live `scan` command's real contract; rewritten only once
`scan` is actually gone), the example catalog rows and fixture directories
under `catalog/cases/case14x-151`/`case181` (single-build-audit examples —
`scan`'s one case `compare --no-baseline` doesn't cover yet), `docs/start/
scanning-conda-packages.md` (names a still-current CLI command), the
`skills-src/` skill body's own `scan libfoo.so` (no `--against`) mention,
and every `changelog.d/*scan*.md` fragment (historical record).

**Mixed — needs splitting before deletion, not a clean row either way:**
`tests/test_preprocessor_scan.py` imports both the survivor
`buildsource.preprocessor_facts` and the scan-only `cli_scan`/`scan_engine`/
`service_scan` — split its scan-only cases into a scan-only file before
Phase 6's deletion pass, or the survivor's own coverage goes with it.

**False positives in the naming sweep (kept, unrelated to `scan` mode):**
`tests/test_scan_accuracy.py` (property/mutation tests over
`checker.compare`, never touches `scan`), `tests/test_realworld_scan.py`
(a local helper happens to be named `_scan()`), `tests/
test_header_scan_deadline_integration.py` (L2 header-*scan* deadline, an
unrelated extraction-path meaning of "scan"), `tests/scenarios/
compliance_scanning.yaml` (persona prose, no `mode: scan`).

### Phase 7 — CLI/config cleanup

**7a/7b/7c: done.** 7a deleted `compare`'s 4 hidden debug-resolution flags
(`--dwarf-only`/`--debug-format`/`--debuginfod`/`--debuginfod-url`) outright,
each already having a `debug.*` config-key twin. 7b removed the whole L2
compile-context family (`--ast-frontend`, `--allow-ast-frontend-fallback`,
`--allow-unsupported-castxml`, `--compiler`, `--compiler-prefix`,
`--compiler-option`, `--sysroot`, `--nostdinc`, `--frontend-context`,
`--lang`) from both `compare` and `dump` as one unit (ADR-037 D8.1), merging
`--compiler`/`--compiler-prefix` into one `compile.compiler` key. 7c applied
7a's identical treatment to `dump`'s own `--dwarf-only`/`--debug-format`/
`--debuginfod`/`--debuginfod-url`/`--pdb-path` (`--debug-format` had been a
*visible*, not hidden, flag on `dump` before this phase — removed anyway for
front-end parity with `compare`, since ADR-037 D8.1 requires the two commands
not to drift). `scan` is unaffected by any of 7a/7b/7c — it keeps every one
of these flags as a real CLI option.

**7d: the release-topology demotion (`--on-incomplete-scope`/
`--fail-on-removed-library`/`--no-fail-on-removed-library`/`--dso-only`/
`--include-private-dso` → `scope.on_incomplete`/`gate.
fail_on_removed_library`/`release.dso_only`/`release.include_private_dso`)
landed in `1e9d59698`.** Four flags from §4.1's table survived that PR
without a ruling either way — each is now decided explicitly, under D5's
three guards, rather than left "pending":

- `--keep-extracted` and `--no-bundle-analysis` are **removed outright, no
  config replacement.** Neither survives D5: `--keep-extracted` is a
  local-debug retention knob with no per-run evidence content and no
  stable-property home either (guard 2 — it doesn't disable a decision, it
  just leaves a tempdir on disk, so there is nothing to re-express in
  config); `--no-bundle-analysis` is exactly the "escape hatch that
  disables real analysis" D4/D5 rule out — a bundle finding a user wants
  gone is a suppression-policy decision, not a flag that silently drops a
  whole analysis stage. Extraction cleanup is now unconditional; bundle
  analysis always runs.
- `--bundle-facts-out` **stays a CLI flag, ruled as a per-run operand.**
  `dump` has no directory/package fan-out at all today — no `dump`
  capability produces a multi-library `BundleFacts` document — so this is
  not a duplicate spelling of a `dump` capability to collapse (§4.1's
  original REMOVE classification assumed one existed). Under D5's own
  test it is exactly `-o/--output`'s shape: PATH names where *this
  invocation's* evidence capture lands, which varies by run/CI job and has
  no config vocabulary to merge into without inventing one purely to move
  a path string. Revisit only if `dump` grows a real release fan-out.
- `--bundle-facts-library-manifest` **stays a CLI flag, ruled as a
  document operand pending G42.** Its per-library header/include/
  compile-context override shape has no existing `.abicheck.yml` home —
  `bundle: {system_providers, cohorts}` is an unrelated concept — and
  inventing one now would be exactly the ad hoc config plumbing this
  workstream warns against, not the kind of migration D5 asks for (guard
  1: "not one-for-one"). It is the same class as `--policy`/`--suppress`:
  a CLI flag naming a document, which stays CLI even though the document
  itself is a stable project artifact. Revisit when G42 (named deployment
  environments and provider resolution) lands.

**7g: done.** `--max-json-object-nodes` is gone from `compare`'s CLI,
replaced by `resource_limits.max_bundle_facts_decode_nodes` in an
explicitly-supplied `.abicheck.yml` (`--config`) for *raising* the budget
past the default; an auto-discovered `.abicheck.yml` may still *lower* it
(Codex review, PR #1174, second round) (a new `INT_SUBKEYS`-typed config
key, `buildsource/build_config_schema.py`).
Measured a real bytes-per-node density first, per this phase's own "do not
pick a number and call it calibrated" bar — this repo's own fixture corpus
tops out at ~10 KB, nowhere near the "large, template-heavy SYCL/DPC++
library" scenario the flag's own help text named, so the corpus is
synthetic, sized like the actual named use case
(`scripts/benchmark_scaling._build_onedal_large_surface`, modeling oneDAL's
~20k-25k-function public header surface): serialize → real
`bundle_facts_to_dict`/`json.dumps` → real container/scalar-token count via
`storage.json_budget`'s own token scanner, at 25k/50k functions × 1/3
libraries — **~9.3 bytes/node, stable across scale**. The existing
`DEFAULT_MAX_JSON_OBJECT_NODES=1_000_000` (~9.3 MB) sits ~6x below the
single-library, 25k-function case's own 5.8M nodes — confirming the
documented "can legitimately need well over this" escape hatch is the
common case for its own stated scenario, not an edge one, exactly as the
flag's help text already said.

**The default itself is deliberately left unchanged (Codex review, PR
#1174, fresh evidence)** — an earlier draft of this row recalibrated
`DEFAULT_MAX_JSON_OBJECT_NODES` up to `20_000_000` to cover that measured
gap directly, which review correctly flagged as a real security
regression: this constant also bounds decode of an unconfigured,
potentially-untrusted `BundleFacts` blob, and raising it 20x raises that
same blob's worst-case decode RSS by 20x (~75 MB → ~1.5 GB, per the
constant's own "~150 MB RSS from a 6 MB payload of ~2M empty objects"
measurement) for every caller, not just the ones who actually have a
large, trusted payload. The calibration stands as a real, useful
measurement — it is what motivated the config key existing at all — but
the fix it justifies is the escape hatch, not a raised ambient default: a
project with oneDAL-scale bundle facts sets
`resource_limits.max_bundle_facts_decode_nodes` explicitly in its own
`.abicheck.yml`, and every other caller keeps the conservative default.

Deliberately node-based, not re-expressed as a memory size, the way
cli-cleanup-phase-two.md's own now-superseded text proposed (`resources:
max_decoded_memory: 2GiB`): converting a memory budget to a node budget
via this measured *legitimate*-payload ratio would size the node budget
for an adversarial payload's much lower bytes/node density too (a payload
of millions of tiny scalar tokens runs under 2 bytes/node) — exactly the
shape `storage.json_budget`'s own pre-`json.loads()` container-count scan
exists to catch, and the same reasoning the default-value regression
above turned out to need anyway. A memory-labelled dial that silently
admits far more real allocations than its own number implies would be
worse than no memory framing at all, so the unit stays the one the
underlying check already uses. Full measurement table and reasoning:
`bundle_facts.py`'s own `DEFAULT_MAX_JSON_OBJECT_NODES` docstring.

**7f: done.** `dump`'s `--git-tag`/`--build-id`/`--no-git` are collapsed into
one repeatable `--provenance KEY=VALUE` (§4.2's MERGE row: "three spellings of
'stamp this snapshot'"). Keys are `git-tag=<tag>`, `build-id=<id>` and
`git=auto`/`git=off` (the latter being `--no-git`); a repeated key is
last-one-wins, matching `--view`'s own report-mode and demangle tokens rather
than inventing a second collision rule. Grammar and parsing live in
`abicheck/frontends/cli/options/provenance.py` — a pure-parsing leaf, the
identical shape `options/view.py` has for `compare --view`, validated eagerly
by a Click callback (`frontends/cli/runtime._validate_provenance`) so a
malformed token is a usage error before any extraction runs. Nothing is
silently dropped: an unknown key, a missing `=`, an empty value and a bad
`git=` value are each exit 64. No Action or typed-API change was needed —
`action/run.sh` never passed any of the three, and neither `DumpRequest` nor
`service_dump_pipeline` carries provenance (that layer is deliberately
excluded from the shared pipeline, see its own module docstring).

**7i: the per-flag audit against D5's three guards — every surviving
`compare` and `dump` option, ruled.** 7d's four-flag ruling is the model:
each flag below is decided explicitly, including the ones that stay, and a
flag that stays names *why* it survives all three guards (a genuine per-run
operand; not a one-for-one duplicate; not an escape hatch that disables real
analysis). Counts are Click-introspected against this branch, not read off
§4.5's own prose.

**Removed by this slice (beyond Phase 5's three and 7f's merge):**

- **`compare --reconcile-build-context` → AUTO, removed.** §4.1 already
  classified it AUTO ("Clearing false positives should never be opt-in"), and
  the measurement backs it: ADR-039's reconciliation is *strictly*
  evidence-gated — a no-op unless both snapshots carry
  `build_context_defines` and per-field guards — and it can only ever move a
  phantom, context-free header-parse finding out of the verdict into the
  audit bucket. It can never manufacture a finding, so an opt-in switch could
  only ever mean "leave a known false positive in your verdict because you
  forgot a flag". Forced on inside the Tier-2 chokepoint
  (`compare_snapshots`) rather than at each front end, so CLI, typed API and
  Action are changed by one edit; `CompareRequest.reconcile_build_context` is
  removed with it (front-end parity — the typed API must not keep a knob the
  CLI no longer has). Unlike `--pattern-verdicts`, no ADR gates this default:
  ADR-039 shipped the reconciliation and never deferred its default. The
  directory/package fan-out's old *rejection* of the flag is gone too — it
  now simply gets the behavior it used to refuse a request for.
- **`compare --pdb-path` → CONFIG `debug.pdb_path`.** §4.1's CONFIG row, and
  the config key already existed: `dump --pdb-path` was demoted to it in
  Phase 7c, and ADR-037 D8.1 forbids the two commands' debug context from
  drifting. Documented capability reduction, stated rather than glossed: the
  flag was side-scoped (`old=`/`new=`) and the config key is not. That case
  is preserved by a different route rather than lost — `--debug-root
  old=…`/`new=…` stays, and `debug_resolver` already searches a debug root
  for a PDB (`pdb_in_root`), which is the per-side spelling now.
- **`compare --support-promise` → CONFIG `release.support_promise`.** The
  flag's own help text called it "a contract-policy field" (ADR-065 D1/D6) —
  which is guard 1's definition of a stable project property, written down
  by the flag itself. A project's declared support promise does not change
  between two runs of the same gate. New key, scalar shape (`off`/`declared`),
  resolved onto `ResolvedCompareConfig` beside Phase 7d's own
  `release.dso_only`/`release.include_private_dso` siblings. The unregistered
  release engine keeps its internal parameter, fed from the resolved config.
- **`dump --compile-db-filter` → CONFIG `build.compile_db_filter`.** §4.2's
  CONFIG row. Which subtree of a large shared `compile_commands.json` belongs
  to *this* library is a property of the project's layout, and the key sits
  next to the `build.compile_db` it scopes. No Action input existed for it, so
  there is no front-end lifecycle question.

**Kept, with the ruling that keeps them.** Grouped by why, rather than
restated one line at a time where the reason is shared:

- **Measured keep — the dependency-walk family: `--follow-deps`,
  `--search-path`, `--ld-library-path` (both commands).** §4.1/§4.2
  classified `--follow-deps` AUTO with a "cost check" prerequisite. The check
  was run, and it **rejects** the AUTO classification (the 7g precedent: do
  the measurement, then let it decide — including deciding against the row).
  A real `dump` of a fixture `.so` with and without the flag differs by
  exactly one snapshot field, `provenance.dependency_info`, and that field
  embeds **absolute host paths** (`{"path": "/home/…/liblib.so",
  "resolution_reason": "root", …}`) plus whatever the host's loader search
  resolves. Making the walk unconditional would therefore (a) put
  host-specific paths into every snapshot, breaking dump reproducibility and
  the ADR-050 comparability contract that compares two snapshots' extraction
  fingerprints, (b) add a filesystem walk of the host's library tree to every
  dump and both sides of every compare, and (c) on `compare`, let findings
  depend on which libraries happen to be installed on the runner — the exact
  "never fabricate a break from missing/host evidence" rule this workstream
  is subordinate to. So it is a genuine per-run evidence selector, not an
  "enable a useful analysis" flag, and it keeps `--search-path`/
  `--ld-library-path` alive with it: those two are that walk's only inputs,
  and §4.3's own reasoning for `deps` ("*the environment is the operand*")
  applies here whenever the walk is requested at all. `--follow-deps` also
  has a dedicated Action input (`follow-deps`), so demoting it would leave a
  documented input with nothing to drive — a capability loss, not a
  simplification. **Revisit only** if the dependency graph is made
  host-independent (recorded as sonames + resolution *reasons* with no
  absolute paths), which is a snapshot-schema change, not a CLI one.
- **Blocked by `scan`, which this workstream may not touch before Phase 6:
  `--env-matrix`, `--require-complete-analysis`.** Both are §4.1 CONFIG rows
  and both are *also* real `scan` options today. `action/run.sh`'s
  scan-to-compare translation routes a request onto `compare` unless a
  predicate says it must stay on the legacy CLI; removing either from
  `compare` alone would silently break that translation for a `mode: scan`
  caller, and widening the predicate is a change to `scan`'s own routing.
  `--require-complete-analysis` additionally has a dedicated Action input.
  Both demotions belong to the same PR that retires `scan` (Phase 6), not
  before it.
- **Blocked by Phase 9, which may not start early: `--scope-public-headers`,
  `--post-manifest`.** Phase 9 owns the contract-mechanism collapse
  (`--scope-public-headers` → `--contract public`, `--post-manifest` → a
  contract overlay) and is blocked on `public-contract-default.md` Phase 6's
  two open relevance defects. "Never trade a possible false negative for a
  shorter CLI" is the governing rule; neither is touched here.
- **Blocked by a named unlanded prerequisite: `--instantiation-manifest`,
  `--bundle-facts-out`, `--bundle-facts-library-manifest`.** Ruled in 7d and
  re-affirmed unchanged: `--instantiation-manifest`'s config home needs the
  ADR-049 coordination that has not landed; `--bundle-facts-out` is
  `-o/--output`'s shape for this invocation's evidence capture, with no `dump`
  fan-out to hold it; `--bundle-facts-library-manifest`'s per-library override
  shape has no `.abicheck.yml` home without inventing one (guard 1 —
  "not one-for-one"), pending G42. `--use-cases` joins this group rather than
  §4.1's CONFIG row: it is the same class — a flag naming a *document* whose
  content is a project artifact — and 7d already ruled that class stays CLI
  (`--policy`/`--suppress` are the precedent); a `use_cases:` block is worth
  landing with the rest of the G29/ADR-057 attribution surface, not as a lone
  path key.
- **Blocked by an Action input, pending an ADR-047 input-lifecycle decision:
  `dump --compression`, `dump --build-target`.** §4.2 classifies the first
  AUTO and the second CONFIG (`build.targets`, a key that already exists).
  Both are wired to documented Action inputs (`snapshot-compression`,
  `build-target`), and `build-target` is additionally a live `scan` option.
  Removing either flag without removing its input leaves a documented input
  that silently does nothing — the one thing §Non-goals rules out ("not
  shortening the CLI by hiding behavior or ignoring supplied input"). On
  `--compression` there is also a substantive counter-argument to §4.2's own
  AUTO row worth recording: the row assumes the `-o` suffix always encodes
  the intent, and `-o build/abi.json --compression zstd` (a CI job publishing
  a fixed artifact name) is a real case where it does not.
- **Genuine per-run operands, no further argument needed** — each is a value
  that differs invocation to invocation and has no project-level meaning:
  the evidence inputs (`-H/--header`, `-I/--include`, `--sources`,
  `--build-info`, `--depth`, `--debug-info`, `--devel-pkg`, `--probe-matrix`,
  `--debug-root`, `--dump-manifest`, `--include-system-declarations`), the
  operand/scope selectors (`--no-baseline`, `--select`, `--select-required`,
  `--old-variant`/`--new-variant`, `--version`, `--since`, `--changed-path`,
  `--abi3`), the consumer contracts (`--used-by`, `--used-by-manifest`,
  `--required-symbol`), the document operands (`--config`, `--policy`,
  `--suppress`, `--pack`), the gate/rendering surface (`--severity-preset`,
  `--contract`, `--format`, `-o/--output`, `--write`, `--view`,
  `--output-dir`, `--dry-run`, `-v/--verbose`, `--help`, `--help-all`), and
  ADR-050 D2's sanctioned escape hatch `--diagnostic-comparison` (the
  calibrated, bounded kind D5 explicitly permits: it never disables analysis,
  it downgrades one hard comparability failure into a diff stamped
  `assurance: "none"` everywhere). Fifteen of these already carry a per-flag
  written rationale in `abicheck/frontends/cli/options/inventory.py`'s
  `COMPARE_FLAG_BUDGET_RAISES` ledger, which is the executable half of this
  ruling — `tests/test_config_rebalance.py` fails if an entry names a flag
  that is no longer visible. (**Fifteen of forty-eight** is the point Phase
  7k acts on: that ledger covered only the flags added *since* an opaque
  base count, so the other two-thirds of the surface had no written ruling
  anywhere in code, and `dump` had none at all. See 7k below.)
- **One MERGE candidate examined and declined: `--old-variant`/
  `--new-variant` → a side-scoped `--variant old=`/`new=`.** It would be a
  net −1 and would match every other two-sided input's spelling. Not done
  here, and recorded rather than left unmentioned: it is a rename of a
  user-visible pair with no analysis consequence, which makes it the lowest-
  value change in this list and the one most likely to churn unrelated tests
  in a PR that is already changing the meaning of six flags. Left as a
  standalone follow-up.

**Where the counts land, re-derived by Click introspection on this branch
rather than read off §4.5:**

| Command | Before | After | Target | Residual, itemized |
|---|---|---|---|---|
| `compare` | 56 | **50** | ≤40 | `--scope-public-headers`, `--post-manifest` (Phase 9) · `--env-matrix`, `--require-complete-analysis` (`scan`, Phase 6) · `--instantiation-manifest`, `--use-cases` (prerequisite) · `--follow-deps`, `--search-path`, `--ld-library-path` (measured keep) · `--old-variant`/`--new-variant` merge (−1, declined here; **done in 7j**) = 10. Re-derived after `--budget` landed and 7j merged the variant pair: **50** again, with `--budget` replacing the merged pair in the residual set |
| `dump` | 24 | **21** | ≤16 | `--follow-deps`, `--search-path`, `--ld-library-path` (measured keep) · `--compression`, `--build-target` (Action input) = 5 |

`compare` reaches **40** and `dump` reaches **16** exactly once those two
itemized residuals close — i.e. the targets are arithmetically reachable and
every option still standing between here and them has a named owner and a
named blocker, not an unexamined one. Three of `compare`'s ten and two of
`dump`'s five are *deliberate keeps* rather than deferrals, so the honest
reading of §4.5's numbers is that they were set before Phase 2 added five
flags to `compare` and before the dependency-walk measurement existed;
they are recorded here as still-useful pressure, not as arithmetic that
survives contact with the per-flag rulings above.

**7j: `--old-variant`/`--new-variant` → one side-scoped `--variant`.**
7i examined this MERGE and *declined* it, on the grounds that it is "a
rename of a user-visible pair with no analysis consequence, which makes it
the lowest-value change in this list and the one most likely to churn
unrelated tests in a PR that is already changing the meaning of six flags",
and left it as a standalone follow-up. That decline is reversed here, for
two reasons the original ruling did not weigh:

1. **Its stated rationale is an effort argument, which AGENTS.md's own
   decision-making principles rule out of technical decisions** ("Don't
   scope, simplify, defer, or pick an implementation approach because it's
   faster or quicker to ship"). "Churns unrelated tests in a PR already
   doing six things" is a reason to make it *its own* PR — which is what
   7i itself proposed and what this slice is — not a reason not to do it.
2. **"No analysis consequence" understates what the pair costs.** Every
   other two-sided input on `compare` — `--header`, `--include`,
   `--version`, `--sources`, `--build-info`, `--debug-info`, `--devel-pkg`,
   `--debug-root`, `--probe-matrix`, `--dump-manifest` — is one option
   carrying an `old=`/`new=` prefix, a convention ADR-040 Lever 1
   established by retiring exactly this shape of pair (`--old-header`/
   `--new-header`, `--old-version`/`--new-version`). The variant pair was
   the last two-sided input still spelled the old way, so it was not a
   neutral naming difference but a live exception to a convention a user
   has already had to learn: one concept represented twice, which is D5's
   guard 2 as literally as the surface has left.

Ruled a **per-run operand throughout** — variant selection is comparison
scope (ADR-065), not a project property, so nothing here moves to
`.abicheck.yml`. What retires is the spelling: `--variant
[old=|new=]VARIANT_ID` (`SIDED_STR_PARAM`, repeatable, bare value applies
to both sides, last-one-wins per bucket). Both old spellings exit `64` with
no alias and no deprecation window; both are registered in
`scripts/retired_surfaces.py`. The *unregistered* release engine
(`cli_compare_release.py`) keeps its own per-side `--old-variant`/
`--new-variant`, exactly as it kept per-side `--old-version`/`--new-version`
through ADR-040 Lever 1 — that engine is not public CLI surface and is not
what this workstream's counts measure.

Two details worth not rediscovering. First, an **empty variant id**
(`--variant old=`, or a bare `--variant ""`) is now a usage error rather
than a silently-`None` selection: the pair's old `default=None` made "flag
absent" and "flag given an empty value" indistinguishable, so a package
declaring several variants would have failed much later with a message
naming neither the empty value nor the flag that supplied it. This follows
7f's `--provenance` precedent — validate the grammar eagerly in the Click
callback, exit 64 before any extraction runs. Second, the resolution rule
lives in a standalone `_resolve_sided_variant` with a **property-test
class** (`TestResolveSidedVariantProperties`) stating its contract as
invariants — last-writer-wins per bucket, a later `both=` re-basing both
sides, a per-side override surviving an earlier base, the two buckets
independent under any interleaving, and unset staying `None` rather than
gaining a synthesized per-side default the way `--version` has. The oracle
is an independently-stated "last token addressing this side" rule, not the
implementation's own loop, and the enumeration is exhaustive over every
token sequence up to length three. AGENTS.md requires this for a reusable
ordering primitive, and the repo's own `_paired_stable_indices` history is
why: order-dependence in a small merge helper is exactly the defect class a
fixed-example test cannot reach.

Front-end parity in the same PR: `action/run.sh`'s two hand-maintained flag
tables (the `extra-args` value-option tokenizer and the scan-only-flag
routing predicate) both move to `--variant`. No typed-API change exists to
make — `variant_options` has always used `expose_value=False`, stashing the
resolved per-side values on `ctx.meta` for the release fan-out alone, so
`CompareRequest`/`run_compare` never carried a variant parameter. No schema
changes: nothing machine-readable moved.

**Count after 7j: `compare` 50** (from 51 — 7i's own table recorded 50, but
Phase 4 commit 2's `--budget` landed between that slice and this one).
`dump` is untouched at **21**.

**7k: every surviving option ruled in *code*, exhaustively — and the budget
mechanism that let one slip through.** 7d and 7i ruled flags in this
document. This slice makes the whole surface's rulings executable, because
the prose ruling and the machine check had drifted apart in a way neither
noticed.

**The defect, measured.** ADR-037 D10.5's ledger derived `compare`'s ceiling
as `COMPARE_FLAG_BUDGET_BASE + len(COMPARE_FLAG_BUDGET_RAISES)`, and
`tests/test_config_rebalance.py` asserted `visible <= budget`. Its own
docstring claimed the consequence: "a new visible flag *cannot* be slipped in
by silently consuming slack — the only way to raise the ceiling is to add a
documented ledger entry." That claim was false. `BASE` was lowered in bulk by
some removals and not others, and a removed flag's `RAISES` entry was
sometimes deleted while `BASE` stayed put; each mismatch became permanent
slack. Introspected on this branch before the fix: **`visible=48`,
`BASE=41`, `len(RAISES)=16`, budget `57` — nine flags of slack**, and
`--budget` (Phase 4 commit 2) had in fact landed as a visible option with no
ledger entry at all. `BASE` being an opaque *count* rather than a list is the
second half of the problem: it named none of the flags it covered, so "which
flags are ruled?" had no answer at all — 16 of 48 `compare` options carried a
written rationale in code, and `dump` carried **zero**, despite ADR-037 D8.1
requiring the two commands' shared families not to drift.

This is the repo's own named bug class — a check that passes identically
before and after the regression it exists to catch — so the replacement's
test does not merely pin `--budget`. `test_the_superseded_budget_shape_
would_have_missed_an_unruled_flag` reconstructs the retired `visible <= BASE
+ len(RAISES)` comparison over a surface carrying an unruled flag, shows it
*passing*, and shows the bijection check failing on the same input. The shape
of the check was the bug, so the shape is what is asserted.

**The replacement.** `abicheck/frontends/cli/options/rulings.py` carries one
`OptionRuling` per visible option on **both** commands — 48 + 19 — each
stating which of D5's three guards lets it stay. The ceiling is now exactly
`len(rulings)`, and the test asserts an **exact bijection in both
directions**: an option with no ruling fails, and a ruling naming a
non-existent option fails. There is no slack left to consume. A ruling is
either `per_run_operand` (clears all three guards, stays) or `deferred`
(judged demotable/removable, blocked by a *named* prerequisite) — and
`blocker` is mandatory for the latter and rejected for the former, enforced
in `__post_init__`, so a deferral cannot quietly become a permanent keep by
nobody re-reading it, and a keep cannot be written as though it were pending
someone else's work. `dump` gains its first ruling table; a shared option's
`dump` entry points at `compare`'s rather than restating it, so ADR-037
D8.1's no-drift requirement is visible in the data.

**Two fresh rulings this audit produced**, beyond transcribing 7d/7i:

- **`--search-path` and `--ld-library-path` audited for a merge and ruled
  *distinct*, with the measurement.** They look like two spellings of "extra
  places to look for libraries", which would be guard 2. They are not:
  `resolver._candidate_dirs` inserts `--ld-library-path`'s directories at
  **loader step 2**, ahead of `DT_RUNPATH` and the defaults, and appends
  `--search-path`'s at **step 4**, after them — and the two record different
  `resolution_reason` values on the resolved node. Collapsing them would
  silently change *which library a run resolves*, which is an analysis
  consequence, not a spelling change. This is the 7g precedent applied to a
  merge rather than a demotion: look at what the code actually does, then
  let it decide.
- **`dump --compression` ruled a keep, rejecting §4.2's AUTO row.** That row
  ("inferred from the `-o` suffix; `auto` is already the default and already
  correct") assumes the suffix always encodes the intent. 7i recorded the
  counter-argument without resolving it; this audit resolves it against the
  row. `-o build/abi.json --compression zstd` — a CI job publishing a fixed
  artifact name — is a real case suffix inference cannot express, so removing
  the flag would remove a capability rather than derive it. Which encoding
  this artifact is published in is a property of the publishing job. Its
  Action input (`snapshot-compression`) therefore stays with something real
  to drive, which also dissolves the "blocked by an Action input" framing for
  this one.

**Three MERGE candidates examined and declined, each recorded rather than
left unmentioned** — `--write` against `--format`/`-o` (the most plausible
one left, since `--write markdown=r.md` and `--format markdown -o r.md` do
coincide: declined because `--format` with no `-o` renders to *stdout*,
which `--write`'s PATH-requiring grammar cannot express, and inventing a
path-less `--write` value to recover it would be one flag carrying two
grammars to save one option); `--select` against `--select-required` (the
second declares a completeness *obligation* feeding ADR-065 D6's scope exit
axis, a distinction that would otherwise need an invented `KEY:required`
grammar); and `--used-by-manifest` against a `--used-by @FILE` form on
Phase 7h's `--required-symbols` precedent (declined because that precedent
does not transfer: `--required-symbols FILE` fed the *identical* contract as
an inline symbol, whereas a manifest carries digest/platform/profile
provenance and an advisory-vs-required distinction a bare consumer path
cannot express, so collapsing them would either drop that content or
overload one flag with two value grammars).

**One deferral's blocker re-attributed.** 7i recorded `dump --build-target`
as "blocked by an Action input, pending an ADR-047 input-lifecycle decision".
Re-verified here, that is not the blocker. Removing an Action input alongside
its flag is ordinary front-end parity, done in the same PR — no lifecycle
decision needed. What actually blocks it is that `--build-target` is a live
`scan` option *and* is listed in `action/run.sh`'s
`_extra_args_has_scan_only_flag`, so the shared `build-target` input still
drives a real flag on a command this workstream may not touch before Phase 6.
The ruling itself is unchanged and is the strongest remaining CONFIG case on
either command — the flag's own help text calls it "CLI equivalent of
`.abicheck.yml` build.targets", which is guard 2 stated by the option itself,
against a key that already exists. Its blocker is now `scan`'s lifetime, and
it is recorded as `deferred`, not as a keep.

**Two 7i deferrals re-verified rather than copied.** `--env-matrix` and
`--require-complete-analysis` are still blocked by `scan`: both were checked
against `action/run.sh`'s routing predicate for this audit, and neither
appears in `_extra_args_has_scan_only_flag`, so a `mode: scan` caller passing
either through `extra-args` is translated onto `compare` today and removing
it from `compare` alone would silently break that translation. Unchanged, and
now recorded where the check runs.

**Nothing else moved.** No option was added, removed, hidden, or renamed by
7k; no verdict, gate, exit code, coverage contribution or assurance value
changes; no machine contract changes, so no schema bump. It is a
documentation-and-enforcement slice whose whole product is that the surface
can no longer grow unruled. **Counts after 7k: `compare` 50, `dump` 21**
(unchanged from 7j). Both numbers are the *pre-Phase-6* surface: `scan`'s
retirement took `--env-matrix`, `--require-complete-analysis` and
`--build-target` with it, so the live, Click-introspected counts are
**`compare` 44, `dump` 18** — 47/18 at Phase 7l, then 7n's evidence-role merge took three off `compare`.

Only now, with one analysis path: the CONFIG/AUTO/MERGE/REMOVE rows of §4,
in small PRs grouped by concept —
7a hidden flags (4) · 7b `compile.*` demotion (shared `compare`+`dump`) ·
7c `debug.*` demotion · 7d release/bundle topology (absorbs cli-cleanup
PR J; **done**, including the explicit four-flag ruling above) ·
7e `--profile` removal · 7f `dump` provenance merge (**done**, see above) ·
7g resource limits (**done**, see above) ·
7h `--required-symbols` (**done**, folded into `--required-symbol @FILE`),
`-j` (**done**, removed outright), `--keep-extracted`/
`--no-bundle-analysis` (**done**, see 7d above) ·
7i the whole-surface per-flag audit (**done**, see above) — every surviving
`compare` and `dump` option ruled against D5's three guards, with
`--reconcile-build-context`, `--pdb-path`, `--support-promise` and
`dump --compile-db-filter` removed and every keep or deferral named ·
7j the `--variant` merge (**done**, see above) — 7i's own declined
follow-up, reversed on the grounds that its stated rationale was an effort
argument · 7k the exhaustive per-option ruling registry (**done**, see
above) — every visible `compare` *and* `dump` option ruled in code, the
D10.5 budget's nine-flag slack hole closed, and two fresh rulings
(`--search-path`/`--ld-library-path` measured distinct, `dump
--compression` keeping against its own AUTO row).

Every PR in this phase meets the merge criteria recorded in
`cli-cleanup-phase-two.md` — old spelling exits
`64` with no hidden alias, front-end parity in the same PR, schema bump where
a machine contract changes, and verdict/gate/exit/coverage/assurance asserted
separately. That list is carried forward unchanged; it is not restated here.

### Phase 7l — external CLI audit (2026-09-12), reconciled

An external, static source/reference audit of the whole CLI surface at
`31cbd4a` was reviewed against this phase's existing rulings. Its counts
reproduce exactly (`compare` **47** visible, `dump` **18**, Click-
introspected — the "50/21" in 7i/7j/7k is the pre-Phase-6 surface, before
`scan`'s retirement took `--env-matrix`, `--require-complete-analysis` and
`--build-target` off `compare`/`dump` with it), so the two surfaces are
talking about the same CLI and the disagreements below are about *rulings*,
not about state.

Its central claim is one this phase should adopt explicitly, because 7i/7k
did not state it: **a necessary capability does not require a dedicated
flag, and a removal that costs the user three commands or an invented
manifest is not a simplification.** Its corollary matters equally — "there
is no replacement today" is a *migration prerequisite*, not permanent
ownership, which is exactly what a `deferred` ruling already says, and
several of our `per_run_operand` rulings would be honestly re-read as
`deferred` under it.

**Adopted — new work this phase did not have.** Each becomes a numbered
slice below rather than a flag row, because each is a *replacement
mechanism* first and an option count second:

- **7m — one export request. Done.** `--format`/`-o/--output`/`--write`/
  `--output-dir`/`--max-findings-per-library` were four mechanisms for
  "which artifacts does this analysis produce". They are now one repeatable
  `-o FORMAT=DESTINATION` with `-` for stdout (`frontends/cli/options/
  export.py`), so 7k's own decline of the `--write` merge — "a path-less
  `--write` value would be one flag carrying two grammars" — does not
  survive this framing: it is one grammar, and `-` is the stdout spelling.
  `--output-dir`'s per-component fan-out and
  `--max-findings-per-library`'s summary cap landed as consequences of the
  export set rather than separate escape routes: a trailing `/` is a
  directory destination writing exactly what `--output-dir` wrote, complete
  machine data is never truncated, and a human summary is bounded
  automatically. **Net −4** on `compare` (47 → 43) and −1 on each of the six
  other report-rendering commands (103 → 93 native), the first removal on
  this surface that reduces what a user must *decide*, not just what they
  must type. The named prerequisites all held: ADR-061 gap C was confirmed
  intact before starting (`report/envelope.py` builds one `ReportEnvelope`
  above format selection; `service_render.render_envelope` is a pure
  projection), and collision detection, stdout exclusivity, Windows path
  handling (split on the first `=` only) and F-19's invariance across the
  new grammar are stated as invariants over generated permutations in
  `tests/test_cli_export_grammar.py`.

  **Three consequences worth not rediscovering**, each a real behaviour
  change the merge forces, none of them a capability loss:

  1. **A display filter applies to every export.** `--write` deliberately
     rendered its artifact unfiltered whatever `--format` asked for. That
     asymmetry was answerable only while "primary" and "secondary" were two
     flags; under one repeatable operand there is no principled answer to
     which of `-o json=a.json -o markdown=-` is the unfiltered one. Every
     machine projection still carries the complete disposition/suppression
     accounting plus `show_only_filter`/`filtered_summary`, so narrowing the
     display hides nothing.
  2. **A directory export on a single pair is a usage error**, where
     `--output-dir` was warned about and ignored. As a knob, ignoring it
     promised nothing; as a destination it promises an artifact, and
     silently not producing one is what §Non-goals forbids.
  3. **The Action's `extra-args` export set replaces the Action's own**
     rather than overriding one field of it. `-o` is repeatable and a
     duplicate destination is a usage error rather than last-wins, so
     `run.sh` injects nothing when the caller states exports — which also
     collapsed its three separate extra-args scanners (`--format`,
     `--write`, `-o`) into one.
- **7n — evidence transports, one input per evidence *role*. Done.**
  `--debug-root` merged into `--debug-info` (a detached debug file, a
  directory of them, and a debug package are three transports of one
  role); `--devel-pkg` merged into `-H` (a development package is a
  carrier of header evidence); `--probe-matrix` merged into a typed
  `--build-info` (probe observations and compile context stay distinct
  *internally* and may be supplied together). **Net −3 on `compare`**,
  independent of 7m's own −4; the two landed separately and the count
  table below states where `compare` sits with both applied. The bar for
  each is that the merged input keeps the schema, side ownership, and
  binary/debug identity validation it has today — accepting more filename
  extensions is not the deliverable. **Explicitly not merged:
  `--sources` and `--build-info`** — a checkout and the context it was
  built under are independent inputs that are routinely supplied
  together; an `--evidence` grammar there would trade a flag for a type
  vocabulary the user must learn and for harder ambiguity diagnosis. That
  boundary is the audit's own, and this plan endorses it.

  **`dump` is 18 → 18, not 17** — this section's own arithmetic was
  wrong and is corrected here rather than met by deleting an unrelated
  flag. `dump` never had a `--debug-info`: the count assumed
  `--debug-root` disappearing, without noticing that the role it carries
  has to keep a spelling on that command. On `dump` this slice is
  therefore a *rename* plus the detached-file transport, net zero. §4.5's
  `dump` ≤16 target is unchanged and still needs the rulings named in
  the table below, not this slice.

  **Where the work landed, and the one thing worth not rediscovering.**
  Routing is content-only: `workflows/evidence_transport.py` answers what
  each operand *is* (a package extractor's own format contract, an ELF's
  debug sections or PDB magic, a probe matrix's `schema`/required-key
  contract), and `frontends/cli/options/evidence_roles.py` splits each
  role's sided values back into the per-transport destinations the command
  bodies already consume — nothing downstream of
  `normalize_sided_options` learns that the flags merged. Two consequences
  the slice forced:

  1. **The pipeline had to become name-independent too**, which is bug
     class `cli_surface.name_independent_dispatch_undone_downstream`
     one layer below where #1242 found it. Every extractor in
     `abicheck/package.py` detected its format from the *filename*
     (`.rpm`, `.tar.gz`, `.whl`, `.conda`, with a magic-byte fallback only
     for RPM/Deb), so a front end classifying a suffix-less debug package
     correctly would still have hit "Unrecognized package format". All of
     them now read magic bytes and container members; `is_package` is
     defined as "some archive extractor claims it" rather than a second
     suffix table beside them. The seed test drives the whole public
     invocation under non-conventional names, and its evidence that the
     package transport engaged is that the *extractor* got to speak — a
     misclassified package falls through to another transport and the
     comparison completes anyway, so "the run succeeded" proves nothing
     (mutation-verified: the assertion that survives a name-keyed
     `detect_extractor` is exactly the one that proves nothing).
  2. **A detached debug file is now resolvable at all.** `--debug-root`
     only ever searched *inside* a directory, so "a bare `.debug` file"
     had no spelling: the closest thing was naming its parent directory
     and hoping the path-mirror layout matched.
     `extract/detached_debug.py` adds the missing transport, first in the
     resolver chain (an artifact the caller named directly outranks what
     the binary happens to carry), with build-id validation that refuses
     a contradicting sidecar and accepts one that carries no build-id —
     absent evidence never manufactures a mismatch.
- **7o — `--view`'s internal grammar. Done.** 7i/7k ruled `--view` a keep
  and never looked inside it. Inside, it carried six independent decisions:
  report mode, a severity/entity/action display filter, demangle toggles,
  pattern explanation, filtered-finding display and suppression audit. Four
  are gone; the net option count is **0**, which is the point — this slice
  reduces what a user must decide, not what they must type.

  1. **`patterns`, `filtered` and `suppressions` are unconditional
     disclosure.** All three gated *rendering* of a ledger the run had
     already computed, which ADR-067's record-before-disposing rule says is
     part of the result rather than a display preference. Verified against
     the real machine projections rather than by reading the renderers:
     nothing was reachable only through a token. The scope/reconciliation
     ledgers and the `--suppress` audit were already in every JSON/SARIF/
     JUnit/HTML projection unconditionally, and the disposition audit
     (ADR-067 D3, `report/disposition_audit.py`) already carried the
     detected/effective totals into every view including the compact ones.
     What the tokens gated was the human-side echo, which now happens
     whenever there is something to disclose. **One deliberate exception,
     recorded because it looks like an inconsistency:** the pattern ledger
     is echoed only when at least one finding was actually modulated. An
     unconditional "No pattern-aware modulations applied." on every run is
     a banner, not accounting, and it would make every quiet run differ
     from before for no reader's benefit.
  2. **`demangle`/`no-demangle` are automatic.** The decision existed only
     because demangling *replaced* the mangled name, leaving nothing to
     paste into `nm`, a suppression selector or a bug report.
     `demangle_text` now renders `lib::gone(int) [_ZN3lib4goneEi]`, human
     formats (markdown/review/html/text/oneline) always demangle, machine
     formats never do — and `reporter.resolve_demangled_symbol` resolves
     `demangled_symbol` for *every* Itanium-mangled finding rather than
     only an `ELF_ONLY`-visibility one, so a JSON/SARIF consumer carries
     both names. Confirmed on real compiled libraries, not fixtures with
     hand-written names: a g++-built ELF pair and a
     `clang++ -target x86_64-apple-macos11 -fuse-ld=lld`-built Mach-O pair
     both produce a real `func_removed` whose machine projection carries
     `symbol=_ZN3lib4goneEi` + `demangled_symbol=lib::gone(int)` and whose
     markdown carries both spellings. **PE is the documented exception and
     is unchanged by this slice**: a real `clang++ -target
     x86_64-pc-windows-msvc` DLL pair produces `?gone@lib@@YAXH@Z` in both
     projections with no `demangled_symbol` at all, because nothing in this
     codebase demangles MSVC decoration (`demangle.py`'s own module
     docstring explains why, and adding a demangler is a dependency
     decision, not a display one). The exact symbol is copyable there too,
     which is the property this deliverable actually promises.
  3. **The display dimensions come from the canonical finding model.**
     `ChangeKindMeta` gains a mandatory `entity` (`ChangeEntity`) and
     `operation` (`ChangeOperation`), declared on the same single
     registration that declares a kind's verdict and impact, and
     `_validate_entry` refuses an entry without them — so a kind added
     tomorrow lands in the right dimension with no second registration.
     The superseded path is deleted, not left beside the catalog:
     `reporter_markdown`'s `_ELEMENT_PREFIXES`/`_ELEMENT_EXACT` name-prefix
     tables, its `_ADDED_SUFFIXES`/`_REMOVED_SUFFIXES` suffix rule, and the
     30-entry `_OPERATION_OVERRIDES` table that corrected the suffix rule
     for every kind whose name ends in `_added` while naming a trait
     *gained by a persisting entity*. **That override table was itself the
     evidence that a name is not the fact.** The measurement that motivates
     this is worth not rediscovering: the prefix table matched **no element
     at all for 238 of the 407 kinds**, so `--view
     show=functions,variables,types,enums,elf` — every token the vocabulary
     had — hid 58% of the catalog, silently. The element vocabulary
     therefore also gains `build`, `source` and `analysis`, the three
     dimensions the prefix table could not express; the five existing
     tokens keep their exact spellings (`elf` stays the alias for
     `ChangeEntity.BINARY`) so the old invocation is unchanged.
     Per-finding `operation` values may move in JSON for a kind the suffix
     rule classified wrongly, and each finding now carries `entity` beside
     it — report schema **5.0**, a MAJOR bump because the `leaf` removal
     takes two keys with it (Codex review, PR #1284: a consumer told to
     accept any 4.x report would otherwise get a document that no longer
     satisfies the contract it implemented).
  4. **`leaf` re-examined against `root-cause` with a measurement, per the
     7g/7k precedent — and retired.** 129 real library pairs were built
     from the catalog corpus (`catalog/cases/*/v1.{c,cpp}` + `v2`, compiled
     to shared objects with debug info) and compared through the typed API,
     then rendered under both modes and under `full`:

     | Measured over the 129 pairs | Result |
     |---|---|
     | pairs compared without error | 129 |
     | pairs with at least one finding | 93 |
     | pairs where `leaf` and `root-cause` exposed the **same finding set** | **93 of 93** |
     | pairs where either mode exposed a finding the other did not | **0** |
     | pairs with findings where `leaf_changes` was **empty** while `root_causes` grouped everything | **40** |
     | keys `leaf` had and `root-cause` did not | `leaf_changes`, `non_type_changes` |
     | keys `root-cause` had and `leaf` did not | `root_causes`, `root_cause_count`, `detectors`, `suppression`, `old_file`, `new_file` |

     The measurement decides it: `leaf` never showed evidence `root-cause`
     lacked, and in 43% of the cases that had findings its own headline
     section was empty because it groups *only* root-type changes. So
     `--view leaf` exits 64 naming `root-cause`, `_to_json_leaf`/
     `_to_markdown_leaf`/`build_leaf_document` and the leaf row/section
     renderers are deleted, and the `leaf_changes`/`non_type_changes` JSON
     keys go with them (what makes that bump MAJOR). The acceptance bar is
     met by asserting what the retired mode's *own* tests asserted — the
     root type grouped with its affected-interface list, and the non-type
     findings alongside it — against `root-cause`, rather than by asserting
     that the removed token now errors.

  **Front-end parity and the merge criteria**, in this PR: every retired
  spelling exits `64` with no hidden alias and is registered in
  `scripts/retired_surfaces.py`; `action/run.sh`'s two hand-maintained
  `--view` token scanners needed no behavioural change (both are
  token-generic and special-case only `show=`) and their comments are
  corrected; the typed API never carried any of these values (`--view` is a
  front-end spelling resolved into `report_mode`/`show_only` before
  `run_compare` is reached), and the `demangle`/`explain_patterns`/
  `show_filtered`/`audit_suppressions` parameters are removed from the CLI
  layer rather than left as dead internal knobs — `demangle` survives only
  as a *resolved per-format* value (`_resolve_demangle(fmt)`, now a
  one-argument function over `HUMAN_FORMATS`). Verdict, gate, exit code,
  coverage contribution and assurance are asserted separately from the
  display change.

  **Three consequences worth not rediscovering**:

  1. **Two release-path rejections disappeared without their gaps
     closing.** `--view filtered` was rejected outright on a directory/
     package operand because the release engine never threaded the ledger
     into its per-library renderer, and `--view suppressions` with a real
     `--suppress` was rejected because the fan-out has no single audit
     result to attach. Both rejections are gone because the requests are
     gone — but the release renderer still shows no per-library scope
     ledger and no per-library suppression audit. That is now a missing
     feature rather than a usage error, and it is the one place where a
     release comparison discloses less in human output than a single-pair
     one. The machine projections are unaffected.
  2. **A stored-bundle-facts comparison lost two rejections the same way**
     (`compare_bundle_facts_rejections.py`): that dispatcher's markdown
     still renders bundle findings without demangling, and it still has no
     stderr channel for the pattern ledger. Neither loses information — the
     ledger is in that comparison's own JSON unconditionally, and every
     machine projection carries both symbol names.
  3. **The F-19 rendering-invariance space shrank from 6048 points to
     189**, because four of its axes stopped existing. The invariant is
     unchanged; there is simply less rendering space left for it to hold
     over, which is what this slice is for. `tests/
     test_presentation_analysis_separation.py`'s own vacuity guard on the
     space size is what keeps that shrink honest rather than silent.

  **Verification.** `scripts/verify.py --profile pr` passes all 21 non-unit
  steps, and the `unit-pr` lane reports **47,447 passed** with the 95%
  line+branch floor held at **96.25%**. Its 45 remaining failures are all
  pre-existing in this environment, not this slice's: 43 of them reproduce
  name-for-name on the parent commit (the git/workflow-harness families --
  `test_protect_committed_baseline_workflow`, `test_changelog_fragment_gate`,
  `test_bugfix_test_contract`, `test_agent_evals`,
  `test_backend_capability_matrix`, `test_classify_perf_paths`,
  `test_subprocess_bash_is_resolved`, `test_model_package_surface`,
  `test_l2_real_profiles`, `test_real_world_false_positives`), and the
  remaining two pass when run individually -- `-n auto` cross-test
  interference in two files whose siblings are in that same pre-existing set.

  The surviving parsing primitive gets the treatment AGENTS.md requires:
  `TestParseViewTokensProperties` (`tests/test_view_internal_grammar.py`)
  states `parse_view_tokens`'s contract as invariants — last mode wins under
  any interleaving, `show=` groups keep their order and are never merged,
  the two dimensions are independent, a group is never joined with a comma
  (which would AND two groups instead of ORing them), every retired token is
  a usage error naming its replacement — exhaustively over every token
  sequence up to length three, against an independently-stated oracle plus a
  vacuity guard on the oracle itself.

- **7p — `project validate` consolidation. Done.** `project validate`,
  `validate-build` and `validate-use-cases` were one question over three
  input schemas. Now `abicheck project validate INPUT`, dispatching on a
  validated schema discriminator or a recognized directory contract —
  never on a filename guess, and recognizing a project file never
  authorizes its nominated toolchain (`--toolchain-bindings` applies to a
  project config and is a usage error elsewhere, rather than being
  silently ignored). **−2 subcommands**, −4 duplicated output options;
  the remaining `--format`/`-o` pair folds with 7m like every other
  command's.

  The classifier is `buildsource/validation_input.py` — beside the
  build-output contract it routes on, since ADR-061 classifies
  `frontends/` as `may_import: [model, workflows, report]` and routing on
  anything but that real contract would be the filename guess this slice
  rules out. A directory (or a `build-output.json` named directly, which resolves to
  its directory) declaring `schema: abicheck.build-output/v1` is a build
  output, reusing `is_build_output_dir` — the same contract every other
  build-output consumer routes on; a top-level list is a use-case
  manifest; a mapping is a project config, as the one shape carrying no
  self-describing tag. Its property test is exhaustive over
  shape × filename, with each shape's *conventional* name among the
  misleading ones, so a filename-based implementation fails rather than
  coincidentally passing — AGENTS.md's primitive-level rule, and the
  reason this is a separate module rather than a branch inside the
  command.

  **Three consequences worth not rediscovering**, each a real behavior
  change the consolidation forces, none of them a capability loss:

  1. **An empty document is validated under every reading, not assigned
     to one.** YAML cannot tell an empty mapping from an empty list, and
     both superseded commands accepted one. So the project-config
     validation still runs (its "no targets declared" warning is the
     whole reason to run it) and the vacuous manifest reading is stated
     beside it. Silently picking either would have dropped whichever the
     caller meant — the empty config's warnings, or the manifest's `0
     use cases` answer.
  2. **A mapping that was meant to be a manifest is read as a config**,
     because shape is the discriminator. Exit code is unchanged (`64`)
     and the message now names the reading applied, so the user is not
     sent hunting for a typo in a document of the wrong kind.
  3. **Unparseable YAML fails at classification**, ahead of every
     validator, rather than inside the project-config loader. Same exit
     code, same named file.
- **7q — `aggregate --run-plan` into `--manifest`.** A run plan is a
  second schema for the same "expected set" input; the projection is
  validated internally. `--discovered-only` stays **explicit** — it states
  that the operator has no expected inventory, and inferring that from an
  empty manifest is how a CI matrix silently goes green with missing jobs.
  **Net −1.**
- **7r — `project plan --allow-empty` retired** once a legitimately empty
  selection produces an *explained skipped plan* rather than needing a
  bypass switch, and bootstrap validation routes to `project validate`.
  **Net −1.** Its other four inputs (`--build-output`, `--project`,
  `--head-sha`, `--toolchain-bindings`) are ruled keeps here for the
  audit's reason, which this plan adopts as a general rule:
  **auto-detection is a useful default, not proof that an explicit
  override is unnecessary** (a Git origin may describe a fork or mirror;
  the planner's checkout is not necessarily the candidate revision).

**Adopted as re-rulings of existing entries** (`rulings.py` changes from
`per_run_operand` to `deferred`, with the named blocker, when the slice
above lands — not before, since a deferral needs a real prerequisite):
Neither 7m's four entries nor 7n's three needed the intermediate
`deferred` state, and for the same reason: each slice landed in one step,
so `--format`, `--write`, `--output-dir`, `--max-findings-per-library`
(7m) and `--debug-root`, `--devel-pkg`, `--probe-matrix` (7n) are simply
*gone* from `rulings.py` rather than re-ruled — the retired export
spellings registered in `scripts/retired_surfaces.py` instead, and the
three surviving evidence inputs (`--debug-info`, `--header`,
`--build-info`) re-ruled to state the whole role each now carries, as
`--output`'s own ruling is rewritten around the new export grammar. A
deferral would have been the wrong record for either: there is no blocker
left to name.

**Declined, with the reason recorded** — each was already measured or
already decided, and the audit did not have the measurement:

- **`dump --compression` → retire.** Declined; 7k already ruled this
  against §4.2's AUTO row, and the audit concedes the counterexample
  itself (`-o build/abi.json --compression zstd`: the publishing job owns
  the encoding, and the suffix cannot express it). "Retire it through a
  storage preference or an external re-compression step" is a capability
  trade the audit labels a "conscious convenience tradeoff"; this plan's
  Non-goals rule it out — we do not shorten the CLI by making a supported
  workflow require an extra step.
- **`--dry-run` → `--plan`.** Declined, and it is not a neutral rename:
  ADR-054 folded a `plan` *command* back into `dump --dump-manifest
  --dry-run` precisely to stop a second "preflight" vocabulary growing
  next to the established one. Re-introducing `--plan` as a flag spelling
  re-opens that drift for a stated zero option-count gain. (`project
  plan` is a different noun — a run plan artifact — and is unaffected.)
- **`--used-by-manifest` → `--used-by @FILE`.** Declined again, same
  reason as 7k: a manifest carries digest/platform/profile provenance and
  a required-vs-advisory distinction a consumer path cannot express, so
  the collapse either drops content or overloads one flag with two value
  grammars. The audit's own `@`-prefix proposal is the second of those.
  (Its ruling that `--required-symbol` stays *distinct* from `--used-by`
  — a host contract is not an observed import table — matches ours.)
- **`--severity-preset` merged into policy selection.** Declined *for
  now*, not on the merits: the audit itself names the real blocker
  (gate activation must be consistent across pairwise, bundle and
  no-baseline execution first), and this plan will not merge a gate
  selector into a policy document while a no-baseline run still describes
  the preset as what arms its audit gate. Re-examine after that
  convergence; the audit's constraint that acceptance, classification and
  suppression stay *separate meanings* under one resolved selection is
  adopted verbatim as the design bar.
- **`--select-required` merged into an expected inventory.** Already this
  plan's ADR-065 dependency (P5), not a new finding; `--select-required`
  stays until package component inventories land, and 7k's own decline
  (it declares a completeness *obligation* feeding the scope exit axis)
  stands until then.
- **`--follow-deps`/`--search-path`/`--ld-library-path` → one
  `--environment old=|new=` operand.** Not declined, but **not adopted as
  a reduction**: it is a *new* operand, the audit counts it as such, and
  7i's measurement (the dependency walk embeds absolute host paths, so it
  may never become unconditional) plus 7k's measurement (the two path
  flags insert at different loader steps and record different
  `resolution_reason` values, so collapsing them changes which library
  resolves) both survive it. Recorded as **future direction, owned by
  G42** (named deployment environments and provider resolution), where an
  environment is a real named object rather than a flag rename. The
  audit's two constraints are carried into G42's own acceptance bar: the
  replacement must keep the *same-run* enrichment of a comparison or
  snapshot (telling the user to "run `deps` separately" is not parity),
  and removing `--follow-deps` must never mean resolving against the
  current host by default.

**Noted, no change here.** `compat`'s 94 logical options (27 hidden) are
frozen by ADR-068 D7 and excluded from every count — the audit agrees and
proposes nothing there. `abicheck-cc` and the Clang plugin take compiler
arguments and `ABICHECK_*` variables, not abicheck flags; the audit's
useful point is that wrapper, plugin and `dump` capture settings should be
*generated from one capture specification* rather than maintained three
times, which is G34's territory, and that `ABICHECK_CC_DISABLE` treating
`"0"` as "disable" is a real defect worth fixing on its own. Its warning
that environment variables must not become the new hidden CLI is adopted
as a standing constraint on every CONFIG demotion in this phase: a
demoted flag lands in `.abicheck.yml`, never in an undocumented variable.

**The acceptance bar for every slice above**, stated once (it is the
audit's, and it is stricter than "the old spelling exits 64"):

> The old user task still has a simple, supported invocation, with the
> same relevant evidence and a truthful result.

A test asserting only that a removed flag now errors proves deletion, not
simplification, and does not satisfy 7m–7r.

**Counts if 7m–7r land:** `compare` 47 → **40** (7m −4, 7n −3), which is
§4.5's target reached without touching the five `deferred` entries;
`dump` stays **18** (7n's `--debug-root` merge is a rename there, not a
removal — see that slice for the corrected arithmetic), with
`--build-target`
already gone with `scan` and §4.5's ≤16 target reachable only through one
further ruling, not through this audit.

**The whole-surface count, verified and re-derived.** 7i/7k only ever
counted `compare` and `dump`; the audit counts every command, and every one
of its twelve numbers reproduces exactly by Click introspection on this
branch (one logical option once, short aliases and `--no-` spellings
collapsed, `--help`/`--help-all` excluded). The "adopted" column is what
7m–7r above actually deliver — not the audit's own end state, which is
listed beside it so the gap is legible rather than averaged away:

| Command | Today | After 7m–7r | Audit's end state | The gap, named |
|---|---|---|---|---|
| `compare` | 41 (was 47; 7m and 7n both landed) | **40** | 25 | The five `deferred` rulings + the CONFIG demotions this plan declines or gates (`--dump-manifest`, `--include-system-declarations`, `--abi3`, `--severity-preset`, `--select-required`) + the `--environment` collapse (G42) |
| `dump` | 19 | **17** | 13 | `--dump-manifest`/`--include-system-declarations` to capture config, `--compression` (ruled a keep, 7k), `--environment` (G42) |
| `aggregate` | 5 (7m landed) | **4** | 4 | — (7q + 7m's shared export) |
| `deps tree` | 6 (7m landed) | **6** | 6 | — (7m) |
| `deps compare` | 7 (7m landed) | **7** | 7 | — (7m) |
| `project history` | 4 (7m landed) | **4** | 4 | — (7m) |
| `project plan` | 7 (7m landed) | **6** | 6 | — (7r + 7m) |
| `project validate` | 3 (7m landed) | **3** | 3 | — (7m); **7p landed**, so this row is now one command over all three schemas |
| `project validate-build` | 3 | **0** | folded | **done (7p)** |
| `project validate-use-cases` | 3 | **0** | folded | **done (7p)** |
| **Native total** | **92** (109 before 7p/7m/7n) | **87** | 68 | **19 options, all of them `compare`'s 15 and `dump`'s 4** || `compat check` / `compat dump` | 75 (22 hidden) / 19 (5 hidden) | frozen | frozen | ADR-068 D7 — excluded from every count |

Read the last column as this phase's actual position: **on six of the ten
native commands the audit's end state and ours are identical**, and the
entire 88-vs-68 difference is the contract/capture-config question this
plan has already gated (Phase 9, G42, P5) or ruled against with a
measurement. There is no third, unexamined bucket. `--format`/`-o`
converging on one export request is what moves every command except
`compare`/`dump`, which is why 7m is sequenced first: it is one mechanism
that closes eight rows.

**Six smaller audit items, ruled here rather than left unrecorded** —
these were in the audit and absent from the first pass of this section:

- **`--abi3` → declared floor activates the check.** Adopted *in part*: an
  `.abicheck.yml`-declared `abi3` floor should arm the applicable check
  without a second enable flag. The flag itself stays until that key
  exists, and the audit's own guard is adopted with it — **an arbitrary
  CPython extension must never be assumed to promise `abi3`**, so the
  check arms from a declaration, never from sniffing the binary. Owner:
  G26 (`--abi3`'s own workstream), not this phase.
- **`--dump-manifest`, `--include-system-declarations` → capture
  contract.** The audit argues both are properties of *how this project
  captures evidence*, against our `per_run_operand` rulings. Recorded as a
  live disagreement rather than settled: it is the same question 7p/7n
  cannot answer alone, and it needs the capture-specification work
  (below) to have a home at all. Neither ruling changes until then, and
  the plan states why on each: a multi-TU recipe and a dependency-surface
  selector both vary per invocation today because no capture spec exists
  to carry them.
- **`--bundle-facts-out`: the blocker is a task, not a property.** 7d
  ruled it a keep because "`dump` has no directory/package fan-out to hold
  this". The audit is right that this is a *missing implementation*, not a
  reason the flag belongs on `compare` forever. Re-recorded as a keep
  **with a named owner** — bundle capture in `dump` — so it reads as
  deferred-by-absence rather than settled. `rulings.py` keeps
  `per_run_operand` until that capture exists (a `deferred` ruling needs a
  blocker that is actually being built).
- **`deps`: the default `/` root must be visible in the resolved plan.**
  Adopted, and it is a correctness point rather than a CLI one: an
  unspecified `--sysroot`/`--old-root`/`--new-root` currently reads in the
  output like a deliberately chosen deployment environment. No flag
  changes; the resolved plan and report state that the root was defaulted.
  Owner: Phase 8's `ReportDocument` projection, which already renders the
  stack report.
- **`ABICHECK_CC_DISABLE` treats `"0"` as disable.** A real defect
  (any non-empty value disables capture), not a CLI-surface item. Fix
  separately with a regression test over the *class* — truthy/falsey
  string parsing across every `ABICHECK_*` boolean, not just this one
  variable — per AGENTS.md's bug-class rule.
- **One capture specification for `dump`, `abicheck-cc` and the Clang
  plugin.** The audit's strongest structural point outside `compare`:
  public roots, library identity and version are configured three times in
  three vocabularies (`-H`/`ABICHECK_CC_HEADERS`/`public-roots=`). Not
  this plan's to own — recorded as G34's, and as the prerequisite that
  makes the two `per_run_operand` rulings above answerable.


### Phase 8 — `deps` convergence (ADR-068 D6) — done

`StackVerdict` → `ExitDecision`: `stack_checker.exit_decision_for_stack_compare`/
`exit_decision_for_stack_tree` fold `deps`'s loadability/ABI-risk/
not-comparable axes through `policy.exit_decision.resolve_exit_decision`
(a new `ExitReason.LOADABILITY`/`ExitDecision.loadability_contribution`
axis, plus the pre-existing `NOT_COMPARABLE` reason reused as-is for
ADR-050 D2's profile/scope mismatch). `cli_stack.py`'s inline
`sys.exit(1)`/`sys.exit(4)`/`sys.exit(5)` chain is replaced by
`sys.exit(decision.code)`; every documented exit code is unchanged and
pinned by tests (`tests/test_cli_deps_stack.py`,
`TestExitDecisionForStackCompare`/`TestExitDecisionForStackTree` in
`tests/test_stack_checker_unit.py`). The stack JSON report is now a
`report.stack.compute_stack_report_document` → `ReportDocument` →
`render_json` projection (Markdown/HTML formatting is unchanged — out of
this phase's "internal convergence only" scope). `deps compare`'s
per-library ABI diff was already routed through `service.run_dump`/
`compare_snapshots` (ADR-037 D10.1); `TestRunAbiDiff.
test_success_path_routes_through_the_canonical_service_module` pins that it
still is. No user-visible flag or exit-code change. A later `compare`/`deps`
unification is recorded as **future direction**, not scoped here.

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
