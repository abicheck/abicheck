# ADR-031: Source and Implementation Graph Augmentation

**Date:** 2026-06-09
**Status:** Accepted — implemented (phases 1–7). The graph schema +
build-evidence graph + L4 public-reachability/source↔binary graph + Clang
call (D4) and `-MM` include (D3) graphs + Kythe/CodeQL pre-captured backends
(D5) are all collected via `collect`; `compare-graph` and the verdict
pipeline emit all six D6 findings; `explain-finding` localizes a finding
through the graph (D8). The compact single-file `source_graph_summary.json`
plus `external_graph_refs` is the storage model (D7) — chunked SQLite/Parquet
remains an optional future scaling optimization, not a capability gap.
**Decision maker:** Nikolay Petrov

---

## Context

The build (ADR-029) and source (ADR-030) layers answer "what changed?" and
"under which build context?". A graph layer can answer a different set of
questions:

- Which public ABI finding is reachable from which public header, target,
  source file, generated file, or build option?
- Did a source change affect a public type only transitively?
- Did a build option change affect the compile units that produced the
  changed binary symbol?
- Which source declarations map to which exported binary symbols and debug
  types?
- What changed between old and new call/type/include/build graphs?
- Which findings are likely implementation-only and which reach the API
  surface?

However, full whole-program graph analysis is heavy, and it is approximate
— especially for virtual dispatch, function pointers, templates, generated
code, link-time optimization, and dynamic loading. The graph layer must
therefore be optional and explanatory-first (L5 in the ADR-028 model).

---

## Decision

### D1. Add optional L5 graph evidence at two levels

| Level | Artifact | Purpose | Default |
|---|---|---|---|
| `summary` | `graph/source_graph_summary.json` | Compact graph facts relevant to ABI/API decisions | Optional, CI-friendly |
| `full` | external graph store or chunked graph pack | Deep graph-to-graph queries and exploration | Nightly/deep mode only |

The primary abicheck snapshot stores only a reference and coverage summary,
never the full graph (ADR-028 D8).

### D2. abicheck-owned graph schema for ABI/API-relevant facts

Node kinds:

```text
file, header, source, compile_unit, target, link_unit,
binary_symbol, debug_type, source_decl, record_type, enum_type,
typedef, macro, build_option, toolchain, generated_file,
external_dependency
```

Edge kinds:

```text
TARGET_HAS_SOURCE, TARGET_HAS_PUBLIC_HEADER, TARGET_DEPENDS_ON,
COMPILE_UNIT_BUILDS_SOURCE, COMPILE_UNIT_USES_OPTION,
COMPILE_UNIT_INCLUDES_FILE, FILE_GENERATED_FROM,
SOURCE_DECLARES, SOURCE_DEFINES, DECL_HAS_TYPE,
DECL_CALLS_DECL, DECL_REFERENCES_DECL,
TYPE_HAS_FIELD_TYPE, TYPE_INHERITS,
BINARY_EXPORTS_SYMBOL, SOURCE_DECL_MAPS_TO_SYMBOL,
SOURCE_TYPE_MAPS_TO_DEBUG_TYPE,
BUILD_OPTION_AFFECTS_DECL, BUILD_OPTION_AFFECTS_SYMBOL,
FINDING_LOCALIZES_TO_DECL, FINDING_CAUSED_BY_OPTION
```

Every edge carries provenance and confidence.

### D3. Start with the graph summary, not a full call graph

The MVP graph summary collects:

- target → source/header/output edges from `BuildEvidence` (ADR-029);
- compile unit → include edges from depfiles, compiler `-M*` output, or
  source parsing;
- public header → declaration edges from L2/L4;
- declaration → type reference edges from L2/L4;
- exported symbol → source declaration mappings from L0/L1/L2/L4;
- generated-file edges from build-system metadata;
- finding → declaration/source/build-option localization.

This already enables graph-to-graph comparison at the useful ABI/API level
without whole-program data-flow.

### D4. Call graph as optional, approximate evidence

Call graph extraction may use:

- Clang LibTooling AST traversal for direct calls;
- GCC `-fcallgraph-info` when builds opt into compiler-emitted VCG
  callgraphs (ADR-029 D8);
