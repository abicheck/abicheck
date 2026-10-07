# CLAUDE.md — `abicheck/buildsource/`

Optional build-info + source/graph data layers (ADR-028 umbrella; ADR-029–033).
The opaque "EvidencePack" container was renamed to concrete build-info/sources
vocabulary; the L0–L5 "evidence layer" *detectability* model (and the
`min_evidence` ground-truth field) is a separate concept and keeps its name.
See `docs/contribute/adr/028-source-build-evidence-pack.md` for the
architecture and `docs/learn/build-source-data.md` for the user-facing guide.

## Storage: embedded vs out-of-band

The normalized facts can ride **inline inside the `.abi.json`** (single-artifact
UX): `dump --build-info/--sources` calls `cli_buildsource.embed_build_source()`,
which sets `AbiSnapshot.build_source` (a `BuildSourcePack` with `root=""`),
serialized under the `build_source` key via `BuildSourcePack.to_embedded_dict()`.
`compare` reads each side's facts from that embedded payload unless an
out-of-band `--old/new-build-info` / `--old/new-sources` pack directory overrides
it (`_resolve_side_pack`). The on-disk pack directory from `collect` remains the
provenance/raw-artifact home.

**Source-tree-centric inputs (ADR-028..033 amendment).** `--sources <tree>` is a
*source checkout* (not a pack): `embed_build_source` calls
`inline.collect_inline_pack()`, which resolves a compile DB → L3, runs L4 replay
and folds the L5 graph, all inline. `--build-info <path>` is the optional,
decoupled L3 input (build dir / `compile_commands.json` / pack), auto-discovered
inside the tree when omitted. A path that *is* a pack directory (has
`manifest.json`, via `inline.is_pack_dir()`) is loaded as that pack for
back-compat. `merge` (in `cli_buildsource.py`) folds independently-produced
dumps' embedded packs into one baseline via `_combine_packs`.

## The one rule that governs everything here

**Artifact-backed L0/L1/L2 evidence stays authoritative for shipped ABI
verdicts.** Evidence from L3/L4/L5 may *explain, localize, scope, add
confidence/provenance, or correlate* an artifact-proven break — but it must
**never silently delete** one (ADR-028 D3). Findings produced *only* by
L3/L4/L5 are ordinary `ChangeKind` entries that default to `API_BREAK_KINDS`
(source-level breaks) or `RISK_KINDS` (deployment/context risk), never
`BREAKING_KINDS` unless an artifact diff also proves the break.

## Module map

One row per module: name, first sentence of its role, ADR. **Read [`docs/contribute/agent-guide/buildsource-module-map.md`](../../docs/contribute/agent-guide/buildsource-module-map.md) before changing a module** — it holds the full rows and the section's preamble (the Phase 6 `cross_source_checks` rename and the classification notes).

