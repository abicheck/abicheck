# ADR-033: CI Rollout, Performance, Caching, and Validation Strategy

**Date:** 2026-06-09
**Status:** Accepted / Implemented (2026-06-12). **Amended 2026-06-12** (ADR-028 source-tree model) — see Amendment below. Implementation status below.
**Decision maker:** Nikolay Petrov

---

## Context

Source/build-aware analysis (ADR-028..032) can easily become too expensive
for normal CI if treated as all-or-nothing. abicheck's target audience
includes projects that want ABI/API checks in day-to-day development.
The rollout must prioritize:

- fast post-build scans;
- no mandatory instrumented rebuild;
- deterministic caching;
- useful partial analysis in PRs;
- full/deep analysis only for baselines, nightly jobs, or release gates;
- honest coverage reporting when evidence is partial.

---

## Decision

### D1. Implement as a complexity ladder

| Phase | Name | Required inputs | Output | CI suitability |
|---|---|---|---|---|
| 0 | Current artifact compare | binary + optional debug + headers | ABI snapshot/report | Existing default |
| 1 | Build context capture | compile DB and/or CMake/Ninja/Bazel query output | build evidence, build-option diff | PR/default feasible |
| 2 | Target ownership and source localization | build graph + header/debug locations | finding-to-target/source/build-file mapping | PR/default feasible |
| 3 | Source ABI replay | sources + compile contexts + public headers | source/API findings | PR `changed` mode, baseline `target` mode |
| 4 | Graph summary | `BuildEvidence` + L2/L4 facts | graph-to-graph summary diff | PR if scoped; nightly preferred |
| 5 | External graph backend | Kythe/CodeQL/full graph DB | deep impact/call/reference queries | Nightly/release only |
| 6 | Instrumented compiler/build plugins | compiler flags/passes/wrappers | rich compiler-emitted graphs | Opt-in specialist mode |

Each phase must be usable independently. Adopting Phase 1 must not require
Phase 3 or Phase 5.

### D2. CI modes

```yaml
abicheck:
  evidence:
    mode: off | build | source-changed | source-target | graph-summary | graph-full
    strict: false
    collect_raw: false
    redact: true
```

| Mode | Behavior | Use |
|---|---|---|
| `off` | Current abicheck only | Existing users, fastest path |
| `build` | Collect build context and compare ABI-relevant flags | Default extension MVP |
| `source-changed` | Build context + source ABI replay only for changed public headers/TUs | PR mode |
| `source-target` | Source ABI replay for the targets producing compared binaries | Release baseline |
| `graph-summary` | Compact graph facts and source/binary mapping | Nightly, or PR on smaller projects |
| `graph-full` | External graph backend | Deep/nightly/release investigation |

These CI modes and the ADR-030 D7 source-replay scopes are two different
knobs: the CI mode selects which evidence layers run, and internally sets
the replay scope. The mapping is:

| CI evidence mode | Layers engaged | ADR-030 replay scope |
|---|---|---|
| `off` | none | `off` |
| `build` | L3 | `off` |
| `source-changed` | L3 + L4 | `changed` |
| `source-target` | L3 + L4 | `target` |
| `graph-summary` | L3 + L4 + L5 summary | `changed` (PR) or `target` (baseline) |
| `graph-full` | L3 + L4 + L5 full | `target` or `full` |

The remaining ADR-030 scopes (`headers-only`, `full`) have no dedicated CI
mode; they stay reachable through explicit replay configuration for users
who need them.

### D3. PR mode is trigger/localizer, not authority

