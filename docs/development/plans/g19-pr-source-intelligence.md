# G19 — PR-Tier Source Intelligence & Cross-Source Validation

**ADR:** [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md)
**Type:** Initiative plan (multi-phase); tracked by six `planned`
`usecase-registry.yaml` entries under gap G19 (see *Use-case tracking*)
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
- **G19.3** Deterministic `scan` command: the level is the pinned `--mode` preset
  or an explicit `--source-method`/`--depth`; the numeric risk score selects depth
  **only** under `--source-method auto` (opt-in), and `--budget` is a failure guard
  on the chosen level (never shrinks scope). One coverage/confidence-annotated
  report; partial results are first-class (ADR-035 D3).
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
- **DONE** — New `abicheck/buildsource/pattern_scan.py`: stdlib-regex scanner over
  changed + public files for the ADR-035 D2 construct list; emits normalized
  advisory facts + per-kind escalation triggers (`PatternFact`/
  `PatternScanResult`/`EscalationTrigger`), with mandatory coverage reporting and
  no compile DB / compiler. Tree-sitter is a pluggable later backend.
  Tests: `tests/test_pattern_scan.py`.
- **TODO** — Extend `buildsource/include_graph.py`: per-TU ABI-macro-value capture
  and private/generated-header-leak detection when a compile DB is present.

### Phase 2 — Cross-source validation engine (G19.2)
- **DONE** — New `abicheck/buildsource/crosscheck.py` (`run_crosschecks`) consumes
  one merged snapshot and diffs its evidence sources against each other
  intra-version. Adds the four ADR-035 D4 `ChangeKind`s — `exported_not_public`
  (RISK), `public_not_exported` (RISK), `header_build_context_mismatch`
  (API_BREAK), `private_header_leak` (RISK) — partitioned per the four-step
  procedure, with per-check coverage rows and the §6.8 provider-agreement matrix.
  Every check skips (never false-positives) when its evidence is absent and is
  never BREAKING (authority rule). Tests: `tests/test_crosscheck.py`.
- **TODO** — `odr_type_variant` and `public_to_internal_dependency` checks;
  wiring into the Phase-3 `scan`/`audit` orchestrator + `crosschecks:` severity
  config; FP-rate-gate corpus cases before any check is promoted to gate.

### Phase 3 — Deterministic `scan` orchestrator (G19.3)
- **DONE** — New `abicheck/cli_scan.py` (sibling-module registration per
  `/CLAUDE.md`): `scan` classifies the changed paths → runs the always-on tier
  (compiler-free `pattern_scan` S3 + intra-version `crosscheck` D4) → runs the
  **pinned** level (`--mode` preset or explicit `--source-method`/`--depth`,
  resolved by `buildsource/scan_levels.py`) by collecting L3/L4/L5 inline at the
  matching ADR-033 D2 evidence mode → (if `--baseline`) `compare`s, folding the
  cross-source findings in as `extra_changes` → emits one coverage-annotated
  report (text/JSON) stating, per L-layer/S-method, collected vs. skipped. Modes
  `pr`/`pr-deep`/`baseline`/`audit`; `--audit` runs intra-version (no baseline).
  `--budget` is a failure guard (exit 5 on overflow, never shrinks scope).
- **DONE** — `buildsource/risk.py` is the scored generalization of
  `source_replay.recommend_collect_mode()`: a tunable `risk_rules` profile
  (`--risk-rules` YAML) scores the changed paths (strongest-signal-present, D3
  ordering) → an S-method, used **only** for `--source-method auto` (opt-in),
  never to change a pinned/deterministic CI run.
  Tests: `tests/test_risk.py`, `tests/test_scan_levels.py`, `tests/test_cli_scan.py`.
- **DONE** — the baseline compare folds embedded L3/L4/L5 evidence in via
  `prepare_embedded_build_source` (the same path `compare` uses), and an
  explicit `--changed-path`/`--since` set is threaded through
  `embed_build_source` → `collect_inline_pack` → `run_source_replay` so a
  `source-changed` collection narrows to the affected TUs (D7 focusing for the
  supplied changed set) instead of replaying the whole target.
