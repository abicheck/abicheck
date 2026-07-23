<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scope_fingerprint`'s merged `headers` field didn't deduplicate**
  (Codex review, PR #624): a side naming the same logical header through
  both `declared_headers` and `public_header_paths` (a full L2 dump that
  also passes `--public-header` for that same file — a real CLI
  combination) retained a duplicate entry (`["foo.h", "foo.h"]`), which
  mismatched a side naming it only once (`["foo.h"]`) purely on element
  count, despite describing the identical declared surface. Normalized
  entries are now deduplicated (`sorted(set(...))`) before hashing.
