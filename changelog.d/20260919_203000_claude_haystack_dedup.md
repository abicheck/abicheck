### Fixed

- **Dependency scoping no longer rescans duplicate signature text.** The
  haystack it matches every candidate spelling against was the joined
  signature text of *every* kept declaration, duplicates included, and the
  alternation scan costs near-linear in that string's length. A real library
  repeats its type spellings heavily — oneDAL's `libonedal_parameters` joins
  11,856 texts that reduce to 1,822, and 212,006 characters to 85,654 — so
  `const ns::Thing &` was rescanned once per declaration mentioning it.
  Deduplicating took one library's whole dump from 116.9 s to 85.4 s with a
  byte-identical snapshot. Safe because the caller folds matches into a set
  and no match may span the separator.

### Changed

- `GraphFact`, `GraphNode` and `GraphEdge` are `slots=True` dataclasses.
  Measured on a real extraction they account for ~585k instances, each of
  which carried a per-instance `__dict__`. This did **not** move peak RSS
  (3136.0 → 3136.4 MiB on the measured library); it is a per-instance
  footprint and attribute-access change, not a fix for the peak.