- **TODO (Phase 3b/3c)** — the *automatic* POI work-list (`buildsource/poi.py`)
  computed from L0/L1/L2 deltas (vs. the explicit changed-path set already
  threaded), the typed `ScanRequest`/`ScanResult` + provider protocol +
  `--estimate` in `service.py`, MCP `scan`/`audit`/`estimate` tools, and the
  `surface-report`-reuse single-release audit catalog. `scan --audit` already
  runs the D2+D4 intra-version pass.

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
exit-code contract (ADR-009); `scan` with no source flags degrades to L0/L1 +
the always-on lexical tier (L2 header-AST added only when castxml is present,
else reported as skipped — not part of the no-compiler path).

**The common case is four flags** — current build (binary + headers + sources)
vs a baseline dump:

```bash
abicheck scan --binary new/libfoo.so --headers new/include --sources . \
              --baseline old/libfoo.abi.json
```

That is the whole basic flow. abicheck **auto-discovers** `compile_commands.json`
inside `--sources` (or `--build-info`); if it finds one the source tier is
high-fidelity (real flags/macros), if not it scans with defaults. Everything
below is optional tuning with sane defaults — for CI scaling and large repos,
not the basic flow.

**Core flags**

```text
  --binary PATH        library/artifact to scan (repeatable for bundles)
  --headers PATH       public header file or dir (repeatable)
  --sources PATH       source tree (compile DB auto-discovered within it)
  --baseline PATH|REF  previous build's dump/lib (path), OR a baseline-registry
                       ref like libfoo@1.5 (ADR-022) — one flag, union value
```

**Optional — inputs**

```text
  --public-header[-dir] PATH  provenance roots (ADR-015), passthrough to dump
  --compile-db PATH           explicit compile_commands.json (only if not under --sources)
  --build-info PATH           build dir / pack dir instead of a raw source tree
  --inputs DIR                ingest a Flow-2 abicheck_inputs/ pack (D5)
```

**Optional — scope / escalation (defaults are fine for small/medium repos)**

```text
  --mode [pr|pr-deep|baseline|audit]   fixed preset of (L,S); default pr (D9)
  --source-method [s0|s1|s2|s3|s4|s5|s6|auto]
                                       exact source-analysis level to reach;
                                       deterministic. auto = risk-driven (opt-in,
                                       local/dev). See the level table below.
  --depth [headers|build|source|full|graph]
                                       coarse L-axis selector → representative S
                                       (lossy: can't reach S2/S3). --source-method
                                       is precise and wins if both given.
  --since GITREF                       focus the scan on files changed vs a git
                                       ref (e.g. origin/main); else scans broadly
  --changed-path PATH                  same focusing, listed by hand (repeatable)
  --budget DURATION (e.g. 15m)         optional guard; FAILS on overflow, never
                                       silently shrinks scope (avoid for gating CI)
  --max-tus N                          targeted-AST TU cap
  --partial-ok / --no-partial-ok       default on; a partial scan (missing tool,
                                       skipped layer) is success. Does NOT cover
                                       --budget overflow, which always fails.
  --estimate                           dry-run: print per-layer cost, scan nothing
  --audit                              single-build hygiene lint, no baseline (D8)
```

**Optional — policy / output**

```text
  --risk-rules PATH        override the risk_rules block
  --crosscheck KEY=LEVEL   repeatable (info|warning|error) (D4/D6)
  --format [text|json|markdown|sarif|junit]
  --report PATH
  -o, --output PATH        write the merged snapshot/result
```

Behaviour (deterministic by default): `scan` resolves inputs → `dump`s L0–L2 →
runs the always-on tier → runs the **pinned** level (from `--mode`'s preset or an
explicit `--source-method`/`--depth`), POI-focused (D7) → (if `--baseline`)
`compare`s → emits one `ScanResult`. Risk scoring escalates the level **only** when
`--source-method auto` is set (opt-in); a `--budget`, if set, is a failure guard
that errors on overflow and never shrinks the pinned scope. `--estimate` stops
after the cost probe; `--audit` runs intra-version (ignores `--baseline`).

Convenience subcommands (thin wrappers, same engine):
`abicheck scan estimate …` ≡ `--estimate`; `abicheck scan audit …` ≡ `--audit`.

### The two axes: L-layers (evidence) and S-methods (source analysis)

Per ADR-035 D1 these are orthogonal, and **both are user-selectable**. **L** =
where the evidence lives + authority (`--depth`). **S** = the six cost-ordered
*source-analysis methods* that produce L3–L5 evidence (`--source-method`), also
the granularity at which coverage is reported. Source analysis is genuinely six
graduated techniques, not one AST step; each S-method runs and lands in an L-layer:

| Provider | S-method | How collected | Compiler? | Produces (L) | Default PR | Scope |
|---|---|---|---|---|---|---|
| classify (D0/D3) | S0 | git diff → risk tags/score | no | — (drives focus) | always | changed |
| pre-scan patterns (D2) | S3 | regex/Tree-sitter over changed+public | no | pre-scan → L2/L5 | always | changed+public |
| L2 headers | — | castxml over public headers | castxml for header-AST | L2 | when castxml present (else skipped, reported) | public surface |
| L1 debug info | — | DWARF / PDB / dSYM from binary | no | L1 | when debug info present | binary artifact |
| L3 build | S1 | parse `compile_commands.json` / CMake / Ninja / Bazel | no | L3 | always (cheap) | whole build |
| preprocessor (D2) | S2 | `clang -E` macros / `-MM` includes | (cpp) | L3→L5 | when DB present | changed+public |
| L5 graph (structural) | S2 fold | fold L3 → target/file/option nodes | no | L5 | when L3 ran | whole build |
| L4 source | S5 / S6 | clang/castxml parse actual TUs | yes | L4 | triggered (S5); baseline (S6) | **POI-scoped** (S5) / all (S6) |
| L5 graph (semantic) | S4 / S5 | clangd-index / call `clang -ast-dump` / Kythe/CodeQL | yes | L5 | pinned scope or skipped for missing tools | POI / changed |

The L5 graph is **not** fully built by default — only the cheap S2 structural fold
is. When `--depth graph` or an S4/S5 method is pinned, semantic edges run to the
pinned scope or fail on budget overflow; budgets never reduce the graph scope. S6
(full AST) is baseline/manual, never the default PR.

**Reporting is mandatory and explicit (4a):** every run — not just partial ones —
prints a table stating, for each layer **and the S-method that produced it when
there is one**,
`collected` / `skipped (reason)` + how much (TUs/files) + cache-hit rate, plus the
confidence per evidence source. A reader always sees exactly which source-analysis
depth (S0…S6) was reached and into which L-layer it landed; never a bare
"source scan ran".

### Selecting the level: `--source-method` (S) and/or `--depth` (L)

The level is an **explicit, exact target you choose** — not a ceiling, not
auto-picked. Two related knobs, **not 1:1**:

```text
  --source-method [s0|s1|s2|s3|s4|s5|s6]   precise: run source analysis AT this method
  --depth [headers|build|source|full|graph]   coarse: pick by L-layer (maps to a
                                               representative S; can't express every S)
```

`--source-method sN` is the **precise** knob — *reach exactly method N* for the
in-scope files, deterministic (same inputs → same scan). It is not a "max that may
do less"; it always reaches N (only a genuinely missing tool, e.g. no clang, makes
it fall short, and that is **reported**, not silently downgraded).

`--depth` is a **coarse convenience** for users who think in evidence layers. The
S→L map is **lossy**, so `--depth` resolves to a *representative* S per layer and
**cannot reach some methods**: `--depth build` runs S1 (not S2 — preprocessor
macros/includes), and S3 (lexical pre-scan, always on anyway) has no `--depth`
form. To pin S2 or force S3 you must use `--source-method`.

| If you pass… | runs S | populates (L) | precise S only via `--source-method`? |
|---|---|---|---|
| `--depth headers` | — | L2 | — |
| `--depth build` | S1 | L3 | S2 needs `--source-method s2` |
| `--depth graph` | S4 | L5 edges | — |
| `--depth source` | S5 | L4 scoped + L5 edges | — |
| `--depth full` | S6 | L4 full-scope | — |
| `--source-method s0..s6` | exactly that | per S | this *is* the precise knob |

`--source-method` is authoritative: if both are given, it wins (it can express
things `--depth` cannot). There is no `min()`/cap behaviour.

### Determinism for CI (no time-bound, no auto by default)

For reproducible gating, the scan scope is fixed by the level you pick, not by the
machine or the clock:

- **Default per `--mode` is a fixed preset**, not risk-varying: `pr` = S1 + S3
  always-on + targeted S5; `baseline` = S6. **Same commit pair + same toolchain →
  identical scan.** Determinism is toolchain-relative: S5/S6 need a compiler (and
  castxml/clang), so a missing tool is a **reported skip**, never a silent
  downgrade. For reproducible gates, pin the toolchain (fixed CI image) as well as
  the level — otherwise a host without the compiler reaches a shallower depth (and
  the report says so).