- CodeQL call-graph queries for resolved/virtual/points-to-based calls;
- Kythe call/reference edges when using Kythe extraction;
- LLVM IR/callgraph passes only in an explicitly instrumented build mode.

Call graph edges must be labeled:

```json
{
  "edge": "DECL_CALLS_DECL",
  "call_kind": "direct|virtual|function_pointer|template_instantiation|unknown",
  "resolution": "exact|points_to|overapprox|underapprox|unknown",
  "confidence": "high|reduced|unknown"
}
```

Call graph differences can explain implementation impact, but they never
decide ABI breakage alone.

### D5. External graph backends are adapters, not core dependencies

| Backend | Integration model | Strength | Limitation |
|---|---|---|---|
| Clang LibTooling | abicheck-owned extractor using the compile DB | Fine-grained AST facts, direct edges, good CI control | C++ frontend compatibility and maintenance burden |
| Kythe | external extractor + `.kzip` + GraphStore | Mature cross-reference graph and generated-code support | Large artifacts; extraction/indexing overhead |
| CodeQL | external DB + queries | Strong query model, call/data-flow libraries | Heavy DB creation; licensing/deployment considerations for some users |
| GCC callgraph | compiler option output | Direct compiler-emitted implementation signal | Requires an opt-in compile flag or pre-existing files |
| LLVM pass | compiler plugin / instrumented build | Rich IR facts | Incompatible with the no-rebuild MVP |

All backends go through the ADR-032 extractor contract; the normalized
graph summary remains abicheck-owned.

### D6. Graph diffs for explanation, scoping, and triage

Graph-to-graph comparison produces secondary findings. Proposed
`ChangeKind` entries (partitioned per ADR-011):

| Proposed kind | Partition | Meaning |
|---|---|---|
| `public_reachability_changed` | `RISK_KINDS` | Entity entered/left the public-API reachability closure |
| `source_to_binary_mapping_changed` | `RISK_KINDS` | Declaration↔symbol mapping changed without a clear ABI diff |
| `build_option_reaches_public_symbol` | `RISK_KINDS` | A changed option affected a compile unit producing a public symbol |
| `generated_header_reaches_public_api` | `RISK_KINDS` | A generated file participates in the public declaration closure |
| `call_graph_public_entry_reachability_changed` | `COMPATIBLE_KINDS` (quality) | Implementation reachable from an exported entry point changed |
| `include_graph_public_header_drift` | `RISK_KINDS` | Public header include closure changed |

These findings explain and prioritize. They must not suppress ABI break
findings without explicit ADR-024-style demotion and disclosure in the
surface ledger.

### D7. Store compact graph summaries by default

`source_graph_summary.json`:

```json
{
  "schema_version": 1,
  "graph_id": "sha256:...",
  "coverage": {
    "targets": 12,
    "compile_units": 142,
    "source_decls": 4110,
    "binary_symbol_mappings": 230,
    "include_edges": 19300,
    "call_edges": {"collected": false}
  },
  "nodes": [],
  "edges": [],
  "indexes": {
    "by_binary_symbol": {},
    "by_source_decl": {},
    "by_target": {},
    "by_file": {}
  },
  "external_graph_refs": []
}
```

For large projects, use chunked JSONL or SQLite/Parquet inside the evidence
pack. A report must never require loading a huge full graph just to compare
core ABI snapshots.

### D8. Graph query commands

```bash
# Collect a compact graph summary.
abicheck collect --source-graph summary --build-dir build --output evidence/

# Use an external backend.
abicheck collect --source-graph kythe --kythe-kzip merged.kzip --output evidence/
abicheck collect --source-graph codeql --codeql-db codeql-db/ --output evidence/

# Explain one finding through graph evidence.
abicheck explain-finding report.json --finding-id F123 --sources evidence/

# Compare graph summaries directly.
abicheck compare-graph old.evidence/graph/source_graph_summary.json \
                       new.evidence/graph/source_graph_summary.json
```

### D9. Confidence and approximation must be visible

Graph-derived output must say whether it came from:

- direct AST facts;
- generated compile/build metadata;
- debug-source provenance;
- points-to approximation;
- virtual dispatch approximation;
- an external graph backend;
- stale or partial extraction.

