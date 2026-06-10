# ADR-030: Source ABI Replay and Linked Source Surface

**Date:** 2026-06-09
**Status:** Accepted — implemented (phases 1–7)
**Decision maker:** Nikolay Petrov

---

## Context

Some API/ABI-relevant facts are weakly represented or absent in final
binary/debug artifacts:

- macro constants and feature macros;
- default arguments;
- inline function bodies and inline behavior fingerprints;
- `constexpr` and template-body changes;
- uninstantiated templates;
- source declarations that are never emitted into a symbol table;
- public header origin/provenance;
- generated headers and configuration-specific header contents.

ADR-026 made this boundary explicit: group-1 gaps are recovered by
supplying headers (the `header_aware` tier), while group-2 changes —
uninstantiated templates, never-included inline bodies — are invisible to
*any* artifact comparison and are documented as a known limitation
(`case122`). ADR-026 also recorded the one place a source pass earns its
keep: an optional pre-filter that operates on source with real context,
with artifact comparison remaining authoritative (ADR-025 D4).

This ADR fills that slot. It adds an **optional** source ABI replay layer
(L4 in the ADR-028 model) that parses selected translation units and public
headers under their real per-TU build context (from ADR-029
`BuildEvidence`) and links the result against the library's exported
surface.

**Boundary update relative to ADR-026.** ADR-026's non-goal — a standalone
source-AST comparator inside abicheck that replaces artifact comparison —
stands unchanged. What this ADR adds is narrower: an opt-in, build-context-
grounded source evidence layer whose findings are classified as source/API
risks, never as sole authority for shipped-ABI `BREAKING` verdicts (ADR-028
D3). The lightweight-core constraint (ADR-001) is preserved by making
castxml the first extractor and keeping Clang LibTooling optional.

The closest existing architecture is Android's header checker flow:
per-source ABI dumps are produced from compiled sources with exported
include directories and compiler flags, then linked into a library-level
ABI dump using a version script or the shared library's exported symbols,
then compared against references. abicheck reuses this pattern conceptually
without adopting Android's unstable intermediate formats as a public
contract.

---

## Decision

### D1. Add optional source ABI replay as L4 evidence

```text
BuildEvidence compile units (ADR-029)
  └── source ABI extractor per selected TU/header
        └── tu_source_abi/*.json
              └── source ABI linker
                    └── source/source_abi.json
                          └── compared as auxiliary evidence
```

The initial implementation works after a normal build, when sources and
build metadata already exist. It does not require compiler plugins or
instrumented rebuilds.

### D2. Parse with real per-TU build context

Source replay must use the same compile context as the real build whenever
possible:

- source file path and working directory;
- compiler frontend mode and language standard;
- defines/undefines;
- include paths and system include paths;
- target triple and sysroot;
- ABI-affecting compiler flags;
- generated headers that already exist in the build tree.

The compile context comes from ADR-029 `BuildEvidence`, not from manual
global flags. This is what separates replay from the naive source-AST
comparison ADR-025/026 reject: replay sees the headers the compiler
actually saw, under the flags it actually used.

### D3. Keep extractor implementation pluggable

Several extractors are supported behind one normalized `SourceAbiTu`
schema, via the ADR-032 extractor interface:

| Extractor | Integration | Use now? | Notes |
|---|---|---|---|
| castxml replay | CLI invocation per public header/TU context | Short-term feasible | Reuses the existing dependency; good for declarations/types; weak for function bodies. |
| Clang LibTooling source dumper | abicheck-owned standalone tool or optional package | Preferred medium-term | Best control over AST, macros, source locations, inline/template fingerprints. |
| Android `header-abi-dumper` adapter | External CLI adapter | Optional/reference | Good precedent; raw `.sdump` is not stable enough for abicheck schema (D9). |
| clang-tidy custom check | Plugin-like source checker | Optional | Useful for source/API lint-style findings; not ideal as a dump format. |
| CodeQL/Kythe | External graph/database backend | Later | Too heavy for the source ABI MVP; useful for the graph layer (ADR-031). |

