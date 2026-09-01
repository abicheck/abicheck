### Fixed

- **`backend_capabilities.py`'s `Function.is_explicit_fact`/`is_override_fact`
  rows now claim real extraction.** Both header backends construct these two
  facts via an explicit literal keyword now (see the previous "not
  applicable vs not collected" fix), so `scan_backend_evidence()`'s own
  AST-verified scan of the parsers correctly found them extracted; the
  curated `FACT_ROWS` entries were stale (still claiming `NONE`) and failed
  `test_matrix_claims_match_parser_source`. Regenerated
  `docs/reference/header-backend-capabilities.md`.
- **Two size-capped bundle-facts-archive tests re-tuned for ADR-063 Phase
  5's schema growth.** Adding a `Fact[...]` sibling per case-(b) field grew
  every declaration's serialized shape; `test_save_load_round_trip_at_
  production_scale`'s 8600-function fixture now legitimately exceeds
  `DEFAULT_MAX_JSON_OBJECT_NODES` (fixed via the documented
  `max_json_object_nodes` override — a known-large, trusted test payload,
  not untrusted input), and `test_load_caps_each_blob_read_by_the_
  remaining_aggregate_allowance`'s two ~2.7 KiB fixture blobs grew to
  ~3.2 KiB each, so its monkeypatched aggregate cap is bumped from 6000 to
  8000 bytes to keep exercising the cap-shrinking behavior under test
  rather than a decode failure. No production default changed.
