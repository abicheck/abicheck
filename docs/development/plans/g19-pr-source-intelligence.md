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

## API & CLI surface (proposed)

Concrete definition of the new functionality. All additive; nothing here changes
existing `dump`/`compare`/`collect` signatures.

### CLI — `abicheck scan`

One orchestrator command (new `cli_scan.py`). Three modes via the existing
exit-code contract (ADR-009); `scan` with no source flags degrades to the
always-on tier over L0–L2.

```text
abicheck scan [OPTIONS]

Inputs
  --binary PATH                 library/artifact to scan (repeatable for bundles)
  --headers PATH                public header file or dir (repeatable)
  --public-header[-dir] PATH    provenance roots (ADR-015), passthrough to dump
  --compile-db PATH             compile_commands.json → unlocks L3 + targeted L4/L5
  --build-info PATH             build dir / pack dir (passthrough to collect)
  --sources PATH                source tree → inline L3/L4/L5
  --baseline REF|PATH           baseline snapshot/registry ref to diff against
  --inputs DIR                  ingest a Flow-2 abicheck_inputs/ pack (D5)

Scope / escalation
  --mode [pr|pr-deep|baseline|audit]      default pr (D9 asymmetry)
  --depth [auto|headers|build|source|graph|full]
                                          cap evidence depth on the L-axis
                                          (headers=L2, build=L3, source=L4,
                                          graph=L5, full=L4 full-scope);
                                          auto = risk-driven. No S0..S6 selector
                                          per ADR-035 D1.
  --changed-path PATH                     repeatable; seeds risk score + POI (D7)
  --since GITREF                          derive changed paths from a git range
  --budget DURATION   (e.g. 15m)          wall-clock ceiling
  --max-tus N                             targeted-AST TU cap
  --partial-ok / --no-partial-ok          default on; partial result is success
  --estimate                              dry-run: print per-level cost, do nothing
  --audit                                 single-release hygiene lint, no baseline (D8)

Policy / output
  --risk-rules PATH               override risk_rules block
  --crosscheck KEY=LEVEL          repeatable (info|warning|error) (D4/D6)
  --format [text|json|markdown|sarif|junit]
  --report PATH
  -o, --output PATH               write merged snapshot/result
```

Behaviour: `scan` resolves inputs → `dump`s L0–L2 → runs the always-on tier →
computes a risk score + POI set → escalates L3/L4/L5 within budget → (if
`--baseline`) `compare`s → emits one `ScanResult`. `--estimate` stops after the
cost probe; `--audit` runs intra-version (ignores `--baseline`).

Convenience subcommands (thin wrappers, same engine):
`abicheck scan estimate …` ≡ `--estimate`; `abicheck scan audit …` ≡ `--audit`.

### Python API — `abicheck/service.py`

```python
@dataclass(frozen=True)
class ScanRequest:
    binaries: list[Path]
    headers: list[Path] = field(default_factory=list)
    compile_db: Path | None = None
    sources: Path | None = None
    inputs_pack: Path | None = None
    baseline: str | Path | None = None
    mode: ScanMode = ScanMode.PR            # PR | PR_DEEP | BASELINE | AUDIT
    depth_cap: EvidenceDepth | None = None    # L-axis cap (see DataLayer); None = auto
    changed_paths: list[str] = field(default_factory=list)
    budget: Budget = Budget()               # total_timeout, max_tus, partial_ok
    risk_rules: RiskRules | None = None
    crosschecks: dict[str, Severity] = field(default_factory=dict)

@dataclass(frozen=True)
class CostEstimate:
    layer: EvidenceLayer               # L-axis: L2_HEADER..L5_SOURCE_GRAPH
    tus: int
    est_seconds: float
    cache_hit_rate: float
    note: str

@dataclass(frozen=True)
class LayerResult:
    layer: EvidenceLayer
    coverage: LayerCoverage            # reuse buildsource.model
    facts: int
    elapsed_s: float
    skipped_reason: str | None = None

@dataclass(frozen=True)
class ScanResult:
    diff: DiffResult | None            # None in --audit (no baseline)
    findings: list[DiffResult]         # incl. new crosscheck ChangeKinds (D4)
    layers: list[LayerResult]          # per-layer coverage (D3/D10)
    confidence: dict[str, str]         # provider-agreement matrix (§6.8)
    estimate: list[CostEstimate]       # projected vs. actual
    verdict: Verdict
    exit_code: int

def run_scan(req: ScanRequest) -> ScanResult: ...
def estimate_scan(req: ScanRequest) -> list[CostEstimate]: ...   # no scanning
def run_audit(req: ScanRequest) -> ScanResult: ...               # mode=AUDIT
```

### Per-layer provider protocol — `abicheck/buildsource/`

`EvidenceLayer` / `EvidenceDepth` are L-axis enums aligned to the existing
`buildsource.model.DataLayer` (L3_BUILD/L4_SOURCE_ABI/L5_SOURCE_GRAPH), plus an
`L2_HEADER` member for the always-on pre-scan tier — **no `S0..S6` type**, per
ADR-035 D1. Every provider (`pattern_scan`, `preprocessor`, L3 build, L4 replay,
L5 graph, `crosscheck`) implements one interface, modelled on the ADR-032
`DataExtractor`, so layers are independently runnable (ADR-033 D1) and
external/build-emitted providers (D5) drop in unchanged:

```python
class LayerProvider(Protocol):
    layer: EvidenceLayer
    def capabilities(self) -> ProviderCapabilities: ...
    def estimate(self, ctx: ScanContext) -> CostEstimate: ...
    def run(self, ctx: ScanContext, poi: PointsOfInterest) -> LevelFacts: ...

@dataclass(frozen=True)
class ScanContext:                      # shared, read-only inputs to every level
    snapshot: AbiSnapshot               # L0–L2 already parsed
    compile_db: Path | None
    changed_paths: list[str]
    budget: Budget
    cache: SourceFactCache
```

`PointsOfInterest` (new `buildsource/poi.py`) is the D7 work-list — a set of
`(symbol|entity|tu, reason)` computed from L0/L1/L2 deltas + risk score, consumed
by `source_replay` scope selection and `crosscheck`.

### MCP tools — `abicheck/mcp_server.py`

Three agent tools wrapping the service API (ADR-021b security model):
`abicheck_scan(request) -> ScanResult`, `abicheck_audit(request) -> ScanResult`,
`abicheck_estimate(request) -> [CostEstimate]`. Same `ScanRequest`/`ScanResult`
schema as the Python API — one contract, three front-ends (CLI, MCP, library).

### Config — `.abicheck.yml` (additive)

```yaml
source:
  budgets: { total_timeout: 15m, max_targeted_tus: 80, partial_ok: true }
  layers:  { headers: always, build: always,     # L2 pre-scan, L3 build
             source: triggered, graph: budgeted } # L4 replay, L5 graph
risk_rules:
  public_headers: { paths: ["include/**","public/**"], weight: 50 }
  build_abi_flags:{ paths: ["CMakeLists.txt","cmake/**","BUILD"], weight: 40 }
  docs_only:      { paths: ["docs/**","*.md"], weight: -100 }
crosschecks:
  exported_not_public: warning
  header_build_context_mismatch: error
  private_header_leak: warning
  odr_type_variant: error
```

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