The normalized schema is authoritative; external tool formats are raw
provenance only (ADR-028 D4).

### D4. Define the `SourceAbiTu` normalized schema

```json
{
  "schema_version": 1,
  "tu_id": "cu://src/foo.cpp#cfg:abc123",
  "target_id": "target://libfoo",
  "extractor": {"name": "abicheck-clang-source-dumper", "version": "0.1"},
  "compile_context_hash": "sha256:...",
  "source": "src/foo.cpp",
  "public_header_roots": ["include/foo/foo.h"],
  "declarations": [],
  "types": [],
  "functions": [],
  "variables": [],
  "macros": [],
  "templates": [],
  "inline_bodies": [],
  "constexpr_values": [],
  "source_edges": [],
  "diagnostics": []
}
```

Entity fields:

```json
{
  "id": "decl://sha256...",
  "kind": "function|method|record|enum|typedef|variable|macro|template",
  "qualified_name": "foo::Bar::baz",
  "mangled_name": "_ZN3foo3Bar3bazEv",
  "signature_hash": "sha256:...",
  "body_hash": "sha256:...",
  "type_hash": "sha256:...",
  "source_location": {"path": "include/foo/bar.h", "line": 42, "origin": "PUBLIC_HEADER"},
  "visibility": "public_header|private_header|system_header|generated|unknown",
  "api_relevant": true,
  "confidence": "high|reduced|unknown"
}
```

### D5. Link TU dumps into a per-library source ABI surface

A source ABI linker merges per-TU facts into `source/source_abi.json` for
one binary/library.

Inputs:

- per-TU `SourceAbiTu` files;
- `BuildEvidence` target/link-unit mapping (ADR-029);
- the public header set;
- exported binary symbols from L0;
- optional version script/export map/`.def` file;
- ADR-024 public-surface provenance and reachability model.

Output:

```json
{
  "schema_version": 1,
  "library": "build/libfoo.so",
  "target_id": "target://libfoo",
  "roots": {
    "exported_symbols": [],
    "public_header_declarations": [],
    "forced_public": []
  },
  "reachable_source_surface": {
    "declarations": [],
    "types": [],
    "macros": [],
    "templates": [],
    "inline_bodies": []
  },
  "mappings": {
    "source_decl_to_binary_symbol": [],
    "source_type_to_debug_type": [],
    "public_header_to_target": []
  },
  "odr_conflicts": [],
  "unmatched": [],
  "coverage": {}
}
```

### D6. Source replay findings

Comparison of two linked source ABI surfaces can produce source/API
findings. Proposed `ChangeKind` entries, each in exactly one partition set
(ADR-011); the resulting verdict follows the existing five-tier system
(ADR-009):

| Proposed kind | Partition | Artifact support needed? | Example |
|---|---|---|---|
| `public_macro_value_changed` | `API_BREAK_KINDS` | No | `FOO_SIZE` changed in a public header |
| `default_argument_changed` | `API_BREAK_KINDS` | No | `void f(int x = 1)` → `x = 2` (already detectable with headers today; replay adds build-context provenance) |
| `inline_body_changed` | `RISK_KINDS` | No | Inline public function body changed but no binary symbol changed |
| `constexpr_value_changed` | `API_BREAK_KINDS` | No | Public `constexpr int` value changed |
| `template_body_changed` | `RISK_KINDS` | No | Uninstantiated public template implementation changed (the ADR-026 `case122` residual) |
| `uninstantiated_template_removed` | `API_BREAK_KINDS` | No | Public template removed without any binary presence |
| `source_decl_binary_symbol_mismatch` | `RISK_KINDS` | Yes, for escalation | Public declaration no longer maps to an exported symbol |
| `odr_source_conflict` | `RISK_KINDS` | No | Same type name differs across TUs |
| `generated_header_changed` | `RISK_KINDS` (policy may escalate to API break) | No | Generated public config header changed |

