### Fixed

- **`dump`'s write-time L3-L5 embed is now idempotent** (CLI cleanup phase
  two, PR 3A blocker 5, sub-issue 3). The `dump` CLI embeds inline
  build/source evidence when it writes the snapshot, while the typed pipeline
  (`execute_dump_request`) embeds during resolution — so routing the real run
  through the typed executor would have embedded twice, re-running L4
  source-ABI replay (a real compiler invocation per translation unit) over a
  snapshot that already carried the result. `cli_buildsource.
  build_source_already_satisfies` is the check-before-embed guard, expressed
  through the same `_missing_requested_evidence_layers` the neighbouring
  G21.7 fail-loud warning already trusts, so the two cannot disagree about
  what "satisfied" means. A no-op for every path that exists today.
