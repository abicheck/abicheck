### Fixed

- A `--diagnostic-comparison` run across two header-AST producers (castxml
  vs. clang) no longer reports `comparability_assurance.declaration`/`source`
  as `trusted`. The producer is now recognised as a declaration/source-level
  difference, not only the layout/runtime one its `compiler_family`/
  `compiler_version` fields imply.

### Performance

- `depth_aware_bare_name` is memoized (4.2M calls over 1,682 distinct inputs,
  ~40 s, on a oneDAL comparison).
- `directly_referenced_stdlib_types` is reused within one detector pass
  (`abicheck/compare/detection_memo.py`) instead of being recomputed by every
  detector that asks (34 calls over 4 distinct inputs, ~22 s).
- Loading a stored sectioned snapshot no longer canonicalizes and copies
  every section several times over: sections are no longer content-hashed
  into a throwaway store on read, `migrate_section_dto` returns an
  already-current DTO as-is, and the section codecs skip re-canonicalizing a
  payload the `SectionDTO` already canonicalized. About half the load time
  on a header-depth snapshot; output is byte-identical.
- The header-graph projection cache now warms under `--ast-frontend clang`.
  The attach step received the primary pass's parsed tree through the
  in-process memo without ever being offered the AST cache entry, so it
  re-projected on every run (6.6 s on oneDAL) and never stored a sidecar.
  It now takes a stored projection when one exists (dropping the tree at
  once), and stores the one it computes otherwise.