| Module | Role | ADR |
|---|---|---|
| `model.py` | `BuildSourceManifest`, `BuildSourceRef`, `LayerCoverage`, `BuildSourceEntity`, `DataLayer`/`LayerConfidence`/`CoverageStatus` enums | 028 D1/D5/D7/D8 |
| `pack.py` | `BuildSourcePack` — the 5-field dataclass, inline embedding (`to_embedded_dict`/`from_embedded_dict`), `empty()`. | 028 D1/D4, 061 Phase 5 |
| `pack_io.py` | `BuildSourcePack` persistence — `load`/`write`, content addressing (`content_hash`), `to_ref()`, `verify_integrity()`. | 028 D1/D4, 061 Phase 5 |
| `inputs_pack.py` | Flow-2 build-emitted facts protocol (**ingest** side): `InputsManifest` + `is_inputs_pack()`/`load_inputs_manifest()`/`read_source_facts()` and `ingest_inputs_pack()`. | 035 D5 (G19.4) |
| `inputs_emit.py` | Flow-2 **producer** side (inverse of `inputs_pack`): `init_inputs_pack()`/`append_source_facts()` (incremental per-TU) write a conformant `abicheck_inputs/` pack that round-trips through … | 035 D5 (G19.4) |
| `inline.py` | Source-tree-centric inline collection for `dump --sources`/`--build-info`: `collect_inline_pack()` (resolve compile DB → L3, replay → L4, fold → L5), `is_pack_dir()`. | 028–033 amendment, 032 (amended) |
| `build_config.py` | `BuildConfig`/`load_build_config()`/`discover_build_config()`/`KNOWN_TOP_LEVEL_KEYS`. | 028 amendment D4, 037 D4, 043 |
| `build_config_io.py` | `load_build_config_with_digest()` — `load_build_config()` plus the sha256 of the exact bytes it parsed, from one read. | 049 |
| `inline_graph_fold.py` | `fold_call_graph()`/`fold_type_graph()`/`fold_override_graph()`/`fold_template_graph()`/`fold_include_graph()`. | 041 P0, 046 D3 |
| `archive_graph.py` | `ar`-index introspection (G29 Phase 5 item 6, G29.6): a pure parser (`parse_ar_archive`/`read_archive`) over an archive's own linker-written symbol index. | 041 P1 #2 (G29 Phase 5) |
| `template_graph.py` | Template-instantiation *parsing* (G29 Phase 5 item 1, G29.6's first-priority open graph family): a pure parser (`parse_clang_ast_templates`) over `clang -ast-dump=json`, empirically verified against … | 041 (G29 Phase 5 item 1), 061 Phase 5 item 2 |
| `template_graph_fold.py` | `augment_graph_with_templates()` — the graph-*folding* half split out of `template_graph.py` (that module was sitting exactly at the repository's absolute 2000-line hard cap, leaving no line budget … | 041 (G29 Phase 5 item 1), 061 Phase 5 item 2 |
| `template_graph_value_decls.py` | `index_value_decls()`/`arg_label_spelling()`. | 041 (G29 Phase 5 item 1) |
| `macro_graph.py` | Macro/config-dependency graph extraction (G29 Phase 5 item 2, G29.6's second open graph family): a Clang AST pass (`parse_clang_ast_decl_ranges`, Pass A — indexes every … | 041 (G29 Phase 5 item 2) |
| `build_query.py` | **Zero-config build-system inference** (ADR-032 amended): `detect_build_system()` (CMakeLists.txt/MODULE.bazel/makefile markers) + `inferred_query_command()` (the fixed, abicheck-authored … | 032 (amended) |
| `cross_source_checks.py` | Intra-version cross-source validation (renamed from `crosscheck.py`, Phase 6 rename off the `scan` identity — ADR-068 §3 #34 / `docs/contribute/plans/one-comparison-product.md`) … | 035 D4/D8 (G19.2/G19.6), 041 P1 #5 |
| `cross_source_checks_base.py` | Leaf plumbing for the cross-check engine + its check modules (renamed from `crosscheck_base.py`, same Phase 6 rename): the `_CheckOutput` result, `_change`/`_exported_symbol_names` helpers, and the … | 035 D4 |
| `cross_source_checks_coherence.py` | The two AC-008/AC-009 evidence-coherence checks (renamed from `crosscheck_coherence.py`, same Phase 6 rename), split out of `cross_source_checks.py` to stay under the 2000-line cap. | 035 D4 |
| `pattern_facts.py` | Compiler-free lexical ABI-risk pre-scan (renamed from `pattern_scan.py`, Phase 6 rename off the `scan` identity — ADR-068 §3 #34 / `docs/contribute/plans/one-comparison-product.md`) … | 035 D2 (G19.1) |
| `pattern_facts_files.py` | The *discovery* half of the lexical pre-scan, split out of `pattern_facts.py`: this scanner's walk policy (suffix allowlist, extensionless-header heuristic, pruned dirs) … | 035 D2 |
| `source_inputs.py` | The **expected-input-set** model every filesystem-reading pre-scan resolves its inputs through, plus the `SourceReadLicence` that decides whether the filesystem may be read at all. | 035 D2 |
| `preprocessor_facts.py` | S2 conditional pre-scan (ADR-035 D2; renamed from `preprocessor_scan.py`, same Phase 6 rename as `pattern_facts.py`): `collect_preprocessor_facts(build, public_headers)` runs **only** when L3 build … | 035 D2 (G19.1) |
| `providers.py` | ADR-035 D10 per-level provider contract: `LayerProvider` Protocol (`capabilities`/`estimate`/`run(ctx, poi)`) + `ScanContext` (shared read-only inputs), `LayerFacts`, `ProviderCapabilities` … | 035 D10 (G19.7) |
| ~~`poi.py`~~ | **Deleted (ADR-068 Phase 6).** Evidence-directed focusing. | 035 D7 (G19.5), removed |
| ~~`risk.py`~~ | **Deleted (ADR-068 Phase 6).** Path-glob risk scoring for the `scan` orchestrator. | 035 D3 (G19.3), removed |
| `build_evidence.py` | `BuildEvidence` normalized model: `Target`, `CompileUnit`, `LinkUnit`, `Toolchain`, `Generator`, `BuildOption`, `TargetScope` (P0.2 root-target-scoping request/resolution accounting  … | 029 D1/D2 |
| `build_diff.py` | `diff_build_evidence()` → build-flag/toolchain drift findings | 029 D9 |
| `source_abi.py` | `SourceAbiTu` (per-TU dump) + `SourceAbiSurface` (linked `source_abi.json`) schemas, `SourceEntity`/`SourceLocation`, `L4_SOURCE_ABI` boundary | 030 D4/D5/D10 |
| `source_link.py` | `link_source_abi()` — fold per-TU dumps into a per-library surface. | 030 D5 |
| `source_diff.py` | `diff_source_abi()` → the 9 source-replay findings (macros/default-args/inline/template/constexpr/…). | 030 D6 |
| `source_graph.py` | Back-compat re-export facade only (ADR-061 Phase 5 item 2, 1352 → 140 lines). | 031 D2/D6/D7, 046 D1/D2/D3, 061 Phase 5 item 2 |
| `source_graph_build.py` | ADR-061 Phase 5 item 2's construction slice, Phase 2: `build_source_graph(build, source_abi=…)` (folds `BuildEvidence` → target/source/header/option/link graph), `project_source_files()` … | 031 D2, 061 Phase 5 item 2 |
| `source_graph_build_source_abi.py` | ADR-061 Phase 5 item 2's construction slice, Phases 3-4: `_augment_with_source_abi()` (an optional `SourceAbiSurface` → decl/type/macro + source↔binary edges), `fold_source_edges()` (the ADR-038 C.9 … | 031 D2, 038 C.9, 061 Phase 5 item 2 |
| `source_graph_compare.py` | ADR-061 Phase 5 item 2's comparison slice: `diff_source_graph()` (structural delta), `localize_symbol()` (`graph explain`), `_label_map()`/`_kind_map()`. | 031 D6/D8, 061 Phase 5 item 2 |
| `source_graph_query.py` | Back-compat re-export facade only (ADR-061 Phase 5 item 2 closure). | 041 P0, 061 Phase 5 item 2 |
| ~~`graph_facts.py`~~ | **Deleted (ADR-063 Phase 10).** The L5 node/edge schema and ADR-046 fact-merge machinery live in `abicheck/model/graph_facts.py` (identity helpers in `model/graph_identity.py`, vocabulary in … | — |
| `source_graph_findings.py` | `diff_source_graph_findings()` → the D6 secondary risk findings (phase 5/6: `SOURCE_TO_BINARY_MAPPING_CHANGED`, `PUBLIC_REACHABILITY_CHANGED`, `GENERATED_HEADER_REACHES_PUBLIC_API` … | 031 D6 |
| `clang_ast_run.py` | The one bounded `clang -ast-dump=json` run (`run_clang_ast_dump`: 120s local cap folded against `--budget`, degrade-to-diagnostic at every failure point). | 031 D4 (phase 6) |
| `l5_ast_pass.py` | **The one clang AST pass every clang-backed L5 graph family reads.** `run_l5_ast_pass` scopes the compile DB once (`inline_graph_fold._scope_narrowed_target`'s precedence), `run_ast_passes` sizes one … | 031 D4, 041 |
| `l5_ast_run.py` | Leaf holding `L5AstRun` (the run's scope + per-family outcomes) and `AstPassOutcome` (result, diagnostics, jobs, elapsed). | 031 D4 |
| `call_graph.py` | `parse_clang_ast_calls()` (pure `clang -ast-dump=json` → `CallEdge`s, unit-tested), `merge_call_edges()` (cross-TU dedup), the replay argv/worker-sizing helpers the L5 AST pass uses … | 031 D4 (phase 6) |
| `type_graph.py` | `parse_clang_ast_types()` (pure `clang -ast-dump=json` → `TypeEdge`s, unit-tested), `merge_type_edges()` (role-aware cross-TU merge), `augment_graph_with_types()` → … | 041 P0 (+ addendum), G29 Phase 5 item 5 |
| `header_graph_ast_stream.py` | Projects a clang AST **from disk**, one top-level declaration at a time, so the document and the tree are never resident together. | perf (#1338/#1339 follow-up) |
| `call_decl_record.py` | What `call_graph`'s `member_index` retains: the exhaustive set of fields such an entry is ever *read* through, and nothing else. | perf |
| `override_graph.py` | ADR-041 P2 item 1: `parse_clang_ast_overrides()` (pure `clang -ast-dump=json` → `OverrideEdge`s, unit-tested — reuses `type_graph.parse_clang_ast_types()`'s resolved `TYPE_INHERITS` edges for the … | 041 P2 |
| `virtual_dispatch_graph.py` | G29 Phase 5 item 3: `augment_graph_with_virtual_call_targets()` (`VIRTUAL_CALL_MAY_DISPATCH_TO` — joins a virtual `DECL_CALLS_DECL` edge against every `METHOD_POSSIBLE_OVERRIDE` edge naming its base … | 041 P2 (G29 Phase 5 item 3) |
| `callback_graph.py` | G29 Phase 5 item 4: `parse_clang_ast_callbacks()` (pure `clang -ast-dump=json` → `CallbackEdge`s, unit-tested — a new AST pass finding every place a function's address flows into a … | 041 (G29 Phase 5 item 4) |
| `header_graph.py` | `build_header_only_graph(snapshot, ast_projection=..., ...)`. | 041 addendum, G31 |
| `include_graph.py` | `parse_depfile()` (pure `clang -MM` parser, unit-tested), `ClangIncludeExtractor` (live clang, integration-only), `augment_graph_with_includes()` → `COMPILE_UNIT_INCLUDES_FILE` edges . | 031 D3 |
| `include_graph_workers.py` | The scheduling half of the depfile pass: `resolve_jobs()` (`ABICHECK_INCLUDE_MAP_JOBS` / shared `process_resources` CPU+RAM sizing), `run_probes()` (bounded thread pool, deadline re-establishment … | 031 D3 |
| `graph_backends.py` | `ingest_kythe_entries()` / `ingest_codeql_call_results()`. | 031 D5 (phase 7) |
| `source_extractors/` | `SourceAbiExtractor` interface + castxml (phase 2), clang (phase 5, body fingerprints), Android adapter (phase 6) | 030 D3 |
| `source_replay.py` | `select_compile_units()` (D7 scopes), `SourceAbiCache` (D8 per-TU cache, hit/miss instrumented, per-pass dep-digest memo), `run_source_replay()` driver (P06 parallel extract — thread pool by default … | 030 D7/D8, 033 D2/D3 |
| `build_cache.py` | `BuildEvidenceCache` + `compute_build_cache_key()`. | 033 D5 |
| `extractor.py` | `DataExtractor` protocol (`discover`/`collect`/`normalize`/`validate`), `CollectionContext`, `ExtractorCapabilities`, `CollectionAction`/`CollectionMode` … | 032 D1/D2/D4/D5/D9 |
| `extractor_manifest.py` | `ExtractorManifest` + `load_extractor_manifest()` (trusted-by-operator YAML), `render_command()`, `ExternalCliExtractor` + `run_external_extractor()`. | 032 D3/D8/D10 |
| `redaction.py` | `RedactionPolicy` — strip secrets/abs paths from command lines | 032 D7 |
| `evidence_policy.py` | ADR-033 D7/D9 pure helpers split from `cli_buildsource.py`: `apply_evidence_policy()` (category verdict modulation via `Change.effective_verdict`), `require_evidence_findings()` … | 033 D7/D9 |
| `adapters/compile_db.py` | `compile_commands.json` → `CompileUnit`s (reuses `build_context.py`) | 029 D3 |
| `adapters/cmake_file_api.py` | CMake File API reply → targets/toolchains/fileSets | 029 D4 |
| `adapters/ninja.py` | Ninja `-t compdb`/`graph` (live or pre-captured) | 029 D5 |
| `adapters/bazel.py` | Bazel `cquery`/`aquery` jsonproto → targets/compile+link units (live or pre-captured). | 029 D6 |
| `adapters/make.py` | Make `-n`/`--trace` dry-run transcript → reduced-confidence compile units | 029 D7 |
| `compiler_record.py` | ELF `.GCC.command.line` + DWARF `DW_AT_producer` → toolchain/options (advisory) | 029 D8 |

## Versioning

Five *independent* schema versions — do not conflate:
- `BUILD_SOURCE_PACK_VERSION` (`model.py`) — pack manifest/layout.
- `BUILD_EVIDENCE_VERSION` (`build_evidence.py`) — L3 normalized model.
- `SOURCE_ABI_VERSION` (`source_abi.py`) — L4 `SourceAbiTu`/`SourceAbiSurface`.
- `SOURCE_GRAPH_VERSION` (`source_graph.py`) — L5 `SourceGraphSummary`.
- `serialization.SCHEMA_VERSION` — the `AbiSnapshot`, which only stores an embedded `build_source` payload or an
  `BuildSourceRef` (old snapshot readers ignore both; ADR-015).

## Conventions

- Every dataclass carries `to_dict()`/`from_dict()` with defensive `.get()`
  parsing so a newer/hand-edited pack never aborts a load (forward-compat).
- Normalized facts are the only stable input to comparison/reporting; raw
  tool output under `raw/` is provenance only (ADR-028 D4) and never feeds the
  content hash.
- Adapters must be **post-build and non-executing by default** (ADR-028 D6):
  inspect existing build outputs / query interfaces only. Anything heavier than
  reading files is gated by the ADR-032 D5 action model (`CollectionAction` in
  `extractor.py`): only `inspect` is allowed by default; `query_build_system`,
  `run_compiler`, `run_build`, `wrap_build`, and `network` are explicit opt-in,
  and a manifest's declared actions are a *ceiling* intersected with the
  run-permitted set — never an escalation.
- Adding an L3/L4/L5 `ChangeKind`: follow the four-step procedure in the root
  `CLAUDE.md`, place it in `API_BREAK_KINDS`/`RISK_KINDS` per the rule above,
  and emit it from `build_diff.py` (or the relevant diff module).
