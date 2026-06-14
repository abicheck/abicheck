# G19 — PR-Tier Source Intelligence & Cross-Source Validation

**ADR:** [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md)
**Type:** Initiative plan (not a `usecase-registry.yaml` gap; multi-phase)
**Effort:** XL (phased) · **Risk:** medium — new `ChangeKind`s and an optional
compiler-version-sensitive plugin; mitigated by keeping everything advisory and
behind the portable replay path.

## Problem

abicheck's optional build/source evidence stack (L3/L4/L5, ADR-028…033) already
covers compile-DB scanning, scoped per-TU AST replay, and the source graph —
but it has **no cheap always-on PR tier**, **no cross-source validation
findings**, and **no build-integrated fact ingestion or unified `scan` UX**. A
field proposal re-derived much of the existing stack as an `S0..S6` ladder; the
genuinely-new value is exactly those three gaps. ADR-035 records the decision to
close them as *extensions of the existing L-layers*, preserving the ADR-028 D3
authority rule (L0–L2 stay authoritative for `BREAKING`).

## Goal & acceptance criteria

- **G19.1** Compiler-free PR pre-scan: changed+public files scanned for ABI-risk
  patterns, emitting advisory facts + escalation triggers, no compile DB needed.
- **G19.2** Cross-source validation: at least `exported_not_public`,
  `public_not_exported`, `header_build_context_mismatch`, `private_header_leak`
  surfaced as correctly-partitioned `RISK`/`API_BREAK` `ChangeKind`s with
  provider-corroborated confidence (ADR-035 D4).
- **G19.3** Risk-scored escalation + `scan` command: a numeric score selects
  evidence depth within a budget; one coverage/confidence-annotated report;
  partial results are first-class (ADR-035 D3).
- **G19.4** Build-integrated extraction: a documented dump/facts artifact
  protocol (Flow 2) ingestible via `merge`, plus a Clang-plugin and
  compiler-wrapper provider under the ADR-032 model, normalized facts canonical
  (ADR-035 D5).
- **G19.5** Evidence-directed focusing: a POI set computed from L0/L1/L2 deltas +
  risk score drives `source_replay` scope and the cross-check work-list, so L4/L5
  cost falls only on flagged entities (ADR-035 D7).
- **G19.6** Single-release audit: `scan --audit` / `surface-report` emit the
  intra-version hygiene catalog with no baseline (ADR-035 D8).
- **G19.7** Programmatic API + estimate: typed `ScanRequest`/`ScanResult` in
  `service.py`, a uniform per-level provider protocol, and `scan --estimate` /
  `service.estimate_scan()` returning per-level projected cost for the project
  (ADR-035 D9/D10); MCP tools for `scan`/`audit`/`estimate`.
- **Acceptance gate:** every new `ChangeKind` passes the import-time partition
  assertion, the AI-readiness `changekind-*` checks, and earns FP-rate-gate
  corpus cases before it is allowed to gate.

## Design (phases)

### Phase 1 — Compiler-free PR pre-scan (G19.1)
- New `abicheck/buildsource/pattern_scan.py`: stdlib-regex scanner over changed +
  public files for the ADR-035 D2 construct list; emits normalized advisory
  facts + escalation triggers. Tree-sitter is a pluggable later backend.
- Extend `buildsource/include_graph.py`: per-TU ABI-macro-value capture and
  private/generated-header-leak detection when a compile DB is present.

### Phase 2 — Cross-source validation engine (G19.2)
- New `abicheck/buildsource/crosscheck.py` consuming one merged snapshot.
- Add the ADR-035 D4 `ChangeKind`s (RISK/API_BREAK only) following the four-step
  procedure in `/CLAUDE.md`; record corroborating providers → `LayerConfidence`.

### Phase 3 — Risk score + budgeted `scan` orchestrator (G19.3)
- Promote `source_replay.recommend_collect_mode()` to a scored function driven by
  a `risk_rules` config block; thresholds map to existing collect-modes.
- New `abicheck/cli_scan.py` (sibling-module registration per `/CLAUDE.md`):
  classify → always-on tier → escalate within budget → single coverage report.

