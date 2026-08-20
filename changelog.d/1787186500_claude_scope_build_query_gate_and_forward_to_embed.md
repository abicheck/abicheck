### Fixed

- **CLI cleanup phase two, PR 3A follow-up (Codex review, real reproduction,
  two findings)**: `_gated_build_query_inputs` is now the single, shared gate
  on `build_config`/`build_query` used by both the L2 seed and the L3-L5
  embed step, computed once per resolution so the two can no longer resolve
  different build configurations for the same input. Two corrections to the
  prior gate: (1) `build_compile_db` — a bare data path/glob naming an
  existing compile database, not an executable command — is no longer gated
  behind `allow_build_query`; only `build_config`/`build_query` (both
  potentially executable) are. (2) `embed_side_build_source`/
  `embed_build_source` now receive the same `build_config`/`build_query`/
  `build_compile_db` the L2 seed used, instead of falling back to their own
  auto-discovery independently.
