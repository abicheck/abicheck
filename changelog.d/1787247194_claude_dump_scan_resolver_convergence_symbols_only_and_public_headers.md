### Fixed

- **CLI cleanup phase two, PR 3A (dump/scan resolver convergence)**:
  `service_input_resolution.resolve_side_snapshot`/`_resolve_side_snapshot_impl`
  now accept and forward `symbols_only`/`debug_presence_only` to
  `service.resolve_input`, closing a gap where only `scan_engine`'s
  hand-rolled candidate resolution (which calls `resolve_input` directly,
  bypassing this shared primitive) could express either flag. Both default
  `False`, matching `resolve_input`'s own defaults, so every pre-existing
  caller (`compare`, `dump`'s typed pipeline) is unaffected.
