<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A content-driven language-mode difference between two unpinned header
  dumps no longer blocks comparison** — when neither side of a `compare`
  pins `--lang` explicitly, the two sides' headers can legitimately
  auto-detect into different C/C++ language modes (e.g. an `extern "C"`
  wrapper removed, or a C++-only destructor added), which previously
  tripped the toolchain-probe comparability gate added for unpinned
  `language_standard` defaults and raised `ProfileMismatchError` instead of
  producing a verdict. A new, narrower carve-out
  (`comparability._language_standard_content_divergence_corroborated`)
  waives this specific divergence once the resolved compiler identity is
  independently confirmed unchanged on both sides, since the difference is
  real signal about the library's own headers, not evidence of a mismatched
  extraction environment.

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
