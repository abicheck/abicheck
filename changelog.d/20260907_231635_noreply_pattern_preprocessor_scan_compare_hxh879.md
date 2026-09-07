<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The export-evidence C++ language-mode fallback's per-header
  correlation (added in the prior fragment above) no longer trusts
  identifiers that only appear in a header's comments, string literals, or
  preprocessor directives.** `dumper_ast_config._header_declared_identifiers`
  previously scanned a header's raw bytes wholesale before correlating a
  candidate export against it; a name mentioned only in a `//` comment, a
  string, or a `#define` body could still correlate against an unrelated
  C++ export elsewhere in the binary and wrongly force a genuinely plain-C
  header into C++ mode — the same failure class the correlation was added
  to close, reached through a different door.
- **`compare()`'s automatic pattern/preprocessor pre-scan no longer reports
  `introduced`/`resolved` from a partially-covered scan.** A side with
  skipped pattern files or a preprocessor probe that failed or was
  truncated by the probe cap now folds as `not_evaluated`, matching the
  already-established rule for a side with no evidence at all — a
  construct or macro divergence absent only because of incomplete coverage
  must not read as a confirmed addition or removal.
