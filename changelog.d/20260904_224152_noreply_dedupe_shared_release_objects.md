### Fixed

- **Stored-release materialization no longer duplicates shared objects per
  artifact** — `materialize_release_variant_artifacts` now hard-links a
  variant-/project-level object (composition, manifest) into every
  single-artifact sub-package after the first materializes it, instead of
  re-reading and re-writing it from the source package once per artifact —
  closing an N-fold temporary-disk blow-up for a many-artifact release
  (e.g. 100 artifacts sharing a 100 MB section previously consumed ~10 GB).
- **Reject a malformed `library_filenames` composition table** —
  `storage.variant_composition.read_variant_composition_library_filenames`
  now validates that a stored `library_filenames` payload is an object of
  string to string, instead of silently normalizing a malformed shape
  (e.g. an iterable of pairs) through `dict(...)`, which could pair a
  stored snapshot with the wrong library or mask a real ABI comparison.