Policy profiles (ADR-010) decide whether source-only findings block a
release. The defaults above keep them clearly distinguished from
artifact-proven `BREAKING` ABI changes.

### D7. Scope source replay aggressively for performance

| Mode | Behavior | Intended use |
|---|---|---|
| `off` | No source ABI replay | Default for existing users |
| `headers-only` | Replay public headers using matched TU context | Fast API/source coverage |
| `changed` | Replay changed public headers and the TUs owning changed headers/sources | PR mode (ADR-025 changed-path signal) |
| `target` | Replay all TUs contributing to the selected library target | Baseline mode |
| `full` | Replay all compile units in the build evidence | Nightly/deep mode |

The MVP implements `headers-only` and `changed`; `target` and `full`
follow. These scopes are an internal knob: the user-facing CI evidence
modes (ADR-033 D2) select a scope automatically, per the mapping table
there.

### D8. Cache per-TU source ABI dumps

Cache key:

```text
hash(
  extractor name/version,
  source file content hash,
  transitive included public/private/generated header hashes,
  normalized compile context hash,
  public header root set,
  language standard / target / sysroot,
  abicheck source schema version
)
```

Cache values are per-TU dumps. Source ABI linking is cheap compared with
parsing and can be recomputed. Cache invalidation must prefer false misses
over false hits (ADR-033 D5).

### D9. No hard dependency on Android `.sdump`/`.lsdump`

Android's tools are useful, but their intermediate formats are documented
as implementation details. abicheck may provide an adapter:

```bash
abicheck collect-evidence --source-abi-extractor android-header-abi --output evidence/
```

The adapter must normalize into `SourceAbiTu` and `source_abi.json`. Raw
`.sdump`/`.lsdump` files may be preserved under `raw/android-header-abi/`.

### D10. Source-only evidence boundaries stay explicit

Every source-only finding carries:

```json
{
  "evidence_tier": "L4_SOURCE_ABI",
  "artifact_backing": "none|symbol_match|debug_type_match|header_ast_match",
  "verdict_authority": "source_api|artifact_abi|policy_escalated",
  "confidence": "high|reduced|unknown"
}
```

This prevents confusion between a shipped binary ABI break and a source/API
compatibility risk, and feeds the evidence coverage report (ADR-028 D7).

---

## Consequences

### Positive

- Covers the residual source/API space acknowledged by ADR-026 without
  replacing artifact comparison.
- Reuses real build context, avoiding false positives from parsing headers
  under the wrong flags.
- Enables comparison of source ABI surfaces, not just binary snapshots.
- Improves explanations for binary findings through source-to-symbol
  mappings.
- Gives projects an optional deeper mode for nightly and release baselines.

### Negative / risks

- C++ template and macro modeling is complex and frontend-dependent.
- Parsing many TUs is expensive without caching and scoping (D7, D8).
- Clang-based replay may not exactly match GCC/MSVC parsing of vendor
  extensions.
- Source-only findings can be noisy unless policy separates API breaks from
  ABI breaks (D6, D10).
- Generated headers must already exist (or be generated explicitly);
  otherwise replay coverage is partial and must be reported as such.

---

## Implementation plan

| Phase | Scope | Output |
|---|---|---|
| 1 | Define `SourceAbiTu` and `source_abi.json` schemas | Schema and empty-source coverage report |
| 2 | castxml/header replay adapter with `BuildEvidence` contexts | Public declaration/macro/default-arg coverage where available |
| 3 | Source ABI linker over public headers + exported symbols | Linked source surface per library |
| 4 | Source ABI diff findings (D6) | Source/API findings with authority labels |
| 5 | Clang LibTooling source dumper prototype | Inline/template/constexpr/body fingerprints |
| 6 | Optional Android header checker adapter | External tool reuse, raw artifact preservation |
| 7 | PR changed-mode and cache optimization | CI-ready source replay |

