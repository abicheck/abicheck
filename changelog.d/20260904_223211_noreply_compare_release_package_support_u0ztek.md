### Fixed

- **Stored-release bundle analysis reconciled with main's writer
  consolidation** — after merging main's retirement of the duplicate
  `BundleFacts`-package writer shapes, `bundle._stored_library_identity`'s
  composition fallback now correctly threads the alias-node budget,
  validates `ObjectRef.kind`, resolves an empty-string bundle key, and
  rejects a malformed `library_filenames`/`filesystem_aliases` shape
  instead of raising `AttributeError` or silently splitting a string into
  per-character aliases. `write_bundle_facts_out` now carries each diff
  pair's own matched release key directly instead of re-deriving one from
  `DiffResult.library`, which could insert the same stored library twice
  under two different logical keys. A stored package's declared-but-
  malformed evidence during bundle analysis is now a usage error instead
  of a silently skipped warning, and `--dso-only` now warns when it
  excludes a stored member for missing/ambiguous ELF evidence.