Reports must avoid language like "all callers" unless extractor coverage
proves it. Prefer "known static callers" or "observed graph edges".

---

## Consequences

### Positive

- Enables true graph-to-graph comparison without making full source
  analysis mandatory.
- Improves false-positive triage by showing whether a change reaches public
  API roots.
- Makes invisible build/generated/transitive causes explainable to
  reviewers.
- Provides a path to advanced source/binary correlation and impact
  analysis (feeding ADR-027 surface intelligence).

### Negative / risks

- Full graphs can be huge.
- Call graphs are approximate for real C++.
- External graph tools add install and runtime burden.
- Users may misinterpret graph absence as safety unless reports show
  coverage clearly (D9).

---

## Implementation plan

| Phase | Scope | Output | Status |
|---|---|---|---|
| 1 | Define node/edge schema and graph summary storage | Empty/metadata graph summaries | **Done** — `evidence/source_graph.py` (`SourceGraphSummary`/`GraphNode`/`GraphEdge`, content-addressed `graph_id`, coverage block, indexes); stored as `graph/source_graph_summary.json` and round-tripped by `EvidencePack` |
| 2 | Build graph edges from ADR-029 `BuildEvidence` | target/source/header/output graph | **Done** — `build_source_graph()`; `collect --source-graph summary` collects it and flips the L5 coverage row to PRESENT |
| 3 | Header/type/declaration graph from L2/L4 | public reachability graph | **Done** — `build_source_graph(build, source_abi=…)` folds an ADR-030 `SourceAbiSurface` into `source_decl`/`record_type`/`enum_type`/`typedef`/`macro` nodes linked to their declaring public header via `SOURCE_DECLARES` |
| 4 | Source-to-binary mapping graph | symbol/declaration/debug mapping explanations | **Done** — `SOURCE_DECL_MAPS_TO_SYMBOL`, `SOURCE_TYPE_MAPS_TO_DEBUG_TYPE`, and `BINARY_EXPORTS_SYMBOL` edges from the surface mappings, completing the target → header → decl → exported-symbol closure |
| 5 | Graph diff and `explain-finding` | graph-to-graph compare, finding localization | **Done** — `diff_source_graph()` (structural delta) + `diff_source_graph_findings()` emit all six D6 `ChangeKind`s, surfaced by `compare-graph` and folded into the `compare --old/--new-build-info` verdict pipeline; `localize_symbol()` + the `explain-finding` command localize a finding through the graph (D8) |
| 6 | Optional Clang direct-call extractor | direct call graph summary | **Done** — `evidence/call_graph.py`: `parse_clang_ast_calls()` (pure AST-JSON parser, unit-tested) + `ClangCallGraphExtractor` (live `clang -ast-dump=json`, integration-only) emit `DECL_CALLS_DECL` edges labelled with `call_kind`/`resolution` (D4); `collect --call-graph` collects them and the `call_graph_public_entry_reachability_changed` finding consumes them |
| 7 | Kythe/CodeQL adapters | external graph backend summaries | **Done** — `evidence/graph_backends.py`: `ingest_kythe_entries()` (Kythe entries → `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`) and `ingest_codeql_call_results()` (CodeQL BQRS→JSON → `DECL_CALLS_DECL`), wired via `collect --kythe-entries`/`--codeql-results`; non-executing (pre-captured exports), with the store noted in `external_graph_refs`. Compile-unit include edges (D3) land via `evidence/include_graph.py` (`clang -MM`) |

---

## Validation

- Golden public-reachability fixtures.
- Generated-header reachability fixture.
- Public symbol mapping fixture with overloads/templates.
- Callgraph fixture clearly separating direct, virtual, and
  function-pointer calls.
- Large-project memory tests with chunked graph summary loading.

---

## References

- ADR-024 — Public ABI Surface Resolution
  ([024-public-abi-surface-resolution.md](024-public-abi-surface-resolution.md))
- ADR-027 — API Surface Intelligence
  ([027-api-surface-intelligence.md](027-api-surface-intelligence.md))
- [Clang LibTooling](https://clang.llvm.org/docs/LibTooling.html)
- [Kythe](https://kythe.io/) extraction and GraphStore model
- CodeQL C/C++ call graph library
- GCC `-fcallgraph-info`