### Implementation status

All seven phases are implemented, in `abicheck/evidence/`:

- **Phase 1** — `source_abi.py`: the `SourceAbiTu` (D4) and `SourceAbiSurface`
  (D5) normalized schemas with `to_dict`/`from_dict` round-trips and the
  `L4_SOURCE_ABI` evidence-boundary label (D10); `pack.py` reads/writes
  `source/source_abi.json` and folds it into the content hash.
- **Phase 2** — `source_extractors/`: the `SourceAbiExtractor` interface
  (ADR-032) and `CastxmlSourceExtractor`, which parses a translation unit under
  its real per-TU `CompileUnit` build context (D2) and emits a `SourceAbiTu`.
  It reuses the existing castxml XML parser, so no new dependency is added
  (ADR-001). castxml covers declarations, types, and public const/constexpr
  values; inline/template *body* fingerprints are the Clang backend's job
  (phase 5, per the D3 table).
- **Phase 3** — `source_link.py` (`link_source_abi`): merges per-TU dumps into a
  per-library surface, mapping public source declarations to exported binary
  symbols and detecting ODR conflicts (D5).
- **Phase 4** — `source_diff.py` (`diff_source_abi`): the nine D6 `ChangeKind`s,
  partitioned `API_BREAK`/`RISK` per ADR-028 D3 (never `BREAKING`), registered
  in `change_registry.py`.
- **Phase 5** — `source_extractors/clang.py` (`ClangSourceExtractor`): the
  *source-based* backend. It parses a TU under its build context with
  `clang -Xclang -ast-dump=json` and derives the fingerprints castxml cannot —
  inline function bodies, function/class **template** bodies, `constexpr` values,
  and default arguments. **Source ABI replay requires clang**; when it is absent
  the extractor raises `SourceExtractionError`, recorded as *partial* L4 coverage
  (ADR-028 D7) — the artifact tiers stay authoritative and the comparison never
  aborts. No new Python dependency (ADR-001): clang is an optional runtime tool,
  discovered like castxml. For a GCC-built project clang replays the GCC build's
  flags (a TU using a GCC-only extension clang rejects degrades to partial
  coverage). The argv builder and the JSON-AST→`SourceAbiTu` mapping are pure and
  unit-tested; only the clang run is integration-marked. Shared compile-context →
  argv logic lives in `source_extractors/_argv.py`, reused by both castxml and
  clang.
- **Phase 6** — `source_extractors/android.py` (`AndroidHeaderAbiAdapter`):
  reuses Android's VNDK header-checker `.sdump`/`.lsdump` output as an L4 backend,
  normalized into the abicheck `SourceAbiTu` (D9). Default behaviour consumes a
  *pre-captured* dump (non-executing, ADR-028 D6); running `header-abi-dumper` is
  opt-in (ADR-032 D5). Raw dumps are not the stable contract.
- **Phase 7** — `source_replay.py`: the `off`/`headers-only`/`changed`/`target`/
  `full` replay scopes (D7) as a pure `select_compile_units`, the per-TU
  `SourceAbiCache` keyed on the D8 inputs (extractor identity, source + header
  *content* hashes, normalized compile context; uncacheable → re-extract, ADR-033
  D5), and the `run_source_replay` driver that ties extraction → linking →
  partial-coverage diagnostics together. `scope_for_ci_mode` maps the ADR-033 D2
  CI modes onto these scopes.

The pipeline is wired into the CLI: `collect-evidence --source-abi
[--source-abi-extractor clang|castxml|android] [--source-abi-scope ...]` writes
`source/source_abi.json`, and `compare --old/--new-evidence` diffs the two
surfaces (`diff_source_abi`) and folds the findings into the verdict pipeline.
The compare output prints an explicit **capability report** — which check
categories are enabled and, for each disabled one, why (no binary / no debug
info / no headers / no build data / no sources-or-clang).

