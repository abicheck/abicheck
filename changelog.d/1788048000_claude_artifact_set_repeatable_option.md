### Changed

- **`scan --artifact-set` now takes a repeatable option instead of a
  comma-separated string.** `abicheck scan --artifact-set a.so --artifact-set
  b.so --artifact-set c.so` replaces `--artifact-set a.so,b.so,c.so`, which is
  no longer accepted (no deprecation alias, consistent with this repo's
  "hard cleanup" stance — CLI cleanup phase two, PR 5). A single directory
  value (`--artifact-set some/dir/`) is unchanged. The composite GitHub
  Action's `new-library-set` input keeps its own comma-separated contract —
  `action/run.sh` now splits it into one `--artifact-set` occurrence per
  member — so existing Action workflows are unaffected.
