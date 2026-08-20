### Fixed

- **CLI cleanup phase two, PR 3A (dump/scan resolver convergence)**:
  `service_input_resolution.resolve_side_snapshot`/`_resolve_side_snapshot_impl`
  now accept and forward `symbols_only`/`debug_presence_only` to
  `service.resolve_input`, closing a gap where only `scan_engine`'s
  hand-rolled candidate resolution (which calls `resolve_input` directly,
  bypassing this shared primitive) could express either flag. Both default
  `False`, matching `resolve_input`'s own defaults, so every pre-existing
  caller (`compare`, `dump`'s typed pipeline) is unaffected. Separately,
  `scan_engine._build_new_snapshot`'s `embed_build_source` call now passes
  `public_headers`/`public_header_dirs` unexpanded, matching
  `service_input_resolution.embed_side_build_source`'s own construction,
  instead of pre-expanding a public header directory into its individual
  files first — the expansion was redundant (a directory root already
  classifies every file under it via segment/prefix matching in
  `source_extractors._argv.split_public_roots`/`_ClassifyContext`), not more
  correct, and diverged from the canonical shared shape for no behavioral
  benefit. No observable behavior change to `scan`'s public/internal header
  classification. Both fixes are additive prerequisites for eventually
  routing `scan_engine._build_new_snapshot` through the same shared
  `_resolve_side_snapshot_impl` primitive `compare`/`dump` already use; see
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR 3A section for what
  remains open.