### Known limitations / follow-ups

The phases above are implemented and wired, but the following are deliberately
deferred and should be handled in later work. None of them weaken the authority
rule (L4 never gates a shipped-ABI `BREAKING` verdict on its own, ADR-028 D3);
they are coverage/precision gaps, not correctness holes.

1. **Validation corpus not yet committed.** The "Validation" section below calls
   for an `examples/case*` fixture corpus (public macro / default-argument /
   inline / template / constexpr changes, extending the ADR-026 `case122`
   calibration fixture), an L4-vs-L2 declaration/type-shape agreement check, an
   L4-vs-L0 exported-symbol cross-check, and performance tests for the `changed`
   and `target` scopes. The behaviours are covered by unit + integration tests,
   but the ground-truth example cases and perf benchmarks are still to be added.
2. **Macros are clang-only.** `public_macro_value_changed` is produced only by
   the clang backend's `-E -dD` preprocessor pass. The castxml and Android
   backends do not extract macros, so a macros-only run on those backends has no
   macro coverage. Include-guard macros (`#ifndef FOO_H`) also surface as
   (harmless, empty-valued) macro entities — a small-noise source a future filter
   could suppress.
3. **Typedef / alias entities are not modeled by clang.** The clang backend emits
   `record`/`enum` types but not `TypedefDecl`/`TypeAliasDecl`; castxml omits
   typedefs entirely (no per-entry provenance, see `castxml.py`). A public
   typedef whose underlying type changes is therefore not surfaced as an L4
   finding. Add provenance-aware typedef extraction (clang is the natural home).
4. **Scope selection is approximate without a full include graph.** `headers-only`
   picks one representative compile unit per target rather than the minimal set
   that covers every public header, and `changed` maps a changed header to TUs
   via target ownership (falling back to all TUs when there is no target/header
   metadata, ADR-025 D3). A precise mapping needs a per-TU include graph
   (depfiles or the L5 graph layer, ADR-031).
5. **Inline auto-collection during `compare --evidence-mode` is still a stub.**
   `compare` consumes pre-built packs via `--old/--new-evidence`; it does not yet
   run `collect-evidence` inline for a requested evidence mode. That inline
   collection is **ADR-033 D2** scope, tracked there, not in this ADR.
6. **clang AST replay is a structural fingerprinter, not a full semantic ABI
   model.** Bodies/values are hashed from a build-root-stable canonical form of
   the clang JSON AST; this reliably detects *changes* but does not produce a
   semantic ABI diff of the body itself. The Clang LibTooling backend in the D3
   table remains the longer-term path for richer source-location/AST fidelity.

---

## Validation

- Fixture corpus for public macro / default argument / inline / template /
  constexpr changes (extending the ADR-026 `case122` calibration fixture).
- Compare source ABI replay against the existing L2 castxml snapshot for
  declarations and type shapes — they must agree where both have coverage.
- Cross-check exported source functions against L0 exported symbols.
- Deliberate source-only fixtures must produce `API_BREAK` /
  `COMPATIBLE_WITH_RISK` verdicts, never binary `BREAKING`, unless policy
  escalates.
- Performance tests for `changed` and `target` modes.

---

## References

- ADR-026 — Source-Only Changes and the Evidence-Tier Boundary
  ([026-source-only-undetectable-changes.md](026-source-only-undetectable-changes.md))
- ADR-020a — Build-Context Aware Header Extraction
  ([020-build-context-capture.md](020-build-context-capture.md))
- ADR-024 — Public ABI Surface Resolution
  ([024-public-abi-surface-resolution.md](024-public-abi-surface-resolution.md))
- Android VNDK header checker: `header-abi-dumper`, `header-abi-linker`, `header-abi-diff`
- [Clang LibTooling](https://clang.llvm.org/docs/LibTooling.html) and AST Matchers
