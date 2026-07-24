<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
### Fixed

- **C++20 auto-detection now accepts parenthesized `__cplusplus`
  fallback-guard comparisons and follows quoted `#include`s when scanning
  for C++20 syntax.** `#if (__cplusplus < 202002L)` (parenthesized, the
  same idiom already accepted for `#if (0)`/`#if (1)`) previously fell
  through to the wrong masking bucket, hiding a genuine C++20 construct in
  the guarded arm or wrongly trusting a circular `#else`. Separately, an
  umbrella header whose only C++20 signal lived in a file it
  `#include`d (quoted, resolved relative to the including file's own
  directory) went undetected because header scanning never followed
  `#include` directives; it now transitively expands quoted includes
  (cycle-safe, angle-bracket includes still left alone) before scanning.

-->
<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
