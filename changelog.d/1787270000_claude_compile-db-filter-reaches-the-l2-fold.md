### Fixed

- **`dump --compile-db-filter` now narrows the P0.3 L3→L2 compile-context
  fold, not only the legacy `-p` auto-match — so the fold's own ambiguity
  error stops recommending a flag that had no effect on it.** When one public
  header is compiled by two translation units that disagree on an
  ABI-relevant `-D`/`-std=`/target/sysroot,
  `resolve_header_compile_context` fails closed with a message naming
  `--compile-db-filter` as one of the ways to narrow the input. The filter
  reached the legacy compile-database auto-match only; the fold still saw
  every compile unit, so a user who followed that advice got the identical
  error back, with no way to resolve it short of hand-writing a pre-filtered
  `compile_commands.json`. The glob is now threaded into the fold itself
  (`buildsource.header_compile_context.resolve_header_compile_context`'s new
  `source_filter`, forwarded by `buildsource.l2_seed` and by the ELF `dump`
  CLI path), and it selects the named unit's real context: the guarded field
  under that TU's own macro is parsed in or out accordingly. A filter that
  matches no compile unit keeps every one of them, the same conservative
  fallback `build_context` and the ADR-039 collector already apply — a glob
  matching nothing is far likelier a typo than a request to discard all real
  build evidence. Matching semantics now live in one shared
  `build_context.source_matches_filter` rather than one copy per layer, so
  the L2 fold, the legacy match, and the ADR-039 collector cannot select
  different translation units for the same filter. The separate refusal to
  combine `--compile-db-filter` with a `--build-info` compile database at a
  collect mode that also *embeds* L3 evidence is unchanged: that guards a
  different inconsistency (L2 filtered, L3 unfiltered) which threading the
  filter through the fold does not address.