- **`auto`** (risk-driven escalation, D3) is **opt-in** (`--source-method auto`),
  for local/dev only — never the silent CI default.
- **`--budget`** is optional and, on overflow, **fails** (nonzero exit); it never
  silently shrinks scope. For gating CI, **pin a level — do not use a time bound.**
- POI focusing (D7) only selects *which* in-scope TUs get the chosen method; for a
  fixed diff it is deterministic, so it does not affect CI consistency.

`.abicheck.yml` mirror: `source.method: s5` (exact level; `auto` only if opted in)
and `source.depth: graph` (coarse L-axis selector).

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
    mode: ScanMode = ScanMode.PR            # PR | PR_DEEP | BASELINE | AUDIT (fixed preset)
    # source_method is precise (S-axis); depth is a coarse L-axis selector (lossy
    # S→L: can't express S2/S3). source_method wins if both set. AUTO = opt-in.
    source_method: SourceMethod | None = None   # S0..S6 | AUTO; None = use mode preset
    depth: EvidenceLayer | None = None          # coarse L target; None = use mode preset
    changed_paths: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)  # total_timeout, max_tus, partial_ok
    risk_rules: RiskRules | None = None
    crosschecks: dict[str, Severity] = field(default_factory=dict)

@dataclass(frozen=True)
class CostEstimate:
    method: SourceMethod | None        # S-axis: S0..S6; None for intrinsic L0-L2
    layer: EvidenceLayer               # L-axis it populates: L0_BINARY..L5_SOURCE_GRAPH
    tus: int
    est_seconds: float
    cache_hit_rate: float
    note: str

@dataclass(frozen=True)
class LayerResult:
    method: SourceMethod | None        # which S-method ran; None for intrinsic L0-L2
    layer: EvidenceLayer               # which L-layer it populated
    coverage: LayerCoverage            # reuse buildsource.model
    facts: int
    elapsed_s: float
    skipped_reason: str | None = None

@dataclass(frozen=True)
class ScanResult:
    diff: DiffResult | None            # whole comparison; None in --audit (no baseline)
    findings: list[Change]             # individual Change objects, incl. new
                                       # crosscheck ChangeKinds (D4); == diff.changes
                                       # when a baseline is given
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

Two enums, one per axis (ADR-035 D1): `EvidenceLayer` is the L-axis and includes
all reportable layers (`L0_BINARY`, `L1_DEBUG`, `L2_HEADER`, `L3_BUILD`,
`L4_SOURCE_ABI`, `L5_SOURCE_GRAPH`). Its L3-L5 values align with
`buildsource.model.DataLayer`, while L0/L1/L2 are intrinsic artifact/header rows
needed for mandatory coverage reporting. **`SourceMethod` is the S-axis**
(`S0..S6` + `AUTO`) — a *distinct* enum, not banned and not collapsed onto
`EvidenceLayer` (the S→L map is lossy: S1≠S2 both touch L3, S3 has no L). A
provider declares both the method it implements and the layer it populates, so a
request that pins `source_method=S2` runs the right provider. Intrinsic
artifact/header providers set `method = None` because L0/L1/L2 evidence is not
produced by an S-method:

```python
class LayerProvider(Protocol):
    method: SourceMethod | None          # S-axis method, or None for intrinsic L0-L2
    layer: EvidenceLayer                 # L-axis: which evidence layer it populates
    def capabilities(self) -> ProviderCapabilities: ...
    def estimate(self, ctx: ScanContext) -> CostEstimate: ...
    def run(self, ctx: ScanContext, poi: PointsOfInterest) -> LayerFacts: ...

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
  method: s5     # exact S-axis selector; may be auto only when opt-in is intended
  depth: source  # coarse L-axis selector; method wins if both are set
  budgets: { total_timeout: 15m, max_targeted_tus: 80, partial_ok: true }
  layers:  { headers: always, build: always,     # L2 pre-scan, L3 build
             source: triggered, graph: triggered } # L4 replay, L5 graph; budget overflow fails
risk_rules:
  public_headers: { paths: ["include/**","public/**"], weight: 50 }
  build_abi_flags: { paths: ["CMakeLists.txt","cmake/**","BUILD"], weight: 40 }
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
