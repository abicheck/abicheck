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
- Template/ABI-tag/CPO reconciliation demangles each side's declarations in
  one `c++filt` batch before resolving per-declaration identities, instead
  of spawning `c++filt` once per first-seen name (about 950 processes and
  4.3 s of an 18.7 s synthetic C++ compare; the compare stage went from
  6.2 s to 2.4 s with identical findings).
- The type-spelling vocabulary is compiled as a prefix-trie regex instead of
  a flat alternation: the same matches and spans (tested differentially
  against the old builder), but a cold lookup no longer tries every
  alternative -- ~500x faster on a 40,000-spelling vocabulary. Vocabularies
  nested too deeply for the trie fall back to the flat alternation.
- The closure-marker string walk that runs on every dump and load decides
  which dataclass fields to visit once per type instead of per node (2.6x
  faster, identical output).
