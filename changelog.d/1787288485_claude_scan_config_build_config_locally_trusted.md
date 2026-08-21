### Fixed

- **`scan --config <path>` no longer silently drops the config's passive
  settings** (`build.compile_db`, `build.internal_namespaces`, ...) whenever
  the config itself declares no `build.query` (the common case). The PR 3A
  resolver migration routed scan's candidate resolution through
  `_resolve_side_snapshot_impl`'s shared `build_config`/`build_query` gate
  (`_gated_build_query_inputs`), which blanket-nulls `build_config` unless
  `allow_build_query` is exactly `True` — a default sized for `dump`/
  `compare`'s typed API, which has no CLI-side consent step of its own.
  `scan`'s own consent gate (`resolve_effective_allow_query`, ADR-037 D4)
  only ever authorizes the config's *executable* `build.query` field, never
  its bare presence, and that field is already, correctly, gated
  downstream (`collect_inline_pack`'s presence-based
  `build_config_trusted_for_query`) regardless of this gate. A new opt-in
  parameter, `build_config_locally_trusted`, restores scan's pre-migration
  behavior (`build_config` always forwarded ungated, trusting the existing
  downstream gate) without weakening `dump`/`compare`'s unchanged default.