In PR workflows (ADR-025's model, extended with evidence):

1. Use changed paths to decide whether to run or scope evidence collection.
2. Always fail open: if classification is uncertain, run the full artifact
   comparison.
3. Build-file-only changes trigger at least Phase 1 build-context
   comparison.
4. Source localization maps artifact findings to changed hunks when
   provenance exists.
5. Source-only prechecks may produce early `API_BREAK`/risk signals, but
   artifact comparison remains the authoritative gate for shipped ABI
   (ADR-028 D3).

### D4. Baseline registry stores evidence packs optionally

Extend baseline registry entries (ADR-022):

```json
{
  "snapshot": "libfoo.abi.json.zst",
  "evidence_pack": "libfoo.evidence.tar.zst",
  "evidence_hash": "sha256:...",
  "coverage": {
    "build_context": true,
    "source_abi": "target",
    "graph": "summary"
  }
}
```

Storage policy:

| Artifact | Store by default? | Notes |
|---|---|---|
| ABI snapshot | yes | Existing behavior |
| `BuildEvidence` normalized JSON | yes, when collected | Small enough for baselines |
| Source ABI linked surface | yes, when collected | Store compressed |
| Per-TU source ABI dumps | optional/cache | Can be large; useful for incremental diff |
| Graph summary | yes, when collected | Compact by design |
| Full graph DB / `.kzip` / CodeQL DB | no by default | Large; store only in audit/deep mode |
| Raw artifacts | no by default in public CI | Enable in audit or private baseline mode (ADR-032 D7/D9) |

### D5. Deterministic caching is required

| Cache | Key includes | Output |
|---|---|---|
| `BuildEvidence` cache | build-system raw inputs, compile DB hash, adapter version | normalized build evidence |
| Header/castxml cache | header content, transitive include metadata, build context | existing L2 output |
| `SourceAbiTu` cache | source/header content, compile context, extractor version | per-TU source ABI dump |
| Source ABI linked cache | TU dump hashes, binary exported symbols, public header set | linked source ABI surface |
| Graph summary cache | `BuildEvidence` hash, L2/L4 hashes, extractor version | graph summary |

Cache invalidation must prefer false misses over false hits. A stale cache
can produce incorrect ABI decisions.

### D6. Performance targets are tiered, not absolute

Project size varies; define relative expectations:

| Operation | Expected cost profile |
|---|---|
| Phase 1 build-context normalization | low; mostly JSON/proto/text parsing |
| Build option diff | very low |
| CMake/Ninja query adapters | low when the build tree exists |
| Bazel `cquery`/`aquery` | medium; depends on workspace analysis cost |
| Source replay `changed` | medium, bounded by changed files/TUs |
| Source replay `target`/`full` | high without cache; acceptable for baseline/nightly |
| Graph summary | medium; bounded by the selected target |
| Full Kythe/CodeQL | high; not a PR default |

Commands must print coverage and timing summaries so users can tune mode
selection.

### D7. Evidence-aware policy controls

Extend policy profiles (ADR-010 / policy files):

```yaml
policy:
  source_only_findings: warn        # ignore | warn | fail-api | fail-release
  build_context_drift: warn         # ignore | warn | fail-on-abi-relevant
  graph_risk_findings: warn         # ignore | warn | fail
  require_evidence:
    build_context: false
    source_abi: false
    graph_summary: false
```

Default policy:

- artifact-proven ABI breaks keep current behavior (verdicts and exit
  codes unchanged, ADR-009);
- a source-only API break is reported with an `API_BREAK` verdict, and
  policy decides whether it fails a release;
- build-context drift is a `COMPATIBLE_WITH_RISK` signal unless artifact
  changes confirm a break;
- graph-only risks are informational/warnings by default.

### D8. Validation strategy

Validation must prove two things:

1. evidence collection improves correctness and false-positive handling;
2. evidence collection never hides real artifact-backed breaks silently.

The second property extends the existing FP-rate gate philosophy
(labelled corpus, zero-FP/zero-FN baselines) to evidence-assisted runs.

Test suites:

| Suite | Purpose |
|---|---|
| Build flag drift corpus | `-D`, `-std`, packing, visibility, sysroot, stdlib ABI toggles |
| Public/private surface corpus | surface ledger and leak guard from ADR-024 |
| Source-only API corpus | macros, default args, inline/template/constexpr changes (extends ADR-026 fixtures) |
| Generated file corpus | generated headers and missing dependencies |
| Cross-tool parity | compare selected outcomes with libabigail, ABI Dumper, Android header checker where feasible (ADR-019) |
| Cross-build-system fixtures | CMake/Ninja/Bazel/Make minimal projects |
| Large-project performance fixtures | cache hit/miss, changed-only scan, target scan |
| Security/redaction tests | command-line secrets and path redaction |

### D9. Metrics

Track in CI and internal benchmarks:

```text
coverage.build_context.present
coverage.source_abi.mode
coverage.graph.mode
extractor.duration_seconds
extractor.cache_hit_rate
findings.artifact_backed.count
findings.source_only.count
findings.build_context_drift.count
findings.demoted_by_surface.count
findings.suppressed_with_reason.count
false_positive_delta_vs_baseline
```

The most important product metric is not the number of findings; it is
reviewable signal quality: fewer noisy non-public/private/build-context
artifacts, more explainable true breaks.

### D10. Documentation and UX rollout

Documentation presents this as optional depth levels:

```text
Level 0: binary only
Level 1: binary + debug
Level 2: binary + debug + public headers
Level 3: + build context
Level 4: + source ABI replay
Level 5: + graph summary / external graph backend
```

After every run, users should be able to answer:

- What did abicheck compare?
- What evidence was missing?
- Which findings are artifact-backed?
- Which findings are source-only or build-context-only?
- Did any suppression or surface scoping demote a finding?
- Which build option or source file likely caused a change?

---

## Consequences

### Positive

- Lets projects adopt the extension gradually.
- Keeps PR scans fast and predictable.
- Makes baseline/deep modes available without burdening every run.
- Creates measurable validation gates before any source/build feature
  becomes a default.
- Preserves existing CI semantics unless users enable new evidence modes.

### Negative / risks

- Multiple modes increase documentation and support burden.
- Partial evidence can confuse users unless reports are clear.
- Full graph/source modes may be expensive on monorepos.
- Baseline storage can grow if raw artifacts and per-TU dumps are retained.

---

## Implementation plan

| Phase | Scope | Output |
|---|---|---|
| 1 | Add `--collect-mode build` and timing/coverage summary | MVP CI mode |
| 2 | Baseline registry pack storage | Evidence reusable across comparisons |
| 3 | Changed-only source replay mode | PR source/API signals |
| 4 | Policy controls for source/build/graph risks | Tunable fail behavior |
| 5 | Graph summary mode | Explanation and graph diff |
| 6 | External backend/nightly examples | Kythe/CodeQL documentation |
| 7 | Performance and false-positive benchmark reports | Confidence before defaults change |

---

## References

- ADR-017 — GitHub Action Design ([017-github-action.md](017-github-action.md))
- ADR-022 — Baseline Registry and Snapshot Distribution
  ([022-baseline-registry.md](022-baseline-registry.md))
- ADR-025 — PR-Diff-Aware ABI Evaluation
  ([025-pr-diff-source-evaluation.md](025-pr-diff-source-evaluation.md))
- ADR-028 — Evidence Pack Architecture
  ([028-source-build-evidence-pack.md](028-source-build-evidence-pack.md))
- ADR-029 — Build Graph and Toolchain Context Capture
  ([029-build-graph-toolchain-context-capture.md](029-build-graph-toolchain-context-capture.md))
- ADR-030 — Source ABI Replay
  ([030-source-abi-replay-and-linked-source-surface.md](030-source-abi-replay-and-linked-source-surface.md))
- ADR-031 — Source Graph Augmentation
  ([031-source-implementation-graph-augmentation.md](031-source-implementation-graph-augmentation.md))


## Amendment (2026-06-12): merge flow and `collect` demotion (see ADR-028)

- `abicheck merge a.json b.json -o out.json` combines independently-produced
  build-side and source-side dumps into one baseline, enabling parallel baseline
  preparation (the embedded single-artifact storage, ADR-028 D8, exists for this).
- `collect` is demoted to an advanced command; the CI evidence modes (D2) select
  the inputs/scopes for `dump --sources/--build-info` internally.

## Implementation status (2026-06-12)

| Decision | Status | Where |
|---|---|---|
| D1 complexity ladder | done | `buildsource/` L0–L5 layers |
| D2 CI modes | done | `dump --collect-mode` (build = L3-only, source/graph = L3+L4+L5); pre-captured packs filtered to the layer set; `collection_for_ci_mode()` |
| D3 PR localizer | done | `recommend_collect_mode()` + `abicheck recommend-collect-mode` (build-file ⇒ `build`, source/header ⇒ `source-changed`); artifact compare stays authoritative |
| D4 baseline coverage block | done | `BaselineMetadata.evidence_coverage` persists `{build_context, source_abi, graph}` |
| D5 deterministic caching | done | per-TU `SourceAbiCache` (L4, the dominant cost) + content-addressed `BuildEvidenceCache` (L3); both false-miss-preferring. The cheap L5 graph fold is recomputed by design (D6 rates it low-cost) |
| D6 timing summary | done | compare `evidence_metrics` (stderr + JSON) |
| D7 evidence policy | done | `evidence_policy` block: `source_only_findings`/`build_context_drift`/`graph_risk_findings`/`require_evidence` + `EVIDENCE_REQUIRED_MISSING` kind |
| D8 validation | done | FP-rate gate exposes D9 deltas; corpus tests per layer |
| D9 metrics | done | compare buckets (post-suppression), `cache_hit_rate` (collect), `false_positive_delta_vs_baseline` (FP gate) |
| D10 docs | done | `docs/concepts/build-source-data.md`, `docs/user-guide/policies.md` |
| Phases 1–7 | done | incl. `scripts/evidence_benchmark.py` (Phase 7 perf + FP report) |
