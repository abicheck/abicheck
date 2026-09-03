### Fixed

- **ADR-063 Phase 1: the legacy compile-database match's `applied` signal
  now also honors non-empty derived tokens on their own** — a caller
  threading `legacy_compile_db_tokens` through
  `_seeded_includes_and_compile_context` without also passing
  `legacy_compile_db_matched=True` (the shape
  `tests/test_legacy_compile_db_typed_threading.py`'s own end-to-end caller
  uses) previously still got `applied=False` in both the early-return and
  main-path branches, even though non-empty tokens are themselves proof a
  legacy `-p`/`--compile-db` match occurred. That under-reporting meant
  `AbiSnapshot.parsed_with_build_context` stayed unset for a typed dump
  relying purely on the legacy-match fallback, wrongly triggering the
  `header_parse_context_drift`/`header_build_context_mismatch` advisory
  findings and wrongly failing a `--depth build` gate. Fixed via a shared
  `_legacy_compile_db_achieved()` helper that treats either an explicit
  `legacy_compile_db_matched=True` or a non-empty token tuple as sufficient
  evidence of a real match.
