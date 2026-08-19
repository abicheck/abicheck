### Fixed

- **A matched compile unit's own include-search directory is no longer
  rendered twice into a header-AST parse command.** Two independent
  compositions could each contribute an `-I`/`-isystem` for the identical
  directory: `dump`'s ELF and PE/Mach-O paths compose their final include
  list as the L2 build-context seed plus `resolve_inferred_header_roots`'s
  own, separately-derived additions, and the P0.3 L3→L2 fold's `derived`
  compile context can independently duplicate a directory the caller's own
  explicit `gcc_options`/`gcc_option_tokens` (e.g. from a legacy `-p`/
  `--compile-db` match against the same compile database) already carries.
  Both are now deduplicated in first-occurrence order, so a real compiler
  no longer processes the same `-I` twice and a `dump`-produced baseline's
  rendered compile-flag argv no longer disagrees with a `scan --against`
  candidate's own single-pass fold for reasons no diagnostic named — the
  root cause behind a `NOT_COMPARABLE` `profile_fingerprint` mismatch on
  `include_sequence` between the two, reproduced end-to-end against a
  minimal, castxml-free `dump`/`scan` pair of the same project (see
  `AGENTS.md`'s L3→L2-fold "nineteenth finding").
