# CLAUDE.md — `abicheck/evidence/source_extractors/`

Source ABI extractors (ADR-030 D3, phase 2+). Each backend parses a translation
unit / public headers under their real per-TU build context (ADR-029
`CompileUnit`) and emits a normalized `SourceAbiTu` (`../source_abi.py`). The
linker (`../source_link.py`) then folds per-TU dumps into a per-library surface
and the diff (`../source_diff.py`) compares two surfaces.

## Module map

| Module | Role | ADR |
|--------|------|-----|
| `base.py` | `SourceAbiExtractor` protocol (ADR-032 interface), `SourceExtractionError`, and the **pure** model→`SourceEntity` mapping + `assemble_source_tu()` | 030 D3/D4 |
| `_argv.py` | **Shared** compile-context helpers (MSVC driver detection, `~` un-redaction, `CXX_LANGS`, public-root splitting), reused by castxml + clang. Launcher/`env` unwrapping and driver resolution moved to `compile_invocation.py` and `launcher_env.py` / `launcher_env_path.py` (plan `l4-l2-extraction-convergence` Phase 3 slice 3). The replayed argv is no longer built here either: see `compile_invocation.py` | 030 D2 |
| `launcher_env.py` | `env`/ccache/sccache/distcc prefix traversal (`-C`/`-u`/`-i`/`-S`, `NAME=VALUE` assignments, `KEY=VALUE` config overrides) and `effective_directory`, driven by `compile_invocation.strip_launchers`. | 030 D2 |
| `launcher_env_path.py` | `env PATH=`/`-C` folding into the driver token (`_apply_env_context`), split out of `launcher_env.py` for the line cap. | 030 D2 |
| `castxml.py` | `CastxmlSourceExtractor` + pure `build_castxml_command()`; reuses `dumper_castxml._CastxmlParser` | 030 D3 (phase 2) |
| `clang.py` | `ClangSourceExtractor` + `source_abi_from_clang_ast()` + the compiler probes; `clang -ast-dump=json` → public **declarations** (functions/methods incl. signatures + mangled names), **types** (record/enum/typedef + type hashes), inline/template/constexpr **body** fingerprints, default args, and (via a `-E -dD` pass) macros. Feeds both `reachable_declarations` and `reachable_types` (gap G4), so no separate libclang/cindex backend is needed. Stamps `fact_set`/`coverage` (ADR-038 C.8) and, via `clang_source_edges.py`, `source_edges` (P1 #17-18) | 030 D3 (phase 5) |
| `compile_invocation.py` | Plan `l4-l2-extraction-convergence` Phase 3 (parent plan P1): `parse_compile_invocation()` reads a `CompileUnit` once into a frozen `CompilerInvocation` (driver, language, standard, replay directory, defines, rebased include dirs, sysroot, target, and the replayed `CarriedFlag`s). `replayed_tokens()` / `replayed_tokens_for()` are the one rule for which replayed tokens the structured argv already suppresses. `replayed_include_dirs` carries the include-search operands `clang_public_roots` probes. Consumed by `clang_command`, `castxml`, `clang_public_roots` and the replay cache key. | 030 D2, parent plan P1 |
| `clang_command.py` | Clang argv renderers: `build_clang_command()` is the one AST-dump argv (L4 and L5 both run it), `build_clang_macro_command()` the `-E -dD` argv. Both render a parsed `CompilerInvocation` (see `compile_invocation.py`), never the raw `CompileUnit` argv; `compile_unit_invocation()` gives the source path and effective directory, and `ast_clang_binary()` the driver every dump runs under. The compiler probes stay in `clang.py` because tests patch its `run_bounded`. | 030 D2 (phase 5) |
| `clang_dump_errors.py` | How L4 reports a failed AST dump: `ast_or_raise()` maps a `TuAstDump` stage to the `SourceExtractionError` text L4 has always recorded, with the generated-header hint. | 030 D2 |
| `clang_macros.py` | `macros_from_preprocessor()` — parses `clang -E -dD` output into public-header macro entities, split out of `clang.py`. | 030 D6 |
| `clang_node_predicates.py` | AST node classification split out of `clang.py`: the node-kind sets, `_ClassifyContext` (public-surface classification), `_location()`, and the pure per-node predicates (`_is_function_node`, `_is_variable_node`, `_mangled_has_internal_linkage`, …). | 030 D3/D4 (phase 5) |
| `clang_public_roots.py` | Public-header-root equivalence + path classification, split out of `clang.py` to keep it under the line-count cap; re-exported so `from ...clang import NAME` keeps working | 030 D3 (phase 5) |
| `clang_source_edges.py` | `build_source_edges(ast_root, diags)` — `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/`TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS` edges from the AST dict `clang.py`'s `extract()` already parsed, reusing `call_graph.py`'s/`type_graph.py`'s pure parsers — no second `clang` invocation. Split out of `clang.py` to keep it under the line-count cap | 031 D4 (P1 #17-18) |
| `android.py` | `AndroidHeaderAbiAdapter` + pure `parse_android_dump()`; normalize a pre-captured `.sdump`/`.lsdump` into a `SourceAbiTu` | 030 D9 (phase 6) |

## The one rule

Same authority rule as the parent `evidence/` package: L4 source facts are never
sole authority for a shipped-ABI `BREAKING` verdict (ADR-028 D3). Extractor
failures (tool missing, parse/timeout) raise `SourceExtractionError` and are
recorded as **partial** L4 coverage — they never abort the artifact comparison.

## Conventions

- **Keep the tool call thin.** The context→argv builder and the model→entity
  mapping are pure and unit-testable *without* the external tool; only the
  `extract()` orchestration shells out. Tests for the pure halves are default
  (fast) lane; tests that actually run castxml/clang are `@pytest.mark.integration`.
- **castxml** is good for declarations / types / public const-constexpr values
  but weak for function bodies and macros (ADR-030 D3 table). **clang** is the
  *source-based* backend that adds inline/template/constexpr *body* fingerprints
  and default arguments — it **requires clang on PATH** and degrades to partial
  coverage when it is absent (the source tier is the one tier gated on a C++
  front-end). For a GCC project, clang replays the GCC build's flags; a TU using
  a GCC-only extension clang rejects degrades to partial coverage, never a hard
  failure.
- The heavy `dumper_castxml` import is done lazily inside castxml's `extract()`;
  clang's `_CastxmlParser`-free path imports `provenance` lazily for the same
  reason (keep the lightweight evidence layer's import graph thin / cycle-free).
