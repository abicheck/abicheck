### Fixed

- A record field that points at (or references) a record which was only
  renamed, at any pointer depth (`Inner *` -> `Inner2 *`, `Inner **`,
  `const Inner &`), is no longer graded an ABI break. The field still stores an
  address at the same offset; it now reads as `ABI_EQUIVALENT_TYPE_CHANGED`,
  and the rename's own `TYPE_REMOVED`/`TAG_TYPE_RENAMED` finding carries the
  source break. A repointing at a type that coexists with the old one, a
  changed pointer shape, or a renamed record whose layout differs still breaks.
