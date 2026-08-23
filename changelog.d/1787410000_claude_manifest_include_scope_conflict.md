### Fixed

- **`dump(..., dump_manifest=...)` silently accepted a `public_include_
  search_dirs` argument alongside a multi-TU manifest.** A manifest merges
  declarations from several independently resolved translation units, each
  with its own per-TU include roots -- unlike `public_headers`/
  `public_header_dirs` (replaced by the manifest's own equivalent fields), a
  single flat `public_include_search_dirs` list has no per-TU replacement,
  so applying it unconditionally would misapply one TU's caller-supplied
  explicit roots to every TU's declarations. Fixed by rejecting the
  combination with the same `ValidationError` every other manifest-
  incompatible parameter already raises, instead of silently misapplying it.
