### Fixed

- **`backend_capabilities.py`'s `Function.hidden_friend_owner_fact` row now
  claims real extraction.** Both header backends construct this fact via an
  explicit literal keyword now (see the preceding "not applicable" fix), so
  `scan_backend_evidence()`'s own AST-verified scan of the parsers correctly
  found it extracted; the curated `FACT_ROWS` entry was stale (still
  claiming `NONE`) and failed `test_matrix_claims_match_parser_source`.
  Regenerated `docs/reference/header-backend-capabilities.md`.
