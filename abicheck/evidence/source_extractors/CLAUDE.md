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
| `_argv.py` | **Shared** compile-context → argv helpers (launcher unwrap, MSVC detection, `~` un-redaction, forced-include/abi-flag carry-through), reused by castxml + clang | 030 D2 |
| `castxml.py` | `CastxmlSourceExtractor` + pure `build_castxml_command()`; reuses `dumper_castxml._CastxmlParser` | 030 D3 (phase 2) |
| `clang.py` | `ClangSourceExtractor` + pure `build_clang_command()` / `source_abi_from_clang_ast()`; `clang -ast-dump=json` → inline/template/constexpr **body** fingerprints + default args | 030 D3 (phase 5) |
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
