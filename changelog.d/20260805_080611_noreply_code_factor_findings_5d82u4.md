### Changed

- **The six most complex functions in the codebase are split into named steps**
  — `mcp_server.abi_compare`'s two ADR-043 scoping branches, `run_compare`'s
  flag validation and manifest preflight, `reconcile_added_removed`'s three
  matching tiers, `directly_referenced_stdlib_types`' partition/seed/walk
  phases, `_fold_scoped_compat_into_text`'s per-format renderings, and the
  preprocessor-chain reachability pass (now its own
  `dumper_ast_config_cpp20_chains` module, taking ~590 lines out of a file at
  its size cap). No behaviour change: same inputs, same outputs, same order.
