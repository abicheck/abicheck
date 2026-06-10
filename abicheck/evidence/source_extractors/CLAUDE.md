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
| `castxml.py` | `CastxmlSourceExtractor` + pure `build_castxml_command()`; reuses `dumper_castxml._CastxmlParser` | 030 D3 (phase 2) |

## The one rule

Same authority rule as the parent `evidence/` package: L4 source facts are never
sole authority for a shipped-ABI `BREAKING` verdict (ADR-028 D3). Extractor
failures (tool missing, parse/timeout) raise `SourceExtractionError` and are
recorded as **partial** L4 coverage — they never abort the artifact comparison.

## Conventions

- **Keep the tool call thin.** The context→argv builder and the model→entity
  mapping are pure and unit-testable *without* the external tool; only the
  `extract()` orchestration shells out. Tests for the pure halves are default
  (fast) lane; tests that actually run castxml are `@pytest.mark.integration`.
- castxml is good for declarations / types / public const-constexpr values but
  weak for function bodies and macros (ADR-030 D3 table). Inline/template *body*
  fingerprints are the Clang backend's job (phase 5), not castxml's.
- The heavy `dumper_castxml` import is done lazily inside `extract()` so the
  lightweight evidence layer does not pull in the dumper model graph at import
  time (and to keep the import-cycle gate green).
