### Fixed

- **A matched compile unit's own include-search directory is no longer
  rendered twice, in the same search class, into a header-AST parse
  command.** Two independent compositions could each contribute an
  identical `-I`/`-isystem`/... entry for the same directory: `dump`'s ELF
  and PE/Mach-O paths compose their final include list as the L2
  build-context seed plus `resolve_inferred_header_roots`'s own,
  separately-derived additions, and the P0.3 L3→L2 fold's `derived`
  compile context can independently duplicate a directory the caller's own
  explicit `gcc_options`/`gcc_option_tokens` (e.g. from a legacy `-p`/
  `--compile-db` match against the same compile database) already carries.
  Both are now deduplicated in first-occurrence order — a real compiler no
  longer processes the same `-I <dir>` twice — with the fix restricted to
  an exact `(search-class, directory)` match, so an `-isystem <dir>` is
  never dropped merely because an unrelated `-I <dir>` exists elsewhere:
  GCC/Clang consult `-iquote`/`-I`/`-isystem`/`-idirafter` as distinct
  search buckets regardless of argv position, and treating them as
  interchangeable could change which bucket a directory is searched from
  for a colliding header basename. This closes one of the mechanisms
  behind a `NOT_COMPARABLE` `profile_fingerprint` mismatch on
  `include_sequence` between a `dump`-produced baseline and a
  `scan --against` candidate of the same project (see `AGENTS.md`'s
  L3→L2-fold "nineteenth finding") — a second, deeper mechanism (which of
  two established resolution paths places a matched directory into
  `declared_includes`) is documented there as still open; this change does
  not by itself restore `dump`/`scan` comparability for that reproducer.