### Phase 3b — Evidence-directed focusing + API/estimate (G19.5, G19.7)
- POI builder: from L0/L1/L2 deltas + risk score, produce a work-list consumed by
  `source_replay` scope selection and `crosscheck.py` (reverse of the
  `explain-finding` localization walk).
- Typed `ScanRequest`/`ScanResult` + uniform per-level provider protocol
  (`capabilities`/`estimate`/`run(ctx, poi)`) in `service.py`; `estimate_scan()`
  and `scan --estimate`.

### Phase 3c — Single-release audit (G19.6)
- `scan --audit` (and `surface-report` reuse): run D2 + D4 intra-version, emit the
  hygiene catalog with no baseline; severity-mapped lint + exit code.

### Phase 4 — Build-integrated extraction (G19.4)
- Define the `abicheck_inputs/` dump/facts artifact protocol; ingest via existing
  `merge`. Normalized `source_facts/*.jsonl` preferred; raw AST debug-only.
- Clang-plugin + `abicheck-cc` wrapper as ADR-032 manifest extractors. GCC/MSVC
  dump fallbacks documented, not required.

## Files & surfaces

- New: `buildsource/pattern_scan.py`, `buildsource/crosscheck.py`,
  `buildsource/poi.py` (evidence-directed work-list), `cli_scan.py`; new
  `ChangeKind`s in `checker_policy.py`.
- Extend: `service.py` (`ScanRequest`/`ScanResult`/`run_scan`/`estimate_scan` +
  provider protocol), `buildsource/include_graph.py`,
  `buildsource/source_replay.py` (consume POI), `buildsource/inline.py` (config),
  `cli_surface.py` (audit reuse), `mcp_server.py` (`scan`/`audit`/`estimate`
  tools), `reporter.py` (coverage/confidence/estimate block),
  `pyproject.toml` (`disallow_untyped_decorators` for `cli_scan`),
  `IMPORT_CYCLE_ALLOWLIST` if `scan` registration flags a cycle.
- Tracking: new `usecase-registry.yaml` entries (see *Use-case tracking* below),
  their `examples/case*/` + `ground_truth.json` rows, and a scorecard row in
  `docs/development/usecase-coverage-evaluation.md`.

## Tests

- Unit: pattern scanner fixtures (each construct), macro-divergence and
  header-leak detection, each cross-check finding, risk-score thresholds,
  `scan` partial-coverage reporting.
- FP-rate gate (`scripts/check_fp_rate.py`): internal-noise pairs stay
  non-breaking; real cross-check breaks stay flagged — before any kind gates.
- Pre-captured artifact-protocol fixture round-trips through `merge` (non-
  executing, no compiler in CI), mirroring the ADR-028 D6 pattern used by G18.

## Example fixtures

- A `case*/` pair exercising `exported_not_public` and `header_build_context_
  mismatch`, with `README.md` + `ground_truth.json` entries (examples gate).

## Effort & risk

- Phase 1–2: M each, high value/cost. Phase 3: M. Phase 4: L (plugin maintenance
  + protocol design). Recommended order is 1 → 2 → 3 → 4; Phase 4's plugin is an
  optimization and can trail.

## Use-case tracking

Six new `planned` entries are registered in `usecase-registry.yaml` under gap
**G19** (with a G19 row added to `usecase-coverage-evaluation.md`). They flip to
`partial`/`complete` as each phase lands, citing the new modules/tests/examples
as evidence:

| Use case | Axis | ADR | Phase |
|---|---|---|---|
| `UC-WORKFLOW-pr-source-tier` | workflow | D2/D3 | 1, 3 |
| `UC-CHANGE-crosscheck-hygiene` | change_class | D4 | 2 |
| `UC-WORKFLOW-single-release-audit` | workflow | D8 | 3c |
| `UC-WORKFLOW-evidence-directed-scope` | workflow | D7 | 3b |
| `UC-TC-build-emitted-facts` | toolchain | D5 | 4 |
| `UC-REPORTING-scan-coverage-estimate` | reporting | D9/D10 | 3, 3b |

## Out of scope

- Re-numbering or replacing L0–L5; clangd-index integration; making any
  source/cross-check finding authoritative for a `BREAKING` verdict.
